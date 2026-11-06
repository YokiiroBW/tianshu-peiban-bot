-- Bounded, lazy source proofs. The historical recursive authority remains the
-- local reference predicate; no recursive graph fallback or depth cutoff is used.
CREATE OR REPLACE FUNCTION sources._rbj_evaluate(
    ref_groups jsonb, viewer text[], nodes jsonb, max_work bigint
) RETURNS jsonb LANGUAGE plpgsql IMMUTABLE AS $$
DECLARE
    node_keys text[];
    degrees integer[];
    local_nodes jsonb[];
    node_has_groups boolean[];
    positions jsonb;
    parents jsonb;
    safe boolean[];
    queue integer[] := ARRAY[]::integer[];
    cursor_pos integer := 1;
    node_index integer;
    parent_index integer;
    parent_key text;
    i integer;
    work bigint := 0;
    refs jsonb;
    root jsonb;
    current_ref jsonb;
    current_key text;
    current_node jsonb;
    current_access boolean;
    isolated jsonb;
    local_key text;
    cache_key text;
    local_slot integer;
    cache_slot integer;
    active_slot integer;
    local_allowed boolean;
    outcome boolean;
    have_value boolean;
    group_allowed boolean;
    result boolean[] := ARRAY[]::boolean[];
    local_cache jsonb[] := array_fill('{}'::jsonb, ARRAY[128]);
    support_cache jsonb[] := array_fill('{}'::jsonb, ARRAY[128]);
    active jsonb[] := array_fill('{}'::jsonb, ARRAY[128]);
    stack_size integer;
    stack_keys text[] := ARRAY[]::text[];
    stack_groups jsonb[] := ARRAY[]::jsonb[];
    stack_access boolean[] := ARRAY[]::boolean[];
    child_access boolean[] := ARRAY[]::boolean[];
    group_index integer[] := ARRAY[]::integer[];
    child_index integer[] := ARRAY[]::integer[];
    selected_group jsonb;
BEGIN
    -- A node is reusable only if its entire reachable subgraph is acyclic.
    -- Removing leaves counts each edge once and never expands whole paths.
    SELECT COALESCE(array_agg(graph.key ORDER BY graph.key), ARRAY[]::text[]),
           COALESCE(array_agg(graph.degree ORDER BY graph.key), ARRAY[]::integer[]),
           COALESCE(array_agg(graph.local_node ORDER BY graph.key), ARRAY[]::jsonb[]),
           COALESCE(array_agg(graph.needs_support ORDER BY graph.key), ARRAY[]::boolean[])
    INTO node_keys, degrees, local_nodes, node_has_groups
    FROM (
        SELECT n.key, COALESCE((
            SELECT sum(jsonb_array_length(g.value))::integer
            FROM jsonb_array_elements(n.value->'groups') g
        ), 0) AS degree,
        jsonb_set(n.value, '{groups}', '[]'::jsonb) AS local_node,
        n.value->'groups' <> '[]'::jsonb AS needs_support
        FROM jsonb_each(nodes) n
    ) graph;
    SELECT COALESCE(jsonb_object_agg(key, ord), '{}'::jsonb) INTO positions
    FROM unnest(node_keys) WITH ORDINALITY AS entries(key, ord);
    SELECT COALESCE(jsonb_object_agg(child, parent_list), '{}'::jsonb) INTO parents
    FROM (
        SELECT (d.ref->>'source_id') || ':' || (d.ref->>'revision') AS child,
               jsonb_agg(n.key) AS parent_list
        FROM jsonb_each(nodes) n
        CROSS JOIN LATERAL jsonb_array_elements(n.value->'groups') g(refs)
        CROSS JOIN LATERAL jsonb_array_elements(g.refs) d(ref)
        GROUP BY 1
    ) edges;
    safe := array_fill(false, ARRAY[cardinality(node_keys)]);
    FOR i IN 1..cardinality(node_keys) LOOP
        work := work + 1 + degrees[i];
        IF work > max_work THEN
            RAISE EXCEPTION 'source_dependency_budget_exceeded' USING ERRCODE='54000';
        END IF;
        IF degrees[i] = 0 THEN queue := array_append(queue, i); END IF;
    END LOOP;
    WHILE cursor_pos <= cardinality(queue) LOOP
        node_index := queue[cursor_pos];
        safe[node_index] := true;
        FOR parent_key IN SELECT value FROM jsonb_array_elements_text(
            COALESCE(parents->(node_keys[node_index]), '[]'::jsonb)
        ) LOOP
            work := work + 1;
            IF work > max_work THEN
                RAISE EXCEPTION 'source_dependency_budget_exceeded' USING ERRCODE='54000';
            END IF;
            parent_index := (positions->>parent_key)::integer;
            degrees[parent_index] := degrees[parent_index] - 1;
            IF degrees[parent_index] = 0 THEN queue := array_append(queue, parent_index); END IF;
        END LOOP;
        cursor_pos := cursor_pos + 1;
    END LOOP;

    -- Explicit DFS keeps the original ordered AND/OR short circuit and detects
    -- a cycle against the current path. Only acyclic cones get support caches.
    FOR refs IN SELECT value FROM jsonb_array_elements(ref_groups) LOOP
        group_allowed := true;
        FOR root IN SELECT value FROM jsonb_array_elements(refs) LOOP
            current_ref := root;
            current_access := true;
            stack_size := 0;
            have_value := false;
            LOOP
                IF NOT have_value THEN
                    work := work + 1;
                    IF work > max_work THEN
                        RAISE EXCEPTION 'source_dependency_budget_exceeded' USING ERRCODE='54000';
                    END IF;
                    current_key := (current_ref->>'source_id') || ':' || (current_ref->>'revision');
                    IF current_key IS NULL THEN
                        outcome := false;
                        have_value := true;
                        CONTINUE;
                    END IF;
                    active_slot := ((hashtextextended(current_key, 0) & 127)::integer) + 1;
                    IF active[active_slot] ? current_key THEN
                        outcome := false;
                        have_value := true;
                        CONTINUE;
                    END IF;
                    node_index := (positions->>current_key)::integer;
                    IF node_index IS NULL THEN
                        outcome := false;
                        have_value := true;
                        CONTINUE;
                    END IF;
                    current_node := local_nodes[node_index];

                    -- Ranges and forgotten fragments are checked for each exact
                    -- ref before consulting any shared support proof.
                    local_key := jsonb_build_array(current_key, current_access, current_ref->'locator')::text;
                    local_slot := ((hashtextextended(local_key, 0) & 127)::integer) + 1;
                    IF local_cache[local_slot] ? local_key THEN
                        local_allowed := (local_cache[local_slot]->>local_key)::boolean;
                    ELSE
                        -- The original local predicate scans scopes and forgotten
                        -- intervals. Charge their upper bound before calling it.
                        work := work + jsonb_array_length(current_node->'scopes')
                                     + jsonb_array_length(current_node->'forgotten');
                        IF work > max_work THEN
                            RAISE EXCEPTION 'source_dependency_budget_exceeded' USING ERRCODE='54000';
                        END IF;
                        isolated := jsonb_build_object(current_key, current_node);
                        local_allowed := sources.valid_reference(current_ref, isolated, viewer, current_access);
                        local_cache[local_slot] := local_cache[local_slot] ||
                            jsonb_build_object(local_key, local_allowed);
                    END IF;
                    IF NOT local_allowed THEN
                        outcome := false;
                        have_value := true;
                        CONTINUE;
                    END IF;
                    IF NOT node_has_groups[node_index] THEN
                        outcome := true;
                        have_value := true;
                        CONTINUE;
                    END IF;
                    cache_key := jsonb_build_array(current_key, current_access)::text;
                    cache_slot := ((hashtextextended(cache_key, 0) & 127)::integer) + 1;
                    IF safe[node_index] AND support_cache[cache_slot] ? cache_key THEN
                        outcome := (support_cache[cache_slot]->>cache_key)::boolean;
                        have_value := true;
                        CONTINUE;
                    END IF;

                    current_node := nodes->current_key;
                    stack_size := stack_size + 1;
                    stack_keys[stack_size] := current_key;
                    stack_groups[stack_size] := current_node->'groups';
                    stack_access[stack_size] := current_access;
                    child_access[stack_size] := current_access AND NOT (current_node->>'disclosure')::boolean;
                    group_index[stack_size] := -1;
                    child_index[stack_size] := 0;
                    active[active_slot] := active[active_slot] || jsonb_build_object(current_key, true);
                    -- A false sentinel selects the first nonempty alternative.
                    outcome := false;
                    have_value := true;
                    CONTINUE;
                END IF;

                IF stack_size = 0 THEN EXIT; END IF;
                IF NOT outcome THEN
                    group_index[stack_size] := group_index[stack_size] + 1;
                    WHILE group_index[stack_size] < jsonb_array_length(stack_groups[stack_size]) LOOP
                        work := work + 1;
                        IF work > max_work THEN
                            RAISE EXCEPTION 'source_dependency_budget_exceeded' USING ERRCODE='54000';
                        END IF;
                        selected_group := stack_groups[stack_size]->(group_index[stack_size]);
                        EXIT WHEN jsonb_array_length(selected_group) > 0;
                        group_index[stack_size] := group_index[stack_size] + 1;
                    END LOOP;
                    IF group_index[stack_size] < jsonb_array_length(stack_groups[stack_size]) THEN
                        child_index[stack_size] := 0;
                        current_ref := selected_group->0;
                        current_access := child_access[stack_size];
                        have_value := false;
                        CONTINUE;
                    END IF;
                    outcome := false;
                ELSE
                    child_index[stack_size] := child_index[stack_size] + 1;
                    selected_group := stack_groups[stack_size]->(group_index[stack_size]);
                    IF child_index[stack_size] < jsonb_array_length(selected_group) THEN
                        current_ref := selected_group->(child_index[stack_size]);
                        current_access := child_access[stack_size];
                        have_value := false;
                        CONTINUE;
                    END IF;
                    outcome := true;
                END IF;

                current_key := stack_keys[stack_size];
                node_index := (positions->>current_key)::integer;
                IF safe[node_index] THEN
                    cache_key := jsonb_build_array(current_key, stack_access[stack_size])::text;
                    cache_slot := ((hashtextextended(cache_key, 0) & 127)::integer) + 1;
                    support_cache[cache_slot] := support_cache[cache_slot] ||
                        jsonb_build_object(cache_key, outcome);
                END IF;
                active_slot := ((hashtextextended(current_key, 0) & 127)::integer) + 1;
                active[active_slot] := active[active_slot] - current_key;
                stack_size := stack_size - 1;
                have_value := true;
            END LOOP;
            IF NOT outcome THEN group_allowed := false; EXIT; END IF;
        END LOOP;
        result := array_append(result, group_allowed);
    END LOOP;
    RETURN jsonb_build_object('allowed', to_jsonb(result), 'work', work,
        'non_acyclic_nodes', cardinality(node_keys) - cardinality(queue));
END $$;

-- Collect only a bounded closure, then bounded edge/fragment facts, before
-- aggregating the JSON graph. The supplied limits are the caller's remaining
-- budget, not a fresh budget for another context or batch.
CREATE OR REPLACE FUNCTION sources._rbj_check_one(
    ref_groups jsonb, viewer text[], max_nodes bigint, max_edges bigint, max_work bigint,
    max_inputs bigint, max_bytes bigint
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
    evaluated := sources._rbj_evaluate(ref_groups, viewer, nodes, max_work - collected_work);
    RETURN jsonb_build_object('allowed', evaluated->'allowed', 'nodes', count_nodes,
        'edges', count_edges + count_fragments, 'inputs', input_count, 'bytes', graph_bytes,
        'work', collected_work + (evaluated->>'work')::bigint,
        'non_acyclic_nodes', (evaluated->>'non_acyclic_nodes')::bigint);
END $$;

-- One statement and snapshot, separate viewers, and one shared finite budget.
-- The first mandatory proof can short-circuit before optional contexts.
CREATE OR REPLACE FUNCTION sources._rbj_check_contexts(requests jsonb, budget jsonb DEFAULT NULL) RETURNS jsonb
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
            remaining_nodes, remaining_edges, remaining_work, remaining_inputs, remaining_bytes);
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

-- Preserve the published SQL signature, function identity and JIT setting.
CREATE OR REPLACE FUNCTION sources.check_groups(ref_groups jsonb, viewer text[]) RETURNS boolean[]
LANGUAGE plpgsql STABLE SET jit = off AS $$
DECLARE checked jsonb;
BEGIN
    IF ref_groups IS NULL THEN RETURN ARRAY[]::boolean[]; END IF;
    checked := sources._rbj_check_contexts(jsonb_build_array(jsonb_build_object(
        'groups', ref_groups, 'viewer', to_jsonb(viewer), 'stop_if_denied', false
    )));
    RETURN ARRAY(SELECT value::boolean FROM jsonb_array_elements_text(checked->'allowed'->0));
END $$;
