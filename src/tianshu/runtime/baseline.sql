-- TianShu 2026-11-05 fresh installation baseline.
-- Frozen schema only; subsequent releases append explicit incremental migrations.
-- No old business rows, sessions, attempts, generation ID or external effects are imported.

--
-- PostgreSQL database dump
--


-- Dumped from database version 17.11 (Debian 17.11-1.pgdg12+2)
-- Dumped by pg_dump version 17.11 (Debian 17.11-1.pgdg12+2)

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET transaction_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: capabilities; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA IF NOT EXISTS capabilities;


--
-- Name: capability_connections; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA IF NOT EXISTS capability_connections;


--
-- Name: capability_infrastructure; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA IF NOT EXISTS capability_infrastructure;


--
-- Name: capability_media; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA IF NOT EXISTS capability_media;


--
-- Name: capability_plugins; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA IF NOT EXISTS capability_plugins;


--
-- Name: capability_projects; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA IF NOT EXISTS capability_projects;


--
-- Name: channels; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA IF NOT EXISTS channels;


--
-- Name: companion; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA IF NOT EXISTS companion;


--
-- Name: conversation; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA IF NOT EXISTS conversation;


--
-- Name: identity; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA IF NOT EXISTS identity;


--
-- Name: life; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA IF NOT EXISTS life;


--
-- Name: memory; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA IF NOT EXISTS memory;


--
-- Name: models; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA IF NOT EXISTS models;


--
-- Name: operations; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA IF NOT EXISTS operations;


--
-- Name: runtime; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA IF NOT EXISTS runtime;


--
-- Name: sources; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA IF NOT EXISTS sources;


--
-- Name: vector; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public;


--
-- Name: procrastinate_job_event_type; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.procrastinate_job_event_type AS ENUM (
    'deferred',
    'started',
    'deferred_for_retry',
    'failed',
    'succeeded',
    'cancelled',
    'abort_requested',
    'aborted',
    'scheduled',
    'retried'
);


--
-- Name: procrastinate_job_status; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.procrastinate_job_status AS ENUM (
    'todo',
    'doing',
    'succeeded',
    'failed',
    'cancelled',
    'aborting',
    'aborted'
);


--
-- Name: procrastinate_job_to_defer_v1; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.procrastinate_job_to_defer_v1 AS (
	queue_name character varying,
	task_name character varying,
	priority integer,
	lock text,
	queueing_lock text,
	args jsonb,
	scheduled_at timestamp with time zone
);


--
-- Name: ensure_embedding_space(text); Type: FUNCTION; Schema: memory; Owner: -
--

CREATE FUNCTION memory.ensure_embedding_space(space_key text) RETURNS void
    LANGUAGE plpgsql
    AS $_$
DECLARE partition_name text;
BEGIN
    IF space_key !~ '^[0-9a-f]{64}$' THEN
        RAISE EXCEPTION 'invalid memory embedding profile';
    END IF;
    partition_name := 'search_vectors_' || substr(space_key, 1, 48);
    IF to_regclass(format('memory.%I', partition_name)) IS NOT NULL THEN
        RETURN;
    END IF;
    -- Partition creation locks the shared parent, so serialize creation across
    -- all spaces, not just competing writers of the same space.
    PERFORM pg_advisory_xact_lock(hashtextextended('memory.search_vectors:create', 0));
    IF to_regclass(format('memory.%I', partition_name)) IS NULL THEN
        EXECUTE format('CREATE TABLE memory.%I PARTITION OF memory.search_vectors FOR VALUES IN (%L)',
                       partition_name, space_key);
    END IF;
END $_$;


--
-- Name: invalidate_episode_vector_on_change(); Type: FUNCTION; Schema: memory; Owner: -
--

CREATE FUNCTION memory.invalidate_episode_vector_on_change() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    IF OLD.revision=NEW.revision AND
       (OLD.summary IS DISTINCT FROM NEW.summary OR OLD.supplement_refs IS DISTINCT FROM NEW.supplement_refs) THEN
        DELETE FROM memory.search_vectors WHERE kind='episode'
            AND object_id=OLD.episode_id AND revision=OLD.revision;
    END IF;
    RETURN NEW;
END $$;


--
-- Name: purge_search_vectors(); Type: FUNCTION; Schema: memory; Owner: -
--

CREATE FUNCTION memory.purge_search_vectors() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    IF TG_TABLE_NAME = 'claim_revisions' THEN
        DELETE FROM memory.search_vectors WHERE kind IN ('claim','hypothesis')
            AND object_id=OLD.claim_id AND revision=OLD.revision;
    ELSIF TG_TABLE_NAME = 'matter_revisions' THEN
        DELETE FROM memory.search_vectors WHERE kind='matter'
            AND object_id=OLD.matter_id AND revision=OLD.revision;
    ELSIF TG_TABLE_NAME = 'episodes' THEN
        DELETE FROM memory.search_vectors WHERE kind='episode' AND object_id=OLD.episode_id;
        DELETE FROM memory.episode_original_terms WHERE episode_id=OLD.episode_id;
    END IF;
    RETURN NEW;
END $$;


--
-- Name: procrastinate_cancel_job_v1(bigint, boolean, boolean); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.procrastinate_cancel_job_v1(job_id bigint, abort boolean, delete_job boolean) RETURNS bigint
    LANGUAGE plpgsql
    AS $$
DECLARE
    _job_id bigint;
BEGIN
    IF delete_job THEN
        DELETE FROM procrastinate_jobs
        WHERE id = job_id AND status = 'todo'
        RETURNING id INTO _job_id;
    END IF;
    IF _job_id IS NULL THEN
        IF abort THEN
            UPDATE procrastinate_jobs
            SET abort_requested = true,
                status = CASE status
                    WHEN 'todo' THEN 'cancelled'::procrastinate_job_status ELSE status
                END
            WHERE id = job_id AND status IN ('todo', 'doing')
            RETURNING id INTO _job_id;
        ELSE
            UPDATE procrastinate_jobs
            SET status = 'cancelled'::procrastinate_job_status
            WHERE id = job_id AND status = 'todo'
            RETURNING id INTO _job_id;
        END IF;
    END IF;
    RETURN _job_id;
END;
$$;


--
-- Name: procrastinate_defer_jobs_v1(public.procrastinate_job_to_defer_v1[]); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.procrastinate_defer_jobs_v1(jobs public.procrastinate_job_to_defer_v1[]) RETURNS bigint[]
    LANGUAGE plpgsql
    AS $$
DECLARE
    job_ids bigint[];
BEGIN
    WITH inserted_jobs AS (
        INSERT INTO procrastinate_jobs (queue_name, task_name, priority, lock, queueing_lock, args, scheduled_at)
        SELECT (job).queue_name,
               (job).task_name,
               (job).priority,
               (job).lock,
               (job).queueing_lock,
               (job).args,
               (job).scheduled_at
        FROM unnest(jobs) AS job
        RETURNING id
    )
    SELECT array_agg(id) FROM inserted_jobs INTO job_ids;

    RETURN job_ids;
END;
$$;


--
-- Name: procrastinate_defer_periodic_job_v2(character varying, character varying, character varying, character varying, integer, character varying, bigint, jsonb); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.procrastinate_defer_periodic_job_v2(_queue_name character varying, _lock character varying, _queueing_lock character varying, _task_name character varying, _priority integer, _periodic_id character varying, _defer_timestamp bigint, _args jsonb) RETURNS bigint
    LANGUAGE plpgsql
    AS $$
DECLARE
	_job_id bigint;
	_defer_id bigint;
BEGIN
    INSERT
        INTO procrastinate_periodic_defers (task_name, periodic_id, defer_timestamp)
        VALUES (_task_name, _periodic_id, _defer_timestamp)
        ON CONFLICT DO NOTHING
        RETURNING id into _defer_id;

    IF _defer_id IS NULL THEN
        RETURN NULL;
    END IF;

    UPDATE procrastinate_periodic_defers
        SET job_id = (
            SELECT COALESCE((
                SELECT unnest(procrastinate_defer_jobs_v1(
                    ARRAY[
                        ROW(
                            _queue_name,
                            _task_name,
                            _priority,
                            _lock,
                            _queueing_lock,
                            _args,
                            NULL::timestamptz
                        )
                    ]::procrastinate_job_to_defer_v1[]
                ))
            ), NULL)
        )
        WHERE id = _defer_id
        RETURNING job_id INTO _job_id;

    DELETE
        FROM procrastinate_periodic_defers
        USING (
            SELECT id
            FROM procrastinate_periodic_defers
            WHERE procrastinate_periodic_defers.task_name = _task_name
            AND procrastinate_periodic_defers.periodic_id = _periodic_id
            AND procrastinate_periodic_defers.defer_timestamp < _defer_timestamp
            ORDER BY id
            FOR UPDATE
        ) to_delete
        WHERE procrastinate_periodic_defers.id = to_delete.id;

    RETURN _job_id;
END;
$$;


SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: procrastinate_jobs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.procrastinate_jobs (
    id bigint NOT NULL,
    queue_name character varying(128) NOT NULL,
    task_name character varying(128) NOT NULL,
    priority integer DEFAULT 0 NOT NULL,
    lock text,
    queueing_lock text,
    args jsonb DEFAULT '{}'::jsonb NOT NULL,
    status public.procrastinate_job_status DEFAULT 'todo'::public.procrastinate_job_status NOT NULL,
    scheduled_at timestamp with time zone,
    attempts integer DEFAULT 0 NOT NULL,
    abort_requested boolean DEFAULT false NOT NULL,
    worker_id bigint,
    CONSTRAINT check_not_todo_abort_requested CHECK ((NOT ((status = 'todo'::public.procrastinate_job_status) AND (abort_requested = true))))
);


--
-- Name: procrastinate_fetch_job_v2(character varying[], bigint); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.procrastinate_fetch_job_v2(target_queue_names character varying[], p_worker_id bigint) RETURNS public.procrastinate_jobs
    LANGUAGE plpgsql
    AS $$
DECLARE
	found_jobs procrastinate_jobs;
BEGIN
    WITH candidate AS (
        SELECT jobs.*
            FROM procrastinate_jobs AS jobs
            WHERE
                -- reject the job if its lock has earlier or higher priority jobs
                NOT EXISTS (
                    SELECT 1
                        FROM procrastinate_jobs AS other_jobs
                        WHERE
                            jobs.lock IS NOT NULL
                            AND other_jobs.lock = jobs.lock
                            AND (
                                -- job with same lock is already running
                                other_jobs.status = 'doing'
                                OR
                                -- job with same lock is waiting and has higher priority (or same priority but was queued first)
                                (
                                    other_jobs.status = 'todo'
                                    AND (
                                        other_jobs.priority > jobs.priority
                                        OR (
                                        other_jobs.priority = jobs.priority
                                        AND other_jobs.id < jobs.id
                                        )
                                    )
                                )
                            )
                )
                AND jobs.status = 'todo'
                AND (target_queue_names IS NULL OR jobs.queue_name = ANY( target_queue_names ))
                AND (jobs.scheduled_at IS NULL OR jobs.scheduled_at <= now())
            ORDER BY jobs.priority DESC, jobs.id ASC LIMIT 1
            FOR UPDATE OF jobs SKIP LOCKED
    )
    UPDATE procrastinate_jobs
        SET status = 'doing', worker_id = p_worker_id
        FROM candidate
        WHERE procrastinate_jobs.id = candidate.id
        RETURNING procrastinate_jobs.* INTO found_jobs;

 RETURN found_jobs;
END;
$$;


--
-- Name: procrastinate_finish_job_v1(bigint, public.procrastinate_job_status, boolean); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.procrastinate_finish_job_v1(job_id bigint, end_status public.procrastinate_job_status, delete_job boolean) RETURNS void
    LANGUAGE plpgsql
    AS $$
DECLARE
    _job_id bigint;
BEGIN
    IF end_status NOT IN ('succeeded', 'failed', 'aborted') THEN
        RAISE 'End status should be either "succeeded", "failed" or "aborted" (job id: %)', job_id;
    END IF;
    IF delete_job THEN
        DELETE FROM procrastinate_jobs
        WHERE id = job_id AND status IN ('todo', 'doing')
        RETURNING id INTO _job_id;
    ELSE
        UPDATE procrastinate_jobs
        SET status = end_status,
            abort_requested = false,
            attempts = CASE status
                WHEN 'doing' THEN attempts + 1 ELSE attempts
            END
        WHERE id = job_id AND status IN ('todo', 'doing')
        RETURNING id INTO _job_id;
    END IF;
    IF _job_id IS NULL THEN
        RAISE 'Job was not found or not in "doing" or "todo" status (job id: %)', job_id;
    END IF;
END;
$$;


--
-- Name: procrastinate_notify_queue_abort_job_v1(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.procrastinate_notify_queue_abort_job_v1() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
    payload TEXT;
BEGIN
    SELECT json_build_object('type', 'abort_job_requested', 'job_id', NEW.id)::text INTO payload;
	PERFORM pg_notify('procrastinate_queue_v1#' || NEW.queue_name, payload);
	PERFORM pg_notify('procrastinate_any_queue_v1', payload);
	RETURN NEW;
END;
$$;


--
-- Name: procrastinate_notify_queue_job_inserted_v1(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.procrastinate_notify_queue_job_inserted_v1() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
    payload TEXT;
BEGIN
    SELECT json_build_object('type', 'job_inserted', 'job_id', NEW.id)::text INTO payload;
	PERFORM pg_notify('procrastinate_queue_v1#' || NEW.queue_name, payload);
	PERFORM pg_notify('procrastinate_any_queue_v1', payload);
	RETURN NEW;
END;
$$;


--
-- Name: procrastinate_prune_stalled_workers_v1(double precision); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.procrastinate_prune_stalled_workers_v1(seconds_since_heartbeat double precision) RETURNS TABLE(worker_id bigint)
    LANGUAGE plpgsql
    AS $$
BEGIN
    RETURN QUERY
    DELETE FROM procrastinate_workers
    WHERE last_heartbeat < NOW() - (seconds_since_heartbeat || 'SECOND')::INTERVAL
    RETURNING procrastinate_workers.id;
END;
$$;


--
-- Name: procrastinate_register_worker_v1(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.procrastinate_register_worker_v1() RETURNS TABLE(worker_id bigint)
    LANGUAGE plpgsql
    AS $$
BEGIN
    RETURN QUERY
    INSERT INTO procrastinate_workers DEFAULT VALUES
    RETURNING procrastinate_workers.id;
END;
$$;


--
-- Name: procrastinate_retry_job_v1(bigint, timestamp with time zone, integer, character varying, character varying); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.procrastinate_retry_job_v1(job_id bigint, retry_at timestamp with time zone, new_priority integer, new_queue_name character varying, new_lock character varying) RETURNS void
    LANGUAGE plpgsql
    AS $$
DECLARE
    _job_id bigint;
    _abort_requested boolean;
BEGIN
    SELECT abort_requested FROM procrastinate_jobs
    WHERE id = job_id AND status = 'doing'
    FOR UPDATE
    INTO _abort_requested;
    IF _abort_requested THEN
        UPDATE procrastinate_jobs
        SET status = 'failed'::procrastinate_job_status
        WHERE id = job_id AND status = 'doing'
        RETURNING id INTO _job_id;
    ELSE
        UPDATE procrastinate_jobs
        SET status = 'todo'::procrastinate_job_status,
            attempts = attempts + 1,
            scheduled_at = retry_at,
            priority = COALESCE(new_priority, priority),
            queue_name = COALESCE(new_queue_name, queue_name),
            lock = COALESCE(new_lock, lock)
        WHERE id = job_id AND status = 'doing'
        RETURNING id INTO _job_id;
    END IF;

    IF _job_id IS NULL THEN
        RAISE 'Job was not found or not in "doing" status (job id: %)', job_id;
    END IF;
END;
$$;


--
-- Name: procrastinate_retry_job_v2(bigint, timestamp with time zone, integer, character varying, character varying); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.procrastinate_retry_job_v2(job_id bigint, retry_at timestamp with time zone, new_priority integer, new_queue_name character varying, new_lock character varying) RETURNS void
    LANGUAGE plpgsql
    AS $$
DECLARE
    _job_id bigint;
    _abort_requested boolean;
    _current_status procrastinate_job_status;
BEGIN
    SELECT status, abort_requested FROM procrastinate_jobs
    WHERE id = job_id AND status IN ('doing', 'failed')
    FOR UPDATE
    INTO _current_status, _abort_requested;
    IF _current_status = 'doing' AND _abort_requested THEN
        UPDATE procrastinate_jobs
        SET status = 'failed'::procrastinate_job_status
        WHERE id = job_id AND status = 'doing'
        RETURNING id INTO _job_id;
    ELSE
        UPDATE procrastinate_jobs
        SET status = 'todo'::procrastinate_job_status,
            attempts = attempts + 1,
            scheduled_at = retry_at,
            priority = COALESCE(new_priority, priority),
            queue_name = COALESCE(new_queue_name, queue_name),
            lock = COALESCE(new_lock, lock)
        WHERE id = job_id AND status IN ('doing', 'failed')
        RETURNING id INTO _job_id;
    END IF;

    IF _job_id IS NULL THEN
        RAISE 'Job was not found or has an invalid status to retry (job id: %)', job_id;
    END IF;

END;
$$;


--
-- Name: procrastinate_trigger_abort_requested_events_procedure_v1(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.procrastinate_trigger_abort_requested_events_procedure_v1() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    INSERT INTO procrastinate_events(job_id, type)
        VALUES (NEW.id, 'abort_requested'::procrastinate_job_event_type);
    RETURN NEW;
END;
$$;


--
-- Name: procrastinate_trigger_function_scheduled_events_v1(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.procrastinate_trigger_function_scheduled_events_v1() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    INSERT INTO procrastinate_events(job_id, type, at)
        VALUES (NEW.id, 'scheduled'::procrastinate_job_event_type, NEW.scheduled_at);

	RETURN NEW;
END;
$$;


--
-- Name: procrastinate_trigger_function_status_events_insert_v1(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.procrastinate_trigger_function_status_events_insert_v1() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    INSERT INTO procrastinate_events(job_id, type)
        VALUES (NEW.id, 'deferred'::procrastinate_job_event_type);
	RETURN NEW;
END;
$$;


--
-- Name: procrastinate_trigger_function_status_events_update_v1(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.procrastinate_trigger_function_status_events_update_v1() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    WITH t AS (
        SELECT CASE
            WHEN OLD.status = 'todo'::procrastinate_job_status
                AND NEW.status = 'doing'::procrastinate_job_status
                THEN 'started'::procrastinate_job_event_type
            WHEN OLD.status = 'doing'::procrastinate_job_status
                AND NEW.status = 'todo'::procrastinate_job_status
                THEN 'deferred_for_retry'::procrastinate_job_event_type
            WHEN OLD.status = 'doing'::procrastinate_job_status
                AND NEW.status = 'failed'::procrastinate_job_status
                THEN 'failed'::procrastinate_job_event_type
            WHEN OLD.status = 'doing'::procrastinate_job_status
                AND NEW.status = 'succeeded'::procrastinate_job_status
                THEN 'succeeded'::procrastinate_job_event_type
            WHEN OLD.status = 'todo'::procrastinate_job_status
                AND (
                    NEW.status = 'cancelled'::procrastinate_job_status
                    OR NEW.status = 'failed'::procrastinate_job_status
                    OR NEW.status = 'succeeded'::procrastinate_job_status
                )
                THEN 'cancelled'::procrastinate_job_event_type
            WHEN OLD.status = 'doing'::procrastinate_job_status
                AND NEW.status = 'aborted'::procrastinate_job_status
                THEN 'aborted'::procrastinate_job_event_type
            WHEN OLD.status = 'failed'::procrastinate_job_status
                AND NEW.status = 'todo'::procrastinate_job_status
                THEN 'retried'::procrastinate_job_event_type
            ELSE NULL
        END as event_type
    )
    INSERT INTO procrastinate_events(job_id, type)
        SELECT NEW.id, t.event_type
        FROM t
        WHERE t.event_type IS NOT NULL;
	RETURN NEW;
END;
$$;


--
-- Name: procrastinate_unlink_periodic_defers_v1(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.procrastinate_unlink_periodic_defers_v1() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    UPDATE procrastinate_periodic_defers
    SET job_id = NULL
    WHERE job_id = OLD.id;
    RETURN OLD;
END;
$$;


--
-- Name: procrastinate_unregister_worker_v1(bigint); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.procrastinate_unregister_worker_v1(worker_id bigint) RETURNS void
    LANGUAGE plpgsql
    AS $$
BEGIN
    DELETE FROM procrastinate_workers
    WHERE id = worker_id;
END;
$$;


--
-- Name: procrastinate_update_heartbeat_v1(bigint); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.procrastinate_update_heartbeat_v1(worker_id bigint) RETURNS void
    LANGUAGE plpgsql
    AS $$
BEGIN
    UPDATE procrastinate_workers
    SET last_heartbeat = NOW()
    WHERE id = worker_id;
END;
$$;


--
-- Name: fair_claim_job(text, bigint); Type: FUNCTION; Schema: runtime; Owner: -
--

CREATE FUNCTION runtime.fair_claim_job(target_queue text, p_worker_id bigint) RETURNS public.procrastinate_jobs
    LANGUAGE plpgsql
    AS $$
DECLARE
    picked_id bigint;
    picked_domain text;
    picked_subject text;
    picked_generation text;
    result procrastinate_jobs;
BEGIN
    SELECT j.id, split_part(j.task_name,'.',1), COALESCE(NULLIF(k.subject_id,''),'task:' || j.task_name),k.data_generation
    INTO picked_id, picked_domain, picked_subject, picked_generation
    FROM procrastinate_jobs j
    JOIN LATERAL (SELECT key,created_at,subject_id,data_generation FROM runtime.job_keys
        WHERE job_id=j.id AND cancelled_at IS NULL ORDER BY created_at,key LIMIT 1) k ON true
    JOIN runtime.installation_generation generation ON generation.singleton=true AND k.data_generation=generation.id
    LEFT JOIN runtime.queue_service service ON service.data_generation=k.data_generation AND service.queue_name=j.queue_name
      AND service.domain=split_part(j.task_name,'.',1)
      AND service.subject_id=COALESCE(NULLIF(k.subject_id,''),'task:' || j.task_name)
    WHERE j.queue_name=target_queue AND j.status='todo'
      AND (j.scheduled_at IS NULL OR j.scheduled_at<=now())
      AND NOT EXISTS (
        SELECT 1 FROM procrastinate_jobs other WHERE j.lock IS NOT NULL AND other.lock=j.lock
          AND (other.status='doing' OR (other.status='todo' AND
            (other.priority>j.priority OR (other.priority=j.priority AND other.id<j.id))))
      )
    ORDER BY
      COALESCE(GREATEST(0,service.service_seconds-EXTRACT(EPOCH FROM (now()-service.measured_at))),0)
      + COALESCE((SELECT sum(GREATEST(1,EXTRACT(EPOCH FROM (now()-active.started_at))))
          FROM runtime.queue_service_runs active
          JOIN procrastinate_jobs running ON running.id=active.job_id AND running.status='doing'
          JOIN procrastinate_workers owner ON owner.id=running.worker_id
            AND owner.last_heartbeat>now()-interval '30 seconds'
          WHERE active.data_generation=k.data_generation AND active.queue_name=j.queue_name AND active.domain=split_part(j.task_name,'.',1)
            AND active.subject_id=COALESCE(NULLIF(k.subject_id,''),'task:' || j.task_name)),0)
      - GREATEST(0,EXTRACT(EPOCH FROM (now()-GREATEST(k.created_at,COALESCE(j.scheduled_at,k.created_at))))),
      j.priority DESC, j.id
    LIMIT 1 FOR UPDATE OF j SKIP LOCKED;
    IF picked_id IS NULL THEN
        RETURN result;
    END IF;
    UPDATE procrastinate_jobs SET status='doing',worker_id=p_worker_id WHERE id=picked_id RETURNING * INTO result;
    INSERT INTO runtime.queue_service(data_generation,queue_name,domain,subject_id)
      VALUES(picked_generation,target_queue,picked_domain,picked_subject) ON CONFLICT DO NOTHING;
    INSERT INTO runtime.queue_service_runs(job_id,data_generation,queue_name,domain,subject_id)
      VALUES(picked_id,picked_generation,target_queue,picked_domain,picked_subject)
      ON CONFLICT(job_id) DO UPDATE SET data_generation=excluded.data_generation,queue_name=excluded.queue_name,domain=excluded.domain,
        subject_id=excluded.subject_id,started_at=now();
    RETURN result;
END;
$$;


--
-- Name: finish_fair_job(bigint); Type: FUNCTION; Schema: runtime; Owner: -
--

CREATE FUNCTION runtime.finish_fair_job(p_job_id bigint) RETURNS void
    LANGUAGE plpgsql
    AS $$
DECLARE finished runtime.queue_service_runs;
BEGIN
    DELETE FROM runtime.queue_service_runs WHERE job_id=p_job_id RETURNING * INTO finished;
    IF finished.job_id IS NOT NULL THEN
        UPDATE runtime.queue_service SET
          service_seconds=GREATEST(0,service_seconds-EXTRACT(EPOCH FROM (now()-measured_at)))
            + GREATEST(0.01,EXTRACT(EPOCH FROM (now()-finished.started_at))),
          measured_at=now()
        WHERE data_generation=finished.data_generation AND queue_name=finished.queue_name
          AND domain=finished.domain AND subject_id=finished.subject_id;
    END IF;
END;
$$;


--
-- Name: any_support_usable(jsonb, text[]); Type: FUNCTION; Schema: sources; Owner: -
--

CREATE FUNCTION sources.any_support_usable(ref_groups jsonb, viewer text[]) RETURNS boolean
    LANGUAGE sql STABLE
    AS $$ SELECT COALESCE(true=ANY(sources.check_groups(ref_groups,viewer)),false) $$;


--
-- Name: check_groups(jsonb, text[]); Type: FUNCTION; Schema: sources; Owner: -
--

CREATE FUNCTION sources.check_groups(ref_groups jsonb, viewer text[]) RETURNS boolean[]
    LANGUAGE plpgsql STABLE
    AS $$
DECLARE nodes jsonb; count_nodes bigint; result boolean[] := '{}'; refs jsonb; ref jsonb; ok boolean;
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


--
-- Name: dependencies_intersect(jsonb, jsonb, text); Type: FUNCTION; Schema: sources; Owner: -
--

CREATE FUNCTION sources.dependencies_intersect(refs jsonb, changes jsonb, reason text) RETURNS boolean
    LANGUAGE plpgsql IMMUTABLE
    AS $$
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


--
-- Name: valid_reference(jsonb, jsonb, text[], boolean, text[]); Type: FUNCTION; Schema: sources; Owner: -
--

CREATE FUNCTION sources.valid_reference(ref jsonb, nodes jsonb, viewer text[], require_access boolean, visited text[] DEFAULT '{}'::text[]) RETURNS boolean
    LANGUAGE plpgsql IMMUTABLE
    AS $_$
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
END $_$;


--
-- Name: action_controls; Type: TABLE; Schema: capabilities; Owner: -
--

CREATE TABLE capabilities.action_controls (
    id text NOT NULL,
    action_id text NOT NULL,
    attempt_id text NOT NULL
);


--
-- Name: actions; Type: TABLE; Schema: capabilities; Owner: -
--

CREATE TABLE capabilities.actions (
    id text NOT NULL,
    intent text NOT NULL,
    semantics text NOT NULL,
    principal_id text NOT NULL,
    kind text NOT NULL,
    instance_id text,
    created_at double precision NOT NULL,
    document text NOT NULL,
    action jsonb NOT NULL,
    attempt jsonb NOT NULL,
    receipt jsonb NOT NULL,
    result_revision bigint DEFAULT 0 NOT NULL,
    result_observed_at timestamp with time zone,
    execution_owner text
);


--
-- Name: artifacts; Type: TABLE; Schema: capabilities; Owner: -
--

CREATE TABLE capabilities.artifacts (
    id text NOT NULL,
    media_id text NOT NULL,
    principal_id text NOT NULL,
    actor_id text,
    scopes text[] NOT NULL,
    audience text[] NOT NULL,
    body jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: attempts; Type: TABLE; Schema: capabilities; Owner: -
--

CREATE TABLE capabilities.attempts (
    id text NOT NULL,
    action_id text NOT NULL,
    attempt jsonb NOT NULL,
    receipt jsonb NOT NULL
);


--
-- Name: command_inputs; Type: TABLE; Schema: capabilities; Owner: -
--

CREATE TABLE capabilities.command_inputs (
    id text NOT NULL,
    principal_id text NOT NULL,
    fingerprint text NOT NULL,
    body bytea NOT NULL
);


--
-- Name: configuration; Type: TABLE; Schema: capabilities; Owner: -
--

CREATE TABLE capabilities.configuration (
    kind text NOT NULL,
    id text NOT NULL,
    revision bigint NOT NULL,
    body jsonb NOT NULL,
    secret bytea
);


--
-- Name: consumptions; Type: TABLE; Schema: capabilities; Owner: -
--

CREATE TABLE capabilities.consumptions (
    id text NOT NULL,
    action_id text NOT NULL,
    source_id text NOT NULL,
    source_revision bigint NOT NULL,
    body jsonb NOT NULL
);


--
-- Name: device_readings; Type: TABLE; Schema: capabilities; Owner: -
--

CREATE TABLE capabilities.device_readings (
    connection_id text NOT NULL,
    entity_id text NOT NULL,
    revision bigint NOT NULL,
    body jsonb NOT NULL
);


--
-- Name: execution_owners; Type: TABLE; Schema: capabilities; Owner: -
--

CREATE TABLE capabilities.execution_owners (
    id text NOT NULL,
    role text NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    stopped_at timestamp with time zone
);


--
-- Name: image_work; Type: TABLE; Schema: capabilities; Owner: -
--

CREATE TABLE capabilities.image_work (
    action_id text NOT NULL,
    body jsonb NOT NULL,
    submitted boolean DEFAULT false NOT NULL,
    provider_handle text
);


--
-- Name: maintenance; Type: TABLE; Schema: capabilities; Owner: -
--

CREATE TABLE capabilities.maintenance (
    name text NOT NULL,
    owner text NOT NULL,
    lease_until timestamp with time zone NOT NULL,
    body jsonb DEFAULT '{}'::jsonb NOT NULL,
    next_due_at timestamp with time zone,
    wake_generation bigint DEFAULT 0 NOT NULL
);


--
-- Name: oauth_refresh_leases; Type: TABLE; Schema: capabilities; Owner: -
--

CREATE TABLE capabilities.oauth_refresh_leases (
    connection_id text NOT NULL,
    fingerprint text NOT NULL,
    owner text NOT NULL,
    state text NOT NULL,
    expires_at timestamp with time zone NOT NULL
);


--
-- Name: oauth_states; Type: TABLE; Schema: capabilities; Owner: -
--

CREATE TABLE capabilities.oauth_states (
    state text NOT NULL,
    principal_id text NOT NULL,
    connection_id text NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    body jsonb NOT NULL
);


--
-- Name: plugin_consumptions; Type: TABLE; Schema: capabilities; Owner: -
--

CREATE TABLE capabilities.plugin_consumptions (
    id text NOT NULL,
    instance_id text NOT NULL,
    event_id text NOT NULL,
    context jsonb NOT NULL,
    event jsonb NOT NULL
);


--
-- Name: reaction_assets; Type: TABLE; Schema: capabilities; Owner: -
--

CREATE TABLE capabilities.reaction_assets (
    id text NOT NULL,
    pack_id text NOT NULL,
    scopes text[] NOT NULL,
    actors text[] NOT NULL,
    revision bigint NOT NULL,
    body jsonb NOT NULL,
    search_text text NOT NULL,
    embedding public.vector,
    profile_id text
);


--
-- Name: reaction_embedding_profiles; Type: TABLE; Schema: capabilities; Owner: -
--

CREATE TABLE capabilities.reaction_embedding_profiles (
    id text NOT NULL,
    dimensions integer NOT NULL,
    model text NOT NULL,
    active boolean DEFAULT true NOT NULL
);


--
-- Name: runtime_observations; Type: TABLE; Schema: capabilities; Owner: -
--

CREATE TABLE capabilities.runtime_observations (
    name text NOT NULL,
    owner_id text NOT NULL,
    revision bigint DEFAULT 1 NOT NULL,
    observed_at timestamp with time zone DEFAULT now() NOT NULL,
    body jsonb NOT NULL
);


--
-- Name: source_dependencies; Type: TABLE; Schema: capabilities; Owner: -
--

CREATE TABLE capabilities.source_dependencies (
    owner_kind text NOT NULL,
    owner_id text NOT NULL,
    source_id text NOT NULL,
    source_revision bigint NOT NULL,
    refs jsonb NOT NULL
);


--
-- Name: speech_cache; Type: TABLE; Schema: capabilities; Owner: -
--

CREATE TABLE capabilities.speech_cache (
    key text NOT NULL,
    result jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: connections; Type: TABLE; Schema: capability_connections; Owner: -
--

CREATE TABLE capability_connections.connections (
    kind text NOT NULL,
    revision bigint NOT NULL,
    body text NOT NULL,
    last_test jsonb
);


--
-- Name: control_operations; Type: TABLE; Schema: capability_infrastructure; Owner: -
--

CREATE TABLE capability_infrastructure.control_operations (
    operation_id text NOT NULL,
    client_id text NOT NULL,
    node_id text NOT NULL,
    revision integer NOT NULL,
    kind text NOT NULL,
    stack_id text,
    container_id text,
    lock_key text NOT NULL,
    semantics text NOT NULL,
    payload text NOT NULL,
    document text NOT NULL,
    state text NOT NULL,
    settled integer DEFAULT 0 NOT NULL,
    created_at double precision NOT NULL,
    updated_at double precision NOT NULL,
    owner text,
    lease_until double precision DEFAULT 0 NOT NULL,
    sent integer DEFAULT 0 NOT NULL,
    next_attempt double precision DEFAULT 0 NOT NULL
);


--
-- Name: control_plan_uses; Type: TABLE; Schema: capability_infrastructure; Owner: -
--

CREATE TABLE capability_infrastructure.control_plan_uses (
    node_id text NOT NULL,
    plan_id text NOT NULL,
    operation_id text NOT NULL
);


--
-- Name: control_plans; Type: TABLE; Schema: capability_infrastructure; Owner: -
--

CREATE TABLE capability_infrastructure.control_plans (
    node_id text NOT NULL,
    plan_id text NOT NULL,
    operation_id text NOT NULL,
    document text NOT NULL
);


--
-- Name: observations; Type: TABLE; Schema: capability_infrastructure; Owner: -
--

CREATE TABLE capability_infrastructure.observations (
    node_id text NOT NULL,
    operation text NOT NULL,
    revision integer NOT NULL,
    latest text NOT NULL,
    sample text
);


--
-- Name: documents; Type: TABLE; Schema: capability_media; Owner: -
--

CREATE TABLE capability_media.documents (
    kind text NOT NULL,
    id text NOT NULL,
    revision integer NOT NULL,
    body text NOT NULL
);


--
-- Name: download_sources; Type: TABLE; Schema: capability_media; Owner: -
--

CREATE TABLE capability_media.download_sources (
    bvid text NOT NULL,
    cid text NOT NULL,
    quality text NOT NULL,
    state text NOT NULL,
    owner text,
    generation integer DEFAULT 0 NOT NULL,
    lease double precision DEFAULT 0 NOT NULL,
    body text
);


--
-- Name: episodes; Type: TABLE; Schema: capability_media; Owner: -
--

CREATE TABLE capability_media.episodes (
    bvid text NOT NULL,
    cid text NOT NULL,
    number integer NOT NULL
);


--
-- Name: events; Type: TABLE; Schema: capability_media; Owner: -
--

CREATE TABLE capability_media.events (
    job_id text NOT NULL,
    state text NOT NULL,
    code text NOT NULL,
    at double precision NOT NULL
);


--
-- Name: jobs; Type: TABLE; Schema: capability_media; Owner: -
--

CREATE TABLE capability_media.jobs (
    id text NOT NULL,
    dedupe text NOT NULL,
    state text NOT NULL,
    created double precision NOT NULL,
    updated double precision NOT NULL,
    generation integer DEFAULT 0 NOT NULL,
    owner text,
    lease double precision DEFAULT 0 NOT NULL,
    body text NOT NULL
);


--
-- Name: member_parts; Type: TABLE; Schema: capability_media; Owner: -
--

CREATE TABLE capability_media.member_parts (
    subscription_id text NOT NULL,
    bvid text NOT NULL,
    cid text NOT NULL
);


--
-- Name: members; Type: TABLE; Schema: capability_media; Owner: -
--

CREATE TABLE capability_media.members (
    subscription_id text NOT NULL,
    bvid text NOT NULL
);


--
-- Name: scan_leases; Type: TABLE; Schema: capability_media; Owner: -
--

CREATE TABLE capability_media.scan_leases (
    id text NOT NULL,
    owner text NOT NULL,
    generation integer NOT NULL,
    expires double precision NOT NULL
);


--
-- Name: plugin_instances; Type: TABLE; Schema: capability_plugins; Owner: -
--

CREATE TABLE capability_plugins.plugin_instances (
    id text NOT NULL,
    document text NOT NULL
);


--
-- Name: plugin_logs; Type: TABLE; Schema: capability_plugins; Owner: -
--

CREATE TABLE capability_plugins.plugin_logs (
    id text NOT NULL,
    instance_id text NOT NULL,
    created_at double precision NOT NULL,
    document text NOT NULL
);


--
-- Name: plugin_notifications; Type: TABLE; Schema: capability_plugins; Owner: -
--

CREATE TABLE capability_plugins.plugin_notifications (
    id text NOT NULL,
    document text NOT NULL
);


--
-- Name: plugin_operations; Type: TABLE; Schema: capability_plugins; Owner: -
--

CREATE TABLE capability_plugins.plugin_operations (
    id text NOT NULL,
    document text NOT NULL
);


--
-- Name: plugin_packages; Type: TABLE; Schema: capability_plugins; Owner: -
--

CREATE TABLE capability_plugins.plugin_packages (
    id text NOT NULL,
    document text NOT NULL
);


--
-- Name: plugin_schedules; Type: TABLE; Schema: capability_plugins; Owner: -
--

CREATE TABLE capability_plugins.plugin_schedules (
    id text NOT NULL,
    document text NOT NULL
);


--
-- Name: plugin_secrets; Type: TABLE; Schema: capability_plugins; Owner: -
--

CREATE TABLE capability_plugins.plugin_secrets (
    instance_id text NOT NULL,
    name text NOT NULL,
    value bytea NOT NULL
);


--
-- Name: plugin_state; Type: TABLE; Schema: capability_plugins; Owner: -
--

CREATE TABLE capability_plugins.plugin_state (
    instance_id text NOT NULL,
    key text NOT NULL,
    value text NOT NULL,
    revision integer NOT NULL
);


--
-- Name: continuations; Type: TABLE; Schema: capability_projects; Owner: -
--

CREATE TABLE capability_projects.continuations (
    pack_id text NOT NULL,
    project_id text NOT NULL,
    principal_id text NOT NULL,
    actor_id text,
    scope_ids text[] NOT NULL,
    project_revision bigint NOT NULL,
    worktree_id text NOT NULL,
    facts jsonb NOT NULL,
    payload jsonb NOT NULL,
    source_refs jsonb NOT NULL,
    sha256 text NOT NULL,
    observed_at timestamp with time zone DEFAULT now() NOT NULL,
    expires_at timestamp with time zone NOT NULL
);


--
-- Name: declared_states; Type: TABLE; Schema: capability_projects; Owner: -
--

CREATE TABLE capability_projects.declared_states (
    project_id text NOT NULL,
    revision bigint NOT NULL,
    payload jsonb NOT NULL,
    source_refs jsonb NOT NULL,
    evidence jsonb NOT NULL,
    state text DEFAULT 'active'::text NOT NULL,
    action_id text NOT NULL,
    observed_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: directory_plans; Type: TABLE; Schema: capability_projects; Owner: -
--

CREATE TABLE capability_projects.directory_plans (
    plan_id text NOT NULL,
    project_id text NOT NULL,
    principal_id text NOT NULL,
    directory text NOT NULL,
    project_revision bigint NOT NULL,
    manifest jsonb NOT NULL,
    sha256 text NOT NULL,
    coverage jsonb NOT NULL,
    result jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: document_versions; Type: TABLE; Schema: capability_projects; Owner: -
--

CREATE TABLE capability_projects.document_versions (
    document_id text NOT NULL,
    version bigint NOT NULL,
    sha256 text NOT NULL,
    mime_type text NOT NULL,
    source_refs jsonb NOT NULL,
    original_ref jsonb NOT NULL,
    text_ref jsonb,
    metadata jsonb NOT NULL,
    units jsonb NOT NULL,
    state text DEFAULT 'active'::text NOT NULL,
    imported_at timestamp with time zone DEFAULT now() NOT NULL,
    action_id text NOT NULL
);


--
-- Name: documents; Type: TABLE; Schema: capability_projects; Owner: -
--

CREATE TABLE capability_projects.documents (
    document_id text NOT NULL,
    project_id text NOT NULL,
    locator text NOT NULL,
    kind text NOT NULL,
    current_version bigint NOT NULL,
    state text DEFAULT 'active'::text NOT NULL,
    last_action_id text NOT NULL
);


--
-- Name: experience_versions; Type: TABLE; Schema: capability_projects; Owner: -
--

CREATE TABLE capability_projects.experience_versions (
    experience_id text NOT NULL,
    revision bigint NOT NULL,
    payload jsonb NOT NULL,
    evidence jsonb NOT NULL,
    source_refs jsonb NOT NULL,
    disclosure_ref jsonb,
    state text NOT NULL,
    action_id text NOT NULL,
    observed_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: experiences; Type: TABLE; Schema: capability_projects; Owner: -
--

CREATE TABLE capability_projects.experiences (
    experience_id text NOT NULL,
    owner_id text NOT NULL,
    actor_id text,
    scope_ids text[] NOT NULL,
    revision bigint NOT NULL,
    payload jsonb NOT NULL,
    evidence jsonb NOT NULL,
    source_refs jsonb NOT NULL,
    disclosure_ref jsonb,
    state text NOT NULL,
    last_action_id text NOT NULL,
    observed_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: imports; Type: TABLE; Schema: capability_projects; Owner: -
--

CREATE TABLE capability_projects.imports (
    import_id text NOT NULL,
    project_id text NOT NULL,
    action_id text NOT NULL,
    plan_id text,
    state text NOT NULL,
    items jsonb DEFAULT '[]'::jsonb NOT NULL,
    result jsonb DEFAULT '{}'::jsonb NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: lesson_versions; Type: TABLE; Schema: capability_projects; Owner: -
--

CREATE TABLE capability_projects.lesson_versions (
    lesson_id text NOT NULL,
    version bigint NOT NULL,
    payload jsonb NOT NULL,
    evidence jsonb NOT NULL,
    source_refs jsonb NOT NULL,
    sha256 text NOT NULL,
    state text NOT NULL,
    action_id text NOT NULL,
    observed_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: lessons; Type: TABLE; Schema: capability_projects; Owner: -
--

CREATE TABLE capability_projects.lessons (
    lesson_id text NOT NULL,
    project_id text NOT NULL,
    current_version bigint NOT NULL,
    state text NOT NULL,
    last_action_id text NOT NULL
);


--
-- Name: note_versions; Type: TABLE; Schema: capability_projects; Owner: -
--

CREATE TABLE capability_projects.note_versions (
    note_id text NOT NULL,
    version bigint NOT NULL,
    payload jsonb NOT NULL,
    citations jsonb NOT NULL,
    source_refs jsonb NOT NULL,
    sha256 text NOT NULL,
    state text NOT NULL,
    action_id text NOT NULL,
    observed_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: notes; Type: TABLE; Schema: capability_projects; Owner: -
--

CREATE TABLE capability_projects.notes (
    note_id text NOT NULL,
    project_id text NOT NULL,
    current_version bigint NOT NULL,
    state text NOT NULL,
    last_action_id text NOT NULL
);


--
-- Name: projects; Type: TABLE; Schema: capability_projects; Owner: -
--

CREATE TABLE capability_projects.projects (
    project_id text NOT NULL,
    actor_id text,
    scope_ids text[] NOT NULL,
    owner_id text NOT NULL,
    conversation_id text,
    label text NOT NULL,
    root text NOT NULL,
    directories jsonb DEFAULT '[]'::jsonb NOT NULL,
    allowed_urls jsonb DEFAULT '[]'::jsonb NOT NULL,
    worktrees jsonb DEFAULT '[]'::jsonb NOT NULL,
    enabled boolean DEFAULT true NOT NULL,
    revision bigint NOT NULL,
    content_revision bigint DEFAULT 0 NOT NULL,
    last_action_id text NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: registry_versions; Type: TABLE; Schema: capability_projects; Owner: -
--

CREATE TABLE capability_projects.registry_versions (
    project_id text NOT NULL,
    revision bigint NOT NULL,
    payload jsonb NOT NULL,
    action_id text NOT NULL
);


--
-- Name: calls; Type: TABLE; Schema: channels; Owner: -
--

CREATE TABLE channels.calls (
    id text NOT NULL,
    action_id text NOT NULL,
    attempt_id text NOT NULL,
    connection_id text NOT NULL,
    expression_id text,
    api_name text NOT NULL,
    control boolean DEFAULT false NOT NULL,
    expected_count integer DEFAULT 1 NOT NULL,
    state text DEFAULT 'prepared'::text NOT NULL,
    payload bytea,
    response bytea,
    result jsonb,
    owner_id text,
    submitted_at timestamp with time zone,
    completed_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    deployment_generation jsonb
);


--
-- Name: connections; Type: TABLE; Schema: channels; Owner: -
--

CREATE TABLE channels.connections (
    id text NOT NULL,
    revision bigint NOT NULL,
    body jsonb NOT NULL,
    secret_ref text,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: event_sources; Type: TABLE; Schema: channels; Owner: -
--

CREATE TABLE channels.event_sources (
    event_key text NOT NULL,
    source_id text NOT NULL,
    source_revision bigint NOT NULL,
    refs jsonb NOT NULL
);


--
-- Name: events; Type: TABLE; Schema: channels; Owner: -
--

CREATE TABLE channels.events (
    id text NOT NULL,
    connection_id text NOT NULL,
    producer text NOT NULL,
    event_id text NOT NULL,
    revision bigint NOT NULL,
    payload bytea,
    state text DEFAULT 'accepted'::text NOT NULL,
    outcome jsonb,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    original_digest text,
    deployment_generation jsonb,
    actor_id text
);


--
-- Name: expressions; Type: TABLE; Schema: channels; Owner: -
--

CREATE TABLE channels.expressions (
    expression_id text NOT NULL,
    action_id text NOT NULL,
    principal_id text NOT NULL,
    conversation_id text NOT NULL,
    semantics text NOT NULL
);


--
-- Name: media_supplements; Type: TABLE; Schema: channels; Owner: -
--

CREATE TABLE channels.media_supplements (
    id text NOT NULL,
    event_key text NOT NULL,
    part_index integer NOT NULL,
    input_id text,
    context jsonb,
    original_ref jsonb,
    deployment_generation jsonb,
    lineage_id text,
    lineage_version text,
    active_attempt_id text,
    probe_token text,
    payload bytea,
    state text NOT NULL,
    attempts integer DEFAULT 0 NOT NULL,
    config_revision bigint NOT NULL,
    error_code text,
    next_due timestamp with time zone,
    lease_until timestamp with time zone,
    attempt_started_at timestamp with time zone,
    observed_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT media_supplements_state_check CHECK ((state = ANY (ARRAY['waiting_ingest'::text, 'waiting_retry'::text, 'fetching'::text, 'waiting_config'::text, 'complete'::text, 'withdrawn'::text, 'unavailable'::text])))
);


--
-- Name: observations; Type: TABLE; Schema: channels; Owner: -
--

CREATE TABLE channels.observations (
    connection_id text NOT NULL,
    kind text NOT NULL,
    name text NOT NULL,
    body jsonb NOT NULL,
    observed_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: owners; Type: TABLE; Schema: channels; Owner: -
--

CREATE TABLE channels.owners (
    id text NOT NULL,
    owner_id text NOT NULL,
    epoch bigint NOT NULL,
    expires_at timestamp with time zone NOT NULL
);


--
-- Name: source_dependencies; Type: TABLE; Schema: channels; Owner: -
--

CREATE TABLE channels.source_dependencies (
    call_id text NOT NULL,
    source_id text NOT NULL,
    source_revision bigint NOT NULL,
    refs jsonb NOT NULL
);


--
-- Name: telegram_offsets; Type: TABLE; Schema: channels; Owner: -
--

CREATE TABLE channels.telegram_offsets (
    connection_id text NOT NULL,
    update_id bigint NOT NULL
);


--
-- Name: actors; Type: TABLE; Schema: companion; Owner: -
--

CREATE TABLE companion.actors (
    actor_id text NOT NULL,
    name text NOT NULL,
    enabled boolean DEFAULT false NOT NULL,
    revision bigint DEFAULT 1 NOT NULL,
    persona_id text,
    persona_revision bigint,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    deleted_at timestamp with time zone
);


--
-- Name: affect_events; Type: TABLE; Schema: companion; Owner: -
--

CREATE TABLE companion.affect_events (
    actor_id text NOT NULL,
    event_id text NOT NULL,
    event_revision bigint NOT NULL,
    scope_ids text[] NOT NULL,
    appraisal text NOT NULL,
    valence double precision NOT NULL,
    arousal double precision NOT NULL,
    stress double precision NOT NULL,
    social_need double precision NOT NULL,
    confidence double precision NOT NULL,
    half_life_seconds integer NOT NULL,
    goal_id text,
    goal_relevance double precision DEFAULT 0 NOT NULL,
    attribution text DEFAULT 'unknown'::text NOT NULL,
    control double precision DEFAULT 0.5 NOT NULL,
    occurred_at timestamp with time zone NOT NULL,
    source_refs jsonb NOT NULL,
    CONSTRAINT affect_events_arousal_check CHECK (((arousal >= (0)::double precision) AND (arousal <= (1)::double precision))),
    CONSTRAINT affect_events_attribution_check CHECK ((attribution = ANY (ARRAY['self'::text, 'other'::text, 'situation'::text, 'unknown'::text]))),
    CONSTRAINT affect_events_confidence_check CHECK (((confidence >= (0)::double precision) AND (confidence <= (1)::double precision))),
    CONSTRAINT affect_events_control_check CHECK (((control >= (0)::double precision) AND (control <= (1)::double precision))),
    CONSTRAINT affect_events_goal_relevance_check CHECK (((goal_relevance >= ('-1'::integer)::double precision) AND (goal_relevance <= (1)::double precision))),
    CONSTRAINT affect_events_half_life_seconds_check CHECK (((half_life_seconds >= 60) AND (half_life_seconds <= 604800))),
    CONSTRAINT affect_events_social_need_check CHECK (((social_need >= (0)::double precision) AND (social_need <= (1)::double precision))),
    CONSTRAINT affect_events_stress_check CHECK (((stress >= (0)::double precision) AND (stress <= (1)::double precision))),
    CONSTRAINT affect_events_valence_check CHECK (((valence >= ('-1'::integer)::double precision) AND (valence <= (1)::double precision)))
);


--
-- Name: affect_regulation; Type: TABLE; Schema: companion; Owner: -
--

CREATE TABLE companion.affect_regulation (
    actor_id text NOT NULL,
    scope_key text NOT NULL,
    mode text DEFAULT 'steady'::text NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: expression_example_usage; Type: TABLE; Schema: companion; Owner: -
--

CREATE TABLE companion.expression_example_usage (
    example_id text NOT NULL,
    usage_id text NOT NULL,
    turn_id text,
    outcome text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: expression_examples; Type: TABLE; Schema: companion; Owner: -
--

CREATE TABLE companion.expression_examples (
    id text NOT NULL,
    rule_id text NOT NULL,
    rule_revision bigint NOT NULL,
    semantic_revision bigint NOT NULL,
    actor_id text NOT NULL,
    person_id text,
    conversation_id text NOT NULL,
    turn_id text NOT NULL,
    expression_id text NOT NULL,
    input_message_ids jsonb NOT NULL,
    output_message_ids jsonb NOT NULL,
    scope_ids text[] NOT NULL,
    source_refs jsonb NOT NULL,
    state text DEFAULT 'active'::text NOT NULL,
    revision bigint DEFAULT 1 NOT NULL,
    reason text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT expression_examples_state_check CHECK ((state = ANY (ARRAY['active'::text, 'rejected'::text, 'invalidated'::text])))
);


--
-- Name: growth_inputs; Type: TABLE; Schema: companion; Owner: -
--

CREATE TABLE companion.growth_inputs (
    actor_id text NOT NULL,
    scope_key text NOT NULL,
    episode_id text NOT NULL,
    revision bigint NOT NULL,
    sequence bigint NOT NULL,
    reviewed_revision bigint DEFAULT 0 NOT NULL,
    state text DEFAULT 'pending'::text NOT NULL,
    missing text[] DEFAULT '{}'::text[] NOT NULL,
    context jsonb NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    retry_key text,
    skip_reason text,
    CONSTRAINT growth_inputs_state_check CHECK ((state = ANY (ARRAY['pending'::text, 'reviewed'::text, 'unavailable'::text])))
);


--
-- Name: growth_material_failures; Type: TABLE; Schema: companion; Owner: -
--

CREATE TABLE companion.growth_material_failures (
    actor_id text NOT NULL,
    scope_key text NOT NULL,
    episode_id text NOT NULL,
    episode_revision bigint NOT NULL,
    failure_key text NOT NULL,
    runtime_generation text NOT NULL,
    error_code text NOT NULL,
    details jsonb NOT NULL,
    context jsonb NOT NULL,
    state text DEFAULT 'blocked'::text NOT NULL,
    observation_revision bigint DEFAULT 1 NOT NULL,
    retry_count integer DEFAULT 1 NOT NULL,
    due_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT growth_material_failures_state_check CHECK ((state = ANY (ARRAY['blocked'::text, 'released'::text, 'superseded'::text])))
);


--
-- Name: growth_retry_batches; Type: TABLE; Schema: companion; Owner: -
--

CREATE TABLE companion.growth_retry_batches (
    actor_id text NOT NULL,
    scope_key text NOT NULL,
    retry_key text NOT NULL,
    batch jsonb NOT NULL,
    current_material_key text NOT NULL,
    previous_material_key text,
    attempt_number integer DEFAULT 1 NOT NULL,
    state text NOT NULL,
    due_at timestamp with time zone,
    last_error text,
    waiting_on_original boolean DEFAULT false NOT NULL,
    explicit_retry boolean DEFAULT false NOT NULL,
    history jsonb DEFAULT '[]'::jsonb NOT NULL,
    outcome text,
    revision bigint DEFAULT 1 NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT growth_retry_batches_attempt_number_check CHECK ((attempt_number > 0)),
    CONSTRAINT growth_retry_batches_history_check CHECK ((jsonb_typeof(history) = 'array'::text)),
    CONSTRAINT growth_retry_batches_state_check CHECK ((state = ANY (ARRAY['waiting'::text, 'active'::text, 'completed'::text, 'blocked'::text])))
);


--
-- Name: growth_statistics_outbox; Type: TABLE; Schema: companion; Owner: -
--

CREATE TABLE companion.growth_statistics_outbox (
    runtime_generation text NOT NULL,
    event_id text NOT NULL,
    revision bigint NOT NULL,
    event jsonb NOT NULL,
    delivered boolean DEFAULT false NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: habit_revisions; Type: TABLE; Schema: companion; Owner: -
--

CREATE TABLE companion.habit_revisions (
    rule_id text NOT NULL,
    revision bigint NOT NULL,
    semantic_revision bigint NOT NULL,
    action text NOT NULL,
    snapshot jsonb NOT NULL,
    source_refs jsonb NOT NULL,
    proposal_id text,
    attempt_id text,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: habit_supports; Type: TABLE; Schema: companion; Owner: -
--

CREATE TABLE companion.habit_supports (
    rule_id text NOT NULL,
    event_id text NOT NULL,
    source_ids text[] NOT NULL,
    source_refs jsonb NOT NULL,
    proposal_id text NOT NULL
);


--
-- Name: habit_unresolved; Type: TABLE; Schema: companion; Owner: -
--

CREATE TABLE companion.habit_unresolved (
    proposal_id text NOT NULL,
    actor_id text NOT NULL,
    person_id text,
    conversation_id text,
    scope_ids text[] NOT NULL,
    source_refs jsonb NOT NULL,
    proposal jsonb NOT NULL,
    reason text NOT NULL,
    state text DEFAULT 'pending'::text NOT NULL,
    revision bigint DEFAULT 1 NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT habit_unresolved_state_check CHECK ((state = ANY (ARRAY['pending'::text, 'dismissed'::text])))
);


--
-- Name: learning_reflections; Type: TABLE; Schema: companion; Owner: -
--

CREATE TABLE companion.learning_reflections (
    proposal_id text NOT NULL,
    actor_id text NOT NULL,
    principal_id text NOT NULL,
    person_id text,
    conversation_id text,
    revision bigint DEFAULT 1 NOT NULL,
    content text NOT NULL,
    episode_ids text[] NOT NULL,
    support_ids text[] NOT NULL,
    source_refs jsonb NOT NULL,
    scope_ids text[] NOT NULL,
    conditions jsonb NOT NULL,
    counterexamples jsonb NOT NULL,
    occurred_at timestamp with time zone NOT NULL,
    attempt_id text NOT NULL,
    state text DEFAULT 'active'::text NOT NULL,
    reason text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT learning_reflections_state_check CHECK ((state = ANY (ARRAY['active'::text, 'invalidated'::text])))
);


--
-- Name: organize_state; Type: TABLE; Schema: companion; Owner: -
--

CREATE TABLE companion.organize_state (
    actor_id text NOT NULL,
    scope_key text NOT NULL,
    cursor text,
    revision bigint DEFAULT 1 NOT NULL,
    watermark text DEFAULT ''::text NOT NULL,
    reviewed_watermark text DEFAULT ''::text NOT NULL,
    context jsonb,
    generation_revision bigint DEFAULT 0 NOT NULL,
    last_error text,
    last_reviewed_at timestamp with time zone,
    last_changed_at timestamp with time zone,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    active_batch jsonb,
    blocked_material jsonb,
    reviewed_material_at timestamp with time zone,
    runtime_generation text
);


--
-- Name: persona_profiles; Type: TABLE; Schema: companion; Owner: -
--

CREATE TABLE companion.persona_profiles (
    persona_id text NOT NULL,
    name text NOT NULL,
    revision bigint DEFAULT 1 NOT NULL,
    current_revision bigint DEFAULT 1 NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: persona_revisions; Type: TABLE; Schema: companion; Owner: -
--

CREATE TABLE companion.persona_revisions (
    persona_id text NOT NULL,
    revision bigint NOT NULL,
    author_text text NOT NULL,
    tone text DEFAULT ''::text NOT NULL,
    style text DEFAULT ''::text NOT NULL,
    address text DEFAULT ''::text NOT NULL,
    world text DEFAULT ''::text NOT NULL,
    note text DEFAULT ''::text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: style_rules; Type: TABLE; Schema: companion; Owner: -
--

CREATE TABLE companion.style_rules (
    id text NOT NULL,
    actor_id text NOT NULL,
    person_id text,
    revision bigint DEFAULT 1 NOT NULL,
    kind text NOT NULL,
    title text NOT NULL,
    content text NOT NULL,
    conditions jsonb DEFAULT '{}'::jsonb NOT NULL,
    counterexamples jsonb DEFAULT '[]'::jsonb NOT NULL,
    episode_ids text[] NOT NULL,
    source_refs jsonb NOT NULL,
    scope_ids text[] NOT NULL,
    state text NOT NULL,
    reason text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    semantic_revision bigint DEFAULT 1 NOT NULL,
    preference_kind text DEFAULT 'inferred'::text NOT NULL,
    conversation_id text,
    purposes text[] DEFAULT '{conversation}'::text[] NOT NULL,
    valid_from timestamp with time zone,
    valid_until timestamp with time zone,
    support_count integer DEFAULT 0 NOT NULL,
    CONSTRAINT style_rules_preference_kind_check CHECK ((preference_kind = ANY (ARRAY['inferred'::text, 'explicit'::text, 'temporary'::text]))),
    CONSTRAINT style_rules_state_check CHECK ((state = ANY (ARRAY['active'::text, 'revoked'::text, 'invalidated'::text])))
);


--
-- Name: context_selections; Type: TABLE; Schema: conversation; Owner: -
--

CREATE TABLE conversation.context_selections (
    attempt_id text NOT NULL,
    turn_id text NOT NULL,
    round integer NOT NULL,
    request_digest text NOT NULL,
    selection jsonb NOT NULL,
    token_usage jsonb NOT NULL,
    source_refs jsonb NOT NULL,
    deployment_generation jsonb,
    round_snapshot jsonb NOT NULL,
    model_receipt jsonb,
    model_result jsonb,
    consumed_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: expression_plans; Type: TABLE; Schema: conversation; Owner: -
--

CREATE TABLE conversation.expression_plans (
    expression_id text NOT NULL,
    actor_id text NOT NULL,
    conversation_id text NOT NULL,
    revision bigint DEFAULT 1 NOT NULL,
    fingerprint text NOT NULL,
    context jsonb NOT NULL,
    plan jsonb NOT NULL,
    source_refs jsonb NOT NULL,
    profile jsonb,
    state text NOT NULL,
    service_class text DEFAULT 'elastic'::text NOT NULL,
    steps jsonb DEFAULT '[]'::jsonb NOT NULL,
    rendered jsonb,
    receipt jsonb,
    reason text,
    deadline_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: expressions; Type: TABLE; Schema: conversation; Owner: -
--

CREATE TABLE conversation.expressions (
    expression_id text NOT NULL,
    actor_id text NOT NULL,
    conversation_id text NOT NULL,
    turn_id text,
    target_ref text NOT NULL,
    context jsonb NOT NULL,
    public_parts jsonb NOT NULL,
    source_refs jsonb NOT NULL,
    artifacts jsonb DEFAULT '[]'::jsonb NOT NULL,
    state text NOT NULL,
    receipt jsonb,
    fingerprint text NOT NULL,
    candidate_id text,
    revision bigint DEFAULT 1 NOT NULL,
    lease_until timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    completed_at timestamp with time zone,
    result_observed_at timestamp with time zone,
    CONSTRAINT expressions_state_check CHECK ((state = ANY (ARRAY['prepared'::text, 'sending'::text, 'waiting_delivery'::text, 'delivered'::text, 'failed'::text, 'unknown'::text])))
);


--
-- Name: generation_requests; Type: TABLE; Schema: conversation; Owner: -
--

CREATE TABLE conversation.generation_requests (
    material_key text NOT NULL,
    attempt_id text NOT NULL,
    context_key text NOT NULL,
    request jsonb,
    source_refs jsonb NOT NULL,
    state text DEFAULT 'prepared'::text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    claimed_at timestamp with time zone,
    CONSTRAINT generation_requests_state_check CHECK ((state = ANY (ARRAY['prepared'::text, 'claimed'::text, 'invalidated'::text, 'expired'::text])))
);


--
-- Name: group_participation; Type: TABLE; Schema: conversation; Owner: -
--

CREATE TABLE conversation.group_participation (
    actor_id text NOT NULL,
    conversation_id text NOT NULL,
    enabled boolean DEFAULT false NOT NULL,
    revision bigint DEFAULT 1 NOT NULL
);


--
-- Name: input_supplements; Type: TABLE; Schema: conversation; Owner: -
--

CREATE TABLE conversation.input_supplements (
    input_id text NOT NULL,
    part_index integer NOT NULL,
    supplement_revision bigint NOT NULL,
    original_ref jsonb NOT NULL,
    supplement_ref jsonb NOT NULL,
    media jsonb NOT NULL,
    content_digest text NOT NULL,
    deployment_generation jsonb,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    notification_state text DEFAULT 'pending'::text NOT NULL,
    notified_at timestamp with time zone
);


--
-- Name: messages; Type: TABLE; Schema: conversation; Owner: -
--

CREATE TABLE conversation.messages (
    id bigint NOT NULL,
    turn_id text,
    actor_id text NOT NULL,
    conversation_id text NOT NULL,
    speaker text NOT NULL,
    parts jsonb NOT NULL,
    source_refs jsonb NOT NULL,
    scope_ids text[] NOT NULL,
    expression_id text,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: messages_id_seq; Type: SEQUENCE; Schema: conversation; Owner: -
--

ALTER TABLE conversation.messages ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME conversation.messages_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: result_consumption; Type: TABLE; Schema: conversation; Owner: -
--

CREATE TABLE conversation.result_consumption (
    receipt_id text NOT NULL,
    action_id text NOT NULL,
    turn_id text,
    processed_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: tool_links; Type: TABLE; Schema: conversation; Owner: -
--

CREATE TABLE conversation.tool_links (
    turn_id text NOT NULL,
    call_id text NOT NULL,
    action_id text NOT NULL,
    capability_id text NOT NULL,
    state text NOT NULL,
    last_receipt_id text,
    tool_name text NOT NULL,
    provider_call_id text NOT NULL,
    arguments_hash text NOT NULL,
    result jsonb,
    background boolean DEFAULT false NOT NULL,
    consumed boolean DEFAULT false NOT NULL,
    result_revision bigint DEFAULT '-1'::integer NOT NULL
);


--
-- Name: turns; Type: TABLE; Schema: conversation; Owner: -
--

CREATE TABLE conversation.turns (
    turn_id text NOT NULL,
    actor_id text NOT NULL,
    conversation_id text NOT NULL,
    producer text NOT NULL,
    event_id text NOT NULL,
    event_revision bigint NOT NULL,
    context jsonb NOT NULL,
    input_parts jsonb NOT NULL,
    source_refs jsonb NOT NULL,
    original_parts jsonb NOT NULL,
    original_source_refs jsonb NOT NULL,
    autonomous boolean DEFAULT false NOT NULL,
    participation_state text,
    participation_policy_revision bigint,
    participation_checked_at timestamp with time zone,
    participation_deadline timestamp with time zone,
    participation_cutoff bigint,
    participation_reason text,
    sequence bigint NOT NULL,
    collector_key text,
    collection_revision bigint DEFAULT 1 NOT NULL,
    collection_messages integer DEFAULT 1 NOT NULL,
    collection_bytes integer DEFAULT 0 NOT NULL,
    seal_at timestamp with time zone,
    merged_into text,
    message_cutoff bigint,
    state text NOT NULL,
    reply_owner text NOT NULL,
    reply_required boolean NOT NULL,
    command text,
    revision bigint DEFAULT 1 NOT NULL,
    model_attempt_id text,
    model_parts jsonb,
    model_native jsonb,
    expression_id text,
    missing text[] DEFAULT '{}'::text[] NOT NULL,
    error_code text,
    occurred_at timestamp with time zone NOT NULL,
    received_at timestamp with time zone NOT NULL,
    started_at timestamp with time zone,
    completed_at timestamp with time zone,
    lease_until timestamp with time zone,
    deadline_at timestamp with time zone NOT NULL,
    round integer DEFAULT 0 NOT NULL,
    continuation jsonb DEFAULT '[]'::jsonb NOT NULL,
    ordered_parts jsonb DEFAULT '[]'::jsonb NOT NULL,
    artifacts jsonb DEFAULT '[]'::jsonb NOT NULL,
    tool_dispatch_complete boolean DEFAULT false NOT NULL,
    original_digest text,
    withdrawn_at timestamp with time zone,
    deployment_generation text,
    CONSTRAINT turns_state_check CHECK ((state = ANY (ARRAY['collecting'::text, 'merged'::text, 'queued'::text, 'running'::text, 'waiting_external'::text, 'waiting_expression'::text, 'completed'::text, 'failed'::text, 'unknown'::text, 'cancelled'::text, 'observed'::text])))
);


--
-- Name: turns_sequence_seq; Type: SEQUENCE; Schema: conversation; Owner: -
--

ALTER TABLE conversation.turns ALTER COLUMN sequence ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME conversation.turns_sequence_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: accounts; Type: TABLE; Schema: identity; Owner: -
--

CREATE TABLE identity.accounts (
    connection_id text NOT NULL,
    external_id text NOT NULL,
    person_id text NOT NULL,
    principal_id text,
    revision bigint DEFAULT 1 NOT NULL
);


--
-- Name: actor_grants; Type: TABLE; Schema: identity; Owner: -
--

CREATE TABLE identity.actor_grants (
    actor_id text NOT NULL,
    principal_id text NOT NULL,
    permissions text[] NOT NULL
);


--
-- Name: actors; Type: TABLE; Schema: identity; Owner: -
--

CREATE TABLE identity.actors (
    actor_id text NOT NULL,
    owner_id text NOT NULL,
    enabled boolean DEFAULT true NOT NULL,
    access_epoch bigint DEFAULT 1 NOT NULL
);


--
-- Name: anonymous_sessions; Type: TABLE; Schema: identity; Owner: -
--

CREATE TABLE identity.anonymous_sessions (
    token_hash text NOT NULL,
    csrf_token text NOT NULL,
    expires_at timestamp with time zone NOT NULL
);


--
-- Name: connections; Type: TABLE; Schema: identity; Owner: -
--

CREATE TABLE identity.connections (
    connection_id text NOT NULL,
    principal_id text NOT NULL,
    actor_id text NOT NULL,
    namespace text NOT NULL,
    self_external_id text NOT NULL,
    enabled boolean DEFAULT true NOT NULL,
    default_mode text DEFAULT 'reply'::text NOT NULL,
    event_permissions text[] DEFAULT ARRAY['chat'::text, 'conversation.read'::text, 'conversation.send'::text, 'companion.read'::text, 'life.read'::text, 'life.manage'::text, 'life.published.read'::text, 'memory.read'::text, 'memory.manage'::text, 'memory.share'::text] NOT NULL,
    revision bigint DEFAULT 1 NOT NULL,
    CONSTRAINT connections_default_mode_check CHECK ((default_mode = ANY (ARRAY['reply'::text, 'observe'::text, 'ignore'::text])))
);


--
-- Name: conversation_links; Type: TABLE; Schema: identity; Owner: -
--

CREATE TABLE identity.conversation_links (
    connection_id text NOT NULL,
    external_id text NOT NULL,
    conversation_id text NOT NULL,
    mode text NOT NULL,
    mention_required boolean DEFAULT false NOT NULL,
    reply_keywords text[] DEFAULT '{}'::text[] NOT NULL,
    inherited_private_policy boolean DEFAULT false NOT NULL,
    reply_owner text DEFAULT 'tianshu'::text NOT NULL,
    revision bigint DEFAULT 1 NOT NULL,
    CONSTRAINT conversation_links_mode_check CHECK ((mode = ANY (ARRAY['reply'::text, 'observe'::text, 'ignore'::text])))
);


--
-- Name: conversation_members; Type: TABLE; Schema: identity; Owner: -
--

CREATE TABLE identity.conversation_members (
    conversation_id text NOT NULL,
    connection_id text NOT NULL,
    external_id text NOT NULL,
    person_id text NOT NULL,
    last_observed_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: conversations; Type: TABLE; Schema: identity; Owner: -
--

CREATE TABLE identity.conversations (
    conversation_id text NOT NULL,
    actor_id text NOT NULL,
    scope_id text NOT NULL,
    person_id text,
    kind text NOT NULL,
    label text NOT NULL,
    adapter text DEFAULT 'web'::text NOT NULL,
    target_ref text,
    revision bigint DEFAULT 1 NOT NULL,
    CONSTRAINT conversations_kind_check CHECK ((kind = ANY (ARRAY['private'::text, 'group'::text, 'project'::text, 'system'::text])))
);


--
-- Name: events; Type: TABLE; Schema: identity; Owner: -
--

CREATE TABLE identity.events (
    connection_id text NOT NULL,
    producer text NOT NULL,
    event_id text NOT NULL,
    revision bigint NOT NULL,
    content_hash text NOT NULL,
    conversation_id text NOT NULL,
    speaker_id text NOT NULL,
    disposition text NOT NULL,
    occurred_at timestamp with time zone NOT NULL
);


--
-- Name: installation; Type: TABLE; Schema: identity; Owner: -
--

CREATE TABLE identity.installation (
    singleton boolean DEFAULT true NOT NULL,
    initialized_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT installation_singleton_check CHECK (singleton)
);


--
-- Name: login_windows; Type: TABLE; Schema: identity; Owner: -
--

CREATE TABLE identity.login_windows (
    username_hash text NOT NULL,
    failures integer DEFAULT 0 NOT NULL,
    window_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: people; Type: TABLE; Schema: identity; Owner: -
--

CREATE TABLE identity.people (
    person_id text NOT NULL,
    display_name text NOT NULL,
    revision bigint DEFAULT 1 NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: person_aliases; Type: TABLE; Schema: identity; Owner: -
--

CREATE TABLE identity.person_aliases (
    person_id text NOT NULL,
    alias text NOT NULL
);


--
-- Name: principals; Type: TABLE; Schema: identity; Owner: -
--

CREATE TABLE identity.principals (
    principal_id text NOT NULL,
    username text NOT NULL,
    display_name text NOT NULL,
    password_hash text,
    person_id text,
    permissions text[] DEFAULT '{}'::text[] NOT NULL,
    enabled boolean DEFAULT true NOT NULL,
    access_epoch bigint DEFAULT 1 NOT NULL,
    auth_epoch bigint DEFAULT 1 NOT NULL,
    revision bigint DEFAULT 1 NOT NULL
);


--
-- Name: scope_grants; Type: TABLE; Schema: identity; Owner: -
--

CREATE TABLE identity.scope_grants (
    scope_id text NOT NULL,
    principal_id text NOT NULL,
    permissions text[] NOT NULL
);


--
-- Name: scope_inclusions; Type: TABLE; Schema: identity; Owner: -
--

CREATE TABLE identity.scope_inclusions (
    publication_scope_id text NOT NULL,
    conversation_scope_id text NOT NULL
);


--
-- Name: scopes; Type: TABLE; Schema: identity; Owner: -
--

CREATE TABLE identity.scopes (
    scope_id text NOT NULL,
    actor_id text,
    person_id text,
    kind text NOT NULL,
    label text NOT NULL,
    access_epoch bigint DEFAULT 1 NOT NULL,
    intake_epoch bigint DEFAULT 1 NOT NULL,
    CONSTRAINT scopes_kind_check CHECK ((kind = ANY (ARRAY['private'::text, 'group'::text, 'project'::text, 'system'::text, 'public'::text, 'publication'::text])))
);


--
-- Name: sessions; Type: TABLE; Schema: identity; Owner: -
--

CREATE TABLE identity.sessions (
    token_hash text NOT NULL,
    principal_id text NOT NULL,
    csrf_token text NOT NULL,
    access_epoch bigint NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: source_scope_epochs; Type: VIEW; Schema: identity; Owner: -
--

CREATE VIEW identity.source_scope_epochs AS
 SELECT scope_id,
    intake_epoch
   FROM identity.scopes;


--
-- Name: activities; Type: TABLE; Schema: life; Owner: -
--

CREATE TABLE life.activities (
    id text NOT NULL,
    actor_id text NOT NULL,
    goal_id text,
    revision bigint DEFAULT 1 NOT NULL,
    title text NOT NULL,
    kind text NOT NULL,
    state text NOT NULL,
    next_due_at timestamp with time zone,
    protected_until timestamp with time zone,
    resume_condition text,
    checkpoint_step integer DEFAULT 0 NOT NULL,
    checkpoint_position double precision DEFAULT 0 NOT NULL,
    checkpoint_unit text DEFAULT 'step'::text NOT NULL,
    checkpoint_data jsonb DEFAULT '{}'::jsonb NOT NULL,
    waiting_reason text,
    wake_condition jsonb,
    action_id text,
    last_receipt_id text,
    last_result_revision bigint DEFAULT '-1'::integer NOT NULL,
    matter_id text,
    scope_ids text[] NOT NULL,
    source_refs jsonb DEFAULT '[]'::jsonb NOT NULL,
    context jsonb,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT activities_state_check CHECK ((state = ANY (ARRAY['planned'::text, 'running'::text, 'paused'::text, 'dormant'::text, 'completed'::text, 'cancelled'::text])))
);


--
-- Name: actor_state; Type: TABLE; Schema: life; Owner: -
--

CREATE TABLE life.actor_state (
    actor_id text NOT NULL,
    revision bigint DEFAULT 1 NOT NULL,
    timezone text DEFAULT 'Asia/Shanghai'::text NOT NULL,
    world_id text DEFAULT 'default'::text NOT NULL,
    room_id text DEFAULT 'room'::text NOT NULL,
    activity_id text,
    outfit_id text,
    room_controls jsonb DEFAULT '{"sheer": 0.5, "window": 0, "curtain": 0, "desk_light": 0, "desk_warmth": 0.8, "bedside_light": 0, "bedside_warmth": 1}'::jsonb NOT NULL,
    weather_location text,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: chapter_revisions; Type: TABLE; Schema: life; Owner: -
--

CREATE TABLE life.chapter_revisions (
    chapter_id text NOT NULL,
    revision bigint NOT NULL,
    content text NOT NULL,
    work_revision bigint NOT NULL,
    model_attempt_id text,
    recipe jsonb NOT NULL,
    disclosure_ref jsonb,
    publication_metadata jsonb,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: chapters; Type: TABLE; Schema: life; Owner: -
--

CREATE TABLE life.chapters (
    id text NOT NULL,
    work_id text NOT NULL,
    "position" integer NOT NULL,
    revision bigint DEFAULT 1 NOT NULL,
    title text NOT NULL,
    plan text DEFAULT ''::text NOT NULL,
    state text DEFAULT 'planned'::text NOT NULL,
    current_revision bigint DEFAULT 0 NOT NULL,
    published_revision bigint,
    generation_id text,
    generation_cancel_requested boolean DEFAULT false NOT NULL,
    generation_context jsonb,
    generation_recipe jsonb,
    reason text,
    review_state text,
    review_note text,
    generation_retry_count integer DEFAULT 0 NOT NULL,
    generation_retry_due_at timestamp with time zone
);


--
-- Name: commitments; Type: TABLE; Schema: life; Owner: -
--

CREATE TABLE life.commitments (
    id text NOT NULL,
    actor_id text NOT NULL,
    revision bigint DEFAULT 1 NOT NULL,
    title text NOT NULL,
    due_at timestamp with time zone,
    timezone text NOT NULL,
    state text NOT NULL,
    target_ref text,
    person_id text,
    matter_id text,
    capability_id text,
    arguments jsonb DEFAULT '{}'::jsonb NOT NULL,
    recurrence text,
    weekdays integer[] DEFAULT '{}'::integer[] NOT NULL,
    grace_seconds integer DEFAULT 3600 NOT NULL,
    missed_policy text DEFAULT 'skip'::text NOT NULL,
    wake_condition jsonb,
    scope_ids text[] NOT NULL,
    source_refs jsonb DEFAULT '[]'::jsonb NOT NULL,
    context jsonb NOT NULL,
    reason text,
    action_id text,
    frozen_action jsonb,
    last_receipt_id text,
    last_result_revision bigint DEFAULT '-1'::integer NOT NULL,
    last_expression_id text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT commitments_missed_policy_check CHECK ((missed_policy = ANY (ARRAY['skip'::text, 'once'::text, 'reschedule'::text]))),
    CONSTRAINT commitments_recurrence_check CHECK ((recurrence = ANY (ARRAY['daily'::text, 'weekly'::text])))
);


--
-- Name: contact_candidates; Type: TABLE; Schema: life; Owner: -
--

CREATE TABLE life.contact_candidates (
    id text NOT NULL,
    actor_id text NOT NULL,
    revision bigint DEFAULT 1 NOT NULL,
    target_ref text NOT NULL,
    person_id text,
    kind text NOT NULL,
    summary text NOT NULL,
    due_at timestamp with time zone NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    state text NOT NULL,
    explicit boolean DEFAULT false NOT NULL,
    delivery_kind text NOT NULL,
    action_id text,
    context jsonb NOT NULL,
    source_refs jsonb DEFAULT '[]'::jsonb NOT NULL,
    payload jsonb DEFAULT '{}'::jsonb NOT NULL,
    reason text,
    expression_id text,
    delivery_generation bigint DEFAULT 1 NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: contact_preferences; Type: TABLE; Schema: life; Owner: -
--

CREATE TABLE life.contact_preferences (
    actor_id text NOT NULL,
    person_id text DEFAULT ''::text NOT NULL,
    revision bigint DEFAULT 1 NOT NULL,
    timezone text DEFAULT 'Asia/Shanghai'::text NOT NULL,
    quiet_start text DEFAULT '23:00'::text NOT NULL,
    quiet_end text DEFAULT '08:00'::text NOT NULL,
    cooldown_seconds integer DEFAULT 1800 NOT NULL,
    daily_quota integer DEFAULT 3 NOT NULL,
    unanswered_limit integer DEFAULT 3 NOT NULL,
    responsiveness double precision DEFAULT 0.5 NOT NULL,
    enabled boolean DEFAULT true NOT NULL,
    availability text DEFAULT 'unknown'::text NOT NULL,
    available_until timestamp with time zone,
    availability_event_id text,
    availability_occurred_at timestamp with time zone,
    availability_source_refs jsonb DEFAULT '[]'::jsonb NOT NULL,
    scope_ids text[] NOT NULL,
    expiry_seconds integer DEFAULT 14400 NOT NULL,
    CONSTRAINT contact_preferences_expiry_seconds_check CHECK (((expiry_seconds >= 60) AND (expiry_seconds <= 2592000)))
);


--
-- Name: contact_replies; Type: TABLE; Schema: life; Owner: -
--

CREATE TABLE life.contact_replies (
    input_id text NOT NULL
);


--
-- Name: contacts; Type: TABLE; Schema: life; Owner: -
--

CREATE TABLE life.contacts (
    expression_id text NOT NULL,
    actor_id text NOT NULL,
    person_id text,
    conversation_id text NOT NULL,
    candidate_id text,
    delivered_at timestamp with time zone,
    replied_at timestamp with time zone,
    signature jsonb NOT NULL,
    receipt jsonb NOT NULL
);


--
-- Name: day_plans; Type: TABLE; Schema: life; Owner: -
--

CREATE TABLE life.day_plans (
    actor_id text NOT NULL,
    day date NOT NULL,
    revision bigint DEFAULT 1 NOT NULL,
    timezone text NOT NULL,
    state text DEFAULT 'open'::text NOT NULL,
    reason text NOT NULL,
    context jsonb NOT NULL,
    scope_ids text[] NOT NULL,
    source_refs jsonb DEFAULT '[]'::jsonb NOT NULL,
    material_fingerprint text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: day_slots; Type: TABLE; Schema: life; Owner: -
--

CREATE TABLE life.day_slots (
    id text NOT NULL,
    actor_id text NOT NULL,
    day date NOT NULL,
    revision bigint DEFAULT 1 NOT NULL,
    kind text NOT NULL,
    state text DEFAULT 'planned'::text NOT NULL,
    starts_at timestamp with time zone NOT NULL,
    ends_at timestamp with time zone NOT NULL,
    activity_id text,
    reason text NOT NULL,
    scope_ids text[] NOT NULL,
    source_refs jsonb DEFAULT '[]'::jsonb NOT NULL,
    CONSTRAINT day_slots_check CHECK ((ends_at > starts_at)),
    CONSTRAINT day_slots_kind_check CHECK ((kind = ANY (ARRAY['activity'::text, 'free'::text])))
);


--
-- Name: diaries; Type: TABLE; Schema: life; Owner: -
--

CREATE TABLE life.diaries (
    id text NOT NULL,
    actor_id text NOT NULL,
    day date NOT NULL,
    revision bigint DEFAULT 1 NOT NULL,
    state text DEFAULT 'draft'::text NOT NULL,
    current_revision bigint DEFAULT 0 NOT NULL,
    published_revision bigint,
    generation_id text,
    generation_cancel_requested boolean DEFAULT false NOT NULL,
    context jsonb,
    reason text,
    material_key text,
    generation_recipe jsonb,
    last_reviewed_at timestamp with time zone,
    last_changed_at timestamp with time zone,
    episode_ids text[] NOT NULL,
    source_refs jsonb NOT NULL,
    scope_ids text[] NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    generation_retry_count integer DEFAULT 0 NOT NULL,
    generation_retry_due_at timestamp with time zone,
    scene_key text NOT NULL,
    review_state text,
    review_note text,
    review_content_revision bigint
);


--
-- Name: diary_days; Type: TABLE; Schema: life; Owner: -
--

CREATE TABLE life.diary_days (
    actor_id text NOT NULL,
    scope_key text NOT NULL,
    day date NOT NULL,
    timezone text NOT NULL,
    context jsonb NOT NULL,
    scope_ids text[] NOT NULL,
    material_revisions jsonb DEFAULT '{}'::jsonb NOT NULL,
    revision bigint DEFAULT 1 NOT NULL,
    reviewed_revision bigint DEFAULT 0 NOT NULL,
    generation_id text,
    generation_revision bigint,
    closed_at timestamp with time zone DEFAULT now() NOT NULL,
    last_reviewed_at timestamp with time zone,
    last_error text,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    blocked_revision bigint DEFAULT 0 NOT NULL
);


--
-- Name: diary_revisions; Type: TABLE; Schema: life; Owner: -
--

CREATE TABLE life.diary_revisions (
    diary_id text NOT NULL,
    revision bigint NOT NULL,
    content text NOT NULL,
    model_attempt_id text,
    recipe jsonb NOT NULL,
    disclosure_ref jsonb,
    publication_metadata jsonb,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: experience_statistics_pending; Type: TABLE; Schema: life; Owner: -
--

CREATE TABLE life.experience_statistics_pending (
    kind text NOT NULL,
    item_id text NOT NULL,
    content_revision bigint NOT NULL,
    CONSTRAINT experience_statistics_pending_kind_check CHECK ((kind = ANY (ARRAY['diary'::text, 'chapter'::text])))
);


--
-- Name: generation_recipes; Type: TABLE; Schema: life; Owner: -
--

CREATE TABLE life.generation_recipes (
    generation_id text NOT NULL,
    kind text NOT NULL,
    item_id text NOT NULL,
    previous_generation_id text,
    context jsonb NOT NULL,
    recipe jsonb,
    source_refs jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    recovery_lineage_id text,
    recovery_owner text,
    recovery_material_id text,
    recovery_material_digest text,
    recovery_dependency text,
    recovery_version text,
    recovery_request_digest text,
    recovery_route text,
    recovery_probe_token text,
    CONSTRAINT generation_recipes_kind_check CHECK ((kind = ANY (ARRAY['diary'::text, 'chapter'::text])))
);


--
-- Name: goal_milestones; Type: TABLE; Schema: life; Owner: -
--

CREATE TABLE life.goal_milestones (
    goal_id text NOT NULL,
    milestone_id text NOT NULL,
    producer text NOT NULL,
    evidence_id text NOT NULL,
    version bigint NOT NULL,
    source_refs jsonb NOT NULL,
    satisfied boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: goal_progress_receipts; Type: TABLE; Schema: life; Owner: -
--

CREATE TABLE life.goal_progress_receipts (
    goal_id text NOT NULL,
    action_id text NOT NULL,
    receipt_id text NOT NULL,
    activity_id text NOT NULL,
    result_revision bigint NOT NULL,
    satisfied boolean NOT NULL,
    source_refs jsonb NOT NULL,
    milestone_key text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: goal_revisions; Type: TABLE; Schema: life; Owner: -
--

CREATE TABLE life.goal_revisions (
    goal_id text NOT NULL,
    revision bigint NOT NULL,
    reason text NOT NULL,
    snapshot jsonb NOT NULL,
    source_refs jsonb DEFAULT '[]'::jsonb NOT NULL,
    generation_key text,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: goals; Type: TABLE; Schema: life; Owner: -
--

CREATE TABLE life.goals (
    id text NOT NULL,
    actor_id text NOT NULL,
    revision bigint DEFAULT 1 NOT NULL,
    title text NOT NULL,
    motivation text NOT NULL,
    state text NOT NULL,
    priority double precision DEFAULT 0.5 NOT NULL,
    progress double precision DEFAULT 0 NOT NULL,
    matter_id text,
    scope_ids text[] NOT NULL,
    source_refs jsonb DEFAULT '[]'::jsonb NOT NULL,
    intention_source_refs jsonb DEFAULT '[]'::jsonb NOT NULL,
    due_at timestamp with time zone,
    criteria jsonb DEFAULT '{}'::jsonb NOT NULL,
    origin text DEFAULT 'authored'::text NOT NULL,
    reason text DEFAULT ''::text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: outfits; Type: TABLE; Schema: life; Owner: -
--

CREATE TABLE life.outfits (
    id text NOT NULL,
    actor_id text NOT NULL,
    revision bigint DEFAULT 1 NOT NULL,
    description text NOT NULL,
    appearance text NOT NULL,
    activity_kinds text[] DEFAULT '{}'::text[] NOT NULL,
    reference jsonb,
    source_refs jsonb DEFAULT '[]'::jsonb NOT NULL,
    scope_ids text[] NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: plan_generations; Type: TABLE; Schema: life; Owner: -
--

CREATE TABLE life.plan_generations (
    generation_key text NOT NULL,
    actor_id text NOT NULL,
    day date NOT NULL,
    material_fingerprint text NOT NULL,
    base_revision bigint NOT NULL,
    context jsonb NOT NULL,
    state text DEFAULT 'pending'::text NOT NULL,
    reason text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    reviewed_at timestamp with time zone,
    previous_generation_key text,
    application_results jsonb,
    recovery_lineage_id text,
    recovery_owner text,
    recovery_version text,
    recovery_probe_token text,
    recovery_attempt_number integer,
    recovery_state text,
    recovery_dependency text,
    recovery_current_dependency text
);


--
-- Name: plan_watermarks; Type: TABLE; Schema: life; Owner: -
--

CREATE TABLE life.plan_watermarks (
    actor_id text NOT NULL,
    revision bigint DEFAULT 1 NOT NULL,
    change_watermark text DEFAULT ''::text NOT NULL,
    planned_watermark text DEFAULT ''::text NOT NULL,
    next_due_at timestamp with time zone,
    last_planned_at timestamp with time zone,
    reason text,
    generation_material text,
    generation_key text,
    generation_consumed boolean DEFAULT false NOT NULL,
    decision_fingerprint text,
    change jsonb DEFAULT '{}'::jsonb NOT NULL,
    last_error text,
    failed_generation_key text,
    repair_count integer DEFAULT 0 NOT NULL,
    repair_due_at timestamp with time zone,
    review_after timestamp with time zone,
    computation_retry_count integer DEFAULT 0 NOT NULL
);


--
-- Name: readings; Type: TABLE; Schema: life; Owner: -
--

CREATE TABLE life.readings (
    id text NOT NULL,
    actor_id text NOT NULL,
    revision bigint DEFAULT 1 NOT NULL,
    source_ref jsonb NOT NULL,
    requested jsonb NOT NULL,
    covered jsonb DEFAULT '[]'::jsonb NOT NULL,
    next_position jsonb,
    state text NOT NULL,
    scope_ids text[] NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: routines; Type: TABLE; Schema: life; Owner: -
--

CREATE TABLE life.routines (
    actor_id text NOT NULL,
    revision bigint DEFAULT 1 NOT NULL,
    enabled boolean DEFAULT true NOT NULL,
    timezone text DEFAULT 'Asia/Shanghai'::text NOT NULL,
    wake_at text DEFAULT '08:00'::text NOT NULL,
    sleep_at text DEFAULT '23:00'::text NOT NULL,
    idle_seconds integer DEFAULT 5400 NOT NULL,
    context jsonb NOT NULL,
    next_due_at timestamp with time zone NOT NULL,
    last_tick_at timestamp with time zone
);


--
-- Name: works; Type: TABLE; Schema: life; Owner: -
--

CREATE TABLE life.works (
    id text NOT NULL,
    actor_id text NOT NULL,
    revision bigint DEFAULT 1 NOT NULL,
    title text NOT NULL,
    synopsis text DEFAULT ''::text NOT NULL,
    outline text DEFAULT ''::text NOT NULL,
    characters text DEFAULT ''::text NOT NULL,
    scope_ids text[] NOT NULL,
    source_refs jsonb DEFAULT '[]'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: claim_revisions; Type: TABLE; Schema: memory; Owner: -
--

CREATE TABLE memory.claim_revisions (
    claim_id text NOT NULL,
    revision bigint NOT NULL,
    speaker_id text,
    subject_id text NOT NULL,
    predicate text NOT NULL,
    value jsonb NOT NULL,
    assertion text NOT NULL,
    polarity boolean NOT NULL,
    conditions jsonb NOT NULL,
    valid_from timestamp with time zone,
    valid_until timestamp with time zone,
    observed_at timestamp with time zone NOT NULL,
    state text NOT NULL,
    source_refs jsonb NOT NULL,
    support_groups jsonb NOT NULL,
    scope_groups jsonb NOT NULL,
    scope_ids text[] NOT NULL,
    matter_id text,
    supersedes text[] DEFAULT '{}'::text[] NOT NULL,
    contradictions text[] DEFAULT '{}'::text[] NOT NULL,
    evidence_units text[] NOT NULL,
    content text NOT NULL,
    lexical text NOT NULL,
    search_vector tsvector GENERATED ALWAYS AS (to_tsvector('simple'::regconfig, lexical)) STORED,
    embedding public.vector(1024),
    embedding_profile text,
    recorded_at timestamp with time zone,
    change_kind text DEFAULT 'create'::text NOT NULL,
    memory_kind text DEFAULT 'fact'::text NOT NULL,
    current_status text DEFAULT 'current'::text NOT NULL,
    semantic_fingerprint text,
    derivation_refs jsonb DEFAULT '[]'::jsonb NOT NULL,
    counterexample_refs jsonb DEFAULT '[]'::jsonb NOT NULL,
    change_reason text DEFAULT ''::text NOT NULL,
    CONSTRAINT claim_revisions_change_kind_check CHECK ((change_kind = ANY (ARRAY['create'::text, 'reinforce'::text, 'correct'::text, 'transition'::text, 'retract'::text]))),
    CONSTRAINT claim_revisions_current_status_check CHECK ((current_status = ANY (ARRAY['current'::text, 'history_only'::text, 'retracted'::text]))),
    CONSTRAINT claim_revisions_memory_kind_check CHECK ((memory_kind = ANY (ARRAY['fact'::text, 'hypothesis'::text])))
);


--
-- Name: claims; Type: TABLE; Schema: memory; Owner: -
--

CREATE TABLE memory.claims (
    claim_id text NOT NULL,
    current_revision bigint NOT NULL,
    actor_id text NOT NULL,
    principal_id text NOT NULL
);


--
-- Name: consolidation_attempts; Type: TABLE; Schema: memory; Owner: -
--

CREATE TABLE memory.consolidation_attempts (
    batch_id text NOT NULL,
    number integer NOT NULL,
    step_id text NOT NULL,
    model_attempt_id text NOT NULL,
    previous_step_id text,
    kind text NOT NULL,
    due_at timestamp with time zone NOT NULL,
    status text DEFAULT 'prepared'::text NOT NULL,
    error_code text,
    first_failure jsonb,
    rejected_error text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    recovery_lineage_id text,
    recovery_policy_state text,
    CONSTRAINT consolidation_attempts_kind_check CHECK ((kind = ANY (ARRAY['original'::text, 'context'::text, 'repair'::text, 'retry'::text]))),
    CONSTRAINT consolidation_attempts_number_check CHECK ((number > 0))
);


--
-- Name: consolidation_batches; Type: TABLE; Schema: memory; Owner: -
--

CREATE TABLE memory.consolidation_batches (
    batch_id text NOT NULL,
    partition_id text NOT NULL,
    schema_version text NOT NULL,
    status text NOT NULL,
    manifest jsonb NOT NULL,
    read_set jsonb DEFAULT '[]'::jsonb NOT NULL,
    dependency_refs jsonb DEFAULT '[]'::jsonb NOT NULL,
    context_fingerprint text NOT NULL,
    step_ids text[] DEFAULT '{}'::text[] NOT NULL,
    proposal_digest text,
    commit_refs jsonb DEFAULT '{}'::jsonb NOT NULL,
    started_at timestamp with time zone DEFAULT now() NOT NULL,
    committed_at timestamp with time zone,
    error_code text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    retry_due_at timestamp with time zone,
    retry_requested boolean DEFAULT false NOT NULL,
    selected_step_id text,
    observation_revision bigint DEFAULT 0 NOT NULL,
    CONSTRAINT consolidation_batches_status_check CHECK ((status = ANY (ARRAY['prepared'::text, 'waiting_model'::text, 'ready'::text, 'quarantined'::text, 'applied'::text, 'stale'::text, 'failed'::text, 'revoked'::text, 'superseded'::text])))
);


--
-- Name: consolidation_compilations; Type: TABLE; Schema: memory; Owner: -
--

CREATE TABLE memory.consolidation_compilations (
    batch_id text NOT NULL,
    attempt integer NOT NULL,
    step_id text,
    compiler_version text NOT NULL,
    schema_version text NOT NULL,
    proposal jsonb NOT NULL,
    alias_map jsonb NOT NULL,
    source_snapshot jsonb NOT NULL,
    frozen_input_digest text NOT NULL,
    proposal_digest text NOT NULL,
    selected boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: consolidation_reorganization_parts; Type: TABLE; Schema: memory; Owner: -
--

CREATE TABLE memory.consolidation_reorganization_parts (
    parent_batch_id text NOT NULL,
    root_batch_id text NOT NULL,
    ordinal integer NOT NULL,
    episodes jsonb NOT NULL,
    child_batch_id text,
    state text DEFAULT 'pending'::text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT consolidation_reorganization_parts_state_check CHECK ((state = ANY (ARRAY['pending'::text, 'active'::text, 'applied'::text, 'superseded'::text, 'failed'::text])))
);


--
-- Name: consolidation_reorganizations; Type: TABLE; Schema: memory; Owner: -
--

CREATE TABLE memory.consolidation_reorganizations (
    root_batch_id text NOT NULL,
    partition_id text NOT NULL,
    context_remaining integer NOT NULL,
    repair_remaining integer NOT NULL,
    retry_remaining integer NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT consolidation_reorganizations_context_remaining_check CHECK ((context_remaining >= 0)),
    CONSTRAINT consolidation_reorganizations_repair_remaining_check CHECK ((repair_remaining >= 0)),
    CONSTRAINT consolidation_reorganizations_retry_remaining_check CHECK ((retry_remaining >= 0))
);


--
-- Name: consolidation_state; Type: TABLE; Schema: memory; Owner: -
--

CREATE TABLE memory.consolidation_state (
    partition_id text NOT NULL,
    actor_id text NOT NULL,
    principal_id text NOT NULL,
    conversation_id text,
    scope_key text NOT NULL,
    authority_bound jsonb NOT NULL,
    dirty_generation bigint DEFAULT 0 NOT NULL,
    first_pending_at timestamp with time zone,
    last_activity_at timestamp with time zone,
    wake_due_at timestamp with time zone,
    active_batch_id text,
    lease_owner text,
    lease_until timestamp with time zone,
    last_error text,
    budget_failure jsonb,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: episode_original_terms; Type: TABLE; Schema: memory; Owner: -
--

CREATE TABLE memory.episode_original_terms (
    episode_id text NOT NULL,
    revision bigint NOT NULL,
    source_refs jsonb NOT NULL,
    search_vector tsvector NOT NULL
);


--
-- Name: episode_revisions; Type: TABLE; Schema: memory; Owner: -
--

CREATE TABLE memory.episode_revisions (
    episode_id text NOT NULL,
    revision bigint NOT NULL,
    event_id text NOT NULL,
    actor_id text NOT NULL,
    person_id text,
    conversation_id text,
    origin_kind text NOT NULL,
    occurred_at timestamp with time zone NOT NULL,
    recorded_at timestamp with time zone,
    summary text NOT NULL,
    source_refs jsonb NOT NULL,
    supplement_refs jsonb DEFAULT '[]'::jsonb NOT NULL,
    scope_ids text[] NOT NULL,
    actual_result jsonb,
    coverage jsonb NOT NULL,
    matter_id text,
    speaker_id text,
    semantic_fingerprint text,
    active boolean DEFAULT true NOT NULL
);


--
-- Name: episode_supplements; Type: TABLE; Schema: memory; Owner: -
--

CREATE TABLE memory.episode_supplements (
    episode_id text NOT NULL,
    event_id text NOT NULL,
    event_revision bigint NOT NULL,
    part_index integer NOT NULL,
    supplement_revision bigint NOT NULL,
    original_ref jsonb NOT NULL,
    supplement_ref jsonb NOT NULL,
    episode_revision bigint NOT NULL,
    active boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT episode_supplements_part_index_check CHECK ((part_index >= 0)),
    CONSTRAINT episode_supplements_supplement_revision_check CHECK ((supplement_revision > 0))
);


--
-- Name: revision_sequence; Type: SEQUENCE; Schema: memory; Owner: -
--

CREATE SEQUENCE memory.revision_sequence
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: episodes; Type: TABLE; Schema: memory; Owner: -
--

CREATE TABLE memory.episodes (
    episode_id text NOT NULL,
    event_id text NOT NULL,
    actor_id text NOT NULL,
    principal_id text NOT NULL,
    person_id text,
    conversation_id text,
    revision bigint DEFAULT 1 NOT NULL,
    sequence bigint DEFAULT nextval('memory.revision_sequence'::regclass) NOT NULL,
    origin_kind text NOT NULL,
    occurred_at timestamp with time zone NOT NULL,
    summary text NOT NULL,
    source_refs jsonb NOT NULL,
    scope_ids text[] NOT NULL,
    actual_result jsonb,
    coverage jsonb NOT NULL,
    matter_id text,
    action_id text,
    receipt_id text,
    content_hash text NOT NULL,
    extraction_state text DEFAULT 'pending'::text NOT NULL,
    extraction_started_at timestamp with time zone,
    extraction_cursor jsonb DEFAULT '{}'::jsonb NOT NULL,
    extraction_sources jsonb DEFAULT '[]'::jsonb NOT NULL,
    extraction_missing text[] DEFAULT '{}'::text[] NOT NULL,
    active boolean DEFAULT true NOT NULL,
    recorded_at timestamp with time zone,
    consolidation_partition_id text,
    speaker_id text,
    lexical text DEFAULT ''::text NOT NULL,
    search_vector tsvector GENERATED ALWAYS AS (to_tsvector('simple'::regconfig, lexical)) STORED,
    embedding public.vector(1024),
    embedding_profile text,
    semantic_fingerprint text,
    event_revision bigint NOT NULL,
    event_content_hash text NOT NULL,
    supplement_refs jsonb DEFAULT '[]'::jsonb NOT NULL,
    consolidation_authority jsonb DEFAULT '{}'::jsonb NOT NULL
);


--
-- Name: group_term_uses; Type: TABLE; Schema: memory; Owner: -
--

CREATE TABLE memory.group_term_uses (
    actor_id text NOT NULL,
    conversation_id text NOT NULL,
    event_id text NOT NULL,
    claim_id text DEFAULT ''::text NOT NULL,
    revision bigint DEFAULT 0 NOT NULL,
    outcome text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT group_term_uses_outcome_check CHECK ((outcome = ANY (ARRAY['no_match'::text, 'matched'::text, 'explained'::text, 'used'::text])))
);


--
-- Name: index_attempts; Type: TABLE; Schema: memory; Owner: -
--

CREATE TABLE memory.index_attempts (
    index_id text NOT NULL,
    number integer NOT NULL,
    intent_id text NOT NULL,
    step_id text NOT NULL,
    model_attempt_id text NOT NULL,
    status text DEFAULT 'prepared'::text NOT NULL,
    error_code text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    recovery_reserved boolean DEFAULT false NOT NULL,
    recovery_probe_token text,
    event_revision bigint DEFAULT 1 NOT NULL,
    CONSTRAINT index_attempts_number_check CHECK ((number > 0))
);


--
-- Name: index_jobs; Type: TABLE; Schema: memory; Owner: -
--

CREATE TABLE memory.index_jobs (
    index_id text NOT NULL,
    kind text NOT NULL,
    object_id text NOT NULL,
    revision bigint NOT NULL,
    profile_key text NOT NULL,
    profile jsonb,
    state text DEFAULT 'pending'::text NOT NULL,
    attempt_count integer DEFAULT 0 NOT NULL,
    next_due timestamp with time zone,
    last_error text,
    lease_owner text,
    lease_until timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    recovery_lineage_id text,
    recovery_dependency text,
    generation text NOT NULL,
    content_hash text NOT NULL,
    CONSTRAINT index_jobs_attempt_count_check CHECK ((attempt_count >= 0)),
    CONSTRAINT index_jobs_kind_check CHECK ((kind = ANY (ARRAY['claim'::text, 'hypothesis'::text, 'episode'::text, 'matter'::text, 'reflection'::text]))),
    CONSTRAINT index_jobs_state_check CHECK ((state = ANY (ARRAY['pending'::text, 'running'::text, 'observing'::text, 'retry_wait'::text, 'succeeded'::text, 'obsolete'::text, 'revoked'::text, 'cancelled'::text, 'failed'::text])))
);


--
-- Name: index_scan; Type: TABLE; Schema: memory; Owner: -
--

CREATE TABLE memory.index_scan (
    kind text NOT NULL,
    after_id text DEFAULT ''::text NOT NULL,
    after_revision bigint DEFAULT 0 NOT NULL,
    profile_key text,
    generation text
);


--
-- Name: index_stats_cursor; Type: TABLE; Schema: memory; Owner: -
--

CREATE TABLE memory.index_stats_cursor (
    singleton boolean DEFAULT true NOT NULL,
    generation text,
    after_index_id text DEFAULT ''::text NOT NULL,
    after_number integer DEFAULT 0 NOT NULL,
    CONSTRAINT index_stats_cursor_singleton_check CHECK (singleton)
);


--
-- Name: learning_index_refs; Type: TABLE; Schema: memory; Owner: -
--

CREATE TABLE memory.learning_index_refs (
    actor_id text NOT NULL,
    principal_id text NOT NULL,
    rule_id text NOT NULL,
    revision bigint NOT NULL,
    semantic_revision bigint NOT NULL,
    kind text NOT NULL,
    source_refs jsonb NOT NULL,
    scope_ids text[] NOT NULL,
    search_vector tsvector NOT NULL,
    recorded_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT learning_index_refs_revision_check CHECK ((revision > 0)),
    CONSTRAINT learning_index_refs_semantic_revision_check CHECK ((semantic_revision > 0))
);


--
-- Name: matter_claims; Type: TABLE; Schema: memory; Owner: -
--

CREATE TABLE memory.matter_claims (
    matter_id text NOT NULL,
    claim_id text NOT NULL
);


--
-- Name: matter_revisions; Type: TABLE; Schema: memory; Owner: -
--

CREATE TABLE memory.matter_revisions (
    matter_id text NOT NULL,
    revision bigint NOT NULL,
    actor_id text NOT NULL,
    principal_id text NOT NULL,
    subject_id text,
    title text NOT NULL,
    state text NOT NULL,
    due_at timestamp with time zone,
    summary text NOT NULL,
    conditions jsonb NOT NULL,
    completion_basis jsonb,
    observed_at timestamp with time zone NOT NULL,
    recorded_at timestamp with time zone,
    change_kind text NOT NULL,
    change_reason text DEFAULT ''::text NOT NULL,
    source_refs jsonb NOT NULL,
    support_groups jsonb NOT NULL,
    scope_groups jsonb NOT NULL,
    derivation_refs jsonb NOT NULL,
    scope_ids text[] NOT NULL,
    current_status text DEFAULT 'current'::text NOT NULL,
    interpretation boolean NOT NULL,
    active boolean DEFAULT true NOT NULL,
    semantic_fingerprint text
);


--
-- Name: matters; Type: TABLE; Schema: memory; Owner: -
--

CREATE TABLE memory.matters (
    matter_id text NOT NULL,
    actor_id text NOT NULL,
    principal_id text NOT NULL,
    subject_id text,
    title text NOT NULL,
    state text NOT NULL,
    due_at timestamp with time zone,
    summary text NOT NULL,
    conditions jsonb DEFAULT '{}'::jsonb NOT NULL,
    source_refs jsonb NOT NULL,
    scope_ids text[] NOT NULL,
    observed_at timestamp with time zone NOT NULL,
    revision bigint DEFAULT 1 NOT NULL,
    interpretation boolean DEFAULT false NOT NULL,
    recorded_at timestamp with time zone,
    change_kind text DEFAULT 'create'::text NOT NULL,
    change_reason text DEFAULT ''::text NOT NULL,
    completion_basis jsonb,
    support_groups jsonb DEFAULT '[]'::jsonb NOT NULL,
    scope_groups jsonb DEFAULT '[]'::jsonb NOT NULL,
    derivation_refs jsonb DEFAULT '[]'::jsonb NOT NULL,
    current_status text DEFAULT 'current'::text NOT NULL,
    active boolean DEFAULT true NOT NULL,
    lexical text DEFAULT ''::text NOT NULL,
    search_vector tsvector GENERATED ALWAYS AS (to_tsvector('simple'::regconfig, lexical)) STORED,
    embedding public.vector(1024),
    embedding_profile text,
    semantic_fingerprint text
);


--
-- Name: model_steps; Type: TABLE; Schema: memory; Owner: -
--

CREATE TABLE memory.model_steps (
    step_id text NOT NULL,
    request jsonb NOT NULL,
    state text DEFAULT 'prepared'::text NOT NULL,
    error_code text,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    request_timeout_seconds integer DEFAULT 120 NOT NULL,
    CONSTRAINT model_steps_request_timeout_seconds_check CHECK (((request_timeout_seconds >= 1) AND (request_timeout_seconds <= 900)))
);


--
-- Name: recovery_due_claims; Type: TABLE; Schema: memory; Owner: -
--

CREATE TABLE memory.recovery_due_claims (
    lineage_id text NOT NULL,
    claim_token text NOT NULL,
    current_version text NOT NULL,
    expires_at timestamp with time zone NOT NULL
);


--
-- Name: relationship_details; Type: TABLE; Schema: memory; Owner: -
--

CREATE TABLE memory.relationship_details (
    detail_id text NOT NULL,
    actor_id text NOT NULL,
    person_id text NOT NULL,
    kind text NOT NULL,
    content text NOT NULL,
    source_refs jsonb NOT NULL,
    scope_ids text[] NOT NULL,
    explicit boolean NOT NULL,
    active boolean DEFAULT true NOT NULL,
    revision bigint DEFAULT 1 NOT NULL,
    CONSTRAINT relationship_details_kind_check CHECK ((kind = ANY (ARRAY['boundary'::text, 'shared_experience'::text, 'preference'::text])))
);


--
-- Name: relationships; Type: TABLE; Schema: memory; Owner: -
--

CREATE TABLE memory.relationships (
    actor_id text NOT NULL,
    person_id text NOT NULL,
    relationship_type text DEFAULT 'acquaintance'::text NOT NULL,
    display_label text DEFAULT '认识的人'::text NOT NULL,
    summary text DEFAULT ''::text NOT NULL,
    frozen boolean DEFAULT false NOT NULL,
    freeze_revision bigint DEFAULT 1 NOT NULL,
    frozen_at timestamp with time zone,
    scope_ids text[] DEFAULT '{}'::text[] NOT NULL,
    source_refs jsonb DEFAULT '[]'::jsonb NOT NULL,
    revision bigint DEFAULT 1 NOT NULL
);


--
-- Name: search_vectors; Type: TABLE; Schema: memory; Owner: -
--

CREATE TABLE memory.search_vectors (
    generation text NOT NULL,
    profile_key text NOT NULL,
    kind text NOT NULL,
    object_id text NOT NULL,
    revision bigint NOT NULL,
    content_hash text NOT NULL,
    embedding public.vector(1024) NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT search_vectors_kind_check CHECK ((kind = ANY (ARRAY['claim'::text, 'hypothesis'::text, 'matter'::text, 'episode'::text]))),
    CONSTRAINT search_vectors_profile_key_check CHECK ((profile_key ~ '^[0-9a-f]{64}$'::text))
)
PARTITION BY LIST (profile_key);


--
-- Name: semantic_links; Type: TABLE; Schema: memory; Owner: -
--

CREATE TABLE memory.semantic_links (
    link_id text NOT NULL,
    actor_id text NOT NULL,
    principal_id text NOT NULL,
    from_kind text NOT NULL,
    from_id text NOT NULL,
    from_revision bigint NOT NULL,
    to_kind text NOT NULL,
    to_id text NOT NULL,
    to_revision bigint NOT NULL,
    relation_kind text NOT NULL,
    valid_from timestamp with time zone,
    valid_until timestamp with time zone,
    recorded_at timestamp with time zone,
    support_groups jsonb NOT NULL,
    scope_groups jsonb NOT NULL,
    derivation_refs jsonb DEFAULT '[]'::jsonb NOT NULL,
    source_refs jsonb NOT NULL,
    scope_ids text[] NOT NULL,
    state text DEFAULT 'active'::text NOT NULL,
    current_status text DEFAULT 'current'::text NOT NULL,
    CONSTRAINT semantic_links_check CHECK (((valid_from IS NULL) OR (valid_until IS NULL) OR (valid_until > valid_from))),
    CONSTRAINT semantic_links_current_status_check CHECK ((current_status = ANY (ARRAY['current'::text, 'history_only'::text]))),
    CONSTRAINT semantic_links_from_kind_check CHECK ((from_kind = ANY (ARRAY['claim'::text, 'hypothesis'::text, 'matter'::text, 'episode'::text]))),
    CONSTRAINT semantic_links_relation_kind_check CHECK ((relation_kind = ANY (ARRAY['about_matter'::text, 'same_event'::text, 'supersedes'::text, 'contradicts'::text, 'supports'::text, 'counterexample'::text]))),
    CONSTRAINT semantic_links_state_check CHECK ((state = ANY (ARRAY['active'::text, 'unsupported'::text]))),
    CONSTRAINT semantic_links_to_kind_check CHECK ((to_kind = ANY (ARRAY['claim'::text, 'hypothesis'::text, 'matter'::text, 'episode'::text])))
);


--
-- Name: statistics_outbox; Type: TABLE; Schema: memory; Owner: -
--

CREATE TABLE memory.statistics_outbox (
    batch_id text NOT NULL,
    revision bigint NOT NULL,
    event jsonb NOT NULL,
    published_revision bigint DEFAULT 0 NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: attempts; Type: TABLE; Schema: models; Owner: -
--

CREATE TABLE models.attempts (
    attempt_id text NOT NULL,
    intent_id text NOT NULL,
    principal_id text NOT NULL,
    actor_id text,
    conversation_id text,
    function text NOT NULL,
    service_class text NOT NULL,
    route_id text NOT NULL,
    route_revision bigint NOT NULL,
    provider_id text NOT NULL,
    model text NOT NULL,
    protocol text NOT NULL,
    quota_group text NOT NULL,
    request_digest text NOT NULL,
    source_refs jsonb DEFAULT '[]'::jsonb NOT NULL,
    context_fingerprint text NOT NULL,
    persona_revision bigint,
    status text NOT NULL,
    dispatch text NOT NULL,
    queued_at timestamp with time zone DEFAULT now() NOT NULL,
    started_at timestamp with time zone,
    finished_at timestamp with time zone,
    deadline_at timestamp with time zone,
    hold_until timestamp with time zone,
    estimated_tokens bigint,
    charged_tokens bigint,
    estimated_units jsonb DEFAULT '{}'::jsonb NOT NULL,
    charged_units jsonb DEFAULT '{}'::jsonb NOT NULL,
    billing_account text,
    usage jsonb,
    provider_handle text,
    first_output_ms double precision,
    error_code text,
    result jsonb,
    output_observed boolean DEFAULT false NOT NULL,
    cancel_requested boolean DEFAULT false NOT NULL,
    broker_instance text,
    revision bigint DEFAULT 1 NOT NULL,
    execution_timeout_seconds double precision,
    business_deadline_at timestamp with time zone,
    max_queue_wait double precision,
    execution_start text DEFAULT 'admitted'::text NOT NULL,
    execution_started_at timestamp with time zone,
    provider_state text,
    provider_observed_at timestamp with time zone,
    http_timing jsonb,
    diagnostic jsonb,
    wire_digest text,
    route_selection jsonb,
    provider_revision bigint,
    estimated_input_tokens bigint,
    data_generation uuid,
    CONSTRAINT attempts_dispatch_check CHECK ((dispatch = ANY (ARRAY['not_started'::text, 'submitted'::text, 'unknown'::text]))),
    CONSTRAINT attempts_execution_start_check CHECK ((execution_start = ANY (ARRAY['admitted'::text, 'provider_running'::text]))),
    CONSTRAINT attempts_execution_timeout_seconds_check CHECK (((execution_timeout_seconds > (0)::double precision) AND (execution_timeout_seconds < 'Infinity'::double precision))),
    CONSTRAINT attempts_max_queue_wait_check CHECK (((max_queue_wait >= (0)::double precision) AND (max_queue_wait < 'Infinity'::double precision))),
    CONSTRAINT attempts_provider_state_check CHECK ((provider_state = ANY (ARRAY['queued'::text, 'running'::text, 'completed'::text]))),
    CONSTRAINT attempts_status_check CHECK ((status = ANY (ARRAY['queued'::text, 'running'::text, 'succeeded'::text, 'failed'::text, 'cancelled'::text, 'unknown'::text])))
);


--
-- Name: bindings; Type: TABLE; Schema: models; Owner: -
--

CREATE TABLE models.bindings (
    function text NOT NULL,
    service_class text NOT NULL,
    actor_id text DEFAULT ''::text NOT NULL,
    route_id text NOT NULL,
    revision bigint DEFAULT 1 NOT NULL,
    fallback_routes text[] DEFAULT '{}'::text[] NOT NULL,
    CONSTRAINT bindings_service_class_check CHECK ((service_class = ANY (ARRAY['interactive'::text, 'due'::text, 'elastic'::text])))
);


--
-- Name: cancellations; Type: TABLE; Schema: models; Owner: -
--

CREATE TABLE models.cancellations (
    attempt_id text NOT NULL,
    requested_at timestamp with time zone DEFAULT now() NOT NULL,
    reason text DEFAULT 'explicit_cancel'::text NOT NULL,
    CONSTRAINT cancellations_reason_check CHECK ((reason = ANY (ARRAY['explicit_cancel'::text, 'consumer_interrupted'::text])))
);


--
-- Name: configuration_reloads; Type: TABLE; Schema: models; Owner: -
--

CREATE TABLE models.configuration_reloads (
    event_id text NOT NULL,
    operation text NOT NULL,
    revision bigint NOT NULL,
    state text NOT NULL,
    error_code text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    applied_at timestamp with time zone,
    CONSTRAINT configuration_reloads_state_check CHECK ((state = ANY (ARRAY['pending'::text, 'applied'::text])))
);


--
-- Name: embedding_profile; Type: TABLE; Schema: models; Owner: -
--

CREATE TABLE models.embedding_profile (
    singleton boolean DEFAULT true NOT NULL,
    route_id text NOT NULL,
    model_version text NOT NULL,
    dimensions integer DEFAULT 1024 NOT NULL,
    normalization text NOT NULL,
    query_instruction text NOT NULL,
    route_fingerprint text NOT NULL,
    revision bigint DEFAULT 1 NOT NULL,
    CONSTRAINT embedding_profile_dimensions_check CHECK ((dimensions = 1024)),
    CONSTRAINT embedding_profile_singleton_check CHECK (singleton)
);


--
-- Name: providers; Type: TABLE; Schema: models; Owner: -
--

CREATE TABLE models.providers (
    provider_id text NOT NULL,
    name text NOT NULL,
    base_url text NOT NULL,
    credential_ref text NOT NULL,
    protocol text NOT NULL,
    enabled boolean DEFAULT true NOT NULL,
    revision bigint DEFAULT 1 NOT NULL
);


--
-- Name: quota_cooldowns; Type: TABLE; Schema: models; Owner: -
--

CREATE TABLE models.quota_cooldowns (
    quota_group text NOT NULL,
    blocked_until timestamp with time zone NOT NULL,
    reason text NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: quota_groups; Type: TABLE; Schema: models; Owner: -
--

CREATE TABLE models.quota_groups (
    quota_group text NOT NULL,
    concurrency integer NOT NULL,
    interactive_reserve integer NOT NULL,
    background_max integer NOT NULL,
    rpm integer,
    tpm integer,
    max_queue integer DEFAULT 128 NOT NULL,
    max_wait_seconds double precision,
    unknown_hold_seconds double precision DEFAULT 300 NOT NULL,
    unit_limits jsonb DEFAULT '{}'::jsonb NOT NULL,
    interactive_rpm_reserve integer DEFAULT 0 NOT NULL,
    background_rpm_max integer,
    interactive_unit_reserve jsonb DEFAULT '{}'::jsonb NOT NULL,
    background_unit_max jsonb DEFAULT '{}'::jsonb NOT NULL,
    revision bigint DEFAULT 1 NOT NULL,
    cloud_concurrency integer,
    CONSTRAINT quota_groups_background_max_check CHECK ((background_max >= 0)),
    CONSTRAINT quota_groups_background_rpm_max_check CHECK ((background_rpm_max >= 0)),
    CONSTRAINT quota_groups_check CHECK ((interactive_reserve <= concurrency)),
    CONSTRAINT quota_groups_check1 CHECK ((background_max <= concurrency)),
    CONSTRAINT quota_groups_cloud_concurrency_check CHECK ((cloud_concurrency > 0)),
    CONSTRAINT quota_groups_concurrency_check CHECK ((concurrency > 0)),
    CONSTRAINT quota_groups_interactive_reserve_check CHECK ((interactive_reserve >= 0)),
    CONSTRAINT quota_groups_interactive_rpm_reserve_check CHECK ((interactive_rpm_reserve >= 0)),
    CONSTRAINT quota_groups_max_queue_check CHECK ((max_queue > 0)),
    CONSTRAINT quota_groups_max_wait_seconds_check CHECK ((max_wait_seconds > (0)::double precision)),
    CONSTRAINT quota_groups_rpm_check CHECK ((rpm > 0)),
    CONSTRAINT quota_groups_tpm_check CHECK ((tpm > 0)),
    CONSTRAINT quota_groups_unknown_hold_seconds_check CHECK ((unknown_hold_seconds > (0)::double precision))
);


--
-- Name: route_cooldowns; Type: TABLE; Schema: models; Owner: -
--

CREATE TABLE models.route_cooldowns (
    route_id text NOT NULL,
    blocked_until timestamp with time zone NOT NULL,
    reason text NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: routes; Type: TABLE; Schema: models; Owner: -
--

CREATE TABLE models.routes (
    route_id text NOT NULL,
    provider_id text NOT NULL,
    model text NOT NULL,
    quota_group text NOT NULL,
    verified_capabilities text[] DEFAULT '{}'::text[] NOT NULL,
    verification jsonb,
    parameters jsonb DEFAULT '{}'::jsonb NOT NULL,
    enabled boolean DEFAULT true NOT NULL,
    revision bigint DEFAULT 1 NOT NULL
);


--
-- Name: stream_events; Type: TABLE; Schema: models; Owner: -
--

CREATE TABLE models.stream_events (
    attempt_id text NOT NULL,
    sequence bigint NOT NULL,
    event jsonb NOT NULL
);


--
-- Name: stream_events_sequence_seq; Type: SEQUENCE; Schema: models; Owner: -
--

ALTER TABLE models.stream_events ALTER COLUMN sequence ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME models.stream_events_sequence_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: submissions; Type: TABLE; Schema: models; Owner: -
--

CREATE TABLE models.submissions (
    attempt_id text NOT NULL,
    request_digest text NOT NULL,
    request jsonb,
    streaming boolean NOT NULL,
    completion jsonb,
    completion_digest text,
    completion_enqueued_at timestamp with time zone,
    stream_truncated boolean DEFAULT false NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    completion_state text DEFAULT 'none'::text NOT NULL,
    completion_error_code text,
    CONSTRAINT submissions_completion_state_check CHECK ((completion_state = ANY (ARRAY['none'::text, 'pending'::text, 'enqueued'::text, 'expired'::text])))
);


--
-- Name: consumer_checkpoints; Type: TABLE; Schema: operations; Owner: -
--

CREATE TABLE operations.consumer_checkpoints (
    consumer text NOT NULL,
    checkpoint text,
    coverage jsonb DEFAULT '{}'::jsonb NOT NULL,
    freshness jsonb DEFAULT '{}'::jsonb NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: control_lineages; Type: TABLE; Schema: operations; Owner: -
--

CREATE TABLE operations.control_lineages (
    control_id text NOT NULL,
    data_generation text NOT NULL,
    material_digest text NOT NULL,
    attempts integer DEFAULT 0 NOT NULL,
    max_attempts integer NOT NULL,
    CONSTRAINT control_lineages_max_attempts_check CHECK ((max_attempts > 0))
);


--
-- Name: diagnostics; Type: TABLE; Schema: operations; Owner: -
--

CREATE TABLE operations.diagnostics (
    attempt_id text NOT NULL,
    incident_id text NOT NULL,
    incident_revision bigint NOT NULL,
    settings_revision bigint NOT NULL,
    route_id text NOT NULL,
    status text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    finished_at timestamp with time zone,
    last_observed_at timestamp with time zone,
    receipt jsonb,
    decision jsonb,
    error_code text
);


--
-- Name: incident_causes; Type: TABLE; Schema: operations; Owner: -
--

CREATE TABLE operations.incident_causes (
    incident_id text NOT NULL,
    episode bigint NOT NULL,
    root_incident_id text NOT NULL,
    root_episode bigint NOT NULL
);


--
-- Name: incident_events; Type: TABLE; Schema: operations; Owner: -
--

CREATE TABLE operations.incident_events (
    event_id text NOT NULL,
    incident_id text NOT NULL,
    observed_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: incident_targets; Type: TABLE; Schema: operations; Owner: -
--

CREATE TABLE operations.incident_targets (
    incident_id text NOT NULL,
    episode bigint NOT NULL,
    target_id text NOT NULL,
    target jsonb NOT NULL,
    state text DEFAULT 'open'::text NOT NULL
);


--
-- Name: incidents; Type: TABLE; Schema: operations; Owner: -
--

CREATE TABLE operations.incidents (
    incident_id text NOT NULL,
    fingerprint text NOT NULL,
    dependency text NOT NULL,
    error_family text NOT NULL,
    version text NOT NULL,
    severity text NOT NULL,
    state text DEFAULT 'open'::text NOT NULL,
    revision bigint DEFAULT 1 NOT NULL,
    occurrences bigint DEFAULT 0 NOT NULL,
    episode bigint DEFAULT 1 NOT NULL,
    unscoped_occurrences bigint DEFAULT 0 NOT NULL,
    uncorrelated_occurrences bigint DEFAULT 0 NOT NULL,
    first_seen timestamp with time zone DEFAULT now() NOT NULL,
    last_seen timestamp with time zone DEFAULT now() NOT NULL,
    resolved_at timestamp with time zone,
    summary jsonb DEFAULT '{}'::jsonb NOT NULL,
    affected jsonb DEFAULT '[]'::jsonb NOT NULL,
    diagnosis_count integer DEFAULT 0 NOT NULL,
    diagnostic_state text DEFAULT 'pending'::text NOT NULL,
    recovery_action text,
    recovery_lineage text,
    last_observation jsonb
);


--
-- Name: notification_outbox; Type: TABLE; Schema: operations; Owner: -
--

CREATE TABLE operations.notification_outbox (
    notification_id text NOT NULL,
    incident_id text NOT NULL,
    kind text NOT NULL,
    episode bigint NOT NULL,
    incident_revision bigint NOT NULL,
    channel text NOT NULL,
    target text NOT NULL,
    payload jsonb NOT NULL,
    status text DEFAULT 'pending'::text NOT NULL,
    attempts integer DEFAULT 0 NOT NULL,
    due_at timestamp with time zone DEFAULT now() NOT NULL,
    owner_token text,
    lease_until timestamp with time zone,
    inflight_state text,
    receipt jsonb,
    last_error text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: recoveries; Type: TABLE; Schema: operations; Owner: -
--

CREATE TABLE operations.recoveries (
    attempt_id text NOT NULL,
    incident_id text NOT NULL,
    lineage_id text NOT NULL,
    control_id text,
    data_generation text,
    policy_mode text DEFAULT 'reserved'::text NOT NULL,
    incident_revision bigint NOT NULL,
    action text NOT NULL,
    target jsonb NOT NULL,
    target_id text,
    inspection_attempts integer DEFAULT 0 NOT NULL,
    attempt_number integer,
    state text NOT NULL,
    claim_token text,
    owner_token text,
    lease_until timestamp with time zone,
    last_observed_at timestamp with time zone,
    due_at timestamp with time zone DEFAULT now() NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    finished_at timestamp with time zone,
    result jsonb,
    observation jsonb
);


--
-- Name: settings; Type: TABLE; Schema: operations; Owner: -
--

CREATE TABLE operations.settings (
    singleton boolean DEFAULT true NOT NULL,
    revision bigint NOT NULL,
    value jsonb NOT NULL,
    author_context jsonb NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT settings_revision_check CHECK ((revision > 0)),
    CONSTRAINT settings_singleton_check CHECK (singleton)
);


--
-- Name: statistic_buckets; Type: TABLE; Schema: operations; Owner: -
--

CREATE TABLE operations.statistic_buckets (
    grain text NOT NULL,
    bucket timestamp with time zone NOT NULL,
    family text NOT NULL,
    module text NOT NULL,
    actor_id text DEFAULT ''::text NOT NULL,
    route_id text DEFAULT ''::text NOT NULL,
    state text DEFAULT ''::text NOT NULL,
    metric text NOT NULL,
    total double precision DEFAULT 0 NOT NULL,
    samples bigint DEFAULT 0 NOT NULL,
    unknown bigint DEFAULT 0 NOT NULL,
    CONSTRAINT statistic_buckets_grain_check CHECK ((grain = ANY (ARRAY['minute'::text, 'hour'::text])))
);


--
-- Name: statistic_events; Type: TABLE; Schema: operations; Owner: -
--

CREATE TABLE operations.statistic_events (
    family text NOT NULL,
    event_id text NOT NULL,
    revision bigint NOT NULL,
    digest text NOT NULL,
    occurred_at timestamp with time zone NOT NULL,
    module text NOT NULL,
    actor_id text DEFAULT ''::text NOT NULL,
    route_id text DEFAULT ''::text NOT NULL,
    state text DEFAULT ''::text NOT NULL,
    metrics jsonb NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: statistic_producers; Type: TABLE; Schema: operations; Owner: -
--

CREATE TABLE operations.statistic_producers (
    producer text NOT NULL,
    watermark jsonb,
    last_seen timestamp with time zone DEFAULT now() NOT NULL,
    complete boolean DEFAULT false NOT NULL,
    detail jsonb
);


--
-- Name: procrastinate_events; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.procrastinate_events (
    id bigint NOT NULL,
    job_id bigint NOT NULL,
    type public.procrastinate_job_event_type,
    at timestamp with time zone DEFAULT now()
);


--
-- Name: procrastinate_events_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.procrastinate_events_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: procrastinate_events_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.procrastinate_events_id_seq OWNED BY public.procrastinate_events.id;


--
-- Name: procrastinate_jobs_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.procrastinate_jobs_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: procrastinate_jobs_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.procrastinate_jobs_id_seq OWNED BY public.procrastinate_jobs.id;


--
-- Name: procrastinate_periodic_defers; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.procrastinate_periodic_defers (
    id bigint NOT NULL,
    task_name character varying(128) NOT NULL,
    defer_timestamp bigint,
    job_id bigint,
    periodic_id character varying(128) DEFAULT ''::character varying NOT NULL
);


--
-- Name: procrastinate_periodic_defers_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.procrastinate_periodic_defers_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: procrastinate_periodic_defers_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.procrastinate_periodic_defers_id_seq OWNED BY public.procrastinate_periodic_defers.id;


--
-- Name: procrastinate_workers; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.procrastinate_workers (
    id bigint NOT NULL,
    last_heartbeat timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: procrastinate_workers_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.procrastinate_workers ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.procrastinate_workers_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: access_settings; Type: TABLE; Schema: runtime; Owner: -
--

CREATE TABLE runtime.access_settings (
    singleton boolean DEFAULT true NOT NULL,
    revision bigint NOT NULL,
    value jsonb NOT NULL,
    CONSTRAINT access_settings_revision_check CHECK ((revision > 0)),
    CONSTRAINT access_settings_singleton_check CHECK (singleton)
);


--
-- Name: commands; Type: TABLE; Schema: runtime; Owner: -
--

CREATE TABLE runtime.commands (
    principal_id text NOT NULL,
    operation text NOT NULL,
    client_request_id text NOT NULL,
    body_hash text NOT NULL,
    result jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: credentials; Type: TABLE; Schema: runtime; Owner: -
--

CREATE TABLE runtime.credentials (
    reference text NOT NULL,
    nonce bytea NOT NULL,
    ciphertext bytea NOT NULL,
    revision bigint DEFAULT 1 NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: installation_generation; Type: TABLE; Schema: runtime; Owner: -
--

CREATE TABLE runtime.installation_generation (
    singleton boolean DEFAULT true NOT NULL,
    id text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT installation_generation_singleton_check CHECK (singleton)
);


--
-- Name: job_keys; Type: TABLE; Schema: runtime; Owner: -
--

CREATE TABLE runtime.job_keys (
    key text NOT NULL,
    task text NOT NULL,
    job_id bigint,
    subject_id text,
    matter_id text,
    cancelled_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    payload_digest text,
    payload_version integer DEFAULT 1 NOT NULL,
    data_generation text,
    CONSTRAINT job_keys_payload_version_check CHECK ((payload_version > 0))
);


--
-- Name: processes; Type: TABLE; Schema: runtime; Owner: -
--

CREATE TABLE runtime.processes (
    instance_id text NOT NULL,
    role text NOT NULL,
    ready boolean NOT NULL,
    registration jsonb NOT NULL,
    heartbeat_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: projection_cursors; Type: TABLE; Schema: runtime; Owner: -
--

CREATE TABLE runtime.projection_cursors (
    consumer text NOT NULL,
    subject_id text NOT NULL,
    source_revision bigint DEFAULT 0 NOT NULL,
    cursor jsonb DEFAULT '{}'::jsonb NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: queue_service; Type: TABLE; Schema: runtime; Owner: -
--

CREATE TABLE runtime.queue_service (
    data_generation text NOT NULL,
    queue_name text NOT NULL,
    domain text NOT NULL,
    subject_id text NOT NULL,
    service_seconds double precision DEFAULT 0 NOT NULL,
    measured_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: queue_service_runs; Type: TABLE; Schema: runtime; Owner: -
--

CREATE TABLE runtime.queue_service_runs (
    job_id bigint NOT NULL,
    data_generation text NOT NULL,
    queue_name text NOT NULL,
    domain text NOT NULL,
    subject_id text NOT NULL,
    started_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: recompute_attempts; Type: TABLE; Schema: runtime; Owner: -
--

CREATE TABLE runtime.recompute_attempts (
    claim_token text NOT NULL,
    data_generation text NOT NULL,
    domain text NOT NULL,
    work_key text NOT NULL,
    revision bigint NOT NULL,
    started_at timestamp with time zone DEFAULT now() NOT NULL,
    finished_at timestamp with time zone,
    outcome text DEFAULT 'running'::text NOT NULL,
    error_code text
);


--
-- Name: recompute_changes; Type: TABLE; Schema: runtime; Owner: -
--

CREATE TABLE runtime.recompute_changes (
    data_generation text NOT NULL,
    domain text NOT NULL,
    work_key text NOT NULL,
    change_id text NOT NULL,
    revision bigint NOT NULL
);


--
-- Name: recompute_gap_history; Type: TABLE; Schema: runtime; Owner: -
--

CREATE TABLE runtime.recompute_gap_history (
    id bigint NOT NULL,
    data_generation text NOT NULL,
    domain text NOT NULL,
    work_key text NOT NULL,
    material_id text NOT NULL,
    revision bigint NOT NULL,
    action text NOT NULL,
    reason text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT recompute_gap_history_action_check CHECK ((action = ANY (ARRAY['isolated'::text, 'repaired'::text])))
);


--
-- Name: recompute_gap_history_id_seq; Type: SEQUENCE; Schema: runtime; Owner: -
--

ALTER TABLE runtime.recompute_gap_history ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME runtime.recompute_gap_history_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: recompute_gaps; Type: TABLE; Schema: runtime; Owner: -
--

CREATE TABLE runtime.recompute_gaps (
    data_generation text NOT NULL,
    domain text NOT NULL,
    work_key text NOT NULL,
    material_id text NOT NULL,
    revision bigint NOT NULL,
    reason text NOT NULL,
    opened_at timestamp with time zone DEFAULT now() NOT NULL,
    repaired_at timestamp with time zone
);


--
-- Name: recompute_work; Type: TABLE; Schema: runtime; Owner: -
--

CREATE TABLE runtime.recompute_work (
    domain text NOT NULL,
    work_key text NOT NULL,
    task text NOT NULL,
    payload jsonb NOT NULL,
    subject_id text,
    matter_id text,
    payload_version integer NOT NULL,
    data_generation text NOT NULL,
    dirty_revision bigint DEFAULT 0 NOT NULL,
    processed_revision bigint DEFAULT 0 NOT NULL,
    covered_revision bigint DEFAULT 0 NOT NULL,
    queue_generation bigint DEFAULT 0 NOT NULL,
    queued_job_key text,
    due_at timestamp with time zone,
    claim_token text,
    claim_revision bigint,
    claim_expires_at timestamp with time zone,
    last_error text,
    CONSTRAINT recompute_work_check CHECK (((covered_revision <= processed_revision) AND (processed_revision <= dirty_revision))),
    CONSTRAINT recompute_work_payload_version_check CHECK ((payload_version > 0))
);


--
-- Name: recovery_attempt_observations; Type: TABLE; Schema: runtime; Owner: -
--

CREATE TABLE runtime.recovery_attempt_observations (
    id bigint NOT NULL,
    attempt_id text NOT NULL,
    status text NOT NULL,
    error_code text,
    observation_ref text,
    observed_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: recovery_attempt_observations_id_seq; Type: SEQUENCE; Schema: runtime; Owner: -
--

ALTER TABLE runtime.recovery_attempt_observations ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME runtime.recovery_attempt_observations_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: recovery_attempts; Type: TABLE; Schema: runtime; Owner: -
--

CREATE TABLE runtime.recovery_attempts (
    attempt_id text NOT NULL,
    lineage_id text NOT NULL,
    request_digest text NOT NULL,
    route text NOT NULL,
    dependency text NOT NULL,
    version text NOT NULL,
    attempt_number integer NOT NULL,
    dependency_probe boolean DEFAULT false NOT NULL,
    continuation_of text,
    continuation_reason text,
    status text DEFAULT 'reserved'::text NOT NULL,
    error_code text,
    observation_ref text,
    started_at timestamp with time zone DEFAULT now() NOT NULL,
    finished_at timestamp with time zone,
    CONSTRAINT recovery_attempts_attempt_number_check CHECK ((attempt_number > 0))
);


--
-- Name: recovery_dependencies; Type: TABLE; Schema: runtime; Owner: -
--

CREATE TABLE runtime.recovery_dependencies (
    data_generation text NOT NULL,
    dependency text NOT NULL,
    cooldown_until timestamp with time zone,
    probe_token text,
    probe_expires_at timestamp with time zone
);


--
-- Name: recovery_dependency_changes; Type: TABLE; Schema: runtime; Owner: -
--

CREATE TABLE runtime.recovery_dependency_changes (
    id bigint NOT NULL,
    lineage_id text NOT NULL,
    previous_dependency text NOT NULL,
    new_dependency text NOT NULL,
    version text NOT NULL,
    changed_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: recovery_dependency_changes_id_seq; Type: SEQUENCE; Schema: runtime; Owner: -
--

ALTER TABLE runtime.recovery_dependency_changes ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME runtime.recovery_dependency_changes_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: recovery_lineages; Type: TABLE; Schema: runtime; Owner: -
--

CREATE TABLE runtime.recovery_lineages (
    lineage_id text NOT NULL,
    data_generation text NOT NULL,
    domain text NOT NULL,
    subject_id text NOT NULL,
    owner text NOT NULL,
    material_digest text NOT NULL,
    dependency text NOT NULL,
    current_version text NOT NULL,
    max_attempts integer NOT NULL,
    attempts integer DEFAULT 0 NOT NULL,
    due_at timestamp with time zone,
    failure_category text,
    paused boolean DEFAULT false NOT NULL,
    cancelled boolean DEFAULT false NOT NULL,
    claim_token text,
    claim_expires_at timestamp with time zone,
    continuation_attempt_id text,
    continuation_reason text,
    source_withdrawn boolean DEFAULT false NOT NULL,
    CONSTRAINT recovery_lineages_attempts_check CHECK ((attempts >= 0)),
    CONSTRAINT recovery_lineages_max_attempts_check CHECK ((max_attempts > 0))
);


--
-- Name: alias_suppression; Type: TABLE; Schema: sources; Owner: -
--

CREATE TABLE sources.alias_suppression (
    alias_key text NOT NULL,
    reason text NOT NULL,
    suppressed_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: archive_records; Type: TABLE; Schema: sources; Owner: -
--

CREATE TABLE sources.archive_records (
    archive_key text NOT NULL,
    source_id text NOT NULL,
    content_hash text NOT NULL,
    alias_keys text[] NOT NULL,
    explicit_authorized boolean DEFAULT false NOT NULL
);


--
-- Name: changes; Type: TABLE; Schema: sources; Owner: -
--

CREATE TABLE sources.changes (
    sequence bigint NOT NULL,
    source_id text NOT NULL,
    revision bigint NOT NULL,
    kind text NOT NULL,
    occurred_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: changes_sequence_seq; Type: SEQUENCE; Schema: sources; Owner: -
--

ALTER TABLE sources.changes ALTER COLUMN sequence ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME sources.changes_sequence_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: evidence_members; Type: TABLE; Schema: sources; Owner: -
--

CREATE TABLE sources.evidence_members (
    evidence_id text NOT NULL,
    role text NOT NULL,
    group_index integer NOT NULL,
    source_id text NOT NULL,
    revision bigint NOT NULL,
    locator jsonb NOT NULL,
    ref_key text NOT NULL,
    CONSTRAINT evidence_members_role_check CHECK ((role = ANY (ARRAY['required'::text, 'alternative'::text, 'generation'::text])))
);


--
-- Name: evidence_sets; Type: TABLE; Schema: sources; Owner: -
--

CREATE TABLE sources.evidence_sets (
    evidence_id text NOT NULL,
    manifest jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: forgotten_fragments; Type: TABLE; Schema: sources; Owner: -
--

CREATE TABLE sources.forgotten_fragments (
    source_id text NOT NULL,
    revision bigint NOT NULL,
    start_position bigint NOT NULL,
    end_position bigint NOT NULL,
    CONSTRAINT forgotten_fragments_check CHECK (((start_position >= 0) AND (end_position > start_position)))
);


--
-- Name: fragment_barriers; Type: TABLE; Schema: sources; Owner: -
--

CREATE TABLE sources.fragment_barriers (
    source_key text NOT NULL,
    revision bigint NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: media_supplements; Type: TABLE; Schema: sources; Owner: -
--

CREATE TABLE sources.media_supplements (
    source_id text NOT NULL,
    revision bigint NOT NULL,
    request_digest text NOT NULL
);


--
-- Name: originals; Type: TABLE; Schema: sources; Owner: -
--

CREATE TABLE sources.originals (
    source_id text NOT NULL,
    source_key text NOT NULL,
    owner_id text NOT NULL,
    person_id text,
    actor_id text,
    head_revision bigint NOT NULL,
    intake_epoch bigint DEFAULT 1 NOT NULL,
    state text DEFAULT 'active'::text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: source_aliases; Type: TABLE; Schema: sources; Owner: -
--

CREATE TABLE sources.source_aliases (
    alias_key text NOT NULL,
    source_id text NOT NULL,
    revision bigint NOT NULL
);


--
-- Name: supports; Type: TABLE; Schema: sources; Owner: -
--

CREATE TABLE sources.supports (
    source_id text NOT NULL,
    revision bigint NOT NULL,
    group_id text NOT NULL,
    support_source_id text NOT NULL,
    support_revision bigint NOT NULL,
    support_locator jsonb DEFAULT '{}'::jsonb NOT NULL,
    support_key text NOT NULL
);


--
-- Name: suppression; Type: TABLE; Schema: sources; Owner: -
--

CREATE TABLE sources.suppression (
    source_key text NOT NULL,
    source_id text NOT NULL,
    intake_epoch bigint NOT NULL,
    forgotten_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: versions; Type: TABLE; Schema: sources; Owner: -
--

CREATE TABLE sources.versions (
    source_id text NOT NULL,
    revision bigint NOT NULL,
    intake_epoch bigint NOT NULL,
    scope_ids text[] NOT NULL,
    scope_epochs jsonb NOT NULL,
    audience text[] NOT NULL,
    origin_kind text NOT NULL,
    speaker_id text,
    evidence_unit text NOT NULL,
    content text,
    media jsonb,
    locator jsonb NOT NULL,
    coverage jsonb NOT NULL,
    occurred_at timestamp with time zone NOT NULL,
    disclosure boolean DEFAULT false NOT NULL,
    state text DEFAULT 'active'::text NOT NULL
);


--
-- Name: procrastinate_events id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.procrastinate_events ALTER COLUMN id SET DEFAULT nextval('public.procrastinate_events_id_seq'::regclass);


--
-- Name: procrastinate_jobs id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.procrastinate_jobs ALTER COLUMN id SET DEFAULT nextval('public.procrastinate_jobs_id_seq'::regclass);


--
-- Name: procrastinate_periodic_defers id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.procrastinate_periodic_defers ALTER COLUMN id SET DEFAULT nextval('public.procrastinate_periodic_defers_id_seq'::regclass);


--
-- Name: action_controls action_controls_pkey; Type: CONSTRAINT; Schema: capabilities; Owner: -
--

ALTER TABLE ONLY capabilities.action_controls
    ADD CONSTRAINT action_controls_pkey PRIMARY KEY (id);


--
-- Name: actions actions_intent_key; Type: CONSTRAINT; Schema: capabilities; Owner: -
--

ALTER TABLE ONLY capabilities.actions
    ADD CONSTRAINT actions_intent_key UNIQUE (intent);


--
-- Name: actions actions_pkey; Type: CONSTRAINT; Schema: capabilities; Owner: -
--

ALTER TABLE ONLY capabilities.actions
    ADD CONSTRAINT actions_pkey PRIMARY KEY (id);


--
-- Name: artifacts artifacts_pkey; Type: CONSTRAINT; Schema: capabilities; Owner: -
--

ALTER TABLE ONLY capabilities.artifacts
    ADD CONSTRAINT artifacts_pkey PRIMARY KEY (id);


--
-- Name: attempts attempts_pkey; Type: CONSTRAINT; Schema: capabilities; Owner: -
--

ALTER TABLE ONLY capabilities.attempts
    ADD CONSTRAINT attempts_pkey PRIMARY KEY (id);


--
-- Name: command_inputs command_inputs_pkey; Type: CONSTRAINT; Schema: capabilities; Owner: -
--

ALTER TABLE ONLY capabilities.command_inputs
    ADD CONSTRAINT command_inputs_pkey PRIMARY KEY (id);


--
-- Name: configuration configuration_pkey; Type: CONSTRAINT; Schema: capabilities; Owner: -
--

ALTER TABLE ONLY capabilities.configuration
    ADD CONSTRAINT configuration_pkey PRIMARY KEY (kind, id);


--
-- Name: consumptions consumptions_pkey; Type: CONSTRAINT; Schema: capabilities; Owner: -
--

ALTER TABLE ONLY capabilities.consumptions
    ADD CONSTRAINT consumptions_pkey PRIMARY KEY (id);


--
-- Name: device_readings device_readings_pkey; Type: CONSTRAINT; Schema: capabilities; Owner: -
--

ALTER TABLE ONLY capabilities.device_readings
    ADD CONSTRAINT device_readings_pkey PRIMARY KEY (connection_id, entity_id);


--
-- Name: execution_owners execution_owners_pkey; Type: CONSTRAINT; Schema: capabilities; Owner: -
--

ALTER TABLE ONLY capabilities.execution_owners
    ADD CONSTRAINT execution_owners_pkey PRIMARY KEY (id);


--
-- Name: image_work image_work_pkey; Type: CONSTRAINT; Schema: capabilities; Owner: -
--

ALTER TABLE ONLY capabilities.image_work
    ADD CONSTRAINT image_work_pkey PRIMARY KEY (action_id);


--
-- Name: maintenance maintenance_pkey; Type: CONSTRAINT; Schema: capabilities; Owner: -
--

ALTER TABLE ONLY capabilities.maintenance
    ADD CONSTRAINT maintenance_pkey PRIMARY KEY (name);


--
-- Name: oauth_refresh_leases oauth_refresh_leases_pkey; Type: CONSTRAINT; Schema: capabilities; Owner: -
--

ALTER TABLE ONLY capabilities.oauth_refresh_leases
    ADD CONSTRAINT oauth_refresh_leases_pkey PRIMARY KEY (connection_id);


--
-- Name: oauth_states oauth_states_pkey; Type: CONSTRAINT; Schema: capabilities; Owner: -
--

ALTER TABLE ONLY capabilities.oauth_states
    ADD CONSTRAINT oauth_states_pkey PRIMARY KEY (state);


--
-- Name: plugin_consumptions plugin_consumptions_pkey; Type: CONSTRAINT; Schema: capabilities; Owner: -
--

ALTER TABLE ONLY capabilities.plugin_consumptions
    ADD CONSTRAINT plugin_consumptions_pkey PRIMARY KEY (id);


--
-- Name: reaction_assets reaction_assets_pkey; Type: CONSTRAINT; Schema: capabilities; Owner: -
--

ALTER TABLE ONLY capabilities.reaction_assets
    ADD CONSTRAINT reaction_assets_pkey PRIMARY KEY (id);


--
-- Name: reaction_embedding_profiles reaction_embedding_profiles_pkey; Type: CONSTRAINT; Schema: capabilities; Owner: -
--

ALTER TABLE ONLY capabilities.reaction_embedding_profiles
    ADD CONSTRAINT reaction_embedding_profiles_pkey PRIMARY KEY (id);


--
-- Name: runtime_observations runtime_observations_pkey; Type: CONSTRAINT; Schema: capabilities; Owner: -
--

ALTER TABLE ONLY capabilities.runtime_observations
    ADD CONSTRAINT runtime_observations_pkey PRIMARY KEY (name);


--
-- Name: source_dependencies source_dependencies_pkey; Type: CONSTRAINT; Schema: capabilities; Owner: -
--

ALTER TABLE ONLY capabilities.source_dependencies
    ADD CONSTRAINT source_dependencies_pkey PRIMARY KEY (owner_kind, owner_id, source_id, source_revision);


--
-- Name: speech_cache speech_cache_pkey; Type: CONSTRAINT; Schema: capabilities; Owner: -
--

ALTER TABLE ONLY capabilities.speech_cache
    ADD CONSTRAINT speech_cache_pkey PRIMARY KEY (key);


--
-- Name: connections connections_pkey; Type: CONSTRAINT; Schema: capability_connections; Owner: -
--

ALTER TABLE ONLY capability_connections.connections
    ADD CONSTRAINT connections_pkey PRIMARY KEY (kind);


--
-- Name: control_operations control_operations_client_id_key; Type: CONSTRAINT; Schema: capability_infrastructure; Owner: -
--

ALTER TABLE ONLY capability_infrastructure.control_operations
    ADD CONSTRAINT control_operations_client_id_key UNIQUE (client_id);


--
-- Name: control_operations control_operations_pkey; Type: CONSTRAINT; Schema: capability_infrastructure; Owner: -
--

ALTER TABLE ONLY capability_infrastructure.control_operations
    ADD CONSTRAINT control_operations_pkey PRIMARY KEY (operation_id);


--
-- Name: control_plan_uses control_plan_uses_pkey; Type: CONSTRAINT; Schema: capability_infrastructure; Owner: -
--

ALTER TABLE ONLY capability_infrastructure.control_plan_uses
    ADD CONSTRAINT control_plan_uses_pkey PRIMARY KEY (node_id, plan_id);


--
-- Name: control_plans control_plans_operation_id_key; Type: CONSTRAINT; Schema: capability_infrastructure; Owner: -
--

ALTER TABLE ONLY capability_infrastructure.control_plans
    ADD CONSTRAINT control_plans_operation_id_key UNIQUE (operation_id);


--
-- Name: control_plans control_plans_pkey; Type: CONSTRAINT; Schema: capability_infrastructure; Owner: -
--

ALTER TABLE ONLY capability_infrastructure.control_plans
    ADD CONSTRAINT control_plans_pkey PRIMARY KEY (node_id, plan_id);


--
-- Name: observations observations_pkey; Type: CONSTRAINT; Schema: capability_infrastructure; Owner: -
--

ALTER TABLE ONLY capability_infrastructure.observations
    ADD CONSTRAINT observations_pkey PRIMARY KEY (node_id, operation);


--
-- Name: documents documents_pkey; Type: CONSTRAINT; Schema: capability_media; Owner: -
--

ALTER TABLE ONLY capability_media.documents
    ADD CONSTRAINT documents_pkey PRIMARY KEY (kind, id);


--
-- Name: download_sources download_sources_pkey; Type: CONSTRAINT; Schema: capability_media; Owner: -
--

ALTER TABLE ONLY capability_media.download_sources
    ADD CONSTRAINT download_sources_pkey PRIMARY KEY (bvid, cid, quality);


--
-- Name: episodes episodes_bvid_number_key; Type: CONSTRAINT; Schema: capability_media; Owner: -
--

ALTER TABLE ONLY capability_media.episodes
    ADD CONSTRAINT episodes_bvid_number_key UNIQUE (bvid, number);


--
-- Name: episodes episodes_pkey; Type: CONSTRAINT; Schema: capability_media; Owner: -
--

ALTER TABLE ONLY capability_media.episodes
    ADD CONSTRAINT episodes_pkey PRIMARY KEY (bvid, cid);


--
-- Name: jobs jobs_dedupe_key; Type: CONSTRAINT; Schema: capability_media; Owner: -
--

ALTER TABLE ONLY capability_media.jobs
    ADD CONSTRAINT jobs_dedupe_key UNIQUE (dedupe);


--
-- Name: jobs jobs_pkey; Type: CONSTRAINT; Schema: capability_media; Owner: -
--

ALTER TABLE ONLY capability_media.jobs
    ADD CONSTRAINT jobs_pkey PRIMARY KEY (id);


--
-- Name: member_parts member_parts_pkey; Type: CONSTRAINT; Schema: capability_media; Owner: -
--

ALTER TABLE ONLY capability_media.member_parts
    ADD CONSTRAINT member_parts_pkey PRIMARY KEY (subscription_id, bvid, cid);


--
-- Name: members members_pkey; Type: CONSTRAINT; Schema: capability_media; Owner: -
--

ALTER TABLE ONLY capability_media.members
    ADD CONSTRAINT members_pkey PRIMARY KEY (subscription_id, bvid);


--
-- Name: scan_leases scan_leases_pkey; Type: CONSTRAINT; Schema: capability_media; Owner: -
--

ALTER TABLE ONLY capability_media.scan_leases
    ADD CONSTRAINT scan_leases_pkey PRIMARY KEY (id);


--
-- Name: plugin_instances plugin_instances_pkey; Type: CONSTRAINT; Schema: capability_plugins; Owner: -
--

ALTER TABLE ONLY capability_plugins.plugin_instances
    ADD CONSTRAINT plugin_instances_pkey PRIMARY KEY (id);


--
-- Name: plugin_logs plugin_logs_pkey; Type: CONSTRAINT; Schema: capability_plugins; Owner: -
--

ALTER TABLE ONLY capability_plugins.plugin_logs
    ADD CONSTRAINT plugin_logs_pkey PRIMARY KEY (id);


--
-- Name: plugin_notifications plugin_notifications_pkey; Type: CONSTRAINT; Schema: capability_plugins; Owner: -
--

ALTER TABLE ONLY capability_plugins.plugin_notifications
    ADD CONSTRAINT plugin_notifications_pkey PRIMARY KEY (id);


--
-- Name: plugin_operations plugin_operations_pkey; Type: CONSTRAINT; Schema: capability_plugins; Owner: -
--

ALTER TABLE ONLY capability_plugins.plugin_operations
    ADD CONSTRAINT plugin_operations_pkey PRIMARY KEY (id);


--
-- Name: plugin_packages plugin_packages_pkey; Type: CONSTRAINT; Schema: capability_plugins; Owner: -
--

ALTER TABLE ONLY capability_plugins.plugin_packages
    ADD CONSTRAINT plugin_packages_pkey PRIMARY KEY (id);


--
-- Name: plugin_schedules plugin_schedules_pkey; Type: CONSTRAINT; Schema: capability_plugins; Owner: -
--

ALTER TABLE ONLY capability_plugins.plugin_schedules
    ADD CONSTRAINT plugin_schedules_pkey PRIMARY KEY (id);


--
-- Name: plugin_secrets plugin_secrets_pkey; Type: CONSTRAINT; Schema: capability_plugins; Owner: -
--

ALTER TABLE ONLY capability_plugins.plugin_secrets
    ADD CONSTRAINT plugin_secrets_pkey PRIMARY KEY (instance_id, name);


--
-- Name: plugin_state plugin_state_pkey; Type: CONSTRAINT; Schema: capability_plugins; Owner: -
--

ALTER TABLE ONLY capability_plugins.plugin_state
    ADD CONSTRAINT plugin_state_pkey PRIMARY KEY (instance_id, key);


--
-- Name: continuations continuations_pkey; Type: CONSTRAINT; Schema: capability_projects; Owner: -
--

ALTER TABLE ONLY capability_projects.continuations
    ADD CONSTRAINT continuations_pkey PRIMARY KEY (pack_id);


--
-- Name: declared_states declared_states_pkey; Type: CONSTRAINT; Schema: capability_projects; Owner: -
--

ALTER TABLE ONLY capability_projects.declared_states
    ADD CONSTRAINT declared_states_pkey PRIMARY KEY (project_id, revision);


--
-- Name: directory_plans directory_plans_pkey; Type: CONSTRAINT; Schema: capability_projects; Owner: -
--

ALTER TABLE ONLY capability_projects.directory_plans
    ADD CONSTRAINT directory_plans_pkey PRIMARY KEY (plan_id);


--
-- Name: document_versions document_versions_pkey; Type: CONSTRAINT; Schema: capability_projects; Owner: -
--

ALTER TABLE ONLY capability_projects.document_versions
    ADD CONSTRAINT document_versions_pkey PRIMARY KEY (document_id, version);


--
-- Name: documents documents_pkey; Type: CONSTRAINT; Schema: capability_projects; Owner: -
--

ALTER TABLE ONLY capability_projects.documents
    ADD CONSTRAINT documents_pkey PRIMARY KEY (document_id);


--
-- Name: documents documents_project_id_kind_locator_key; Type: CONSTRAINT; Schema: capability_projects; Owner: -
--

ALTER TABLE ONLY capability_projects.documents
    ADD CONSTRAINT documents_project_id_kind_locator_key UNIQUE (project_id, kind, locator);


--
-- Name: experience_versions experience_versions_pkey; Type: CONSTRAINT; Schema: capability_projects; Owner: -
--

ALTER TABLE ONLY capability_projects.experience_versions
    ADD CONSTRAINT experience_versions_pkey PRIMARY KEY (experience_id, revision);


--
-- Name: experiences experiences_pkey; Type: CONSTRAINT; Schema: capability_projects; Owner: -
--

ALTER TABLE ONLY capability_projects.experiences
    ADD CONSTRAINT experiences_pkey PRIMARY KEY (experience_id);


--
-- Name: imports imports_action_id_key; Type: CONSTRAINT; Schema: capability_projects; Owner: -
--

ALTER TABLE ONLY capability_projects.imports
    ADD CONSTRAINT imports_action_id_key UNIQUE (action_id);


--
-- Name: imports imports_pkey; Type: CONSTRAINT; Schema: capability_projects; Owner: -
--

ALTER TABLE ONLY capability_projects.imports
    ADD CONSTRAINT imports_pkey PRIMARY KEY (import_id);


--
-- Name: lesson_versions lesson_versions_pkey; Type: CONSTRAINT; Schema: capability_projects; Owner: -
--

ALTER TABLE ONLY capability_projects.lesson_versions
    ADD CONSTRAINT lesson_versions_pkey PRIMARY KEY (lesson_id, version);


--
-- Name: lessons lessons_pkey; Type: CONSTRAINT; Schema: capability_projects; Owner: -
--

ALTER TABLE ONLY capability_projects.lessons
    ADD CONSTRAINT lessons_pkey PRIMARY KEY (lesson_id);


--
-- Name: note_versions note_versions_pkey; Type: CONSTRAINT; Schema: capability_projects; Owner: -
--

ALTER TABLE ONLY capability_projects.note_versions
    ADD CONSTRAINT note_versions_pkey PRIMARY KEY (note_id, version);


--
-- Name: notes notes_pkey; Type: CONSTRAINT; Schema: capability_projects; Owner: -
--

ALTER TABLE ONLY capability_projects.notes
    ADD CONSTRAINT notes_pkey PRIMARY KEY (note_id);


--
-- Name: projects projects_pkey; Type: CONSTRAINT; Schema: capability_projects; Owner: -
--

ALTER TABLE ONLY capability_projects.projects
    ADD CONSTRAINT projects_pkey PRIMARY KEY (project_id);


--
-- Name: registry_versions registry_versions_pkey; Type: CONSTRAINT; Schema: capability_projects; Owner: -
--

ALTER TABLE ONLY capability_projects.registry_versions
    ADD CONSTRAINT registry_versions_pkey PRIMARY KEY (project_id, revision);


--
-- Name: calls calls_pkey; Type: CONSTRAINT; Schema: channels; Owner: -
--

ALTER TABLE ONLY channels.calls
    ADD CONSTRAINT calls_pkey PRIMARY KEY (id);


--
-- Name: connections connections_pkey; Type: CONSTRAINT; Schema: channels; Owner: -
--

ALTER TABLE ONLY channels.connections
    ADD CONSTRAINT connections_pkey PRIMARY KEY (id);


--
-- Name: event_sources event_sources_pkey; Type: CONSTRAINT; Schema: channels; Owner: -
--

ALTER TABLE ONLY channels.event_sources
    ADD CONSTRAINT event_sources_pkey PRIMARY KEY (event_key, source_id, source_revision);


--
-- Name: events events_connection_id_producer_event_id_revision_key; Type: CONSTRAINT; Schema: channels; Owner: -
--

ALTER TABLE ONLY channels.events
    ADD CONSTRAINT events_connection_id_producer_event_id_revision_key UNIQUE (connection_id, producer, event_id, revision);


--
-- Name: events events_pkey; Type: CONSTRAINT; Schema: channels; Owner: -
--

ALTER TABLE ONLY channels.events
    ADD CONSTRAINT events_pkey PRIMARY KEY (id);


--
-- Name: expressions expressions_action_id_key; Type: CONSTRAINT; Schema: channels; Owner: -
--

ALTER TABLE ONLY channels.expressions
    ADD CONSTRAINT expressions_action_id_key UNIQUE (action_id);


--
-- Name: expressions expressions_pkey; Type: CONSTRAINT; Schema: channels; Owner: -
--

ALTER TABLE ONLY channels.expressions
    ADD CONSTRAINT expressions_pkey PRIMARY KEY (expression_id);


--
-- Name: media_supplements media_supplements_event_key_part_index_key; Type: CONSTRAINT; Schema: channels; Owner: -
--

ALTER TABLE ONLY channels.media_supplements
    ADD CONSTRAINT media_supplements_event_key_part_index_key UNIQUE (event_key, part_index);


--
-- Name: media_supplements media_supplements_pkey; Type: CONSTRAINT; Schema: channels; Owner: -
--

ALTER TABLE ONLY channels.media_supplements
    ADD CONSTRAINT media_supplements_pkey PRIMARY KEY (id);


--
-- Name: observations observations_pkey; Type: CONSTRAINT; Schema: channels; Owner: -
--

ALTER TABLE ONLY channels.observations
    ADD CONSTRAINT observations_pkey PRIMARY KEY (connection_id, kind, name);


--
-- Name: owners owners_pkey; Type: CONSTRAINT; Schema: channels; Owner: -
--

ALTER TABLE ONLY channels.owners
    ADD CONSTRAINT owners_pkey PRIMARY KEY (id);


--
-- Name: source_dependencies source_dependencies_pkey; Type: CONSTRAINT; Schema: channels; Owner: -
--

ALTER TABLE ONLY channels.source_dependencies
    ADD CONSTRAINT source_dependencies_pkey PRIMARY KEY (call_id, source_id, source_revision);


--
-- Name: telegram_offsets telegram_offsets_pkey; Type: CONSTRAINT; Schema: channels; Owner: -
--

ALTER TABLE ONLY channels.telegram_offsets
    ADD CONSTRAINT telegram_offsets_pkey PRIMARY KEY (connection_id);


--
-- Name: actors actors_pkey; Type: CONSTRAINT; Schema: companion; Owner: -
--

ALTER TABLE ONLY companion.actors
    ADD CONSTRAINT actors_pkey PRIMARY KEY (actor_id);


--
-- Name: affect_events affect_events_pkey; Type: CONSTRAINT; Schema: companion; Owner: -
--

ALTER TABLE ONLY companion.affect_events
    ADD CONSTRAINT affect_events_pkey PRIMARY KEY (actor_id, event_id);


--
-- Name: affect_regulation affect_regulation_pkey; Type: CONSTRAINT; Schema: companion; Owner: -
--

ALTER TABLE ONLY companion.affect_regulation
    ADD CONSTRAINT affect_regulation_pkey PRIMARY KEY (actor_id, scope_key);


--
-- Name: expression_example_usage expression_example_usage_pkey; Type: CONSTRAINT; Schema: companion; Owner: -
--

ALTER TABLE ONLY companion.expression_example_usage
    ADD CONSTRAINT expression_example_usage_pkey PRIMARY KEY (example_id, usage_id, outcome);


--
-- Name: expression_examples expression_examples_pkey; Type: CONSTRAINT; Schema: companion; Owner: -
--

ALTER TABLE ONLY companion.expression_examples
    ADD CONSTRAINT expression_examples_pkey PRIMARY KEY (id);


--
-- Name: expression_examples expression_examples_rule_id_expression_id_key; Type: CONSTRAINT; Schema: companion; Owner: -
--

ALTER TABLE ONLY companion.expression_examples
    ADD CONSTRAINT expression_examples_rule_id_expression_id_key UNIQUE (rule_id, expression_id);


--
-- Name: growth_inputs growth_inputs_pkey; Type: CONSTRAINT; Schema: companion; Owner: -
--

ALTER TABLE ONLY companion.growth_inputs
    ADD CONSTRAINT growth_inputs_pkey PRIMARY KEY (actor_id, scope_key, episode_id);


--
-- Name: growth_material_failures growth_material_failures_pkey; Type: CONSTRAINT; Schema: companion; Owner: -
--

ALTER TABLE ONLY companion.growth_material_failures
    ADD CONSTRAINT growth_material_failures_pkey PRIMARY KEY (runtime_generation, actor_id, scope_key, episode_id, episode_revision);


--
-- Name: growth_retry_batches growth_retry_batches_pkey; Type: CONSTRAINT; Schema: companion; Owner: -
--

ALTER TABLE ONLY companion.growth_retry_batches
    ADD CONSTRAINT growth_retry_batches_pkey PRIMARY KEY (actor_id, scope_key, retry_key);


--
-- Name: growth_statistics_outbox growth_statistics_outbox_pkey; Type: CONSTRAINT; Schema: companion; Owner: -
--

ALTER TABLE ONLY companion.growth_statistics_outbox
    ADD CONSTRAINT growth_statistics_outbox_pkey PRIMARY KEY (runtime_generation, event_id, revision);


--
-- Name: habit_revisions habit_revisions_pkey; Type: CONSTRAINT; Schema: companion; Owner: -
--

ALTER TABLE ONLY companion.habit_revisions
    ADD CONSTRAINT habit_revisions_pkey PRIMARY KEY (rule_id, revision);


--
-- Name: habit_supports habit_supports_pkey; Type: CONSTRAINT; Schema: companion; Owner: -
--

ALTER TABLE ONLY companion.habit_supports
    ADD CONSTRAINT habit_supports_pkey PRIMARY KEY (rule_id, event_id);


--
-- Name: habit_unresolved habit_unresolved_pkey; Type: CONSTRAINT; Schema: companion; Owner: -
--

ALTER TABLE ONLY companion.habit_unresolved
    ADD CONSTRAINT habit_unresolved_pkey PRIMARY KEY (proposal_id);


--
-- Name: learning_reflections learning_reflections_pkey; Type: CONSTRAINT; Schema: companion; Owner: -
--

ALTER TABLE ONLY companion.learning_reflections
    ADD CONSTRAINT learning_reflections_pkey PRIMARY KEY (proposal_id);


--
-- Name: organize_state organize_state_pkey; Type: CONSTRAINT; Schema: companion; Owner: -
--

ALTER TABLE ONLY companion.organize_state
    ADD CONSTRAINT organize_state_pkey PRIMARY KEY (actor_id, scope_key);


--
-- Name: persona_profiles persona_profiles_pkey; Type: CONSTRAINT; Schema: companion; Owner: -
--

ALTER TABLE ONLY companion.persona_profiles
    ADD CONSTRAINT persona_profiles_pkey PRIMARY KEY (persona_id);


--
-- Name: persona_revisions persona_revisions_pkey; Type: CONSTRAINT; Schema: companion; Owner: -
--

ALTER TABLE ONLY companion.persona_revisions
    ADD CONSTRAINT persona_revisions_pkey PRIMARY KEY (persona_id, revision);


--
-- Name: style_rules style_rules_pkey; Type: CONSTRAINT; Schema: companion; Owner: -
--

ALTER TABLE ONLY companion.style_rules
    ADD CONSTRAINT style_rules_pkey PRIMARY KEY (id);


--
-- Name: context_selections context_selections_pkey; Type: CONSTRAINT; Schema: conversation; Owner: -
--

ALTER TABLE ONLY conversation.context_selections
    ADD CONSTRAINT context_selections_pkey PRIMARY KEY (attempt_id);


--
-- Name: expression_plans expression_plans_pkey; Type: CONSTRAINT; Schema: conversation; Owner: -
--

ALTER TABLE ONLY conversation.expression_plans
    ADD CONSTRAINT expression_plans_pkey PRIMARY KEY (expression_id);


--
-- Name: expressions expressions_pkey; Type: CONSTRAINT; Schema: conversation; Owner: -
--

ALTER TABLE ONLY conversation.expressions
    ADD CONSTRAINT expressions_pkey PRIMARY KEY (expression_id);


--
-- Name: generation_requests generation_requests_attempt_id_key; Type: CONSTRAINT; Schema: conversation; Owner: -
--

ALTER TABLE ONLY conversation.generation_requests
    ADD CONSTRAINT generation_requests_attempt_id_key UNIQUE (attempt_id);


--
-- Name: generation_requests generation_requests_pkey; Type: CONSTRAINT; Schema: conversation; Owner: -
--

ALTER TABLE ONLY conversation.generation_requests
    ADD CONSTRAINT generation_requests_pkey PRIMARY KEY (material_key);


--
-- Name: group_participation group_participation_pkey; Type: CONSTRAINT; Schema: conversation; Owner: -
--

ALTER TABLE ONLY conversation.group_participation
    ADD CONSTRAINT group_participation_pkey PRIMARY KEY (actor_id, conversation_id);


--
-- Name: input_supplements input_supplements_input_id_part_index_content_digest_key; Type: CONSTRAINT; Schema: conversation; Owner: -
--

ALTER TABLE ONLY conversation.input_supplements
    ADD CONSTRAINT input_supplements_input_id_part_index_content_digest_key UNIQUE (input_id, part_index, content_digest);


--
-- Name: input_supplements input_supplements_pkey; Type: CONSTRAINT; Schema: conversation; Owner: -
--

ALTER TABLE ONLY conversation.input_supplements
    ADD CONSTRAINT input_supplements_pkey PRIMARY KEY (input_id, part_index, supplement_revision);


--
-- Name: messages messages_expression_id_key; Type: CONSTRAINT; Schema: conversation; Owner: -
--

ALTER TABLE ONLY conversation.messages
    ADD CONSTRAINT messages_expression_id_key UNIQUE (expression_id);


--
-- Name: messages messages_pkey; Type: CONSTRAINT; Schema: conversation; Owner: -
--

ALTER TABLE ONLY conversation.messages
    ADD CONSTRAINT messages_pkey PRIMARY KEY (id);


--
-- Name: result_consumption result_consumption_pkey; Type: CONSTRAINT; Schema: conversation; Owner: -
--

ALTER TABLE ONLY conversation.result_consumption
    ADD CONSTRAINT result_consumption_pkey PRIMARY KEY (receipt_id);


--
-- Name: tool_links tool_links_action_id_key; Type: CONSTRAINT; Schema: conversation; Owner: -
--

ALTER TABLE ONLY conversation.tool_links
    ADD CONSTRAINT tool_links_action_id_key UNIQUE (action_id);


--
-- Name: tool_links tool_links_pkey; Type: CONSTRAINT; Schema: conversation; Owner: -
--

ALTER TABLE ONLY conversation.tool_links
    ADD CONSTRAINT tool_links_pkey PRIMARY KEY (turn_id, call_id);


--
-- Name: turns turns_actor_id_conversation_id_producer_event_id_event_revi_key; Type: CONSTRAINT; Schema: conversation; Owner: -
--

ALTER TABLE ONLY conversation.turns
    ADD CONSTRAINT turns_actor_id_conversation_id_producer_event_id_event_revi_key UNIQUE (actor_id, conversation_id, producer, event_id, event_revision);


--
-- Name: turns turns_pkey; Type: CONSTRAINT; Schema: conversation; Owner: -
--

ALTER TABLE ONLY conversation.turns
    ADD CONSTRAINT turns_pkey PRIMARY KEY (turn_id);


--
-- Name: accounts accounts_pkey; Type: CONSTRAINT; Schema: identity; Owner: -
--

ALTER TABLE ONLY identity.accounts
    ADD CONSTRAINT accounts_pkey PRIMARY KEY (connection_id, external_id);


--
-- Name: actor_grants actor_grants_pkey; Type: CONSTRAINT; Schema: identity; Owner: -
--

ALTER TABLE ONLY identity.actor_grants
    ADD CONSTRAINT actor_grants_pkey PRIMARY KEY (actor_id, principal_id);


--
-- Name: actors actors_pkey; Type: CONSTRAINT; Schema: identity; Owner: -
--

ALTER TABLE ONLY identity.actors
    ADD CONSTRAINT actors_pkey PRIMARY KEY (actor_id);


--
-- Name: anonymous_sessions anonymous_sessions_pkey; Type: CONSTRAINT; Schema: identity; Owner: -
--

ALTER TABLE ONLY identity.anonymous_sessions
    ADD CONSTRAINT anonymous_sessions_pkey PRIMARY KEY (token_hash);


--
-- Name: connections connections_pkey; Type: CONSTRAINT; Schema: identity; Owner: -
--

ALTER TABLE ONLY identity.connections
    ADD CONSTRAINT connections_pkey PRIMARY KEY (connection_id);


--
-- Name: conversation_links conversation_links_pkey; Type: CONSTRAINT; Schema: identity; Owner: -
--

ALTER TABLE ONLY identity.conversation_links
    ADD CONSTRAINT conversation_links_pkey PRIMARY KEY (connection_id, external_id);


--
-- Name: conversation_members conversation_members_pkey; Type: CONSTRAINT; Schema: identity; Owner: -
--

ALTER TABLE ONLY identity.conversation_members
    ADD CONSTRAINT conversation_members_pkey PRIMARY KEY (conversation_id, connection_id, external_id);


--
-- Name: conversations conversations_actor_id_scope_id_kind_adapter_target_ref_key; Type: CONSTRAINT; Schema: identity; Owner: -
--

ALTER TABLE ONLY identity.conversations
    ADD CONSTRAINT conversations_actor_id_scope_id_kind_adapter_target_ref_key UNIQUE NULLS NOT DISTINCT (actor_id, scope_id, kind, adapter, target_ref);


--
-- Name: conversations conversations_pkey; Type: CONSTRAINT; Schema: identity; Owner: -
--

ALTER TABLE ONLY identity.conversations
    ADD CONSTRAINT conversations_pkey PRIMARY KEY (conversation_id);


--
-- Name: events events_pkey; Type: CONSTRAINT; Schema: identity; Owner: -
--

ALTER TABLE ONLY identity.events
    ADD CONSTRAINT events_pkey PRIMARY KEY (connection_id, producer, event_id, revision);


--
-- Name: installation installation_pkey; Type: CONSTRAINT; Schema: identity; Owner: -
--

ALTER TABLE ONLY identity.installation
    ADD CONSTRAINT installation_pkey PRIMARY KEY (singleton);


--
-- Name: login_windows login_windows_pkey; Type: CONSTRAINT; Schema: identity; Owner: -
--

ALTER TABLE ONLY identity.login_windows
    ADD CONSTRAINT login_windows_pkey PRIMARY KEY (username_hash);


--
-- Name: people people_pkey; Type: CONSTRAINT; Schema: identity; Owner: -
--

ALTER TABLE ONLY identity.people
    ADD CONSTRAINT people_pkey PRIMARY KEY (person_id);


--
-- Name: person_aliases person_aliases_pkey; Type: CONSTRAINT; Schema: identity; Owner: -
--

ALTER TABLE ONLY identity.person_aliases
    ADD CONSTRAINT person_aliases_pkey PRIMARY KEY (person_id, alias);


--
-- Name: principals principals_pkey; Type: CONSTRAINT; Schema: identity; Owner: -
--

ALTER TABLE ONLY identity.principals
    ADD CONSTRAINT principals_pkey PRIMARY KEY (principal_id);


--
-- Name: principals principals_username_key; Type: CONSTRAINT; Schema: identity; Owner: -
--

ALTER TABLE ONLY identity.principals
    ADD CONSTRAINT principals_username_key UNIQUE (username);


--
-- Name: scope_grants scope_grants_pkey; Type: CONSTRAINT; Schema: identity; Owner: -
--

ALTER TABLE ONLY identity.scope_grants
    ADD CONSTRAINT scope_grants_pkey PRIMARY KEY (scope_id, principal_id);


--
-- Name: scope_inclusions scope_inclusions_pkey; Type: CONSTRAINT; Schema: identity; Owner: -
--

ALTER TABLE ONLY identity.scope_inclusions
    ADD CONSTRAINT scope_inclusions_pkey PRIMARY KEY (publication_scope_id, conversation_scope_id);


--
-- Name: scopes scopes_pkey; Type: CONSTRAINT; Schema: identity; Owner: -
--

ALTER TABLE ONLY identity.scopes
    ADD CONSTRAINT scopes_pkey PRIMARY KEY (scope_id);


--
-- Name: sessions sessions_pkey; Type: CONSTRAINT; Schema: identity; Owner: -
--

ALTER TABLE ONLY identity.sessions
    ADD CONSTRAINT sessions_pkey PRIMARY KEY (token_hash);


--
-- Name: activities activities_pkey; Type: CONSTRAINT; Schema: life; Owner: -
--

ALTER TABLE ONLY life.activities
    ADD CONSTRAINT activities_pkey PRIMARY KEY (id);


--
-- Name: actor_state actor_state_pkey; Type: CONSTRAINT; Schema: life; Owner: -
--

ALTER TABLE ONLY life.actor_state
    ADD CONSTRAINT actor_state_pkey PRIMARY KEY (actor_id);


--
-- Name: chapter_revisions chapter_revisions_pkey; Type: CONSTRAINT; Schema: life; Owner: -
--

ALTER TABLE ONLY life.chapter_revisions
    ADD CONSTRAINT chapter_revisions_pkey PRIMARY KEY (chapter_id, revision);


--
-- Name: chapters chapters_pkey; Type: CONSTRAINT; Schema: life; Owner: -
--

ALTER TABLE ONLY life.chapters
    ADD CONSTRAINT chapters_pkey PRIMARY KEY (id);


--
-- Name: chapters chapters_work_id_position_key; Type: CONSTRAINT; Schema: life; Owner: -
--

ALTER TABLE ONLY life.chapters
    ADD CONSTRAINT chapters_work_id_position_key UNIQUE (work_id, "position");


--
-- Name: commitments commitments_pkey; Type: CONSTRAINT; Schema: life; Owner: -
--

ALTER TABLE ONLY life.commitments
    ADD CONSTRAINT commitments_pkey PRIMARY KEY (id);


--
-- Name: contact_candidates contact_candidates_pkey; Type: CONSTRAINT; Schema: life; Owner: -
--

ALTER TABLE ONLY life.contact_candidates
    ADD CONSTRAINT contact_candidates_pkey PRIMARY KEY (id);


--
-- Name: contact_preferences contact_preferences_pkey; Type: CONSTRAINT; Schema: life; Owner: -
--

ALTER TABLE ONLY life.contact_preferences
    ADD CONSTRAINT contact_preferences_pkey PRIMARY KEY (actor_id, person_id);


--
-- Name: contact_replies contact_replies_pkey; Type: CONSTRAINT; Schema: life; Owner: -
--

ALTER TABLE ONLY life.contact_replies
    ADD CONSTRAINT contact_replies_pkey PRIMARY KEY (input_id);


--
-- Name: contacts contacts_pkey; Type: CONSTRAINT; Schema: life; Owner: -
--

ALTER TABLE ONLY life.contacts
    ADD CONSTRAINT contacts_pkey PRIMARY KEY (expression_id);


--
-- Name: day_plans day_plans_pkey; Type: CONSTRAINT; Schema: life; Owner: -
--

ALTER TABLE ONLY life.day_plans
    ADD CONSTRAINT day_plans_pkey PRIMARY KEY (actor_id, day);


--
-- Name: day_slots day_slots_pkey; Type: CONSTRAINT; Schema: life; Owner: -
--

ALTER TABLE ONLY life.day_slots
    ADD CONSTRAINT day_slots_pkey PRIMARY KEY (id);


--
-- Name: diaries diaries_actor_day_scene_key; Type: CONSTRAINT; Schema: life; Owner: -
--

ALTER TABLE ONLY life.diaries
    ADD CONSTRAINT diaries_actor_day_scene_key UNIQUE (actor_id, day, scene_key);


--
-- Name: diaries diaries_pkey; Type: CONSTRAINT; Schema: life; Owner: -
--

ALTER TABLE ONLY life.diaries
    ADD CONSTRAINT diaries_pkey PRIMARY KEY (id);


--
-- Name: diary_days diary_days_pkey; Type: CONSTRAINT; Schema: life; Owner: -
--

ALTER TABLE ONLY life.diary_days
    ADD CONSTRAINT diary_days_pkey PRIMARY KEY (actor_id, scope_key, day);


--
-- Name: diary_revisions diary_revisions_pkey; Type: CONSTRAINT; Schema: life; Owner: -
--

ALTER TABLE ONLY life.diary_revisions
    ADD CONSTRAINT diary_revisions_pkey PRIMARY KEY (diary_id, revision);


--
-- Name: experience_statistics_pending experience_statistics_pending_pkey; Type: CONSTRAINT; Schema: life; Owner: -
--

ALTER TABLE ONLY life.experience_statistics_pending
    ADD CONSTRAINT experience_statistics_pending_pkey PRIMARY KEY (kind, item_id, content_revision);


--
-- Name: generation_recipes generation_recipes_pkey; Type: CONSTRAINT; Schema: life; Owner: -
--

ALTER TABLE ONLY life.generation_recipes
    ADD CONSTRAINT generation_recipes_pkey PRIMARY KEY (generation_id);


--
-- Name: goal_milestones goal_milestones_pkey; Type: CONSTRAINT; Schema: life; Owner: -
--

ALTER TABLE ONLY life.goal_milestones
    ADD CONSTRAINT goal_milestones_pkey PRIMARY KEY (goal_id, milestone_id);


--
-- Name: goal_progress_receipts goal_progress_receipts_goal_id_milestone_key_key; Type: CONSTRAINT; Schema: life; Owner: -
--

ALTER TABLE ONLY life.goal_progress_receipts
    ADD CONSTRAINT goal_progress_receipts_goal_id_milestone_key_key UNIQUE (goal_id, milestone_key);


--
-- Name: goal_progress_receipts goal_progress_receipts_pkey; Type: CONSTRAINT; Schema: life; Owner: -
--

ALTER TABLE ONLY life.goal_progress_receipts
    ADD CONSTRAINT goal_progress_receipts_pkey PRIMARY KEY (goal_id, action_id);


--
-- Name: goal_revisions goal_revisions_pkey; Type: CONSTRAINT; Schema: life; Owner: -
--

ALTER TABLE ONLY life.goal_revisions
    ADD CONSTRAINT goal_revisions_pkey PRIMARY KEY (goal_id, revision);


--
-- Name: goals goals_pkey; Type: CONSTRAINT; Schema: life; Owner: -
--

ALTER TABLE ONLY life.goals
    ADD CONSTRAINT goals_pkey PRIMARY KEY (id);


--
-- Name: outfits outfits_pkey; Type: CONSTRAINT; Schema: life; Owner: -
--

ALTER TABLE ONLY life.outfits
    ADD CONSTRAINT outfits_pkey PRIMARY KEY (id);


--
-- Name: plan_generations plan_generations_pkey; Type: CONSTRAINT; Schema: life; Owner: -
--

ALTER TABLE ONLY life.plan_generations
    ADD CONSTRAINT plan_generations_pkey PRIMARY KEY (generation_key);


--
-- Name: plan_watermarks plan_watermarks_pkey; Type: CONSTRAINT; Schema: life; Owner: -
--

ALTER TABLE ONLY life.plan_watermarks
    ADD CONSTRAINT plan_watermarks_pkey PRIMARY KEY (actor_id);


--
-- Name: readings readings_pkey; Type: CONSTRAINT; Schema: life; Owner: -
--

ALTER TABLE ONLY life.readings
    ADD CONSTRAINT readings_pkey PRIMARY KEY (id);


--
-- Name: routines routines_pkey; Type: CONSTRAINT; Schema: life; Owner: -
--

ALTER TABLE ONLY life.routines
    ADD CONSTRAINT routines_pkey PRIMARY KEY (actor_id);


--
-- Name: works works_pkey; Type: CONSTRAINT; Schema: life; Owner: -
--

ALTER TABLE ONLY life.works
    ADD CONSTRAINT works_pkey PRIMARY KEY (id);


--
-- Name: claim_revisions claim_revisions_pkey; Type: CONSTRAINT; Schema: memory; Owner: -
--

ALTER TABLE ONLY memory.claim_revisions
    ADD CONSTRAINT claim_revisions_pkey PRIMARY KEY (claim_id, revision);


--
-- Name: claims claims_pkey; Type: CONSTRAINT; Schema: memory; Owner: -
--

ALTER TABLE ONLY memory.claims
    ADD CONSTRAINT claims_pkey PRIMARY KEY (claim_id);


--
-- Name: consolidation_attempts consolidation_attempts_model_attempt_id_key; Type: CONSTRAINT; Schema: memory; Owner: -
--

ALTER TABLE ONLY memory.consolidation_attempts
    ADD CONSTRAINT consolidation_attempts_model_attempt_id_key UNIQUE (model_attempt_id);


--
-- Name: consolidation_attempts consolidation_attempts_pkey; Type: CONSTRAINT; Schema: memory; Owner: -
--

ALTER TABLE ONLY memory.consolidation_attempts
    ADD CONSTRAINT consolidation_attempts_pkey PRIMARY KEY (batch_id, number);


--
-- Name: consolidation_attempts consolidation_attempts_step_id_key; Type: CONSTRAINT; Schema: memory; Owner: -
--

ALTER TABLE ONLY memory.consolidation_attempts
    ADD CONSTRAINT consolidation_attempts_step_id_key UNIQUE (step_id);


--
-- Name: consolidation_batches consolidation_batches_pkey; Type: CONSTRAINT; Schema: memory; Owner: -
--

ALTER TABLE ONLY memory.consolidation_batches
    ADD CONSTRAINT consolidation_batches_pkey PRIMARY KEY (batch_id);


--
-- Name: consolidation_compilations consolidation_compilations_pkey; Type: CONSTRAINT; Schema: memory; Owner: -
--

ALTER TABLE ONLY memory.consolidation_compilations
    ADD CONSTRAINT consolidation_compilations_pkey PRIMARY KEY (batch_id, attempt);


--
-- Name: consolidation_reorganization_parts consolidation_reorganization_parts_child_batch_id_key; Type: CONSTRAINT; Schema: memory; Owner: -
--

ALTER TABLE ONLY memory.consolidation_reorganization_parts
    ADD CONSTRAINT consolidation_reorganization_parts_child_batch_id_key UNIQUE (child_batch_id);


--
-- Name: consolidation_reorganization_parts consolidation_reorganization_parts_pkey; Type: CONSTRAINT; Schema: memory; Owner: -
--

ALTER TABLE ONLY memory.consolidation_reorganization_parts
    ADD CONSTRAINT consolidation_reorganization_parts_pkey PRIMARY KEY (parent_batch_id, ordinal);


--
-- Name: consolidation_reorganizations consolidation_reorganizations_pkey; Type: CONSTRAINT; Schema: memory; Owner: -
--

ALTER TABLE ONLY memory.consolidation_reorganizations
    ADD CONSTRAINT consolidation_reorganizations_pkey PRIMARY KEY (root_batch_id);


--
-- Name: consolidation_state consolidation_state_pkey; Type: CONSTRAINT; Schema: memory; Owner: -
--

ALTER TABLE ONLY memory.consolidation_state
    ADD CONSTRAINT consolidation_state_pkey PRIMARY KEY (partition_id);


--
-- Name: episode_original_terms episode_original_terms_pkey; Type: CONSTRAINT; Schema: memory; Owner: -
--

ALTER TABLE ONLY memory.episode_original_terms
    ADD CONSTRAINT episode_original_terms_pkey PRIMARY KEY (episode_id, revision);


--
-- Name: episode_revisions episode_revisions_pkey; Type: CONSTRAINT; Schema: memory; Owner: -
--

ALTER TABLE ONLY memory.episode_revisions
    ADD CONSTRAINT episode_revisions_pkey PRIMARY KEY (episode_id, revision);


--
-- Name: episode_supplements episode_supplements_pkey; Type: CONSTRAINT; Schema: memory; Owner: -
--

ALTER TABLE ONLY memory.episode_supplements
    ADD CONSTRAINT episode_supplements_pkey PRIMARY KEY (episode_id, event_revision, part_index, supplement_revision);


--
-- Name: episodes episodes_actor_id_event_id_key; Type: CONSTRAINT; Schema: memory; Owner: -
--

ALTER TABLE ONLY memory.episodes
    ADD CONSTRAINT episodes_actor_id_event_id_key UNIQUE (actor_id, event_id);


--
-- Name: episodes episodes_pkey; Type: CONSTRAINT; Schema: memory; Owner: -
--

ALTER TABLE ONLY memory.episodes
    ADD CONSTRAINT episodes_pkey PRIMARY KEY (episode_id);


--
-- Name: group_term_uses group_term_uses_pkey; Type: CONSTRAINT; Schema: memory; Owner: -
--

ALTER TABLE ONLY memory.group_term_uses
    ADD CONSTRAINT group_term_uses_pkey PRIMARY KEY (actor_id, conversation_id, event_id, claim_id, revision, outcome);


--
-- Name: index_attempts index_attempts_model_attempt_id_key; Type: CONSTRAINT; Schema: memory; Owner: -
--

ALTER TABLE ONLY memory.index_attempts
    ADD CONSTRAINT index_attempts_model_attempt_id_key UNIQUE (model_attempt_id);


--
-- Name: index_attempts index_attempts_pkey; Type: CONSTRAINT; Schema: memory; Owner: -
--

ALTER TABLE ONLY memory.index_attempts
    ADD CONSTRAINT index_attempts_pkey PRIMARY KEY (index_id, number);


--
-- Name: index_attempts index_attempts_step_id_key; Type: CONSTRAINT; Schema: memory; Owner: -
--

ALTER TABLE ONLY memory.index_attempts
    ADD CONSTRAINT index_attempts_step_id_key UNIQUE (step_id);


--
-- Name: index_jobs index_jobs_generation_identity; Type: CONSTRAINT; Schema: memory; Owner: -
--

ALTER TABLE ONLY memory.index_jobs
    ADD CONSTRAINT index_jobs_generation_identity UNIQUE (generation, kind, object_id, revision, profile_key, content_hash);


--
-- Name: index_jobs index_jobs_pkey; Type: CONSTRAINT; Schema: memory; Owner: -
--

ALTER TABLE ONLY memory.index_jobs
    ADD CONSTRAINT index_jobs_pkey PRIMARY KEY (index_id);


--
-- Name: index_scan index_scan_pkey; Type: CONSTRAINT; Schema: memory; Owner: -
--

ALTER TABLE ONLY memory.index_scan
    ADD CONSTRAINT index_scan_pkey PRIMARY KEY (kind);


--
-- Name: index_stats_cursor index_stats_cursor_pkey; Type: CONSTRAINT; Schema: memory; Owner: -
--

ALTER TABLE ONLY memory.index_stats_cursor
    ADD CONSTRAINT index_stats_cursor_pkey PRIMARY KEY (singleton);


--
-- Name: learning_index_refs learning_index_refs_pkey; Type: CONSTRAINT; Schema: memory; Owner: -
--

ALTER TABLE ONLY memory.learning_index_refs
    ADD CONSTRAINT learning_index_refs_pkey PRIMARY KEY (actor_id, rule_id, revision);


--
-- Name: matter_claims matter_claims_pkey; Type: CONSTRAINT; Schema: memory; Owner: -
--

ALTER TABLE ONLY memory.matter_claims
    ADD CONSTRAINT matter_claims_pkey PRIMARY KEY (matter_id, claim_id);


--
-- Name: matter_revisions matter_revisions_pkey; Type: CONSTRAINT; Schema: memory; Owner: -
--

ALTER TABLE ONLY memory.matter_revisions
    ADD CONSTRAINT matter_revisions_pkey PRIMARY KEY (matter_id, revision);


--
-- Name: matters matters_pkey; Type: CONSTRAINT; Schema: memory; Owner: -
--

ALTER TABLE ONLY memory.matters
    ADD CONSTRAINT matters_pkey PRIMARY KEY (matter_id);


--
-- Name: model_steps model_steps_pkey; Type: CONSTRAINT; Schema: memory; Owner: -
--

ALTER TABLE ONLY memory.model_steps
    ADD CONSTRAINT model_steps_pkey PRIMARY KEY (step_id);


--
-- Name: recovery_due_claims recovery_due_claims_pkey; Type: CONSTRAINT; Schema: memory; Owner: -
--

ALTER TABLE ONLY memory.recovery_due_claims
    ADD CONSTRAINT recovery_due_claims_pkey PRIMARY KEY (lineage_id);


--
-- Name: relationship_details relationship_details_pkey; Type: CONSTRAINT; Schema: memory; Owner: -
--

ALTER TABLE ONLY memory.relationship_details
    ADD CONSTRAINT relationship_details_pkey PRIMARY KEY (detail_id);


--
-- Name: relationships relationships_pkey; Type: CONSTRAINT; Schema: memory; Owner: -
--

ALTER TABLE ONLY memory.relationships
    ADD CONSTRAINT relationships_pkey PRIMARY KEY (actor_id, person_id);


--
-- Name: search_vectors search_vectors_pkey; Type: CONSTRAINT; Schema: memory; Owner: -
--

ALTER TABLE ONLY memory.search_vectors
    ADD CONSTRAINT search_vectors_pkey PRIMARY KEY (profile_key, generation, kind, object_id, revision);


--
-- Name: semantic_links semantic_links_pkey; Type: CONSTRAINT; Schema: memory; Owner: -
--

ALTER TABLE ONLY memory.semantic_links
    ADD CONSTRAINT semantic_links_pkey PRIMARY KEY (link_id);


--
-- Name: statistics_outbox statistics_outbox_pkey; Type: CONSTRAINT; Schema: memory; Owner: -
--

ALTER TABLE ONLY memory.statistics_outbox
    ADD CONSTRAINT statistics_outbox_pkey PRIMARY KEY (batch_id);


--
-- Name: attempts attempts_pkey; Type: CONSTRAINT; Schema: models; Owner: -
--

ALTER TABLE ONLY models.attempts
    ADD CONSTRAINT attempts_pkey PRIMARY KEY (attempt_id);


--
-- Name: bindings bindings_pkey; Type: CONSTRAINT; Schema: models; Owner: -
--

ALTER TABLE ONLY models.bindings
    ADD CONSTRAINT bindings_pkey PRIMARY KEY (function, service_class, actor_id);


--
-- Name: cancellations cancellations_pkey; Type: CONSTRAINT; Schema: models; Owner: -
--

ALTER TABLE ONLY models.cancellations
    ADD CONSTRAINT cancellations_pkey PRIMARY KEY (attempt_id);


--
-- Name: configuration_reloads configuration_reloads_pkey; Type: CONSTRAINT; Schema: models; Owner: -
--

ALTER TABLE ONLY models.configuration_reloads
    ADD CONSTRAINT configuration_reloads_pkey PRIMARY KEY (event_id);


--
-- Name: embedding_profile embedding_profile_pkey; Type: CONSTRAINT; Schema: models; Owner: -
--

ALTER TABLE ONLY models.embedding_profile
    ADD CONSTRAINT embedding_profile_pkey PRIMARY KEY (singleton);


--
-- Name: providers providers_pkey; Type: CONSTRAINT; Schema: models; Owner: -
--

ALTER TABLE ONLY models.providers
    ADD CONSTRAINT providers_pkey PRIMARY KEY (provider_id);


--
-- Name: quota_cooldowns quota_cooldowns_pkey; Type: CONSTRAINT; Schema: models; Owner: -
--

ALTER TABLE ONLY models.quota_cooldowns
    ADD CONSTRAINT quota_cooldowns_pkey PRIMARY KEY (quota_group);


--
-- Name: quota_groups quota_groups_pkey; Type: CONSTRAINT; Schema: models; Owner: -
--

ALTER TABLE ONLY models.quota_groups
    ADD CONSTRAINT quota_groups_pkey PRIMARY KEY (quota_group);


--
-- Name: route_cooldowns route_cooldowns_pkey; Type: CONSTRAINT; Schema: models; Owner: -
--

ALTER TABLE ONLY models.route_cooldowns
    ADD CONSTRAINT route_cooldowns_pkey PRIMARY KEY (route_id);


--
-- Name: routes routes_pkey; Type: CONSTRAINT; Schema: models; Owner: -
--

ALTER TABLE ONLY models.routes
    ADD CONSTRAINT routes_pkey PRIMARY KEY (route_id);


--
-- Name: stream_events stream_events_pkey; Type: CONSTRAINT; Schema: models; Owner: -
--

ALTER TABLE ONLY models.stream_events
    ADD CONSTRAINT stream_events_pkey PRIMARY KEY (attempt_id, sequence);


--
-- Name: submissions submissions_pkey; Type: CONSTRAINT; Schema: models; Owner: -
--

ALTER TABLE ONLY models.submissions
    ADD CONSTRAINT submissions_pkey PRIMARY KEY (attempt_id);


--
-- Name: consumer_checkpoints consumer_checkpoints_pkey; Type: CONSTRAINT; Schema: operations; Owner: -
--

ALTER TABLE ONLY operations.consumer_checkpoints
    ADD CONSTRAINT consumer_checkpoints_pkey PRIMARY KEY (consumer);


--
-- Name: control_lineages control_lineages_pkey; Type: CONSTRAINT; Schema: operations; Owner: -
--

ALTER TABLE ONLY operations.control_lineages
    ADD CONSTRAINT control_lineages_pkey PRIMARY KEY (control_id);


--
-- Name: diagnostics diagnostics_pkey; Type: CONSTRAINT; Schema: operations; Owner: -
--

ALTER TABLE ONLY operations.diagnostics
    ADD CONSTRAINT diagnostics_pkey PRIMARY KEY (attempt_id);


--
-- Name: incident_causes incident_causes_pkey; Type: CONSTRAINT; Schema: operations; Owner: -
--

ALTER TABLE ONLY operations.incident_causes
    ADD CONSTRAINT incident_causes_pkey PRIMARY KEY (incident_id, episode, root_incident_id, root_episode);


--
-- Name: incident_events incident_events_pkey; Type: CONSTRAINT; Schema: operations; Owner: -
--

ALTER TABLE ONLY operations.incident_events
    ADD CONSTRAINT incident_events_pkey PRIMARY KEY (event_id);


--
-- Name: incident_targets incident_targets_pkey; Type: CONSTRAINT; Schema: operations; Owner: -
--

ALTER TABLE ONLY operations.incident_targets
    ADD CONSTRAINT incident_targets_pkey PRIMARY KEY (incident_id, episode, target_id);


--
-- Name: incidents incidents_fingerprint_key; Type: CONSTRAINT; Schema: operations; Owner: -
--

ALTER TABLE ONLY operations.incidents
    ADD CONSTRAINT incidents_fingerprint_key UNIQUE (fingerprint);


--
-- Name: incidents incidents_pkey; Type: CONSTRAINT; Schema: operations; Owner: -
--

ALTER TABLE ONLY operations.incidents
    ADD CONSTRAINT incidents_pkey PRIMARY KEY (incident_id);


--
-- Name: notification_outbox notification_outbox_pkey; Type: CONSTRAINT; Schema: operations; Owner: -
--

ALTER TABLE ONLY operations.notification_outbox
    ADD CONSTRAINT notification_outbox_pkey PRIMARY KEY (notification_id);


--
-- Name: recoveries recoveries_pkey; Type: CONSTRAINT; Schema: operations; Owner: -
--

ALTER TABLE ONLY operations.recoveries
    ADD CONSTRAINT recoveries_pkey PRIMARY KEY (attempt_id);


--
-- Name: settings settings_pkey; Type: CONSTRAINT; Schema: operations; Owner: -
--

ALTER TABLE ONLY operations.settings
    ADD CONSTRAINT settings_pkey PRIMARY KEY (singleton);


--
-- Name: statistic_buckets statistic_buckets_pkey; Type: CONSTRAINT; Schema: operations; Owner: -
--

ALTER TABLE ONLY operations.statistic_buckets
    ADD CONSTRAINT statistic_buckets_pkey PRIMARY KEY (grain, bucket, family, module, actor_id, route_id, state, metric);


--
-- Name: statistic_events statistic_events_pkey; Type: CONSTRAINT; Schema: operations; Owner: -
--

ALTER TABLE ONLY operations.statistic_events
    ADD CONSTRAINT statistic_events_pkey PRIMARY KEY (family, event_id);


--
-- Name: statistic_producers statistic_producers_pkey; Type: CONSTRAINT; Schema: operations; Owner: -
--

ALTER TABLE ONLY operations.statistic_producers
    ADD CONSTRAINT statistic_producers_pkey PRIMARY KEY (producer);


--
-- Name: procrastinate_events procrastinate_events_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.procrastinate_events
    ADD CONSTRAINT procrastinate_events_pkey PRIMARY KEY (id);


--
-- Name: procrastinate_jobs procrastinate_jobs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.procrastinate_jobs
    ADD CONSTRAINT procrastinate_jobs_pkey PRIMARY KEY (id);


--
-- Name: procrastinate_periodic_defers procrastinate_periodic_defers_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.procrastinate_periodic_defers
    ADD CONSTRAINT procrastinate_periodic_defers_pkey PRIMARY KEY (id);


--
-- Name: procrastinate_periodic_defers procrastinate_periodic_defers_unique; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.procrastinate_periodic_defers
    ADD CONSTRAINT procrastinate_periodic_defers_unique UNIQUE (task_name, periodic_id, defer_timestamp);


--
-- Name: procrastinate_workers procrastinate_workers_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.procrastinate_workers
    ADD CONSTRAINT procrastinate_workers_pkey PRIMARY KEY (id);


--
-- Name: access_settings access_settings_pkey; Type: CONSTRAINT; Schema: runtime; Owner: -
--

ALTER TABLE ONLY runtime.access_settings
    ADD CONSTRAINT access_settings_pkey PRIMARY KEY (singleton);


--
-- Name: commands commands_pkey; Type: CONSTRAINT; Schema: runtime; Owner: -
--

ALTER TABLE ONLY runtime.commands
    ADD CONSTRAINT commands_pkey PRIMARY KEY (principal_id, operation, client_request_id);


--
-- Name: credentials credentials_pkey; Type: CONSTRAINT; Schema: runtime; Owner: -
--

ALTER TABLE ONLY runtime.credentials
    ADD CONSTRAINT credentials_pkey PRIMARY KEY (reference);


--
-- Name: installation_generation installation_generation_pkey; Type: CONSTRAINT; Schema: runtime; Owner: -
--

ALTER TABLE ONLY runtime.installation_generation
    ADD CONSTRAINT installation_generation_pkey PRIMARY KEY (singleton);


--
-- Name: job_keys job_keys_pkey; Type: CONSTRAINT; Schema: runtime; Owner: -
--

ALTER TABLE ONLY runtime.job_keys
    ADD CONSTRAINT job_keys_pkey PRIMARY KEY (key);


--
-- Name: processes processes_pkey; Type: CONSTRAINT; Schema: runtime; Owner: -
--

ALTER TABLE ONLY runtime.processes
    ADD CONSTRAINT processes_pkey PRIMARY KEY (instance_id);


--
-- Name: projection_cursors projection_cursors_pkey; Type: CONSTRAINT; Schema: runtime; Owner: -
--

ALTER TABLE ONLY runtime.projection_cursors
    ADD CONSTRAINT projection_cursors_pkey PRIMARY KEY (consumer, subject_id);


--
-- Name: queue_service queue_service_pkey; Type: CONSTRAINT; Schema: runtime; Owner: -
--

ALTER TABLE ONLY runtime.queue_service
    ADD CONSTRAINT queue_service_pkey PRIMARY KEY (data_generation, queue_name, domain, subject_id);


--
-- Name: queue_service_runs queue_service_runs_pkey; Type: CONSTRAINT; Schema: runtime; Owner: -
--

ALTER TABLE ONLY runtime.queue_service_runs
    ADD CONSTRAINT queue_service_runs_pkey PRIMARY KEY (job_id);


--
-- Name: recompute_attempts recompute_attempts_pkey; Type: CONSTRAINT; Schema: runtime; Owner: -
--

ALTER TABLE ONLY runtime.recompute_attempts
    ADD CONSTRAINT recompute_attempts_pkey PRIMARY KEY (claim_token);


--
-- Name: recompute_changes recompute_changes_pkey; Type: CONSTRAINT; Schema: runtime; Owner: -
--

ALTER TABLE ONLY runtime.recompute_changes
    ADD CONSTRAINT recompute_changes_pkey PRIMARY KEY (data_generation, domain, work_key, change_id);


--
-- Name: recompute_gap_history recompute_gap_history_pkey; Type: CONSTRAINT; Schema: runtime; Owner: -
--

ALTER TABLE ONLY runtime.recompute_gap_history
    ADD CONSTRAINT recompute_gap_history_pkey PRIMARY KEY (id);


--
-- Name: recompute_gaps recompute_gaps_pkey; Type: CONSTRAINT; Schema: runtime; Owner: -
--

ALTER TABLE ONLY runtime.recompute_gaps
    ADD CONSTRAINT recompute_gaps_pkey PRIMARY KEY (data_generation, domain, work_key, material_id);


--
-- Name: recompute_work recompute_work_pkey; Type: CONSTRAINT; Schema: runtime; Owner: -
--

ALTER TABLE ONLY runtime.recompute_work
    ADD CONSTRAINT recompute_work_pkey PRIMARY KEY (data_generation, domain, work_key);


--
-- Name: recovery_attempt_observations recovery_attempt_observations_pkey; Type: CONSTRAINT; Schema: runtime; Owner: -
--

ALTER TABLE ONLY runtime.recovery_attempt_observations
    ADD CONSTRAINT recovery_attempt_observations_pkey PRIMARY KEY (id);


--
-- Name: recovery_attempts recovery_attempts_pkey; Type: CONSTRAINT; Schema: runtime; Owner: -
--

ALTER TABLE ONLY runtime.recovery_attempts
    ADD CONSTRAINT recovery_attempts_pkey PRIMARY KEY (attempt_id);


--
-- Name: recovery_dependencies recovery_dependencies_pkey; Type: CONSTRAINT; Schema: runtime; Owner: -
--

ALTER TABLE ONLY runtime.recovery_dependencies
    ADD CONSTRAINT recovery_dependencies_pkey PRIMARY KEY (data_generation, dependency);


--
-- Name: recovery_dependency_changes recovery_dependency_changes_pkey; Type: CONSTRAINT; Schema: runtime; Owner: -
--

ALTER TABLE ONLY runtime.recovery_dependency_changes
    ADD CONSTRAINT recovery_dependency_changes_pkey PRIMARY KEY (id);


--
-- Name: recovery_lineages recovery_lineages_pkey; Type: CONSTRAINT; Schema: runtime; Owner: -
--

ALTER TABLE ONLY runtime.recovery_lineages
    ADD CONSTRAINT recovery_lineages_pkey PRIMARY KEY (lineage_id);


--
-- Name: alias_suppression alias_suppression_pkey; Type: CONSTRAINT; Schema: sources; Owner: -
--

ALTER TABLE ONLY sources.alias_suppression
    ADD CONSTRAINT alias_suppression_pkey PRIMARY KEY (alias_key);


--
-- Name: archive_records archive_records_pkey; Type: CONSTRAINT; Schema: sources; Owner: -
--

ALTER TABLE ONLY sources.archive_records
    ADD CONSTRAINT archive_records_pkey PRIMARY KEY (archive_key);


--
-- Name: archive_records archive_records_source_id_key; Type: CONSTRAINT; Schema: sources; Owner: -
--

ALTER TABLE ONLY sources.archive_records
    ADD CONSTRAINT archive_records_source_id_key UNIQUE (source_id);


--
-- Name: changes changes_pkey; Type: CONSTRAINT; Schema: sources; Owner: -
--

ALTER TABLE ONLY sources.changes
    ADD CONSTRAINT changes_pkey PRIMARY KEY (sequence);


--
-- Name: evidence_members evidence_members_pkey; Type: CONSTRAINT; Schema: sources; Owner: -
--

ALTER TABLE ONLY sources.evidence_members
    ADD CONSTRAINT evidence_members_pkey PRIMARY KEY (evidence_id, role, group_index, ref_key);


--
-- Name: evidence_sets evidence_sets_pkey; Type: CONSTRAINT; Schema: sources; Owner: -
--

ALTER TABLE ONLY sources.evidence_sets
    ADD CONSTRAINT evidence_sets_pkey PRIMARY KEY (evidence_id);


--
-- Name: forgotten_fragments forgotten_fragments_pkey; Type: CONSTRAINT; Schema: sources; Owner: -
--

ALTER TABLE ONLY sources.forgotten_fragments
    ADD CONSTRAINT forgotten_fragments_pkey PRIMARY KEY (source_id, revision, start_position, end_position);


--
-- Name: fragment_barriers fragment_barriers_pkey; Type: CONSTRAINT; Schema: sources; Owner: -
--

ALTER TABLE ONLY sources.fragment_barriers
    ADD CONSTRAINT fragment_barriers_pkey PRIMARY KEY (source_key);


--
-- Name: media_supplements media_supplements_pkey; Type: CONSTRAINT; Schema: sources; Owner: -
--

ALTER TABLE ONLY sources.media_supplements
    ADD CONSTRAINT media_supplements_pkey PRIMARY KEY (source_id, revision);


--
-- Name: originals originals_pkey; Type: CONSTRAINT; Schema: sources; Owner: -
--

ALTER TABLE ONLY sources.originals
    ADD CONSTRAINT originals_pkey PRIMARY KEY (source_id);


--
-- Name: originals originals_source_key_key; Type: CONSTRAINT; Schema: sources; Owner: -
--

ALTER TABLE ONLY sources.originals
    ADD CONSTRAINT originals_source_key_key UNIQUE (source_key);


--
-- Name: source_aliases source_aliases_pkey; Type: CONSTRAINT; Schema: sources; Owner: -
--

ALTER TABLE ONLY sources.source_aliases
    ADD CONSTRAINT source_aliases_pkey PRIMARY KEY (alias_key, source_id, revision);


--
-- Name: supports supports_pkey; Type: CONSTRAINT; Schema: sources; Owner: -
--

ALTER TABLE ONLY sources.supports
    ADD CONSTRAINT supports_pkey PRIMARY KEY (source_id, revision, group_id, support_key);


--
-- Name: suppression suppression_pkey; Type: CONSTRAINT; Schema: sources; Owner: -
--

ALTER TABLE ONLY sources.suppression
    ADD CONSTRAINT suppression_pkey PRIMARY KEY (source_key);


--
-- Name: versions versions_pkey; Type: CONSTRAINT; Schema: sources; Owner: -
--

ALTER TABLE ONLY sources.versions
    ADD CONSTRAINT versions_pkey PRIMARY KEY (source_id, revision);


--
-- Name: artifacts_media_scope; Type: INDEX; Schema: capabilities; Owner: -
--

CREATE INDEX artifacts_media_scope ON capabilities.artifacts USING btree (media_id, created_at DESC);


--
-- Name: capability_actor_actions; Type: INDEX; Schema: capabilities; Owner: -
--

CREATE INDEX capability_actor_actions ON capabilities.actions USING btree (((action ->> 'actor_id'::text)), created_at, id);


--
-- Name: capability_consumption_source; Type: INDEX; Schema: capabilities; Owner: -
--

CREATE INDEX capability_consumption_source ON capabilities.consumptions USING btree (source_id);


--
-- Name: capability_running_owner; Type: INDEX; Schema: capabilities; Owner: -
--

CREATE INDEX capability_running_owner ON capabilities.actions USING btree (execution_owner) WHERE ((receipt ->> 'execution'::text) = 'running'::text);


--
-- Name: capability_source_dependants; Type: INDEX; Schema: capabilities; Owner: -
--

CREATE INDEX capability_source_dependants ON capabilities.source_dependencies USING btree (source_id, owner_kind, owner_id);


--
-- Name: capability_source_revision_dependants; Type: INDEX; Schema: capabilities; Owner: -
--

CREATE INDEX capability_source_revision_dependants ON capabilities.source_dependencies USING btree (source_id, source_revision, owner_kind, owner_id);


--
-- Name: reaction_lexical; Type: INDEX; Schema: capabilities; Owner: -
--

CREATE INDEX reaction_lexical ON capabilities.reaction_assets USING gin (to_tsvector('simple'::regconfig, search_text));


--
-- Name: reaction_original_media; Type: INDEX; Schema: capabilities; Owner: -
--

CREATE INDEX reaction_original_media ON capabilities.reaction_assets USING btree ((((body -> 'media'::text) ->> 'media_id'::text)));


--
-- Name: jobs_audio_recording; Type: INDEX; Schema: capability_media; Owner: -
--

CREATE INDEX jobs_audio_recording ON capability_media.jobs USING btree ((((body)::jsonb #>> '{recording_id}'::text[])), (((body)::jsonb #>> '{destination_directory}'::text[])), created, id) WHERE (((body)::jsonb #>> '{media_kind}'::text[]) = 'audio'::text);


--
-- Name: jobs_order; Type: INDEX; Schema: capability_media; Owner: -
--

CREATE INDEX jobs_order ON capability_media.jobs USING btree (((created)::integer) DESC, id DESC);


--
-- Name: jobs_queue; Type: INDEX; Schema: capability_media; Owner: -
--

CREATE INDEX jobs_queue ON capability_media.jobs USING btree (state, lease, created);


--
-- Name: documents_project_page; Type: INDEX; Schema: capability_projects; Owner: -
--

CREATE INDEX documents_project_page ON capability_projects.documents USING btree (project_id, document_id);


--
-- Name: experiences_actor_page; Type: INDEX; Schema: capability_projects; Owner: -
--

CREATE INDEX experiences_actor_page ON capability_projects.experiences USING btree (actor_id, experience_id);


--
-- Name: lessons_project_page; Type: INDEX; Schema: capability_projects; Owner: -
--

CREATE INDEX lessons_project_page ON capability_projects.lessons USING btree (project_id, lesson_id);


--
-- Name: notes_project_page; Type: INDEX; Schema: capability_projects; Owner: -
--

CREATE INDEX notes_project_page ON capability_projects.notes USING btree (project_id, note_id);


--
-- Name: project_action_documents; Type: INDEX; Schema: capability_projects; Owner: -
--

CREATE INDEX project_action_documents ON capability_projects.document_versions USING btree (action_id);


--
-- Name: project_action_experience; Type: INDEX; Schema: capability_projects; Owner: -
--

CREATE INDEX project_action_experience ON capability_projects.experience_versions USING btree (action_id);


--
-- Name: project_action_lessons; Type: INDEX; Schema: capability_projects; Owner: -
--

CREATE INDEX project_action_lessons ON capability_projects.lesson_versions USING btree (action_id);


--
-- Name: project_action_notes; Type: INDEX; Schema: capability_projects; Owner: -
--

CREATE INDEX project_action_notes ON capability_projects.note_versions USING btree (action_id);


--
-- Name: project_action_registry; Type: INDEX; Schema: capability_projects; Owner: -
--

CREATE INDEX project_action_registry ON capability_projects.registry_versions USING btree (action_id);


--
-- Name: projects_actor_page; Type: INDEX; Schema: capability_projects; Owner: -
--

CREATE INDEX projects_actor_page ON capability_projects.projects USING btree (actor_id, project_id);


--
-- Name: channel_action_calls; Type: INDEX; Schema: channels; Owner: -
--

CREATE INDEX channel_action_calls ON channels.calls USING btree (action_id, created_at, id);


--
-- Name: channel_event_sources; Type: INDEX; Schema: channels; Owner: -
--

CREATE INDEX channel_event_sources ON channels.event_sources USING btree (source_id, source_revision, event_key);


--
-- Name: channel_expression_calls; Type: INDEX; Schema: channels; Owner: -
--

CREATE INDEX channel_expression_calls ON channels.calls USING btree (expression_id, created_at, id);


--
-- Name: channel_source_calls; Type: INDEX; Schema: channels; Owner: -
--

CREATE INDEX channel_source_calls ON channels.source_dependencies USING btree (source_id, source_revision, call_id);


--
-- Name: channel_supplements_pending; Type: INDEX; Schema: channels; Owner: -
--

CREATE INDEX channel_supplements_pending ON channels.media_supplements USING btree (state, next_due);


--
-- Name: affect_scope_time; Type: INDEX; Schema: companion; Owner: -
--

CREATE INDEX affect_scope_time ON companion.affect_events USING btree (actor_id, occurred_at DESC);


--
-- Name: expression_examples_scene; Type: INDEX; Schema: companion; Owner: -
--

CREATE INDEX expression_examples_scene ON companion.expression_examples USING btree (actor_id, conversation_id, person_id, state);


--
-- Name: expression_examples_sources; Type: INDEX; Schema: companion; Owner: -
--

CREATE INDEX expression_examples_sources ON companion.expression_examples USING gin (source_refs);


--
-- Name: growth_input_episode_status; Type: INDEX; Schema: companion; Owner: -
--

CREATE INDEX growth_input_episode_status ON companion.growth_inputs USING btree (actor_id, episode_id);


--
-- Name: growth_pending_versions; Type: INDEX; Schema: companion; Owner: -
--

CREATE INDEX growth_pending_versions ON companion.growth_inputs USING btree (actor_id, scope_key, sequence, episode_id) WHERE (reviewed_revision < revision);


--
-- Name: growth_retry_due; Type: INDEX; Schema: companion; Owner: -
--

CREATE INDEX growth_retry_due ON companion.growth_retry_batches USING btree (due_at, actor_id, scope_key) WHERE (state = ANY (ARRAY['waiting'::text, 'active'::text]));


--
-- Name: growth_statistics_pending; Type: INDEX; Schema: companion; Owner: -
--

CREATE INDEX growth_statistics_pending ON companion.growth_statistics_outbox USING btree (created_at, event_id, revision) WHERE (NOT delivered);


--
-- Name: habit_support_event; Type: INDEX; Schema: companion; Owner: -
--

CREATE INDEX habit_support_event ON companion.habit_supports USING btree (event_id, proposal_id, rule_id);


--
-- Name: habit_support_source; Type: INDEX; Schema: companion; Owner: -
--

CREATE INDEX habit_support_source ON companion.habit_supports USING gin (source_ids);


--
-- Name: learning_reflection_actor; Type: INDEX; Schema: companion; Owner: -
--

CREATE INDEX learning_reflection_actor ON companion.learning_reflections USING btree (actor_id, occurred_at DESC);


--
-- Name: learning_reflection_sources; Type: INDEX; Schema: companion; Owner: -
--

CREATE INDEX learning_reflection_sources ON companion.learning_reflections USING gin (source_refs);


--
-- Name: style_actor_scope; Type: INDEX; Schema: companion; Owner: -
--

CREATE INDEX style_actor_scope ON companion.style_rules USING btree (actor_id, person_id, state);


--
-- Name: context_selections_turn; Type: INDEX; Schema: conversation; Owner: -
--

CREATE INDEX context_selections_turn ON conversation.context_selections USING btree (turn_id, round);


--
-- Name: messages_conversation; Type: INDEX; Schema: conversation; Owner: -
--

CREATE INDEX messages_conversation ON conversation.messages USING btree (conversation_id, id DESC);


--
-- Name: one_group_participation; Type: INDEX; Schema: conversation; Owner: -
--

CREATE UNIQUE INDEX one_group_participation ON conversation.turns USING btree (actor_id, conversation_id) WHERE (participation_state = ANY (ARRAY['waiting'::text, 'evaluating'::text]));


--
-- Name: turns_pending; Type: INDEX; Schema: conversation; Owner: -
--

CREATE INDEX turns_pending ON conversation.turns USING btree (actor_id, conversation_id, received_at) WHERE (state = ANY (ARRAY['queued'::text, 'running'::text]));


--
-- Name: turns_running; Type: INDEX; Schema: conversation; Owner: -
--

CREATE INDEX turns_running ON conversation.turns USING btree (actor_id, conversation_id) WHERE (state = 'running'::text);


--
-- Name: conversation_members_person; Type: INDEX; Schema: identity; Owner: -
--

CREATE INDEX conversation_members_person ON identity.conversation_members USING btree (conversation_id, person_id);


--
-- Name: person_alias_lookup; Type: INDEX; Schema: identity; Owner: -
--

CREATE INDEX person_alias_lookup ON identity.person_aliases USING btree (lower(alias));


--
-- Name: scopes_actor_person; Type: INDEX; Schema: identity; Owner: -
--

CREATE INDEX scopes_actor_person ON identity.scopes USING btree (actor_id, person_id, kind);


--
-- Name: sessions_expiry; Type: INDEX; Schema: identity; Owner: -
--

CREATE INDEX sessions_expiry ON identity.sessions USING btree (expires_at);


--
-- Name: commitments_due; Type: INDEX; Schema: life; Owner: -
--

CREATE INDEX commitments_due ON life.commitments USING btree (due_at) WHERE (state = ANY (ARRAY['scheduled'::text, 'waiting'::text]));


--
-- Name: contacts_person_time; Type: INDEX; Schema: life; Owner: -
--

CREATE INDEX contacts_person_time ON life.contacts USING btree (actor_id, person_id, delivered_at DESC);


--
-- Name: day_slots_due; Type: INDEX; Schema: life; Owner: -
--

CREATE INDEX day_slots_due ON life.day_slots USING btree (actor_id, starts_at) WHERE ((kind = 'activity'::text) AND (state = 'planned'::text));


--
-- Name: generation_recipes_sources; Type: INDEX; Schema: life; Owner: -
--

CREATE INDEX generation_recipes_sources ON life.generation_recipes USING gin (source_refs);


--
-- Name: claim_derivations; Type: INDEX; Schema: memory; Owner: -
--

CREATE INDEX claim_derivations ON memory.claim_revisions USING gin (derivation_refs);


--
-- Name: claim_missing_index; Type: INDEX; Schema: memory; Owner: -
--

CREATE INDEX claim_missing_index ON memory.claim_revisions USING btree (claim_id, revision) WHERE ((state = 'active'::text) AND (current_status = 'current'::text) AND ((embedding IS NULL) OR (lexical = ''::text)));


--
-- Name: claim_revision_recording; Type: INDEX; Schema: memory; Owner: -
--

CREATE INDEX claim_revision_recording ON memory.claim_revisions USING btree (claim_id, recorded_at, revision);


--
-- Name: claims_embedding; Type: INDEX; Schema: memory; Owner: -
--

CREATE INDEX claims_embedding ON memory.claim_revisions USING hnsw (embedding public.vector_cosine_ops) WHERE (embedding IS NOT NULL);


--
-- Name: claims_lexical; Type: INDEX; Schema: memory; Owner: -
--

CREATE INDEX claims_lexical ON memory.claim_revisions USING gin (search_vector);


--
-- Name: claims_scopes; Type: INDEX; Schema: memory; Owner: -
--

CREATE INDEX claims_scopes ON memory.claim_revisions USING gin (scope_ids);


--
-- Name: claims_sources; Type: INDEX; Schema: memory; Owner: -
--

CREATE INDEX claims_sources ON memory.claim_revisions USING gin (source_refs);


--
-- Name: claims_subject_current; Type: INDEX; Schema: memory; Owner: -
--

CREATE INDEX claims_subject_current ON memory.claim_revisions USING btree (subject_id, predicate, state, observed_at DESC);


--
-- Name: consolidation_attempt_candidates; Type: INDEX; Schema: memory; Owner: -
--

CREATE INDEX consolidation_attempt_candidates ON memory.consolidation_attempts USING btree (batch_id, number) WHERE (rejected_error IS NULL);


--
-- Name: consolidation_compilation_selected; Type: INDEX; Schema: memory; Owner: -
--

CREATE UNIQUE INDEX consolidation_compilation_selected ON memory.consolidation_compilations USING btree (batch_id) WHERE selected;


--
-- Name: consolidation_dependencies; Type: INDEX; Schema: memory; Owner: -
--

CREATE INDEX consolidation_dependencies ON memory.consolidation_batches USING gin (dependency_refs);


--
-- Name: consolidation_due; Type: INDEX; Schema: memory; Owner: -
--

CREATE INDEX consolidation_due ON memory.consolidation_state USING btree (wake_due_at, partition_id);


--
-- Name: consolidation_one_active; Type: INDEX; Schema: memory; Owner: -
--

CREATE UNIQUE INDEX consolidation_one_active ON memory.consolidation_batches USING btree (partition_id) WHERE (status = ANY (ARRAY['prepared'::text, 'waiting_model'::text, 'ready'::text]));


--
-- Name: consolidation_quarantined; Type: INDEX; Schema: memory; Owner: -
--

CREATE INDEX consolidation_quarantined ON memory.consolidation_batches USING btree (partition_id, updated_at) WHERE (status = 'quarantined'::text);


--
-- Name: consolidation_reorganization_pending; Type: INDEX; Schema: memory; Owner: -
--

CREATE INDEX consolidation_reorganization_pending ON memory.consolidation_reorganization_parts USING btree (root_batch_id, created_at, ordinal) WHERE (state = 'pending'::text);


--
-- Name: consolidation_retry_due; Type: INDEX; Schema: memory; Owner: -
--

CREATE INDEX consolidation_retry_due ON memory.consolidation_batches USING btree (retry_due_at, partition_id) WHERE (status = 'quarantined'::text);


--
-- Name: consolidation_status; Type: INDEX; Schema: memory; Owner: -
--

CREATE INDEX consolidation_status ON memory.consolidation_batches USING btree (partition_id, status, started_at);


--
-- Name: episode_missing_index; Type: INDEX; Schema: memory; Owner: -
--

CREATE INDEX episode_missing_index ON memory.episodes USING btree (episode_id, revision) WHERE (active AND ((embedding IS NULL) OR (lexical = ''::text)));


--
-- Name: episode_original_terms_search; Type: INDEX; Schema: memory; Owner: -
--

CREATE INDEX episode_original_terms_search ON memory.episode_original_terms USING gin (search_vector);


--
-- Name: episode_revisions_known; Type: INDEX; Schema: memory; Owner: -
--

CREATE INDEX episode_revisions_known ON memory.episode_revisions USING btree (episode_id, recorded_at, revision);


--
-- Name: episode_revisions_sources; Type: INDEX; Schema: memory; Owner: -
--

CREATE INDEX episode_revisions_sources ON memory.episode_revisions USING gin (source_refs);


--
-- Name: episode_supplement_original; Type: INDEX; Schema: memory; Owner: -
--

CREATE INDEX episode_supplement_original ON memory.episode_supplements USING gin (original_ref);


--
-- Name: episode_supplement_source; Type: INDEX; Schema: memory; Owner: -
--

CREATE INDEX episode_supplement_source ON memory.episode_supplements USING gin (supplement_ref);


--
-- Name: episodes_actor_sequence; Type: INDEX; Schema: memory; Owner: -
--

CREATE INDEX episodes_actor_sequence ON memory.episodes USING btree (actor_id, sequence);


--
-- Name: episodes_actor_time; Type: INDEX; Schema: memory; Owner: -
--

CREATE INDEX episodes_actor_time ON memory.episodes USING btree (actor_id, occurred_at, sequence);


--
-- Name: episodes_embedding; Type: INDEX; Schema: memory; Owner: -
--

CREATE INDEX episodes_embedding ON memory.episodes USING hnsw (embedding public.vector_cosine_ops) WHERE (embedding IS NOT NULL);


--
-- Name: episodes_lexical; Type: INDEX; Schema: memory; Owner: -
--

CREATE INDEX episodes_lexical ON memory.episodes USING gin (search_vector);


--
-- Name: episodes_pending_partition; Type: INDEX; Schema: memory; Owner: -
--

CREATE INDEX episodes_pending_partition ON memory.episodes USING btree (actor_id, consolidation_partition_id, sequence) WHERE (active AND (extraction_state = 'pending'::text));


--
-- Name: episodes_sources; Type: INDEX; Schema: memory; Owner: -
--

CREATE INDEX episodes_sources ON memory.episodes USING gin (source_refs);


--
-- Name: episodes_unindexed; Type: INDEX; Schema: memory; Owner: -
--

CREATE INDEX episodes_unindexed ON memory.episodes USING btree (episode_id) WHERE (active AND (lexical = ''::text));


--
-- Name: index_jobs_due; Type: INDEX; Schema: memory; Owner: -
--

CREATE INDEX index_jobs_due ON memory.index_jobs USING btree (next_due, index_id) WHERE (state = ANY (ARRAY['pending'::text, 'running'::text, 'observing'::text, 'retry_wait'::text]));


--
-- Name: learning_refs_actor_recorded; Type: INDEX; Schema: memory; Owner: -
--

CREATE INDEX learning_refs_actor_recorded ON memory.learning_index_refs USING btree (actor_id, recorded_at, rule_id, revision);


--
-- Name: learning_refs_search; Type: INDEX; Schema: memory; Owner: -
--

CREATE INDEX learning_refs_search ON memory.learning_index_refs USING gin (search_vector);


--
-- Name: matter_history_sources; Type: INDEX; Schema: memory; Owner: -
--

CREATE INDEX matter_history_sources ON memory.matter_revisions USING gin (source_refs);


--
-- Name: matter_history_time; Type: INDEX; Schema: memory; Owner: -
--

CREATE INDEX matter_history_time ON memory.matter_revisions USING btree (matter_id, recorded_at, revision);


--
-- Name: matter_missing_index; Type: INDEX; Schema: memory; Owner: -
--

CREATE INDEX matter_missing_index ON memory.matters USING btree (matter_id, revision) WHERE (active AND ((embedding IS NULL) OR (lexical = ''::text)));


--
-- Name: matters_actor_current; Type: INDEX; Schema: memory; Owner: -
--

CREATE INDEX matters_actor_current ON memory.matters USING btree (actor_id, state, due_at);


--
-- Name: matters_embedding; Type: INDEX; Schema: memory; Owner: -
--

CREATE INDEX matters_embedding ON memory.matters USING hnsw (embedding public.vector_cosine_ops) WHERE (embedding IS NOT NULL);


--
-- Name: matters_lexical; Type: INDEX; Schema: memory; Owner: -
--

CREATE INDEX matters_lexical ON memory.matters USING gin (search_vector);


--
-- Name: matters_sources; Type: INDEX; Schema: memory; Owner: -
--

CREATE INDEX matters_sources ON memory.matters USING gin (source_refs);


--
-- Name: matters_unindexed; Type: INDEX; Schema: memory; Owner: -
--

CREATE INDEX matters_unindexed ON memory.matters USING btree (matter_id) WHERE (active AND (lexical = ''::text));


--
-- Name: model_steps_sources; Type: INDEX; Schema: memory; Owner: -
--

CREATE INDEX model_steps_sources ON memory.model_steps USING gin (((request -> 'source_refs'::text)));


--
-- Name: relationship_details_pair; Type: INDEX; Schema: memory; Owner: -
--

CREATE INDEX relationship_details_pair ON memory.relationship_details USING btree (actor_id, person_id, kind);


--
-- Name: relationship_details_sources; Type: INDEX; Schema: memory; Owner: -
--

CREATE INDEX relationship_details_sources ON memory.relationship_details USING gin (source_refs);


--
-- Name: relationships_sources; Type: INDEX; Schema: memory; Owner: -
--

CREATE INDEX relationships_sources ON memory.relationships USING gin (source_refs);


--
-- Name: search_vectors_hnsw; Type: INDEX; Schema: memory; Owner: -
--

CREATE INDEX search_vectors_hnsw ON ONLY memory.search_vectors USING hnsw (embedding public.vector_cosine_ops);


--
-- Name: semantic_links_from; Type: INDEX; Schema: memory; Owner: -
--

CREATE INDEX semantic_links_from ON memory.semantic_links USING btree (actor_id, from_kind, from_id, from_revision);


--
-- Name: semantic_links_scopes; Type: INDEX; Schema: memory; Owner: -
--

CREATE INDEX semantic_links_scopes ON memory.semantic_links USING gin (scope_ids);


--
-- Name: semantic_links_sources; Type: INDEX; Schema: memory; Owner: -
--

CREATE INDEX semantic_links_sources ON memory.semantic_links USING gin (source_refs);


--
-- Name: semantic_links_time; Type: INDEX; Schema: memory; Owner: -
--

CREATE INDEX semantic_links_time ON memory.semantic_links USING btree (recorded_at, link_id);


--
-- Name: semantic_links_to; Type: INDEX; Schema: memory; Owner: -
--

CREATE INDEX semantic_links_to ON memory.semantic_links USING btree (actor_id, to_kind, to_id, to_revision);


--
-- Name: attempts_owner; Type: INDEX; Schema: models; Owner: -
--

CREATE INDEX attempts_owner ON models.attempts USING btree (principal_id, queued_at DESC);


--
-- Name: attempts_rate; Type: INDEX; Schema: models; Owner: -
--

CREATE INDEX attempts_rate ON models.attempts USING btree (quota_group, started_at) WHERE (started_at IS NOT NULL);


--
-- Name: attempts_resource; Type: INDEX; Schema: models; Owner: -
--

CREATE INDEX attempts_resource ON models.attempts USING btree (quota_group, status, hold_until);


--
-- Name: attempts_sources; Type: INDEX; Schema: models; Owner: -
--

CREATE INDEX attempts_sources ON models.attempts USING gin (source_refs jsonb_path_ops);


--
-- Name: models_configuration_reloads_pending; Type: INDEX; Schema: models; Owner: -
--

CREATE INDEX models_configuration_reloads_pending ON models.configuration_reloads USING btree (created_at, event_id) WHERE (state = 'pending'::text);


--
-- Name: models_submissions_completion_pending; Type: INDEX; Schema: models; Owner: -
--

CREATE INDEX models_submissions_completion_pending ON models.submissions USING btree (attempt_id) WHERE ((completion IS NOT NULL) AND (completion_enqueued_at IS NULL));


--
-- Name: incidents_open; Type: INDEX; Schema: operations; Owner: -
--

CREATE INDEX incidents_open ON operations.incidents USING btree (state, last_seen DESC);


--
-- Name: notification_due; Type: INDEX; Schema: operations; Owner: -
--

CREATE INDEX notification_due ON operations.notification_outbox USING btree (due_at) WHERE (status = ANY (ARRAY['pending'::text, 'sending'::text]));


--
-- Name: notification_incident_episode; Type: INDEX; Schema: operations; Owner: -
--

CREATE INDEX notification_incident_episode ON operations.notification_outbox USING btree (incident_id, episode, kind, channel, target);


--
-- Name: statistic_buckets_range; Type: INDEX; Schema: operations; Owner: -
--

CREATE INDEX statistic_buckets_range ON operations.statistic_buckets USING btree (grain, bucket);


--
-- Name: idx_procrastinate_jobs_worker_not_null; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_procrastinate_jobs_worker_not_null ON public.procrastinate_jobs USING btree (worker_id) WHERE ((worker_id IS NOT NULL) AND (status = 'doing'::public.procrastinate_job_status));


--
-- Name: idx_procrastinate_workers_last_heartbeat; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_procrastinate_workers_last_heartbeat ON public.procrastinate_workers USING btree (last_heartbeat);


--
-- Name: procrastinate_events_job_id_fkey_v1; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX procrastinate_events_job_id_fkey_v1 ON public.procrastinate_events USING btree (job_id);


--
-- Name: procrastinate_jobs_id_lock_idx_v1; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX procrastinate_jobs_id_lock_idx_v1 ON public.procrastinate_jobs USING btree (id, lock) WHERE (status = ANY (ARRAY['todo'::public.procrastinate_job_status, 'doing'::public.procrastinate_job_status]));


--
-- Name: procrastinate_jobs_lock_idx_v1; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX procrastinate_jobs_lock_idx_v1 ON public.procrastinate_jobs USING btree (lock) WHERE (status = 'doing'::public.procrastinate_job_status);


--
-- Name: procrastinate_jobs_priority_idx_v1; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX procrastinate_jobs_priority_idx_v1 ON public.procrastinate_jobs USING btree (priority DESC, id) WHERE (status = 'todo'::public.procrastinate_job_status);


--
-- Name: procrastinate_jobs_queue_name_idx_v1; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX procrastinate_jobs_queue_name_idx_v1 ON public.procrastinate_jobs USING btree (queue_name);


--
-- Name: procrastinate_jobs_queueing_lock_idx_v1; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX procrastinate_jobs_queueing_lock_idx_v1 ON public.procrastinate_jobs USING btree (queueing_lock) WHERE (status = 'todo'::public.procrastinate_job_status);


--
-- Name: procrastinate_periodic_defers_job_id_fkey_v1; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX procrastinate_periodic_defers_job_id_fkey_v1 ON public.procrastinate_periodic_defers USING btree (job_id);


--
-- Name: job_keys_job_id; Type: INDEX; Schema: runtime; Owner: -
--

CREATE INDEX job_keys_job_id ON runtime.job_keys USING btree (job_id);


--
-- Name: job_keys_subject; Type: INDEX; Schema: runtime; Owner: -
--

CREATE INDEX job_keys_subject ON runtime.job_keys USING btree (subject_id, matter_id);


--
-- Name: queue_service_active_subject; Type: INDEX; Schema: runtime; Owner: -
--

CREATE INDEX queue_service_active_subject ON runtime.queue_service_runs USING btree (data_generation, queue_name, domain, subject_id);


--
-- Name: recompute_claim_expiry; Type: INDEX; Schema: runtime; Owner: -
--

CREATE INDEX recompute_claim_expiry ON runtime.recompute_work USING btree (claim_expires_at) WHERE (claim_token IS NOT NULL);


--
-- Name: recompute_due; Type: INDEX; Schema: runtime; Owner: -
--

CREATE INDEX recompute_due ON runtime.recompute_work USING btree (due_at) WHERE (dirty_revision > processed_revision);


--
-- Name: recovery_lineages_due; Type: INDEX; Schema: runtime; Owner: -
--

CREATE INDEX recovery_lineages_due ON runtime.recovery_lineages USING btree (due_at, domain, subject_id) WHERE ((due_at IS NOT NULL) AND (NOT paused) AND (NOT cancelled));


--
-- Name: archive_aliases; Type: INDEX; Schema: sources; Owner: -
--

CREATE INDEX archive_aliases ON sources.archive_records USING gin (alias_keys);


--
-- Name: evidence_members_reverse; Type: INDEX; Schema: sources; Owner: -
--

CREATE INDEX evidence_members_reverse ON sources.evidence_members USING btree (source_id, revision);


--
-- Name: source_aliases_reverse; Type: INDEX; Schema: sources; Owner: -
--

CREATE INDEX source_aliases_reverse ON sources.source_aliases USING btree (source_id, revision, alias_key);


--
-- Name: sources_evidence_unit; Type: INDEX; Schema: sources; Owner: -
--

CREATE INDEX sources_evidence_unit ON sources.versions USING btree (evidence_unit);


--
-- Name: sources_scope; Type: INDEX; Schema: sources; Owner: -
--

CREATE INDEX sources_scope ON sources.versions USING gin (scope_ids);


--
-- Name: supports_reverse; Type: INDEX; Schema: sources; Owner: -
--

CREATE INDEX supports_reverse ON sources.supports USING btree (support_source_id, support_revision);


--
-- Name: claim_revisions search_vectors_claim_purge; Type: TRIGGER; Schema: memory; Owner: -
--

CREATE TRIGGER search_vectors_claim_purge AFTER UPDATE OF state ON memory.claim_revisions FOR EACH ROW WHEN (((old.state = 'active'::text) AND (new.state <> 'active'::text))) EXECUTE FUNCTION memory.purge_search_vectors();


--
-- Name: episodes search_vectors_episode_changed; Type: TRIGGER; Schema: memory; Owner: -
--

CREATE TRIGGER search_vectors_episode_changed AFTER UPDATE OF summary, supplement_refs ON memory.episodes FOR EACH ROW EXECUTE FUNCTION memory.invalidate_episode_vector_on_change();


--
-- Name: episodes search_vectors_episode_purge; Type: TRIGGER; Schema: memory; Owner: -
--

CREATE TRIGGER search_vectors_episode_purge AFTER UPDATE OF active ON memory.episodes FOR EACH ROW WHEN ((old.active AND (NOT new.active))) EXECUTE FUNCTION memory.purge_search_vectors();


--
-- Name: matter_revisions search_vectors_matter_purge; Type: TRIGGER; Schema: memory; Owner: -
--

CREATE TRIGGER search_vectors_matter_purge AFTER UPDATE OF active ON memory.matter_revisions FOR EACH ROW WHEN ((old.active AND (NOT new.active))) EXECUTE FUNCTION memory.purge_search_vectors();


--
-- Name: procrastinate_jobs procrastinate_jobs_notify_queue_job_aborted_v1; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER procrastinate_jobs_notify_queue_job_aborted_v1 AFTER UPDATE OF abort_requested ON public.procrastinate_jobs FOR EACH ROW WHEN (((old.abort_requested = false) AND (new.abort_requested = true) AND (new.status = 'doing'::public.procrastinate_job_status))) EXECUTE FUNCTION public.procrastinate_notify_queue_abort_job_v1();


--
-- Name: procrastinate_jobs procrastinate_jobs_notify_queue_job_inserted_v1; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER procrastinate_jobs_notify_queue_job_inserted_v1 AFTER INSERT ON public.procrastinate_jobs FOR EACH ROW WHEN ((new.status = 'todo'::public.procrastinate_job_status)) EXECUTE FUNCTION public.procrastinate_notify_queue_job_inserted_v1();


--
-- Name: procrastinate_jobs procrastinate_trigger_abort_requested_events_v1; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER procrastinate_trigger_abort_requested_events_v1 AFTER UPDATE OF abort_requested ON public.procrastinate_jobs FOR EACH ROW WHEN ((new.abort_requested = true)) EXECUTE FUNCTION public.procrastinate_trigger_abort_requested_events_procedure_v1();


--
-- Name: procrastinate_jobs procrastinate_trigger_delete_jobs_v1; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER procrastinate_trigger_delete_jobs_v1 BEFORE DELETE ON public.procrastinate_jobs FOR EACH ROW EXECUTE FUNCTION public.procrastinate_unlink_periodic_defers_v1();


--
-- Name: procrastinate_jobs procrastinate_trigger_scheduled_events_v1; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER procrastinate_trigger_scheduled_events_v1 AFTER INSERT OR UPDATE ON public.procrastinate_jobs FOR EACH ROW WHEN (((new.scheduled_at IS NOT NULL) AND (new.status = 'todo'::public.procrastinate_job_status))) EXECUTE FUNCTION public.procrastinate_trigger_function_scheduled_events_v1();


--
-- Name: procrastinate_jobs procrastinate_trigger_status_events_insert_v1; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER procrastinate_trigger_status_events_insert_v1 AFTER INSERT ON public.procrastinate_jobs FOR EACH ROW WHEN ((new.status = 'todo'::public.procrastinate_job_status)) EXECUTE FUNCTION public.procrastinate_trigger_function_status_events_insert_v1();


--
-- Name: procrastinate_jobs procrastinate_trigger_status_events_update_v1; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER procrastinate_trigger_status_events_update_v1 AFTER UPDATE OF status ON public.procrastinate_jobs FOR EACH ROW EXECUTE FUNCTION public.procrastinate_trigger_function_status_events_update_v1();


--
-- Name: action_controls action_controls_action_id_fkey; Type: FK CONSTRAINT; Schema: capabilities; Owner: -
--

ALTER TABLE ONLY capabilities.action_controls
    ADD CONSTRAINT action_controls_action_id_fkey FOREIGN KEY (action_id) REFERENCES capabilities.actions(id);


--
-- Name: attempts attempts_action_id_fkey; Type: FK CONSTRAINT; Schema: capabilities; Owner: -
--

ALTER TABLE ONLY capabilities.attempts
    ADD CONSTRAINT attempts_action_id_fkey FOREIGN KEY (action_id) REFERENCES capabilities.actions(id);


--
-- Name: image_work image_work_action_id_fkey; Type: FK CONSTRAINT; Schema: capabilities; Owner: -
--

ALTER TABLE ONLY capabilities.image_work
    ADD CONSTRAINT image_work_action_id_fkey FOREIGN KEY (action_id) REFERENCES capabilities.actions(id);


--
-- Name: reaction_assets reaction_assets_profile_id_fkey; Type: FK CONSTRAINT; Schema: capabilities; Owner: -
--

ALTER TABLE ONLY capabilities.reaction_assets
    ADD CONSTRAINT reaction_assets_profile_id_fkey FOREIGN KEY (profile_id) REFERENCES capabilities.reaction_embedding_profiles(id);


--
-- Name: runtime_observations runtime_observations_owner_id_fkey; Type: FK CONSTRAINT; Schema: capabilities; Owner: -
--

ALTER TABLE ONLY capabilities.runtime_observations
    ADD CONSTRAINT runtime_observations_owner_id_fkey FOREIGN KEY (owner_id) REFERENCES capabilities.execution_owners(id);


--
-- Name: continuations continuations_project_id_fkey; Type: FK CONSTRAINT; Schema: capability_projects; Owner: -
--

ALTER TABLE ONLY capability_projects.continuations
    ADD CONSTRAINT continuations_project_id_fkey FOREIGN KEY (project_id) REFERENCES capability_projects.projects(project_id);


--
-- Name: declared_states declared_states_project_id_fkey; Type: FK CONSTRAINT; Schema: capability_projects; Owner: -
--

ALTER TABLE ONLY capability_projects.declared_states
    ADD CONSTRAINT declared_states_project_id_fkey FOREIGN KEY (project_id) REFERENCES capability_projects.projects(project_id);


--
-- Name: directory_plans directory_plans_project_id_fkey; Type: FK CONSTRAINT; Schema: capability_projects; Owner: -
--

ALTER TABLE ONLY capability_projects.directory_plans
    ADD CONSTRAINT directory_plans_project_id_fkey FOREIGN KEY (project_id) REFERENCES capability_projects.projects(project_id);


--
-- Name: document_versions document_versions_document_id_fkey; Type: FK CONSTRAINT; Schema: capability_projects; Owner: -
--

ALTER TABLE ONLY capability_projects.document_versions
    ADD CONSTRAINT document_versions_document_id_fkey FOREIGN KEY (document_id) REFERENCES capability_projects.documents(document_id);


--
-- Name: documents documents_project_id_fkey; Type: FK CONSTRAINT; Schema: capability_projects; Owner: -
--

ALTER TABLE ONLY capability_projects.documents
    ADD CONSTRAINT documents_project_id_fkey FOREIGN KEY (project_id) REFERENCES capability_projects.projects(project_id);


--
-- Name: experience_versions experience_versions_experience_id_fkey; Type: FK CONSTRAINT; Schema: capability_projects; Owner: -
--

ALTER TABLE ONLY capability_projects.experience_versions
    ADD CONSTRAINT experience_versions_experience_id_fkey FOREIGN KEY (experience_id) REFERENCES capability_projects.experiences(experience_id);


--
-- Name: imports imports_project_id_fkey; Type: FK CONSTRAINT; Schema: capability_projects; Owner: -
--

ALTER TABLE ONLY capability_projects.imports
    ADD CONSTRAINT imports_project_id_fkey FOREIGN KEY (project_id) REFERENCES capability_projects.projects(project_id);


--
-- Name: lesson_versions lesson_versions_lesson_id_fkey; Type: FK CONSTRAINT; Schema: capability_projects; Owner: -
--

ALTER TABLE ONLY capability_projects.lesson_versions
    ADD CONSTRAINT lesson_versions_lesson_id_fkey FOREIGN KEY (lesson_id) REFERENCES capability_projects.lessons(lesson_id);


--
-- Name: lessons lessons_project_id_fkey; Type: FK CONSTRAINT; Schema: capability_projects; Owner: -
--

ALTER TABLE ONLY capability_projects.lessons
    ADD CONSTRAINT lessons_project_id_fkey FOREIGN KEY (project_id) REFERENCES capability_projects.projects(project_id);


--
-- Name: note_versions note_versions_note_id_fkey; Type: FK CONSTRAINT; Schema: capability_projects; Owner: -
--

ALTER TABLE ONLY capability_projects.note_versions
    ADD CONSTRAINT note_versions_note_id_fkey FOREIGN KEY (note_id) REFERENCES capability_projects.notes(note_id);


--
-- Name: notes notes_project_id_fkey; Type: FK CONSTRAINT; Schema: capability_projects; Owner: -
--

ALTER TABLE ONLY capability_projects.notes
    ADD CONSTRAINT notes_project_id_fkey FOREIGN KEY (project_id) REFERENCES capability_projects.projects(project_id);


--
-- Name: registry_versions registry_versions_project_id_fkey; Type: FK CONSTRAINT; Schema: capability_projects; Owner: -
--

ALTER TABLE ONLY capability_projects.registry_versions
    ADD CONSTRAINT registry_versions_project_id_fkey FOREIGN KEY (project_id) REFERENCES capability_projects.projects(project_id);


--
-- Name: media_supplements media_supplements_event_key_fkey; Type: FK CONSTRAINT; Schema: channels; Owner: -
--

ALTER TABLE ONLY channels.media_supplements
    ADD CONSTRAINT media_supplements_event_key_fkey FOREIGN KEY (event_key) REFERENCES channels.events(id);


--
-- Name: actors actors_persona_id_fkey; Type: FK CONSTRAINT; Schema: companion; Owner: -
--

ALTER TABLE ONLY companion.actors
    ADD CONSTRAINT actors_persona_id_fkey FOREIGN KEY (persona_id) REFERENCES companion.persona_profiles(persona_id);


--
-- Name: actors actors_persona_id_persona_revision_fkey; Type: FK CONSTRAINT; Schema: companion; Owner: -
--

ALTER TABLE ONLY companion.actors
    ADD CONSTRAINT actors_persona_id_persona_revision_fkey FOREIGN KEY (persona_id, persona_revision) REFERENCES companion.persona_revisions(persona_id, revision);


--
-- Name: affect_events affect_events_actor_id_fkey; Type: FK CONSTRAINT; Schema: companion; Owner: -
--

ALTER TABLE ONLY companion.affect_events
    ADD CONSTRAINT affect_events_actor_id_fkey FOREIGN KEY (actor_id) REFERENCES companion.actors(actor_id);


--
-- Name: expression_example_usage expression_example_usage_example_id_fkey; Type: FK CONSTRAINT; Schema: companion; Owner: -
--

ALTER TABLE ONLY companion.expression_example_usage
    ADD CONSTRAINT expression_example_usage_example_id_fkey FOREIGN KEY (example_id) REFERENCES companion.expression_examples(id);


--
-- Name: expression_examples expression_examples_rule_id_fkey; Type: FK CONSTRAINT; Schema: companion; Owner: -
--

ALTER TABLE ONLY companion.expression_examples
    ADD CONSTRAINT expression_examples_rule_id_fkey FOREIGN KEY (rule_id) REFERENCES companion.style_rules(id);


--
-- Name: habit_revisions habit_revisions_rule_id_fkey; Type: FK CONSTRAINT; Schema: companion; Owner: -
--

ALTER TABLE ONLY companion.habit_revisions
    ADD CONSTRAINT habit_revisions_rule_id_fkey FOREIGN KEY (rule_id) REFERENCES companion.style_rules(id);


--
-- Name: habit_supports habit_supports_rule_id_fkey; Type: FK CONSTRAINT; Schema: companion; Owner: -
--

ALTER TABLE ONLY companion.habit_supports
    ADD CONSTRAINT habit_supports_rule_id_fkey FOREIGN KEY (rule_id) REFERENCES companion.style_rules(id);


--
-- Name: learning_reflections learning_reflections_actor_id_fkey; Type: FK CONSTRAINT; Schema: companion; Owner: -
--

ALTER TABLE ONLY companion.learning_reflections
    ADD CONSTRAINT learning_reflections_actor_id_fkey FOREIGN KEY (actor_id) REFERENCES companion.actors(actor_id);


--
-- Name: persona_revisions persona_revisions_persona_id_fkey; Type: FK CONSTRAINT; Schema: companion; Owner: -
--

ALTER TABLE ONLY companion.persona_revisions
    ADD CONSTRAINT persona_revisions_persona_id_fkey FOREIGN KEY (persona_id) REFERENCES companion.persona_profiles(persona_id);


--
-- Name: style_rules style_rules_actor_id_fkey; Type: FK CONSTRAINT; Schema: companion; Owner: -
--

ALTER TABLE ONLY companion.style_rules
    ADD CONSTRAINT style_rules_actor_id_fkey FOREIGN KEY (actor_id) REFERENCES companion.actors(actor_id);


--
-- Name: context_selections context_selections_turn_id_fkey; Type: FK CONSTRAINT; Schema: conversation; Owner: -
--

ALTER TABLE ONLY conversation.context_selections
    ADD CONSTRAINT context_selections_turn_id_fkey FOREIGN KEY (turn_id) REFERENCES conversation.turns(turn_id);


--
-- Name: input_supplements input_supplements_input_id_fkey; Type: FK CONSTRAINT; Schema: conversation; Owner: -
--

ALTER TABLE ONLY conversation.input_supplements
    ADD CONSTRAINT input_supplements_input_id_fkey FOREIGN KEY (input_id) REFERENCES conversation.turns(turn_id);


--
-- Name: messages messages_turn_id_fkey; Type: FK CONSTRAINT; Schema: conversation; Owner: -
--

ALTER TABLE ONLY conversation.messages
    ADD CONSTRAINT messages_turn_id_fkey FOREIGN KEY (turn_id) REFERENCES conversation.turns(turn_id);


--
-- Name: tool_links tool_links_turn_id_fkey; Type: FK CONSTRAINT; Schema: conversation; Owner: -
--

ALTER TABLE ONLY conversation.tool_links
    ADD CONSTRAINT tool_links_turn_id_fkey FOREIGN KEY (turn_id) REFERENCES conversation.turns(turn_id);


--
-- Name: accounts accounts_connection_id_fkey; Type: FK CONSTRAINT; Schema: identity; Owner: -
--

ALTER TABLE ONLY identity.accounts
    ADD CONSTRAINT accounts_connection_id_fkey FOREIGN KEY (connection_id) REFERENCES identity.connections(connection_id);


--
-- Name: accounts accounts_person_id_fkey; Type: FK CONSTRAINT; Schema: identity; Owner: -
--

ALTER TABLE ONLY identity.accounts
    ADD CONSTRAINT accounts_person_id_fkey FOREIGN KEY (person_id) REFERENCES identity.people(person_id);


--
-- Name: accounts accounts_principal_id_fkey; Type: FK CONSTRAINT; Schema: identity; Owner: -
--

ALTER TABLE ONLY identity.accounts
    ADD CONSTRAINT accounts_principal_id_fkey FOREIGN KEY (principal_id) REFERENCES identity.principals(principal_id);


--
-- Name: actor_grants actor_grants_actor_id_fkey; Type: FK CONSTRAINT; Schema: identity; Owner: -
--

ALTER TABLE ONLY identity.actor_grants
    ADD CONSTRAINT actor_grants_actor_id_fkey FOREIGN KEY (actor_id) REFERENCES identity.actors(actor_id);


--
-- Name: actor_grants actor_grants_principal_id_fkey; Type: FK CONSTRAINT; Schema: identity; Owner: -
--

ALTER TABLE ONLY identity.actor_grants
    ADD CONSTRAINT actor_grants_principal_id_fkey FOREIGN KEY (principal_id) REFERENCES identity.principals(principal_id);


--
-- Name: actors actors_owner_id_fkey; Type: FK CONSTRAINT; Schema: identity; Owner: -
--

ALTER TABLE ONLY identity.actors
    ADD CONSTRAINT actors_owner_id_fkey FOREIGN KEY (owner_id) REFERENCES identity.principals(principal_id);


--
-- Name: connections connections_actor_id_fkey; Type: FK CONSTRAINT; Schema: identity; Owner: -
--

ALTER TABLE ONLY identity.connections
    ADD CONSTRAINT connections_actor_id_fkey FOREIGN KEY (actor_id) REFERENCES identity.actors(actor_id);


--
-- Name: connections connections_principal_id_fkey; Type: FK CONSTRAINT; Schema: identity; Owner: -
--

ALTER TABLE ONLY identity.connections
    ADD CONSTRAINT connections_principal_id_fkey FOREIGN KEY (principal_id) REFERENCES identity.principals(principal_id);


--
-- Name: conversation_links conversation_links_connection_id_fkey; Type: FK CONSTRAINT; Schema: identity; Owner: -
--

ALTER TABLE ONLY identity.conversation_links
    ADD CONSTRAINT conversation_links_connection_id_fkey FOREIGN KEY (connection_id) REFERENCES identity.connections(connection_id);


--
-- Name: conversation_links conversation_links_conversation_id_fkey; Type: FK CONSTRAINT; Schema: identity; Owner: -
--

ALTER TABLE ONLY identity.conversation_links
    ADD CONSTRAINT conversation_links_conversation_id_fkey FOREIGN KEY (conversation_id) REFERENCES identity.conversations(conversation_id);


--
-- Name: conversation_members conversation_members_connection_id_fkey; Type: FK CONSTRAINT; Schema: identity; Owner: -
--

ALTER TABLE ONLY identity.conversation_members
    ADD CONSTRAINT conversation_members_connection_id_fkey FOREIGN KEY (connection_id) REFERENCES identity.connections(connection_id);


--
-- Name: conversation_members conversation_members_conversation_id_fkey; Type: FK CONSTRAINT; Schema: identity; Owner: -
--

ALTER TABLE ONLY identity.conversation_members
    ADD CONSTRAINT conversation_members_conversation_id_fkey FOREIGN KEY (conversation_id) REFERENCES identity.conversations(conversation_id);


--
-- Name: conversation_members conversation_members_person_id_fkey; Type: FK CONSTRAINT; Schema: identity; Owner: -
--

ALTER TABLE ONLY identity.conversation_members
    ADD CONSTRAINT conversation_members_person_id_fkey FOREIGN KEY (person_id) REFERENCES identity.people(person_id);


--
-- Name: conversations conversations_actor_id_fkey; Type: FK CONSTRAINT; Schema: identity; Owner: -
--

ALTER TABLE ONLY identity.conversations
    ADD CONSTRAINT conversations_actor_id_fkey FOREIGN KEY (actor_id) REFERENCES identity.actors(actor_id);


--
-- Name: conversations conversations_person_id_fkey; Type: FK CONSTRAINT; Schema: identity; Owner: -
--

ALTER TABLE ONLY identity.conversations
    ADD CONSTRAINT conversations_person_id_fkey FOREIGN KEY (person_id) REFERENCES identity.people(person_id);


--
-- Name: conversations conversations_scope_id_fkey; Type: FK CONSTRAINT; Schema: identity; Owner: -
--

ALTER TABLE ONLY identity.conversations
    ADD CONSTRAINT conversations_scope_id_fkey FOREIGN KEY (scope_id) REFERENCES identity.scopes(scope_id);


--
-- Name: events events_connection_id_fkey; Type: FK CONSTRAINT; Schema: identity; Owner: -
--

ALTER TABLE ONLY identity.events
    ADD CONSTRAINT events_connection_id_fkey FOREIGN KEY (connection_id) REFERENCES identity.connections(connection_id);


--
-- Name: person_aliases person_aliases_person_id_fkey; Type: FK CONSTRAINT; Schema: identity; Owner: -
--

ALTER TABLE ONLY identity.person_aliases
    ADD CONSTRAINT person_aliases_person_id_fkey FOREIGN KEY (person_id) REFERENCES identity.people(person_id);


--
-- Name: principals principals_person_id_fkey; Type: FK CONSTRAINT; Schema: identity; Owner: -
--

ALTER TABLE ONLY identity.principals
    ADD CONSTRAINT principals_person_id_fkey FOREIGN KEY (person_id) REFERENCES identity.people(person_id);


--
-- Name: scope_grants scope_grants_principal_id_fkey; Type: FK CONSTRAINT; Schema: identity; Owner: -
--

ALTER TABLE ONLY identity.scope_grants
    ADD CONSTRAINT scope_grants_principal_id_fkey FOREIGN KEY (principal_id) REFERENCES identity.principals(principal_id);


--
-- Name: scope_grants scope_grants_scope_id_fkey; Type: FK CONSTRAINT; Schema: identity; Owner: -
--

ALTER TABLE ONLY identity.scope_grants
    ADD CONSTRAINT scope_grants_scope_id_fkey FOREIGN KEY (scope_id) REFERENCES identity.scopes(scope_id);


--
-- Name: scope_inclusions scope_inclusions_conversation_scope_id_fkey; Type: FK CONSTRAINT; Schema: identity; Owner: -
--

ALTER TABLE ONLY identity.scope_inclusions
    ADD CONSTRAINT scope_inclusions_conversation_scope_id_fkey FOREIGN KEY (conversation_scope_id) REFERENCES identity.scopes(scope_id);


--
-- Name: scope_inclusions scope_inclusions_publication_scope_id_fkey; Type: FK CONSTRAINT; Schema: identity; Owner: -
--

ALTER TABLE ONLY identity.scope_inclusions
    ADD CONSTRAINT scope_inclusions_publication_scope_id_fkey FOREIGN KEY (publication_scope_id) REFERENCES identity.scopes(scope_id);


--
-- Name: scopes scopes_actor_id_fkey; Type: FK CONSTRAINT; Schema: identity; Owner: -
--

ALTER TABLE ONLY identity.scopes
    ADD CONSTRAINT scopes_actor_id_fkey FOREIGN KEY (actor_id) REFERENCES identity.actors(actor_id);


--
-- Name: scopes scopes_person_id_fkey; Type: FK CONSTRAINT; Schema: identity; Owner: -
--

ALTER TABLE ONLY identity.scopes
    ADD CONSTRAINT scopes_person_id_fkey FOREIGN KEY (person_id) REFERENCES identity.people(person_id);


--
-- Name: sessions sessions_principal_id_fkey; Type: FK CONSTRAINT; Schema: identity; Owner: -
--

ALTER TABLE ONLY identity.sessions
    ADD CONSTRAINT sessions_principal_id_fkey FOREIGN KEY (principal_id) REFERENCES identity.principals(principal_id);


--
-- Name: activities activities_goal_id_fkey; Type: FK CONSTRAINT; Schema: life; Owner: -
--

ALTER TABLE ONLY life.activities
    ADD CONSTRAINT activities_goal_id_fkey FOREIGN KEY (goal_id) REFERENCES life.goals(id);


--
-- Name: chapter_revisions chapter_revisions_chapter_id_fkey; Type: FK CONSTRAINT; Schema: life; Owner: -
--

ALTER TABLE ONLY life.chapter_revisions
    ADD CONSTRAINT chapter_revisions_chapter_id_fkey FOREIGN KEY (chapter_id) REFERENCES life.chapters(id);


--
-- Name: chapters chapters_work_id_fkey; Type: FK CONSTRAINT; Schema: life; Owner: -
--

ALTER TABLE ONLY life.chapters
    ADD CONSTRAINT chapters_work_id_fkey FOREIGN KEY (work_id) REFERENCES life.works(id);


--
-- Name: day_slots day_slots_activity_id_fkey; Type: FK CONSTRAINT; Schema: life; Owner: -
--

ALTER TABLE ONLY life.day_slots
    ADD CONSTRAINT day_slots_activity_id_fkey FOREIGN KEY (activity_id) REFERENCES life.activities(id);


--
-- Name: day_slots day_slots_actor_id_day_fkey; Type: FK CONSTRAINT; Schema: life; Owner: -
--

ALTER TABLE ONLY life.day_slots
    ADD CONSTRAINT day_slots_actor_id_day_fkey FOREIGN KEY (actor_id, day) REFERENCES life.day_plans(actor_id, day);


--
-- Name: diary_revisions diary_revisions_diary_id_fkey; Type: FK CONSTRAINT; Schema: life; Owner: -
--

ALTER TABLE ONLY life.diary_revisions
    ADD CONSTRAINT diary_revisions_diary_id_fkey FOREIGN KEY (diary_id) REFERENCES life.diaries(id);


--
-- Name: goal_milestones goal_milestones_goal_id_fkey; Type: FK CONSTRAINT; Schema: life; Owner: -
--

ALTER TABLE ONLY life.goal_milestones
    ADD CONSTRAINT goal_milestones_goal_id_fkey FOREIGN KEY (goal_id) REFERENCES life.goals(id);


--
-- Name: goal_progress_receipts goal_progress_receipts_goal_id_fkey; Type: FK CONSTRAINT; Schema: life; Owner: -
--

ALTER TABLE ONLY life.goal_progress_receipts
    ADD CONSTRAINT goal_progress_receipts_goal_id_fkey FOREIGN KEY (goal_id) REFERENCES life.goals(id);


--
-- Name: goal_revisions goal_revisions_goal_id_fkey; Type: FK CONSTRAINT; Schema: life; Owner: -
--

ALTER TABLE ONLY life.goal_revisions
    ADD CONSTRAINT goal_revisions_goal_id_fkey FOREIGN KEY (goal_id) REFERENCES life.goals(id);


--
-- Name: claim_revisions claim_revisions_claim_id_fkey; Type: FK CONSTRAINT; Schema: memory; Owner: -
--

ALTER TABLE ONLY memory.claim_revisions
    ADD CONSTRAINT claim_revisions_claim_id_fkey FOREIGN KEY (claim_id) REFERENCES memory.claims(claim_id);


--
-- Name: consolidation_attempts consolidation_attempts_batch_id_fkey; Type: FK CONSTRAINT; Schema: memory; Owner: -
--

ALTER TABLE ONLY memory.consolidation_attempts
    ADD CONSTRAINT consolidation_attempts_batch_id_fkey FOREIGN KEY (batch_id) REFERENCES memory.consolidation_batches(batch_id);


--
-- Name: consolidation_attempts consolidation_attempts_step_id_fkey; Type: FK CONSTRAINT; Schema: memory; Owner: -
--

ALTER TABLE ONLY memory.consolidation_attempts
    ADD CONSTRAINT consolidation_attempts_step_id_fkey FOREIGN KEY (step_id) REFERENCES memory.model_steps(step_id);


--
-- Name: consolidation_batches consolidation_batches_partition_id_fkey; Type: FK CONSTRAINT; Schema: memory; Owner: -
--

ALTER TABLE ONLY memory.consolidation_batches
    ADD CONSTRAINT consolidation_batches_partition_id_fkey FOREIGN KEY (partition_id) REFERENCES memory.consolidation_state(partition_id);


--
-- Name: consolidation_compilations consolidation_compilations_batch_id_fkey; Type: FK CONSTRAINT; Schema: memory; Owner: -
--

ALTER TABLE ONLY memory.consolidation_compilations
    ADD CONSTRAINT consolidation_compilations_batch_id_fkey FOREIGN KEY (batch_id) REFERENCES memory.consolidation_batches(batch_id);


--
-- Name: consolidation_reorganization_parts consolidation_reorganization_parts_child_batch_id_fkey; Type: FK CONSTRAINT; Schema: memory; Owner: -
--

ALTER TABLE ONLY memory.consolidation_reorganization_parts
    ADD CONSTRAINT consolidation_reorganization_parts_child_batch_id_fkey FOREIGN KEY (child_batch_id) REFERENCES memory.consolidation_batches(batch_id);


--
-- Name: consolidation_reorganization_parts consolidation_reorganization_parts_parent_batch_id_fkey; Type: FK CONSTRAINT; Schema: memory; Owner: -
--

ALTER TABLE ONLY memory.consolidation_reorganization_parts
    ADD CONSTRAINT consolidation_reorganization_parts_parent_batch_id_fkey FOREIGN KEY (parent_batch_id) REFERENCES memory.consolidation_batches(batch_id);


--
-- Name: consolidation_reorganization_parts consolidation_reorganization_parts_root_batch_id_fkey; Type: FK CONSTRAINT; Schema: memory; Owner: -
--

ALTER TABLE ONLY memory.consolidation_reorganization_parts
    ADD CONSTRAINT consolidation_reorganization_parts_root_batch_id_fkey FOREIGN KEY (root_batch_id) REFERENCES memory.consolidation_reorganizations(root_batch_id);


--
-- Name: consolidation_reorganizations consolidation_reorganizations_root_batch_id_fkey; Type: FK CONSTRAINT; Schema: memory; Owner: -
--

ALTER TABLE ONLY memory.consolidation_reorganizations
    ADD CONSTRAINT consolidation_reorganizations_root_batch_id_fkey FOREIGN KEY (root_batch_id) REFERENCES memory.consolidation_batches(batch_id);


--
-- Name: episode_revisions episode_revisions_episode_id_fkey; Type: FK CONSTRAINT; Schema: memory; Owner: -
--

ALTER TABLE ONLY memory.episode_revisions
    ADD CONSTRAINT episode_revisions_episode_id_fkey FOREIGN KEY (episode_id) REFERENCES memory.episodes(episode_id);


--
-- Name: episode_supplements episode_supplements_episode_id_fkey; Type: FK CONSTRAINT; Schema: memory; Owner: -
--

ALTER TABLE ONLY memory.episode_supplements
    ADD CONSTRAINT episode_supplements_episode_id_fkey FOREIGN KEY (episode_id) REFERENCES memory.episodes(episode_id);


--
-- Name: index_attempts index_attempts_index_id_fkey; Type: FK CONSTRAINT; Schema: memory; Owner: -
--

ALTER TABLE ONLY memory.index_attempts
    ADD CONSTRAINT index_attempts_index_id_fkey FOREIGN KEY (index_id) REFERENCES memory.index_jobs(index_id);


--
-- Name: matter_claims matter_claims_claim_id_fkey; Type: FK CONSTRAINT; Schema: memory; Owner: -
--

ALTER TABLE ONLY memory.matter_claims
    ADD CONSTRAINT matter_claims_claim_id_fkey FOREIGN KEY (claim_id) REFERENCES memory.claims(claim_id);


--
-- Name: matter_claims matter_claims_matter_id_fkey; Type: FK CONSTRAINT; Schema: memory; Owner: -
--

ALTER TABLE ONLY memory.matter_claims
    ADD CONSTRAINT matter_claims_matter_id_fkey FOREIGN KEY (matter_id) REFERENCES memory.matters(matter_id);


--
-- Name: matter_revisions matter_revisions_matter_id_fkey; Type: FK CONSTRAINT; Schema: memory; Owner: -
--

ALTER TABLE ONLY memory.matter_revisions
    ADD CONSTRAINT matter_revisions_matter_id_fkey FOREIGN KEY (matter_id) REFERENCES memory.matters(matter_id);


--
-- Name: statistics_outbox statistics_outbox_batch_id_fkey; Type: FK CONSTRAINT; Schema: memory; Owner: -
--

ALTER TABLE ONLY memory.statistics_outbox
    ADD CONSTRAINT statistics_outbox_batch_id_fkey FOREIGN KEY (batch_id) REFERENCES memory.consolidation_batches(batch_id);


--
-- Name: bindings bindings_route_id_fkey; Type: FK CONSTRAINT; Schema: models; Owner: -
--

ALTER TABLE ONLY models.bindings
    ADD CONSTRAINT bindings_route_id_fkey FOREIGN KEY (route_id) REFERENCES models.routes(route_id);


--
-- Name: embedding_profile embedding_profile_route_id_fkey; Type: FK CONSTRAINT; Schema: models; Owner: -
--

ALTER TABLE ONLY models.embedding_profile
    ADD CONSTRAINT embedding_profile_route_id_fkey FOREIGN KEY (route_id) REFERENCES models.routes(route_id);


--
-- Name: quota_cooldowns quota_cooldowns_quota_group_fkey; Type: FK CONSTRAINT; Schema: models; Owner: -
--

ALTER TABLE ONLY models.quota_cooldowns
    ADD CONSTRAINT quota_cooldowns_quota_group_fkey FOREIGN KEY (quota_group) REFERENCES models.quota_groups(quota_group) ON DELETE CASCADE;


--
-- Name: route_cooldowns route_cooldowns_route_id_fkey; Type: FK CONSTRAINT; Schema: models; Owner: -
--

ALTER TABLE ONLY models.route_cooldowns
    ADD CONSTRAINT route_cooldowns_route_id_fkey FOREIGN KEY (route_id) REFERENCES models.routes(route_id) ON DELETE CASCADE;


--
-- Name: routes routes_provider_id_fkey; Type: FK CONSTRAINT; Schema: models; Owner: -
--

ALTER TABLE ONLY models.routes
    ADD CONSTRAINT routes_provider_id_fkey FOREIGN KEY (provider_id) REFERENCES models.providers(provider_id);


--
-- Name: routes routes_quota_group_fkey; Type: FK CONSTRAINT; Schema: models; Owner: -
--

ALTER TABLE ONLY models.routes
    ADD CONSTRAINT routes_quota_group_fkey FOREIGN KEY (quota_group) REFERENCES models.quota_groups(quota_group);


--
-- Name: stream_events stream_events_attempt_id_fkey; Type: FK CONSTRAINT; Schema: models; Owner: -
--

ALTER TABLE ONLY models.stream_events
    ADD CONSTRAINT stream_events_attempt_id_fkey FOREIGN KEY (attempt_id) REFERENCES models.submissions(attempt_id) ON DELETE CASCADE;


--
-- Name: submissions submissions_attempt_id_fkey; Type: FK CONSTRAINT; Schema: models; Owner: -
--

ALTER TABLE ONLY models.submissions
    ADD CONSTRAINT submissions_attempt_id_fkey FOREIGN KEY (attempt_id) REFERENCES models.attempts(attempt_id) ON DELETE CASCADE;


--
-- Name: diagnostics diagnostics_incident_id_fkey; Type: FK CONSTRAINT; Schema: operations; Owner: -
--

ALTER TABLE ONLY operations.diagnostics
    ADD CONSTRAINT diagnostics_incident_id_fkey FOREIGN KEY (incident_id) REFERENCES operations.incidents(incident_id);


--
-- Name: incident_causes incident_causes_incident_id_fkey; Type: FK CONSTRAINT; Schema: operations; Owner: -
--

ALTER TABLE ONLY operations.incident_causes
    ADD CONSTRAINT incident_causes_incident_id_fkey FOREIGN KEY (incident_id) REFERENCES operations.incidents(incident_id);


--
-- Name: incident_causes incident_causes_root_incident_id_fkey; Type: FK CONSTRAINT; Schema: operations; Owner: -
--

ALTER TABLE ONLY operations.incident_causes
    ADD CONSTRAINT incident_causes_root_incident_id_fkey FOREIGN KEY (root_incident_id) REFERENCES operations.incidents(incident_id);


--
-- Name: incident_events incident_events_incident_id_fkey; Type: FK CONSTRAINT; Schema: operations; Owner: -
--

ALTER TABLE ONLY operations.incident_events
    ADD CONSTRAINT incident_events_incident_id_fkey FOREIGN KEY (incident_id) REFERENCES operations.incidents(incident_id);


--
-- Name: incident_targets incident_targets_incident_id_fkey; Type: FK CONSTRAINT; Schema: operations; Owner: -
--

ALTER TABLE ONLY operations.incident_targets
    ADD CONSTRAINT incident_targets_incident_id_fkey FOREIGN KEY (incident_id) REFERENCES operations.incidents(incident_id);


--
-- Name: notification_outbox notification_outbox_incident_id_fkey; Type: FK CONSTRAINT; Schema: operations; Owner: -
--

ALTER TABLE ONLY operations.notification_outbox
    ADD CONSTRAINT notification_outbox_incident_id_fkey FOREIGN KEY (incident_id) REFERENCES operations.incidents(incident_id);


--
-- Name: recoveries recoveries_incident_id_fkey; Type: FK CONSTRAINT; Schema: operations; Owner: -
--

ALTER TABLE ONLY operations.recoveries
    ADD CONSTRAINT recoveries_incident_id_fkey FOREIGN KEY (incident_id) REFERENCES operations.incidents(incident_id);


--
-- Name: procrastinate_events procrastinate_events_job_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.procrastinate_events
    ADD CONSTRAINT procrastinate_events_job_id_fkey FOREIGN KEY (job_id) REFERENCES public.procrastinate_jobs(id) ON DELETE CASCADE;


--
-- Name: procrastinate_jobs procrastinate_jobs_worker_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.procrastinate_jobs
    ADD CONSTRAINT procrastinate_jobs_worker_id_fkey FOREIGN KEY (worker_id) REFERENCES public.procrastinate_workers(id) ON DELETE SET NULL;


--
-- Name: procrastinate_periodic_defers procrastinate_periodic_defers_job_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.procrastinate_periodic_defers
    ADD CONSTRAINT procrastinate_periodic_defers_job_id_fkey FOREIGN KEY (job_id) REFERENCES public.procrastinate_jobs(id);


--
-- Name: recompute_attempts recompute_attempts_data_generation_domain_work_key_fkey; Type: FK CONSTRAINT; Schema: runtime; Owner: -
--

ALTER TABLE ONLY runtime.recompute_attempts
    ADD CONSTRAINT recompute_attempts_data_generation_domain_work_key_fkey FOREIGN KEY (data_generation, domain, work_key) REFERENCES runtime.recompute_work(data_generation, domain, work_key);


--
-- Name: recompute_changes recompute_changes_data_generation_domain_work_key_fkey; Type: FK CONSTRAINT; Schema: runtime; Owner: -
--

ALTER TABLE ONLY runtime.recompute_changes
    ADD CONSTRAINT recompute_changes_data_generation_domain_work_key_fkey FOREIGN KEY (data_generation, domain, work_key) REFERENCES runtime.recompute_work(data_generation, domain, work_key);


--
-- Name: recompute_gap_history recompute_gap_history_data_generation_domain_work_key_fkey; Type: FK CONSTRAINT; Schema: runtime; Owner: -
--

ALTER TABLE ONLY runtime.recompute_gap_history
    ADD CONSTRAINT recompute_gap_history_data_generation_domain_work_key_fkey FOREIGN KEY (data_generation, domain, work_key) REFERENCES runtime.recompute_work(data_generation, domain, work_key);


--
-- Name: recompute_gaps recompute_gaps_data_generation_domain_work_key_fkey; Type: FK CONSTRAINT; Schema: runtime; Owner: -
--

ALTER TABLE ONLY runtime.recompute_gaps
    ADD CONSTRAINT recompute_gaps_data_generation_domain_work_key_fkey FOREIGN KEY (data_generation, domain, work_key) REFERENCES runtime.recompute_work(data_generation, domain, work_key);


--
-- Name: recovery_attempt_observations recovery_attempt_observations_attempt_id_fkey; Type: FK CONSTRAINT; Schema: runtime; Owner: -
--

ALTER TABLE ONLY runtime.recovery_attempt_observations
    ADD CONSTRAINT recovery_attempt_observations_attempt_id_fkey FOREIGN KEY (attempt_id) REFERENCES runtime.recovery_attempts(attempt_id);


--
-- Name: recovery_attempts recovery_attempts_lineage_id_fkey; Type: FK CONSTRAINT; Schema: runtime; Owner: -
--

ALTER TABLE ONLY runtime.recovery_attempts
    ADD CONSTRAINT recovery_attempts_lineage_id_fkey FOREIGN KEY (lineage_id) REFERENCES runtime.recovery_lineages(lineage_id);


--
-- Name: recovery_dependency_changes recovery_dependency_changes_lineage_id_fkey; Type: FK CONSTRAINT; Schema: runtime; Owner: -
--

ALTER TABLE ONLY runtime.recovery_dependency_changes
    ADD CONSTRAINT recovery_dependency_changes_lineage_id_fkey FOREIGN KEY (lineage_id) REFERENCES runtime.recovery_lineages(lineage_id);


--
-- Name: archive_records archive_records_source_id_fkey; Type: FK CONSTRAINT; Schema: sources; Owner: -
--

ALTER TABLE ONLY sources.archive_records
    ADD CONSTRAINT archive_records_source_id_fkey FOREIGN KEY (source_id) REFERENCES sources.originals(source_id);


--
-- Name: evidence_members evidence_members_evidence_id_fkey; Type: FK CONSTRAINT; Schema: sources; Owner: -
--

ALTER TABLE ONLY sources.evidence_members
    ADD CONSTRAINT evidence_members_evidence_id_fkey FOREIGN KEY (evidence_id) REFERENCES sources.evidence_sets(evidence_id);


--
-- Name: evidence_members evidence_members_source_id_revision_fkey; Type: FK CONSTRAINT; Schema: sources; Owner: -
--

ALTER TABLE ONLY sources.evidence_members
    ADD CONSTRAINT evidence_members_source_id_revision_fkey FOREIGN KEY (source_id, revision) REFERENCES sources.versions(source_id, revision);


--
-- Name: media_supplements media_supplements_source_id_revision_fkey; Type: FK CONSTRAINT; Schema: sources; Owner: -
--

ALTER TABLE ONLY sources.media_supplements
    ADD CONSTRAINT media_supplements_source_id_revision_fkey FOREIGN KEY (source_id, revision) REFERENCES sources.versions(source_id, revision);


--
-- Name: source_aliases source_aliases_source_id_revision_fkey; Type: FK CONSTRAINT; Schema: sources; Owner: -
--

ALTER TABLE ONLY sources.source_aliases
    ADD CONSTRAINT source_aliases_source_id_revision_fkey FOREIGN KEY (source_id, revision) REFERENCES sources.versions(source_id, revision);


--
-- Name: supports supports_source_id_revision_fkey; Type: FK CONSTRAINT; Schema: sources; Owner: -
--

ALTER TABLE ONLY sources.supports
    ADD CONSTRAINT supports_source_id_revision_fkey FOREIGN KEY (source_id, revision) REFERENCES sources.versions(source_id, revision);


--
-- Name: versions versions_source_id_fkey; Type: FK CONSTRAINT; Schema: sources; Owner: -
--

ALTER TABLE ONLY sources.versions
    ADD CONSTRAINT versions_source_id_fkey FOREIGN KEY (source_id) REFERENCES sources.originals(source_id);


--
-- PostgreSQL database dump complete
--



--
-- PostgreSQL database dump
--


-- Dumped from database version 17.11 (Debian 17.11-1.pgdg12+2)
-- Dumped by pg_dump version 17.11 (Debian 17.11-1.pgdg12+2)

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET transaction_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Data for Name: index_scan; Type: TABLE DATA; Schema: memory; Owner: -
--

INSERT INTO memory.index_scan (kind, after_id, after_revision, profile_key, generation) VALUES ('claim', '', 0, NULL, NULL);
INSERT INTO memory.index_scan (kind, after_id, after_revision, profile_key, generation) VALUES ('episode', '', 0, NULL, NULL);
INSERT INTO memory.index_scan (kind, after_id, after_revision, profile_key, generation) VALUES ('matter', '', 0, NULL, NULL);


--
-- Data for Name: index_stats_cursor; Type: TABLE DATA; Schema: memory; Owner: -
--

INSERT INTO memory.index_stats_cursor (singleton, generation, after_index_id, after_number) VALUES (true, NULL, '', 0);


--
-- PostgreSQL database dump complete
--


INSERT INTO runtime.installation_generation(singleton,id) VALUES(true,gen_random_uuid()::text);
SELECT pg_catalog.set_config('search_path','public',true);
