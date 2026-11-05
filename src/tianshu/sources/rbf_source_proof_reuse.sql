-- Reuse proofs only inside this call, for graphs where that reuse is independent
-- of the recursion path. NULL means the existing recursive evaluator must run.
CREATE OR REPLACE FUNCTION sources._proof_acyclic_plain(
    ref_groups jsonb, viewer text[], nodes jsonb, expected_nodes bigint
) RETURNS boolean[] LANGUAGE plpgsql IMMUTABLE AS $$
DECLARE edge_count bigint; child_count bigint; other_locators bigint;
        entry record; visited_nodes bigint := 0;
        buckets jsonb[] := array_fill('{}'::jsonb, ARRAY[64]);
        slot integer; child_key text; child_status jsonb; own_status jsonb;
        isolated jsonb; local_any boolean; local_access boolean;
        support_any boolean; support_access boolean;
        group_any boolean; group_access boolean;
        deps jsonb; dep jsonb; refs jsonb; ref jsonb; key text; node jsonb;
        allowed boolean; result boolean[] := '{}';
BEGIN
    -- A nonempty support locator must retain the original lazy range checking.
    -- Shared edges are the case this path is intended to accelerate.
    SELECT count(*), count(DISTINCT selected_edges.child_key),
        count(*) FILTER (WHERE selected_edges.locator IS DISTINCT FROM '{}'::jsonb)
    INTO edge_count, child_count, other_locators
    FROM (
        SELECT ((d.ref->>'source_id') || ':' || (d.ref->>'revision')) COLLATE "C" AS child_key,
               d.ref->'locator' AS locator
        FROM jsonb_each(nodes) n
        CROSS JOIN LATERAL jsonb_array_elements(n.value->'groups') g(refs)
        CROSS JOIN LATERAL jsonb_array_elements(g.refs) d(ref)
    ) selected_edges;
    IF edge_count = 0 OR edge_count = child_count OR other_locators > 0 THEN
        RETURN NULL;
    END IF;

    -- Deduplicate (node, height), not whole paths. A missing ordering, a cycle,
    -- or a path deeper than the original 64-level limit falls back unchanged.
    FOR entry IN
        WITH RECURSIVE graph_nodes AS MATERIALIZED (
            SELECT j.key COLLATE "C" AS key, j.value AS node FROM jsonb_each(nodes) j
        ), graph_edges AS MATERIALIZED (
            SELECT n.key AS parent,
                ((d.ref->>'source_id') || ':' || (d.ref->>'revision')) COLLATE "C" AS child
            FROM graph_nodes n
            CROSS JOIN LATERAL jsonb_array_elements(n.node->'groups') g(refs)
            CROSS JOIN LATERAL jsonb_array_elements(g.refs) d(ref)
        ), heights(key, height) AS (
            SELECT n.key, 0 FROM graph_nodes n
            WHERE NOT EXISTS (SELECT 1 FROM graph_edges e WHERE e.parent=n.key)
            UNION
            SELECT e.parent, h.height+1 FROM heights h
            JOIN graph_edges e ON e.child=h.key WHERE h.height<64
        ), ordering AS MATERIALIZED (
            SELECT h.key, max(h.height) AS height FROM heights h GROUP BY h.key
        ), safety AS MATERIALIZED (
            SELECT (SELECT count(*) FROM ordering)=expected_nodes
                AND NOT EXISTS (
                    SELECT 1 FROM graph_edges e
                    LEFT JOIN ordering p ON p.key=e.parent
                    LEFT JOIN ordering c ON c.key=e.child
                    WHERE p.key IS NULL OR c.key IS NULL OR p.height<=c.height
                ) AS usable
        )
        SELECT n.key, n.node, safety.usable
        FROM ordering o JOIN graph_nodes n ON n.key=o.key CROSS JOIN safety
        ORDER BY o.height, n.key
    LOOP
        IF NOT entry.usable THEN RETURN NULL; END IF;
        visited_nodes := visited_nodes+1;

        -- Keep the existing source/fragment/scope authority for each node.
        -- The one-node view checks its local facts without descending again.
        isolated := jsonb_build_object(entry.key,
            jsonb_set(entry.node, '{groups}', '[]'::jsonb));
        local_any := sources.valid_reference(entry.node->'_proof_ref', isolated, viewer, false);
        local_access := sources.valid_reference(entry.node->'_proof_ref', isolated, viewer, true);

        IF entry.node->'groups'='[]'::jsonb THEN
            support_any := true;
            support_access := true;
        ELSE
            support_any := false;
            support_access := false;
            FOR deps IN SELECT value FROM jsonb_array_elements(entry.node->'groups') LOOP
                group_any := jsonb_array_length(deps)>0;
                group_access := group_any;
                FOR dep IN SELECT value FROM jsonb_array_elements(deps) LOOP
                    child_key := (dep->>'source_id') || ':' || (dep->>'revision');
                    slot := ((hashtextextended(child_key, 0) & 63)::integer)+1;
                    child_status := buckets[slot]->child_key;
                    IF child_status IS NULL THEN RETURN NULL; END IF;
                    group_any := group_any AND (child_status->>0)::boolean;
                    group_access := group_access AND (child_status->>1)::boolean;
                    IF NOT group_any AND NOT group_access THEN EXIT; END IF;
                END LOOP;
                support_any := support_any OR group_any;
                support_access := support_access OR group_access;
                IF support_any AND support_access THEN EXIT; END IF;
            END LOOP;
            IF (entry.node->>'disclosure')::boolean THEN
                support_access := support_any;
            END IF;
        END IF;
        slot := ((hashtextextended(entry.key, 0) & 63)::integer)+1;
        buckets[slot] := buckets[slot] || jsonb_build_object(entry.key, jsonb_build_array(
            local_any AND support_any, local_access AND support_access,
            support_any, support_access
        ));
    END LOOP;
    IF visited_nodes<>expected_nodes THEN RETURN NULL; END IF;

    -- Keep top-level order and short circuit. For ranged roots, the existing
    -- validator checks the exact locator before the cached support result.
    FOR refs IN SELECT value FROM jsonb_array_elements(ref_groups) LOOP
        allowed := true;
        FOR ref IN SELECT value FROM jsonb_array_elements(refs) LOOP
            key := (ref->>'source_id') || ':' || (ref->>'revision');
            IF key IS NULL THEN allowed := false; EXIT; END IF;
            slot := ((hashtextextended(key, 0) & 63)::integer)+1;
            own_status := buckets[slot]->key;
            IF own_status IS NULL THEN allowed := false; EXIT; END IF;
            IF ref->'locator'='{}'::jsonb THEN
                allowed := (own_status->>1)::boolean;
            ELSE
                node := nodes->key;
                IF node IS NULL THEN allowed := false; EXIT; END IF;
                isolated := jsonb_build_object(key, jsonb_set(node, '{groups}', '[]'::jsonb));
                allowed := sources.valid_reference(ref, isolated, viewer, true)
                           AND (own_status->>3)::boolean;
            END IF;
            IF NOT allowed THEN EXIT; END IF;
        END LOOP;
        result := array_append(result, allowed);
    END LOOP;
    RETURN result;
END $$;

-- The existing function remains the only owner of source closure/authority.
-- Non-eligible graphs take the original recursive path without modification.
CREATE OR REPLACE FUNCTION sources.check_groups(ref_groups jsonb, viewer text[]) RETURNS boolean[]
LANGUAGE plpgsql STABLE SET jit = off AS $$
DECLARE nodes jsonb; count_nodes bigint; result boolean[] := '{}'; refs jsonb; ref jsonb; ok boolean;
        reused boolean[];
BEGIN
    WITH RECURSIVE selected(source_id,revision) AS (
        SELECT entries.value->>'source_id',(entries.value->>'revision')::bigint
        FROM jsonb_array_elements(ref_groups) AS group_items(value)
        CROSS JOIN LATERAL jsonb_array_elements(group_items.value) AS entries(value)
        UNION SELECT d.support_source_id,d.support_revision FROM sources.supports d
        JOIN selected s ON s.source_id=d.source_id AND s.revision=d.revision
    ), facts AS (
        SELECT s.source_id||':'||s.revision AS key,
            jsonb_build_object('valid',COALESCE(o.state='active' AND v.state='active'
                AND o.head_revision=v.revision AND o.intake_epoch=v.intake_epoch
                AND cardinality(v.scope_ids)>0
                AND NOT EXISTS(SELECT 1 FROM unnest(v.scope_ids) AS selected_scope(value) WHERE NOT (v.scope_epochs ? selected_scope.value))
                AND NOT EXISTS(SELECT 1 FROM sources.suppression x WHERE x.source_key=o.source_key)
                AND NOT EXISTS(SELECT 1 FROM jsonb_each_text(v.scope_epochs) e
                    LEFT JOIN identity.source_scope_epochs i ON i.scope_id=e.key
                    WHERE i.intake_epoch IS NULL OR i.intake_epoch<>e.value::bigint),false),
            'scopes',COALESCE(to_jsonb(v.scope_ids),'[]'), 'length',length(v.content),
            'disclosure',COALESCE(v.disclosure,false),
            'forgotten',COALESCE((SELECT jsonb_agg(jsonb_build_object('start',f.start_position,'end',f.end_position))
                FROM sources.forgotten_fragments f WHERE f.source_id=s.source_id AND f.revision=s.revision),'[]'),
            'groups',COALESCE((SELECT jsonb_agg(g.refs) FROM (SELECT jsonb_agg(jsonb_build_object(
                'source_id',d.support_source_id,'revision',d.support_revision,'locator',d.support_locator)) AS refs
                FROM sources.supports d WHERE d.source_id=s.source_id AND d.revision=s.revision GROUP BY d.group_id) g),'[]'),
            '_proof_ref',jsonb_build_object('source_id',s.source_id,'revision',s.revision,'locator','{}'::jsonb)) AS node
        FROM selected s LEFT JOIN sources.originals o USING(source_id)
        LEFT JOIN sources.versions v ON v.source_id=s.source_id AND v.revision=s.revision LIMIT 8193
    ) SELECT jsonb_object_agg(key,node),count(*) INTO nodes,count_nodes FROM facts;
    IF count_nodes>8192 THEN RAISE EXCEPTION 'source dependency budget' USING ERRCODE='54000'; END IF;

    reused := sources._proof_acyclic_plain(ref_groups,viewer,COALESCE(nodes,'{}'),count_nodes);
    IF reused IS NOT NULL THEN RETURN reused; END IF;

    FOR refs IN SELECT value FROM jsonb_array_elements(ref_groups) LOOP
        ok := true;
        FOR ref IN SELECT value FROM jsonb_array_elements(refs) LOOP
            IF NOT sources.valid_reference(ref,COALESCE(nodes,'{}'),viewer,true) THEN ok := false; EXIT; END IF;
        END LOOP;
        result := array_append(result,ok);
    END LOOP;
    RETURN result;
END $$;
