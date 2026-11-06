-- Bounded lock-closure helpers. Every call returns its remaining caller budget.
CREATE OR REPLACE FUNCTION sources._rbj_lock_keys(targets text[], aliases text[], budget jsonb)
RETURNS jsonb LANGUAGE plpgsql STABLE SET jit = off AS $$
DECLARE
    nodes_left bigint := (budget->>'nodes')::bigint;
    edges_left bigint := (budget->>'edges')::bigint;
    work_left bigint := (budget->>'work')::bigint;
    inputs_left bigint := (budget->>'inputs')::bigint;
    bytes_left bigint := (budget->>'bytes')::bigint;
    keys text[] := ARRAY[]::text[];
    seen jsonb[] := array_fill('{}'::jsonb, ARRAY[128]);
    entry record;
    alias_key text;
    slot integer;
BEGIN
    inputs_left := inputs_left - cardinality(targets) - cardinality(aliases);
    work_left := work_left - cardinality(targets) - cardinality(aliases);
    bytes_left := bytes_left - pg_column_size(targets) - pg_column_size(aliases);
    IF inputs_left < 0 OR work_left < 0 OR bytes_left < 0 THEN
        RAISE EXCEPTION 'source_dependency_budget_exceeded' USING ERRCODE='54000';
    END IF;
    FOREACH alias_key IN ARRAY aliases LOOP
        bytes_left := bytes_left - octet_length(alias_key);
        IF bytes_left < 0 THEN RAISE EXCEPTION 'source_dependency_budget_exceeded' USING ERRCODE='54000'; END IF;
        slot := ((hashtextextended(alias_key, 0) & 127)::integer) + 1;
        IF NOT (seen[slot] ? alias_key) THEN
            seen[slot] := seen[slot] || jsonb_build_object(alias_key, true);
            keys := array_append(keys, alias_key);
        END IF;
    END LOOP;
    FOR entry IN
        SELECT a.alias_key, octet_length(a.alias_key) AS bytes
        FROM unnest(targets) AS source_input(source_id)
        CROSS JOIN LATERAL (
            SELECT k.alias_key FROM sources.source_aliases k
            WHERE k.source_id=source_input.source_id LIMIT GREATEST(LEAST(edges_left, work_left), 0) + 1
        ) a
        LIMIT GREATEST(LEAST(edges_left, work_left), 0) + 1
    LOOP
        edges_left := edges_left - 1;
        work_left := work_left - 1;
        bytes_left := bytes_left - entry.bytes;
        IF edges_left < 0 OR work_left < 0 OR bytes_left < 0 THEN
            RAISE EXCEPTION 'source_dependency_budget_exceeded' USING ERRCODE='54000';
        END IF;
        slot := ((hashtextextended(entry.alias_key, 0) & 127)::integer) + 1;
        IF NOT (seen[slot] ? entry.alias_key) THEN
            seen[slot] := seen[slot] || jsonb_build_object(entry.alias_key, true);
            keys := array_append(keys, entry.alias_key);
        END IF;
    END LOOP;
    RETURN jsonb_build_object('keys', to_jsonb(keys), 'remaining', jsonb_build_object(
        'nodes', nodes_left, 'edges', edges_left, 'work', work_left,
        'inputs', inputs_left, 'bytes', bytes_left));
END $$;

CREATE OR REPLACE FUNCTION sources._rbj_lock_targets(targets text[], aliases text[], budget jsonb)
RETURNS jsonb LANGUAGE plpgsql STABLE SET jit = off AS $$
DECLARE
    prepared jsonb;
    keys text[];
    nodes_left bigint;
    edges_left bigint;
    work_left bigint;
    inputs_left bigint;
    bytes_left bigint;
    found_ids text[] := ARRAY[]::text[];
    seen jsonb[] := array_fill('{}'::jsonb, ARRAY[128]);
    entry record;
    source_id text;
    slot integer;
BEGIN
    prepared := sources._rbj_lock_keys(targets, aliases, budget);
    budget := prepared->'remaining';
    keys := ARRAY(SELECT value FROM jsonb_array_elements_text(prepared->'keys'));
    nodes_left := (budget->>'nodes')::bigint;
    edges_left := (budget->>'edges')::bigint;
    work_left := (budget->>'work')::bigint;
    inputs_left := (budget->>'inputs')::bigint;
    bytes_left := (budget->>'bytes')::bigint;
    FOREACH source_id IN ARRAY targets LOOP
        slot := ((hashtextextended(source_id, 0) & 127)::integer) + 1;
        IF NOT (seen[slot] ? source_id) THEN
            nodes_left := nodes_left - 1;
            work_left := work_left - 1;
            bytes_left := bytes_left - octet_length(source_id);
            IF nodes_left < 0 OR work_left < 0 OR bytes_left < 0 THEN
                RAISE EXCEPTION 'source_dependency_budget_exceeded' USING ERRCODE='54000';
            END IF;
            seen[slot] := seen[slot] || jsonb_build_object(source_id, true);
            found_ids := array_append(found_ids, source_id);
        END IF;
    END LOOP;
    FOR entry IN
        SELECT b.source_id, octet_length(b.source_id) AS bytes
        FROM unnest(keys) AS selected(alias_key)
        CROSS JOIN LATERAL (
            SELECT a.source_id FROM sources.source_aliases a
            WHERE a.alias_key=selected.alias_key LIMIT GREATEST(LEAST(edges_left, work_left), 0) + 1
        ) b
        LIMIT GREATEST(LEAST(edges_left, work_left), 0) + 1
    LOOP
        edges_left := edges_left - 1;
        work_left := work_left - 1;
        bytes_left := bytes_left - entry.bytes;
        IF edges_left < 0 OR work_left < 0 OR bytes_left < 0 THEN
            RAISE EXCEPTION 'source_dependency_budget_exceeded' USING ERRCODE='54000';
        END IF;
        slot := ((hashtextextended(entry.source_id, 0) & 127)::integer) + 1;
        IF NOT (seen[slot] ? entry.source_id) THEN
            nodes_left := nodes_left - 1;
            work_left := work_left - 1;
            IF nodes_left < 0 OR work_left < 0 THEN
                RAISE EXCEPTION 'source_dependency_budget_exceeded' USING ERRCODE='54000';
            END IF;
            seen[slot] := seen[slot] || jsonb_build_object(entry.source_id, true);
            found_ids := array_append(found_ids, entry.source_id);
        END IF;
    END LOOP;
    RETURN jsonb_build_object('ids', to_jsonb(found_ids), 'remaining', jsonb_build_object(
        'nodes', nodes_left, 'edges', edges_left, 'work', work_left,
        'inputs', inputs_left, 'bytes', bytes_left));
END $$;

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
            pairs_seen[pair_slot] := pairs_seen[pair_slot] || jsonb_build_object(pair_key, true);
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
                pairs_seen[pair_slot] := pairs_seen[pair_slot] || jsonb_build_object(pair_key, true);
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
    RETURN jsonb_build_object('ids', to_jsonb(source_ids), 'remaining', jsonb_build_object(
        'nodes', nodes_left, 'edges', edges_left, 'work', work_left,
        'inputs', inputs_left, 'bytes', bytes_left));
END $$;

-- Evidence manifests are bounded before JSON is returned to the caller.
CREATE OR REPLACE FUNCTION sources._rbj_evidence_manifests(evidence_ids text[], budget jsonb)
RETURNS jsonb LANGUAGE plpgsql STABLE SET jit = off AS $$
DECLARE
    nodes_left bigint := (budget->>'nodes')::bigint;
    edges_left bigint := (budget->>'edges')::bigint;
    work_left bigint := (budget->>'work')::bigint;
    inputs_left bigint := (budget->>'inputs')::bigint;
    bytes_left bigint := (budget->>'bytes')::bigint;
    entries jsonb[] := ARRAY[]::jsonb[];
    entry record;
BEGIN
    inputs_left := inputs_left - cardinality(evidence_ids);
    work_left := work_left - cardinality(evidence_ids);
    bytes_left := bytes_left - pg_column_size(evidence_ids);
    IF inputs_left < 0 OR work_left < 0 OR bytes_left < 0 THEN
        RAISE EXCEPTION 'source_dependency_budget_exceeded' USING ERRCODE='54000';
    END IF;
    FOR entry IN
        SELECT e.evidence_id, e.manifest, octet_length(e.evidence_id) + octet_length(e.manifest::text) AS bytes
        FROM unnest(evidence_ids) AS requested(evidence_id)
        CROSS JOIN LATERAL (
            SELECT item.evidence_id, item.manifest FROM sources.evidence_sets item
            WHERE item.evidence_id=requested.evidence_id LIMIT 1
        ) e
    LOOP
        work_left := work_left - 1;
        bytes_left := bytes_left - entry.bytes;
        IF work_left < 0 OR bytes_left < 0 THEN
            RAISE EXCEPTION 'source_dependency_budget_exceeded' USING ERRCODE='54000';
        END IF;
        entries := array_append(entries, jsonb_build_object('evidence_id', entry.evidence_id, 'manifest', entry.manifest));
    END LOOP;
    RETURN jsonb_build_object('rows', to_jsonb(entries), 'remaining', jsonb_build_object(
        'nodes', nodes_left, 'edges', edges_left, 'work', work_left,
        'inputs', inputs_left, 'bytes', bytes_left));
END $$;

-- Read the original support rows for exact revision comparisons without
-- materializing an unbounded legacy version before applying the call budget.
CREATE OR REPLACE FUNCTION sources._rbj_support_rows(target_source text, target_revision bigint, budget jsonb)
RETURNS jsonb LANGUAGE plpgsql STABLE SET jit = off AS $$
DECLARE
    nodes_left bigint := (budget->>'nodes')::bigint;
    edges_left bigint := (budget->>'edges')::bigint;
    work_left bigint := (budget->>'work')::bigint;
    inputs_left bigint := (budget->>'inputs')::bigint;
    bytes_left bigint := (budget->>'bytes')::bigint;
    entries jsonb[] := ARRAY[]::jsonb[];
    item record;
BEGIN
    inputs_left := inputs_left - 1;
    work_left := work_left - 1;
    bytes_left := bytes_left - octet_length(target_source) - 8;
    IF inputs_left < 0 OR work_left < 0 OR bytes_left < 0 THEN
        RAISE EXCEPTION 'source_dependency_budget_exceeded' USING ERRCODE='54000';
    END IF;
    FOR item IN
        SELECT d.group_id, d.support_source_id, d.support_revision, d.support_locator,
               octet_length(d.group_id) + octet_length(d.support_source_id)
                   + octet_length(d.support_locator::text) + 8 AS bytes
        FROM sources.supports d WHERE d.source_id=target_source AND d.revision=target_revision
        ORDER BY d.group_id, d.support_key
        LIMIT GREATEST(LEAST(edges_left, work_left), 0) + 1
    LOOP
        edges_left := edges_left - 1;
        work_left := work_left - 1;
        bytes_left := bytes_left - item.bytes;
        IF edges_left < 0 OR work_left < 0 OR bytes_left < 0 THEN
            RAISE EXCEPTION 'source_dependency_budget_exceeded' USING ERRCODE='54000';
        END IF;
        entries := array_append(entries, jsonb_build_object(
            'group_id', item.group_id, 'support_source_id', item.support_source_id,
            'support_revision', item.support_revision, 'support_locator', item.support_locator));
    END LOOP;
    RETURN jsonb_build_object('rows', to_jsonb(entries), 'remaining', jsonb_build_object(
        'nodes', nodes_left, 'edges', edges_left, 'work', work_left,
        'inputs', inputs_left, 'bytes', bytes_left));
END $$;

-- Metadata for already locked archive aliases, bounded before result assembly.
CREATE OR REPLACE FUNCTION sources._rbj_archive_bound(aliases text[], budget jsonb)
RETURNS jsonb LANGUAGE plpgsql STABLE SET jit = off AS $$
DECLARE
    nodes_left bigint := (budget->>'nodes')::bigint;
    edges_left bigint := (budget->>'edges')::bigint;
    work_left bigint := (budget->>'work')::bigint;
    inputs_left bigint := (budget->>'inputs')::bigint;
    bytes_left bigint := (budget->>'bytes')::bigint;
    entries jsonb[] := ARRAY[]::jsonb[];
    item record;
BEGIN
    inputs_left := inputs_left - cardinality(aliases);
    work_left := work_left - cardinality(aliases);
    bytes_left := bytes_left - pg_column_size(aliases);
    IF inputs_left < 0 OR work_left < 0 OR bytes_left < 0 THEN
        RAISE EXCEPTION 'source_dependency_budget_exceeded' USING ERRCODE='54000';
    END IF;
    FOR item IN
        SELECT k.alias_key,a.source_id,a.revision,o.state,o.head_revision,v.state AS version_state,
               EXISTS(SELECT 1 FROM sources.forgotten_fragments f WHERE f.source_id=a.source_id
                      AND f.revision=a.revision) AS fragment_forgotten,
               octet_length(k.alias_key) + octet_length(a.source_id) + 64 AS bytes
        FROM unnest(aliases) AS k(alias_key)
        CROSS JOIN LATERAL (
            SELECT x.source_id,x.revision FROM sources.source_aliases x WHERE x.alias_key=k.alias_key
            LIMIT GREATEST(LEAST(edges_left, work_left / 2), 0) + 1
        ) a
        JOIN sources.originals o USING(source_id)
        JOIN sources.versions v USING(source_id,revision)
        LIMIT GREATEST(LEAST(edges_left, work_left / 2), 0) + 1
    LOOP
        edges_left := edges_left - 1;
        work_left := work_left - 2;
        bytes_left := bytes_left - item.bytes;
        IF edges_left < 0 OR work_left < 0 OR bytes_left < 0 THEN
            RAISE EXCEPTION 'source_dependency_budget_exceeded' USING ERRCODE='54000';
        END IF;
        entries := array_append(entries, jsonb_build_object(
            'alias_key', item.alias_key, 'source_id', item.source_id, 'revision', item.revision,
            'state', item.state, 'head_revision', item.head_revision, 'version_state', item.version_state,
            'fragment_forgotten', item.fragment_forgotten));
    END LOOP;
    RETURN jsonb_build_object('rows', to_jsonb(entries), 'remaining', jsonb_build_object(
        'nodes', nodes_left, 'edges', edges_left, 'work', work_left,
        'inputs', inputs_left, 'bytes', bytes_left));
END $$;
