-- One authority predicate for direct reads and filtering before lexical/vector ranking.
CREATE FUNCTION sources.valid_reference(ref jsonb, nodes jsonb, viewer text[], require_access boolean,
                                        visited text[] DEFAULT '{}') RETURNS boolean
LANGUAGE plpgsql IMMUTABLE AS $$
DECLARE key text := (ref->>'source_id') || ':' || (ref->>'revision'); node jsonb;
        deps jsonb; dep jsonb; fragment jsonb; ok boolean; a bigint; b bigint;
BEGIN
    IF key IS NULL OR key=ANY(visited) THEN RETURN false; END IF;
    IF cardinality(visited)>64 THEN RAISE EXCEPTION 'source dependency depth' USING ERRCODE='54000'; END IF;
    node := nodes->key;
    IF node IS NULL OR NOT COALESCE((node->>'valid')::boolean,false) THEN RETURN false; END IF;
    IF require_access AND NOT ARRAY(SELECT jsonb_array_elements_text(node->'scopes')) <@ viewer THEN RETURN false; END IF;
    IF (ref->'locator') ? 'start' OR (ref->'locator') ? 'end' THEN
        IF jsonb_typeof(ref->'locator'->'start') IS DISTINCT FROM 'number'
           OR jsonb_typeof(ref->'locator'->'end') IS DISTINCT FROM 'number'
           OR (ref->'locator'->>'start') !~ '^[0-9]+$' OR (ref->'locator'->>'end') !~ '^[0-9]+$'
        THEN RETURN false; END IF;
        a := (ref->'locator'->>'start')::bigint; b := (ref->'locator'->>'end')::bigint;
        IF node->>'length' IS NULL OR a>b OR b>(node->>'length')::bigint THEN RETURN false; END IF;
    END IF;
    FOR fragment IN SELECT value FROM jsonb_array_elements(node->'forgotten') LOOP
        IF a IS NULL OR b IS NULL OR (a<(fragment->>'end')::bigint AND b>(fragment->>'start')::bigint)
        THEN RETURN false; END IF;
    END LOOP;
    IF node->'groups'='[]'::jsonb THEN RETURN true; END IF;
    FOR deps IN SELECT value FROM jsonb_array_elements(node->'groups') LOOP
        ok := true;
        FOR dep IN SELECT value FROM jsonb_array_elements(deps) LOOP
            IF NOT sources.valid_reference(dep,nodes,viewer,
                require_access AND NOT (node->>'disclosure')::boolean,visited||key)
            THEN ok := false; EXIT; END IF;
        END LOOP;
        IF ok AND jsonb_array_length(deps)>0 THEN RETURN true; END IF;
    END LOOP;
    RETURN false;
END $$;

CREATE FUNCTION sources.check_groups(ref_groups jsonb, viewer text[]) RETURNS boolean[]
LANGUAGE plpgsql STABLE AS $$
DECLARE nodes jsonb; count_nodes bigint; result boolean[] := '{}'; refs jsonb; ref jsonb; ok boolean;
BEGIN
    WITH RECURSIVE selected(source_id,revision) AS (
        SELECT ref->>'source_id',(ref->>'revision')::bigint
        FROM jsonb_array_elements(ref_groups) g CROSS JOIN LATERAL jsonb_array_elements(g) ref
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
                FROM sources.supports d WHERE d.source_id=s.source_id AND d.revision=s.revision GROUP BY d.group_id) g),'[]')) AS node
        FROM selected s LEFT JOIN sources.originals o USING(source_id)
        LEFT JOIN sources.versions v ON v.source_id=s.source_id AND v.revision=s.revision LIMIT 8193
    ) SELECT jsonb_object_agg(key,node),count(*) INTO nodes,count_nodes FROM facts;
    IF count_nodes>8192 THEN RAISE EXCEPTION 'source dependency budget' USING ERRCODE='54000'; END IF;
    FOR refs IN SELECT value FROM jsonb_array_elements(ref_groups) LOOP
        ok := true;
        FOR ref IN SELECT value FROM jsonb_array_elements(refs) LOOP
            IF NOT sources.valid_reference(ref,COALESCE(nodes,'{}'),viewer,true) THEN ok := false; EXIT; END IF;
        END LOOP;
        result := array_append(result,ok);
    END LOOP;
    RETURN result;
END $$;

CREATE FUNCTION sources.any_support_usable(ref_groups jsonb, viewer text[]) RETURNS boolean
LANGUAGE sql STABLE AS $$ SELECT COALESCE(true=ANY(sources.check_groups(ref_groups,viewer)),false) $$;

CREATE FUNCTION sources.dependencies_intersect(refs jsonb, changes jsonb, reason text) RETURNS boolean
LANGUAGE plpgsql IMMUTABLE AS $$
DECLARE own jsonb; changed jsonb; a jsonb; b jsonb;
BEGIN
    FOR own IN SELECT value FROM jsonb_array_elements(COALESCE(refs,'[]')) LOOP
        FOR changed IN SELECT value FROM jsonb_array_elements(changes) LOOP
            IF own->>'source_id' IS DISTINCT FROM changed->>'source_id'
               OR own->>'revision' IS DISTINCT FROM changed->>'revision' THEN CONTINUE; END IF;
            IF reason='fragment_forgotten' THEN
                a:=COALESCE(own->'locator','{}'); b:=COALESCE(changed->'locator','{}');
                IF jsonb_typeof(a->'start')='number' AND jsonb_typeof(a->'end')='number'
                   AND jsonb_typeof(b->'start')='number' AND jsonb_typeof(b->'end')='number'
                   AND a->>'unit' IS NOT DISTINCT FROM b->>'unit'
                   AND ((a->>'end')::numeric<=(b->>'start')::numeric OR (b->>'end')::numeric<=(a->>'start')::numeric)
                THEN CONTINUE; END IF;
            END IF;
            RETURN true;
        END LOOP;
    END LOOP;
    RETURN false;
END $$;
