-- Reuse a transaction-local locked closure; re-evaluate all authority facts.

CREATE OR REPLACE FUNCTION sources._rbj_lock_select(refs jsonb, extra text[], aliases text[], budget jsonb)
RETURNS jsonb LANGUAGE plpgsql STABLE SET jit = off AS $$
DECLARE
    nodes_left bigint := (budget->>'nodes')::bigint;
    edges_left bigint := (budget->>'edges')::bigint;
    work_left bigint := (budget->>'work')::bigint;
    inputs_left bigint := (budget->>'inputs')::bigint;
    bytes_left bigint := (budget->>'bytes')::bigint;
    pairs_seen jsonb[] := array_fill('{}'::jsonb, ARRAY[128]);
    ids_seen jsonb[] := array_fill('{}'::jsonb, ARRAY[128]);
    aliases_seen jsonb[] := array_fill('{}'::jsonb, ARRAY[128]);
    source_ids text[] := ARRAY[]::text[];
    frontier_ids text[] := ARRAY[]::text[];
    frontier_revisions bigint[] := ARRAY[]::bigint[];
    next_ids text[];
    next_revisions bigint[];
    pending_aliases text[] := ARRAY[]::text[];
    entry record;
    source_id text;
    alias_key text;
    pair_key text;
    pair_slot integer;
    id_slot integer;
    alias_slot integer;
BEGIN
    inputs_left := inputs_left - jsonb_array_length(refs) - cardinality(extra) - cardinality(aliases);
    work_left := work_left - jsonb_array_length(refs) - cardinality(extra) - cardinality(aliases);
    bytes_left := bytes_left - pg_column_size(refs) - pg_column_size(extra) - pg_column_size(aliases);
    IF inputs_left < 0 OR work_left < 0 OR bytes_left < 0 THEN
        RAISE EXCEPTION 'source_dependency_budget_exceeded' USING ERRCODE='54000';
    END IF;
    -- Reference roots plus current heads of extra roots; nonexistent extra
    -- identities are retained below to protect a future insertion.
    FOR entry IN
        SELECT r.value->>'source_id' AS source_id, (r.value->>'revision')::bigint AS revision
        FROM jsonb_array_elements(refs) r
        UNION ALL
        SELECT o.source_id, o.head_revision FROM unnest(extra) AS supplied(source_id)
        CROSS JOIN LATERAL (
            SELECT original.source_id, original.head_revision FROM sources.originals original
            WHERE original.source_id=supplied.source_id LIMIT 1
        ) o
    LOOP
        pair_key := entry.source_id || ':' || entry.revision;
        IF pair_key IS NULL THEN RAISE EXCEPTION 'invalid_source_reference' USING ERRCODE='22023'; END IF;
        pair_slot := ((hashtextextended(pair_key, 0) & 127)::integer) + 1;
        IF NOT (pairs_seen[pair_slot] ? pair_key) THEN
            nodes_left := nodes_left - 1;
            work_left := work_left - 1;
            bytes_left := bytes_left - octet_length(entry.source_id) - 8;
            IF nodes_left < 0 OR work_left < 0 OR bytes_left < 0 THEN
                RAISE EXCEPTION 'source_dependency_budget_exceeded' USING ERRCODE='54000';
            END IF;
            pairs_seen[pair_slot] := pairs_seen[pair_slot] || jsonb_build_object(pair_key, jsonb_build_object('source_id', entry.source_id, 'revision', entry.revision));
            frontier_ids := array_append(frontier_ids, entry.source_id);
            frontier_revisions := array_append(frontier_revisions, entry.revision);
            id_slot := ((hashtextextended(entry.source_id, 0) & 127)::integer) + 1;
            IF NOT (ids_seen[id_slot] ? entry.source_id) THEN
                ids_seen[id_slot] := ids_seen[id_slot] || jsonb_build_object(entry.source_id, true);
                source_ids := array_append(source_ids, entry.source_id);
            END IF;
        END IF;
    END LOOP;
    FOREACH source_id IN ARRAY extra LOOP
        id_slot := ((hashtextextended(source_id, 0) & 127)::integer) + 1;
        IF NOT (ids_seen[id_slot] ? source_id) THEN
            nodes_left := nodes_left - 1;
            work_left := work_left - 1;
            bytes_left := bytes_left - octet_length(source_id);
            IF nodes_left < 0 OR work_left < 0 OR bytes_left < 0 THEN
                RAISE EXCEPTION 'source_dependency_budget_exceeded' USING ERRCODE='54000';
            END IF;
            ids_seen[id_slot] := ids_seen[id_slot] || jsonb_build_object(source_id, true);
            source_ids := array_append(source_ids, source_id);
        END IF;
    END LOOP;
    FOREACH alias_key IN ARRAY aliases LOOP
        bytes_left := bytes_left - octet_length(alias_key);
        IF bytes_left < 0 THEN RAISE EXCEPTION 'source_dependency_budget_exceeded' USING ERRCODE='54000'; END IF;
        alias_slot := ((hashtextextended(alias_key, 0) & 127)::integer) + 1;
        IF NOT (aliases_seen[alias_slot] ? alias_key) THEN
            aliases_seen[alias_slot] := aliases_seen[alias_slot] || jsonb_build_object(alias_key, true);
            pending_aliases := array_append(pending_aliases, alias_key);
        END IF;
    END LOOP;

    WHILE cardinality(frontier_ids) > 0 OR cardinality(pending_aliases) > 0 LOOP
        next_ids := ARRAY[]::text[];
        next_revisions := ARRAY[]::bigint[];
        -- Find alias keys attached to this exact revision, once per row.
        FOR entry IN
            SELECT a.alias_key, octet_length(a.alias_key) AS bytes
            FROM unnest(frontier_ids, frontier_revisions) AS f(source_id, revision)
            CROSS JOIN LATERAL (
                SELECT k.alias_key FROM sources.source_aliases k
                WHERE k.source_id=f.source_id AND k.revision=f.revision
                LIMIT GREATEST(LEAST(edges_left, work_left), 0) + 1
            ) a
            LIMIT GREATEST(LEAST(edges_left, work_left), 0) + 1
        LOOP
            edges_left := edges_left - 1;
            work_left := work_left - 1;
            bytes_left := bytes_left - entry.bytes;
            IF edges_left < 0 OR work_left < 0 OR bytes_left < 0 THEN
                RAISE EXCEPTION 'source_dependency_budget_exceeded' USING ERRCODE='54000';
            END IF;
            alias_slot := ((hashtextextended(entry.alias_key, 0) & 127)::integer) + 1;
            IF NOT (aliases_seen[alias_slot] ? entry.alias_key) THEN
                aliases_seen[alias_slot] := aliases_seen[alias_slot] || jsonb_build_object(entry.alias_key, true);
                pending_aliases := array_append(pending_aliases, entry.alias_key);
            END IF;
        END LOOP;
        -- A shared alias expands once, avoiding the old quadratic alias self-join.
        FOR entry IN
            SELECT * FROM (
                SELECT d.support_source_id AS source_id, d.support_revision AS revision
                FROM unnest(frontier_ids, frontier_revisions) AS f(source_id, revision)
                CROSS JOIN LATERAL (
                    SELECT s.support_source_id, s.support_revision FROM sources.supports s
                    WHERE s.source_id=f.source_id AND s.revision=f.revision
                    LIMIT GREATEST(LEAST(edges_left, work_left), 0) + 1
                ) d
                UNION ALL
                SELECT m.source_id, m.revision FROM unnest(pending_aliases) AS a(alias_key)
                CROSS JOIN LATERAL (
                    SELECT s.source_id, s.revision FROM sources.source_aliases s
                    WHERE s.alias_key=a.alias_key LIMIT GREATEST(LEAST(edges_left, work_left), 0) + 1
                ) m
            ) reached
            LIMIT GREATEST(LEAST(edges_left, work_left), 0) + 1
        LOOP
            edges_left := edges_left - 1;
            work_left := work_left - 1;
            bytes_left := bytes_left - octet_length(entry.source_id) - 8;
            IF edges_left < 0 OR work_left < 0 OR bytes_left < 0 THEN
                RAISE EXCEPTION 'source_dependency_budget_exceeded' USING ERRCODE='54000';
            END IF;
            pair_key := entry.source_id || ':' || entry.revision;
            pair_slot := ((hashtextextended(pair_key, 0) & 127)::integer) + 1;
            IF NOT (pairs_seen[pair_slot] ? pair_key) THEN
                nodes_left := nodes_left - 1;
                work_left := work_left - 1;
                IF nodes_left < 0 OR work_left < 0 THEN
                    RAISE EXCEPTION 'source_dependency_budget_exceeded' USING ERRCODE='54000';
                END IF;
                pairs_seen[pair_slot] := pairs_seen[pair_slot] || jsonb_build_object(pair_key, jsonb_build_object('source_id', entry.source_id, 'revision', entry.revision));
                next_ids := array_append(next_ids, entry.source_id);
                next_revisions := array_append(next_revisions, entry.revision);
                id_slot := ((hashtextextended(entry.source_id, 0) & 127)::integer) + 1;
                IF NOT (ids_seen[id_slot] ? entry.source_id) THEN
                    ids_seen[id_slot] := ids_seen[id_slot] || jsonb_build_object(entry.source_id, true);
                    source_ids := array_append(source_ids, entry.source_id);
                END IF;
            END IF;
        END LOOP;
        pending_aliases := ARRAY[]::text[];
        frontier_ids := next_ids;
        frontier_revisions := next_revisions;
    END LOOP;
    RETURN jsonb_build_object('ids', to_jsonb(source_ids), 'pairs',
        (SELECT COALESCE(jsonb_agg(pair.value), '[]'::jsonb) FROM unnest(pairs_seen) bucket
         CROSS JOIN LATERAL jsonb_each(bucket) pair), 'remaining', jsonb_build_object(
        'nodes', nodes_left, 'edges', edges_left, 'work', work_left,
        'inputs', inputs_left, 'bytes', bytes_left));
END $$;

CREATE OR REPLACE FUNCTION sources._rbj_check_one(
    ref_groups jsonb, viewer text[], max_nodes bigint, max_edges bigint, max_work bigint,
    max_inputs bigint, max_bytes bigint, locked_pairs jsonb
) RETURNS jsonb LANGUAGE plpgsql STABLE AS $$
DECLARE
    refs jsonb;
    root jsonb;
    candidate record;
    source_key text;
    slot integer;
    seen jsonb[] := array_fill('{}'::jsonb, ARRAY[128]);
    input_count bigint := 0;
    count_nodes bigint := 0;
    count_edges bigint := 0;
    count_fragments bigint := 0;
    graph_bytes bigint := 0;
    collected_work bigint := 0;
    ids text[] := ARRAY[]::text[];
    revisions bigint[] := ARRAY[]::bigint[];
    frontier_ids text[] := ARRAY[]::text[];
    frontier_revisions bigint[] := ARRAY[]::bigint[];
    next_ids text[];
    next_revisions bigint[];
    nodes jsonb;
    evaluated jsonb;
BEGIN
    IF pg_column_size(ref_groups) > 16777216 THEN
        RAISE EXCEPTION 'source_dependency_budget_exceeded' USING ERRCODE='54000';
    END IF;
    FOR refs IN SELECT value FROM jsonb_array_elements(ref_groups) LOOP
        input_count := input_count + 1 + jsonb_array_length(refs);
        IF input_count > max_inputs OR input_count > max_work THEN
            RAISE EXCEPTION 'source_dependency_budget_exceeded' USING ERRCODE='54000';
        END IF;
    END LOOP;
    collected_work := input_count;
    IF locked_pairs IS NOT NULL THEN
        IF jsonb_typeof(locked_pairs) <> 'array' OR jsonb_array_length(locked_pairs) > 8192 THEN
            RAISE EXCEPTION 'source_proof_incomplete' USING ERRCODE='22023';
        END IF;
        -- Closure discovery already ran after ordered row locks. Load its exact
        -- version pairs directly; authorization and content facts remain fresh.
        FOR root IN SELECT value FROM jsonb_array_elements(locked_pairs) LOOP
            source_key := (root->>'source_id') || ':' || ((root->>'revision')::bigint)::text;
            slot := ((hashtextextended(source_key, 0) & 127)::integer) + 1;
            seen[slot] := seen[slot] || jsonb_build_object(source_key, true);
            ids := array_append(ids, root->>'source_id');
            revisions := array_append(revisions, (root->>'revision')::bigint);
        END LOOP;
        collected_work := collected_work + cardinality(ids);
        -- No recursive discovery here. Bound the fresh authority projection and
        -- reject an in-transaction dependency extension beyond the locked set.
        FOR candidate IN
            SELECT d.support_source_id, d.support_revision
            FROM unnest(ids, revisions) AS s(source_id, revision)
            CROSS JOIN LATERAL (
                SELECT edge.support_source_id, edge.support_revision FROM sources.supports edge
                WHERE edge.source_id=s.source_id AND edge.revision=s.revision
                LIMIT GREATEST(max_work - collected_work, 0) + 1
            ) d LIMIT GREATEST(max_work - collected_work, 0) + 1
        LOOP
            collected_work := collected_work + 1;
            IF collected_work > max_work THEN
                RAISE EXCEPTION 'source_dependency_budget_exceeded' USING ERRCODE='54000';
            END IF;
            source_key := candidate.support_source_id || ':' || candidate.support_revision;
            slot := ((hashtextextended(source_key, 0) & 127)::integer) + 1;
            IF NOT (seen[slot] ? source_key) THEN
                RAISE EXCEPTION 'source_lock_set_changed' USING ERRCODE='40001';
            END IF;
        END LOOP;
        IF collected_work > max_work THEN
            RAISE EXCEPTION 'source_dependency_budget_exceeded' USING ERRCODE='54000';
        END IF;
    ELSE
        FOR refs IN SELECT value FROM jsonb_array_elements(ref_groups) LOOP
            FOR root IN SELECT value FROM jsonb_array_elements(refs) LOOP
                source_key := (root->>'source_id') || ':' || ((root->>'revision')::bigint)::text;
                IF source_key IS NULL THEN
                    RAISE EXCEPTION 'field name must not be null' USING ERRCODE='22023';
                END IF;
                slot := ((hashtextextended(source_key, 0) & 127)::integer) + 1;
                IF NOT (seen[slot] ? source_key) THEN
                    count_nodes := count_nodes + 1;
                    collected_work := collected_work + 1;
                    graph_bytes := graph_bytes + octet_length(root->>'source_id') + 8;
                    IF count_nodes > max_nodes OR collected_work > max_work OR graph_bytes > max_bytes THEN
                        RAISE EXCEPTION 'source_dependency_budget_exceeded' USING ERRCODE='54000';
                    END IF;
                    seen[slot] := seen[slot] || jsonb_build_object(source_key, true);
                    ids := array_append(ids, root->>'source_id');
                    revisions := array_append(revisions, (root->>'revision')::bigint);
                    frontier_ids := array_append(frontier_ids, root->>'source_id');
                    frontier_revisions := array_append(frontier_revisions, (root->>'revision')::bigint);
                END IF;
            END LOOP;
        END LOOP;

        -- Expand each newly reached node once. Limit and count actual outgoing
        -- edges while scanning, before another frontier or graph JSON is built.
        WHILE cardinality(frontier_ids) > 0 LOOP
            next_ids := ARRAY[]::text[];
            next_revisions := ARRAY[]::bigint[];
            FOR candidate IN
                SELECT d.support_source_id, d.support_revision, d.bytes
                FROM unnest(frontier_ids, frontier_revisions) AS s(source_id, revision)
                CROSS JOIN LATERAL (
                    SELECT edge.support_source_id, edge.support_revision,
                           octet_length(edge.support_locator::text) + octet_length(edge.support_source_id)
                               + octet_length(edge.group_id) + octet_length(edge.support_key) + 8 AS bytes
                    FROM sources.supports edge
                    WHERE edge.source_id=s.source_id AND edge.revision=s.revision
                    LIMIT GREATEST(max_edges - count_edges, 0) + 1
                ) d
                LIMIT GREATEST(max_edges - count_edges, 0) + 1
            LOOP
                count_edges := count_edges + 1;
                collected_work := collected_work + 1;
                graph_bytes := graph_bytes + candidate.bytes;
                IF count_edges > max_edges OR collected_work > max_work OR graph_bytes > max_bytes THEN
                    RAISE EXCEPTION 'source_dependency_budget_exceeded' USING ERRCODE='54000';
                END IF;
                source_key := candidate.support_source_id || ':' || candidate.support_revision;
                slot := ((hashtextextended(source_key, 0) & 127)::integer) + 1;
                IF NOT (seen[slot] ? source_key) THEN
                    count_nodes := count_nodes + 1;
                    collected_work := collected_work + 1;
                    graph_bytes := graph_bytes + octet_length(candidate.support_source_id) + 8;
                    IF count_nodes > max_nodes OR collected_work > max_work OR graph_bytes > max_bytes THEN
                        RAISE EXCEPTION 'source_dependency_budget_exceeded' USING ERRCODE='54000';
                    END IF;
                    seen[slot] := seen[slot] || jsonb_build_object(source_key, true);
                    ids := array_append(ids, candidate.support_source_id);
                    revisions := array_append(revisions, candidate.support_revision);
                    next_ids := array_append(next_ids, candidate.support_source_id);
                    next_revisions := array_append(next_revisions, candidate.support_revision);
                END IF;
            END LOOP;
            frontier_ids := next_ids;
            frontier_revisions := next_revisions;
        END LOOP;

    END IF;

    SELECT count(*) INTO count_fragments FROM (
        SELECT 1 FROM unnest(ids, revisions) AS s(source_id, revision)
        CROSS JOIN LATERAL (
            SELECT 1 FROM sources.forgotten_fragments f
            WHERE f.source_id=s.source_id AND f.revision=s.revision
            LIMIT GREATEST(max_edges - count_edges, 0) + 1
        ) fragments
        LIMIT GREATEST(max_edges - count_edges, 0) + 1
    ) bounded;
    collected_work := collected_work + count_fragments;
    graph_bytes := graph_bytes + count_fragments * 48;
    IF count_edges + count_fragments > max_edges OR collected_work > max_work OR graph_bytes > max_bytes THEN
        RAISE EXCEPTION 'source_dependency_budget_exceeded' USING ERRCODE='54000';
    END IF;
    FOR candidate IN
        SELECT octet_length(array_to_string(v.scope_ids, ','))
                   + octet_length(v.scope_epochs::text) AS bytes
        FROM unnest(ids, revisions) AS s(source_id, revision)
        JOIN sources.versions v ON v.source_id=s.source_id AND v.revision=s.revision
    LOOP
        graph_bytes := graph_bytes + candidate.bytes;
        IF graph_bytes > max_bytes THEN
            RAISE EXCEPTION 'source_dependency_budget_exceeded' USING ERRCODE='54000';
        END IF;
    END LOOP;

    SELECT COALESCE(jsonb_object_agg(key, node), '{}'::jsonb) INTO nodes FROM (
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
                FROM sources.supports d WHERE d.source_id=s.source_id AND d.revision=s.revision GROUP BY d.group_id) g),'[]')) AS node
        FROM unnest(ids, revisions) AS s(source_id, revision)
        LEFT JOIN sources.originals o USING(source_id)
        LEFT JOIN sources.versions v ON v.source_id=s.source_id AND v.revision=s.revision
    ) facts;
    IF locked_pairs IS NOT NULL THEN
        graph_bytes := pg_column_size(nodes);
        IF graph_bytes > max_bytes THEN
            RAISE EXCEPTION 'source_dependency_budget_exceeded' USING ERRCODE='54000';
        END IF;
    END IF;
    evaluated := sources._rbj_evaluate(ref_groups, viewer, nodes, max_work - collected_work);
    RETURN jsonb_build_object('allowed', evaluated->'allowed', 'nodes', count_nodes,
        'edges', count_edges + count_fragments, 'inputs', input_count, 'bytes', graph_bytes,
        'work', collected_work + (evaluated->>'work')::bigint,
        'non_acyclic_nodes', (evaluated->>'non_acyclic_nodes')::bigint);
END $$;

CREATE OR REPLACE FUNCTION sources._rbj_check_one(
    ref_groups jsonb, viewer text[], max_nodes bigint, max_edges bigint, max_work bigint,
    max_inputs bigint, max_bytes bigint
) RETURNS jsonb LANGUAGE sql STABLE AS $$
    SELECT sources._rbj_check_one(ref_groups, viewer, max_nodes, max_edges, max_work,
                                 max_inputs, max_bytes, NULL::jsonb)
$$;

CREATE OR REPLACE FUNCTION sources._rbj_check_locked_contexts(requests jsonb, locked_pairs jsonb, budget jsonb) RETURNS jsonb
LANGUAGE plpgsql STABLE SET jit = off AS $$
DECLARE
    remaining_nodes bigint := COALESCE((budget->>'nodes')::bigint, 8192);
    remaining_edges bigint := COALESCE((budget->>'edges')::bigint, 65536);
    remaining_work bigint := COALESCE((budget->>'work')::bigint, 1000000);
    remaining_inputs bigint := COALESCE((budget->>'inputs')::bigint, 32768);
    remaining_bytes bigint := COALESCE((budget->>'bytes')::bigint, 33554432);
    request jsonb;
    viewer text[];
    checked jsonb;
    output jsonb := '[]'::jsonb;
BEGIN
    IF pg_column_size(requests) > 16777216 THEN
        RAISE EXCEPTION 'source_dependency_budget_exceeded' USING ERRCODE='54000';
    END IF;
    FOR request IN SELECT value FROM jsonb_array_elements(requests) LOOP
        remaining_work := remaining_work - 1;
        IF remaining_work < 0 THEN
            RAISE EXCEPTION 'source_dependency_budget_exceeded' USING ERRCODE='54000';
        END IF;
        IF jsonb_typeof(request->'viewer') = 'array' THEN
            viewer := ARRAY(SELECT value FROM jsonb_array_elements_text(request->'viewer'));
        ELSE
            viewer := NULL;
        END IF;
        checked := sources._rbj_check_one(request->'groups', viewer,
            remaining_nodes, remaining_edges, remaining_work, remaining_inputs, remaining_bytes, locked_pairs);
        remaining_nodes := remaining_nodes - (checked->>'nodes')::bigint;
        remaining_edges := remaining_edges - (checked->>'edges')::bigint;
        remaining_work := remaining_work - (checked->>'work')::bigint;
        remaining_inputs := remaining_inputs - (checked->>'inputs')::bigint;
        remaining_bytes := remaining_bytes - (checked->>'bytes')::bigint;
        RAISE DEBUG 'source_proof nodes=% edges=% work=% non_acyclic_nodes=%',
            checked->>'nodes', checked->>'edges', checked->>'work', checked->>'non_acyclic_nodes';
        output := output || jsonb_build_array(checked->'allowed');
        IF COALESCE((request->>'stop_if_denied')::boolean, false)
           AND EXISTS (SELECT 1 FROM jsonb_array_elements_text(checked->'allowed') a(value)
                       WHERE value::boolean IS FALSE) THEN
            EXIT;
        END IF;
    END LOOP;
    RETURN jsonb_build_object('allowed', output, 'remaining', jsonb_build_object(
        'nodes', remaining_nodes, 'edges', remaining_edges, 'work', remaining_work,
        'inputs', remaining_inputs, 'bytes', remaining_bytes));
END $$;
