"""Single owner of generated Vector/Loki/Grafana/Prometheus configuration."""

import json

if __package__:
    from .alerts import MONITOR_STALE_SECONDS
    from .policy import FIELDS, LEVELS, OUTCOMES
    from .retention import apply_retention
else:
    from alerts import MONITOR_STALE_SECONDS
    from policy import FIELDS, LEVELS, OUTCOMES
    from retention import apply_retention

PRODUCTS = ("platform", "companion", "memory", "gateway")


def vector(snapshot, buffer_bytes=4 * 1024**3, *, include_knowledge=False):
    """Use Memory's vocabulary for an optional dedicated Knowledge log source."""
    services = (
        (*PRODUCTS[:3], "knowledge", PRODUCTS[3]) if include_knowledge else PRODUCTS
    )
    sources, transforms = {}, {}
    for service in services:
        sources[service] = {
            "type": "file",
            "include": [
                f"/sources/{service}/*.jsonl",
                f"/sources/{service}/*.jsonl.[0-9]*",
            ],
            "read_from": "beginning",
            "ignore_checkpoints": False,
            "max_line_bytes": 4095,
            "fingerprint": {"strategy": "checksum", "lines": 1},
            "host_key": "",
            "file_key": "",
            "oldest_first": True,
            "glob_minimum_cooldown_ms": 1000,
        }
        registry = snapshot["products"]["memory" if service == "knowledge" else service]
        if service == "knowledge":
            allowed = ["memory-knowledge"]
        else:
            allowed = (
                [service] if service != "memory" else ["memory", "memory-knowledge"]
            )
        code = [
            "raw = string!(.message)",
            'assert!(length(raw) <= 4095, "invalid_safe_event")',
            'assert!(!contains(raw, "\\r"), "invalid_safe_event")',
            ". = object!(parse_json!(raw))",
            'assert!((length(keys(.)) == 12 || (.schema_version == "1.1.0" && .service == "companion" && length(keys(.)) == 13 && exists(.attributes))) && '
            + " && ".join("exists(." + field + ")" for field in sorted(FIELDS))
            + ', "invalid_safe_event")',
            'assert!(.schema_version == "1.0.0" || (.schema_version == "1.1.0" && .service == "companion" && exists(.attributes)), "invalid_safe_event")',
            f'assert!(includes({json.dumps(allowed)}, .service), "invalid_safe_event")',
            "sequence = int!(.sequence)",
            'assert!(sequence >= 1, "invalid_safe_event")',
            f'assert!(includes({json.dumps(LEVELS)}, .level), "invalid_safe_event")',
            f'assert!(includes({json.dumps(OUTCOMES)}, .outcome), "invalid_safe_event")',
            f'assert!(includes({json.dumps(registry["events"])}, .event), "invalid_safe_event")',
            f'assert!(is_null(.error_code) || includes({json.dumps(registry["error_codes"])}, .error_code), "invalid_safe_event")',
            'if !is_null(.duration_ms) { assert!(is_integer(.duration_ms) || is_float(.duration_ms), "invalid_safe_event"); elapsed_ms = to_float!(.duration_ms); assert!(elapsed_ms >= 0.0, "invalid_safe_event") }',
            "if !is_null(.correlation_id) { assert!(match(string!(.correlation_id), r'^[a-f0-9]{32}$'), \"invalid_safe_event\") }",
            "assert!(match(string!(.instance_id), r'^[a-fA-F0-9]{8}(-[a-fA-F0-9]{4}){3}-[a-fA-F0-9]{12}$'), \"invalid_safe_event\")",
            "assert!(match(string!(.event_id), r'^[a-fA-F0-9]{8}(-[a-fA-F0-9]{4}){3}-[a-fA-F0-9]{12}$'), \"invalid_safe_event\")",
            'original_time = parse_timestamp!(string!(.timestamp), format: "%+")',
            # Transport timestamp is ingestion time. Old backlog retains its original field.
            "._collected_at = now()",
        ]
        if service == "companion" and "peer_attribute_schema" in registry:
            schema = registry["peer_attribute_schema"]
            checks = [
                'assert!(includes(["peer.call.started", "peer.call.finished", "runtime.background_failed"], .event), "invalid_peer_event")',
                f'assert!(length(keys(object!(.attributes))) == {len(schema["required"])}, "invalid_peer_attributes")',
                'assert!(.attributes.error_code == .error_code, "invalid_peer_attributes")',
            ]
            for key, rule in schema["properties"].items():
                path = ".attributes." + key
                checks.append(f'assert!(exists({path}), "invalid_peer_attributes")')
                kinds = (
                    rule["type"] if isinstance(rule["type"], list) else [rule["type"]]
                )
                expressions = [
                    f"is_integer({path})"
                    if "integer" in kinds
                    else f"is_string({path})"
                ]
                if "enum" in rule:
                    expressions.append(f"includes({json.dumps(rule['enum'])}, {path})")
                if "pattern" in rule:
                    expressions.append(f"match(string!({path}), r'{rule['pattern']}')")
                if "minimum" in rule:
                    expressions.append(
                        f"int!({path}) >= {rule['minimum']} && int!({path}) <= {rule['maximum']}"
                    )
                expression = " && ".join(expressions)
                if "null" in kinds:
                    expression = f"is_null({path}) || ({expression})"
                checks.append(f'assert!({expression}, "invalid_peer_attributes")')
            code.append('if .schema_version == "1.1.0" {\n' + "\n".join(checks) + "\n}")
        else:
            # An old vocabulary cannot admit an unvalidated extension.
            code.append(
                'assert!(.schema_version == "1.0.0" && !exists(.attributes), "invalid_safe_event")'
            )
        transforms[service + "_safe"] = {
            "type": "remap",
            "inputs": [service],
            "source": "\n".join(code),
            "drop_on_error": True,
            "drop_on_abort": True,
            "reroute_dropped": False,
        }
    sources["internal_metrics"] = {
        "type": "internal_metrics",
        "scrape_interval_secs": 15,
    }
    return {
        "data_dir": "/var/lib/vector",
        "log_schema": {"timestamp_key": "_collected_at"},
        "secret": {
            "files": {
                "type": "directory",
                "path": "/run/secrets",
                "remove_trailing_whitespace": True,
            }
        },
        "sources": sources,
        "transforms": transforms,
        "sinks": {
            "loki": {
                "type": "loki",
                "inputs": [p + "_safe" for p in services],
                "endpoint": "https://obs-guard:8443",
                "auth": {"strategy": "bearer", "token": "SECRET[files.writer_token]"},
                "tls": {
                    "ca_file": "/run/tls/ca.pem",
                    "verify_certificate": True,
                    "verify_hostname": True,
                },
                "labels": {
                    "stack": "tianshu",
                    "service": "tianshu_{{ service }}",
                    "level": "level_{{ level }}",
                },
                "tenant_id": "tianshu",
                "encoding": {"codec": "json", "only_fields": sorted(FIELDS)},
                "remove_timestamp": True,
                "remove_label_fields": False,
                "out_of_order_action": "accept",
                "compression": "snappy",
                "acknowledgements": {"enabled": True},
                "buffer": {
                    "type": "disk",
                    "max_size": buffer_bytes,
                    "when_full": "block",
                },
                "batch": {"max_bytes": 262144, "timeout_secs": 1},
                "request": {
                    "concurrency": 1,
                    "timeout_secs": 15,
                    "retry_initial_backoff_secs": 1,
                    "retry_max_duration_secs": 60,
                },
                "healthcheck": {"enabled": False},
            },
            "metrics": {
                "type": "prometheus_exporter",
                "inputs": ["internal_metrics"],
                "address": "0.0.0.0:9598",
                "tls": {
                    "enabled": True,
                    "crt_file": "/run/tls/vector.pem",
                    "key_file": "/run/tls/vector.key",
                },
            },
        },
    }


def loki(retention_hours=720, *, retention_days=None):
    configuration = {
        "auth_enabled": True,
        "server": {
            "http_listen_port": 3100,
            "grpc_listen_port": 9096,
            "grpc_listen_address": "127.0.0.1",
            "log_level": "error",
            "http_tls_config": {
                "cert_file": "/run/tls/loki.pem",
                "key_file": "/run/tls/loki.key",
                "client_auth_type": "RequireAndVerifyClientCert",
                "client_ca_file": "/run/tls/client-ca.pem",
            },
        },
        "common": {
            "path_prefix": "/var/lib/loki",
            "instance_addr": "127.0.0.1",
            "replication_factor": 1,
            "ring": {"kvstore": {"store": "inmemory"}},
            "storage": {
                "filesystem": {
                    "chunks_directory": "/var/lib/loki/chunks",
                    "rules_directory": "/var/lib/loki/rules",
                }
            },
        },
        "schema_config": {
            "configs": [
                {
                    "from": "2024-01-01",
                    "store": "tsdb",
                    "object_store": "filesystem",
                    "schema": "v13",
                    "index": {"prefix": "index_", "period": "24h"},
                }
            ]
        },
        "storage_config": {
            "tsdb_shipper": {
                "active_index_directory": "/var/lib/loki/index",
                "cache_location": "/var/lib/loki/index-cache",
            }
        },
        "ingester": {
            "wal": {
                "enabled": True,
                "dir": "/var/lib/loki/wal",
                "flush_on_shutdown": True,
            },
            "max_chunk_age": "2h",
        },
        "compactor": {
            "working_directory": "/var/lib/loki/compactor",
            "compaction_interval": "10m",
            "retention_enabled": True,
            "retention_delete_delay": "2h",
            "retention_delete_worker_count": 8,
            "delete_request_store": "filesystem",
            # SQLite retires completed query-time filters after index propagation.
            # Loki imports the existing BoltDB once and dual-writes it for rollback.
            "delete_request_store_db_type": "sqlite",
            "backup_delete_request_store_db_type": "boltdb",
            "delete_request_cancel_period": "1m",
        },
        "limits_config": {
            "retention_period": f"{retention_hours}h",
            "reject_old_samples": True,
            "reject_old_samples_max_age": "720h",
            "ingestion_rate_mb": 4,
            "ingestion_burst_size_mb": 8,
            "per_stream_rate_limit": "2MB",
            "per_stream_rate_limit_burst": "4MB",
            "max_query_length": "744h",
            # This is one local Loki, so 30 days need 31 splits rather than 721.
            "split_queries_by_interval": "24h",
            "max_entries_limit_per_query": 5000,
            "allow_structured_metadata": False,
            "max_line_size": "4KB",
            "max_line_size_truncate": False,
        },
        "analytics": {"reporting_enabled": False},
    }
    if retention_days is not None:
        return apply_retention({}, configuration, retention_days)[1]
    return configuration


ALERTS = {
    "PipelineTargetDown": ('max(1-up{job=~"guard|vector|loki"})', "1m"),
    "PipelineMonitorStale": (
        f"time()-tianshu_obs_monitor_last_success_timestamp > bool {MONITOR_STALE_SECONDS}",
        "1m",
    ),
    "LocalMonitorFailed": ("1-tianshu_obs_monitor_success", "1m"),
    "CollectorDiscardOrError": (
        "sum(increase(vector_component_discarded_events_total[5m])) + sum(increase(vector_component_errors_total[5m]))",
        "0s",
    ),
    "StorageReject": ("increase(tianshu_obs_push_rejected_total[5m])", "0s"),
    "ApplicationLogBudget": (
        'max({__name__=~"tianshu_obs_source_.*_ratio",__name__!~".*filesystem.*"}) > bool 0.8',
        "1m",
    ),
    "DiskHighWater": (
        'max({__name__=~"tianshu_obs_(source_.*_filesystem|storage_.*)_ratio"}) > bool 0.85',
        "1m",
    ),
    "DiskForecast": (
        'min(predict_linear(label_replace({__name__=~"tianshu_obs_storage_.*_free_bytes"}, "storage_metric", "$1", "__name__", "(.*)")[1h:1m],86400)) < bool 1073741824',
        "10m",
    ),
    "RetentionFailure": (
        "(tianshu_obs_source_retention_failures > bool 0) + (tianshu_obs_retention_delete_failures_current > bool 0)",
        "1m",
    ),
    "VectorBufferHigh": (
        'sum(vector_buffer_size_bytes{component_id="loki"}) / sum(vector_buffer_max_size_bytes{component_id="loki"}) > bool 0.8',
        "1m",
    ),
}


def alert_rules():
    rules = []
    for name, (expr, duration) in ALERTS.items():
        rules.append(
            {
                "uid": "obs-" + name.lower(),
                "title": name,
                "condition": "B",
                "for": duration,
                "noDataState": "Alerting",
                "execErrState": "Alerting",
                "labels": {"stack": "tianshu", "severity": "warning"},
                "annotations": {
                    "summary": "TianShu logging requires attention: " + name
                },
                "data": [
                    {
                        "refId": "A",
                        "datasourceUid": "obs-prometheus",
                        "relativeTimeRange": {"from": 600, "to": 0},
                        "model": {
                            "refId": "A",
                            "expr": expr,
                            "instant": True,
                            "range": False,
                            "intervalMs": 1000,
                            "maxDataPoints": 43200,
                        },
                    },
                    {
                        "refId": "B",
                        "datasourceUid": "__expr__",
                        "relativeTimeRange": {"from": 0, "to": 0},
                        "model": {
                            "refId": "B",
                            "type": "threshold",
                            "expression": "A",
                            "conditions": [
                                {
                                    "evaluator": {"type": "gt", "params": [0]},
                                    "operator": {"type": "and"},
                                    "query": {"params": ["B"]},
                                    "reducer": {"type": "last", "params": []},
                                    "type": "query",
                                }
                            ],
                        },
                    },
                ],
            }
        )
    return {
        "apiVersion": 1,
        "groups": [
            {
                "orgId": 1,
                "name": "TianShu log pipeline",
                "folder": "TianShu",
                "interval": "30s",
                "rules": rules,
            }
        ],
    }


def project_existing(files):
    """Update our provisioned rules/panels while preserving unrelated entries."""
    retired = {
        "obs-integritymonitorstale", "obs-pendingdelivery", "obs-sequencegap",
        "obs-invalidorchangedsource", "obs-retrievalconflict", "obs-ledgercapacity",
        "obs-scanbehind", "obs-storagequeryfailed",
    }
    wanted = {rule["uid"]: rule for rule in alert_rules()["groups"][0]["rules"]}
    for name, value in list(files.items()):
        if name.startswith("observability/provisioning/alerting/"):
            try:
                item = json.loads(value) if isinstance(value, (str, bytes)) else value
            except (ValueError, TypeError):
                continue
            if not isinstance(item, dict):
                continue
            for group in item.get("groups", []):
                old = group.get("rules", [])
                if not any(r.get("uid") in retired | set(wanted) for r in old):
                    continue
                group["rules"] = [r for r in old if r.get("uid") not in retired]
                by_uid = {r.get("uid"): r for r in group["rules"]}
                for uid in ("obs-pipelinemonitorstale", "obs-retentionfailure", "obs-localmonitorfailed"):
                    if uid not in by_uid:
                        group["rules"].append(wanted[uid])
                discard = by_uid.get("obs-collectordiscardorerror")
                if discard:
                    for entry in discard.get("data", []):
                        if entry.get("refId") == "A":
                            entry["model"]["expr"] = ALERTS["CollectorDiscardOrError"][0]
                deletions = item.setdefault("deleteRules", [])
                existing = {(d.get("orgId"), d.get("uid")) for d in deletions}
                for uid in sorted(retired):
                    if (group.get("orgId", 1), uid) not in existing:
                        deletions.append({"orgId": group.get("orgId", 1), "uid": uid})
                files[name] = item
        elif name.startswith("observability/dashboards/"):
            try:
                item = json.loads(value) if isinstance(value, (str, bytes)) else value
            except (ValueError, TypeError):
                continue
            if isinstance(item, dict) and item.get("uid") == "tianshu-logs":
                replacement = {p["id"]: p for p in dashboard()["panels"][:4]}
                retired_metrics = ("pending_events", "sequence_gaps", "landed_events", "oldest_pending_seconds")
                item["panels"] = [
                    replacement[p["id"]] if p.get("id") in replacement and any(
                        metric in str(p.get("targets", [])) for metric in retired_metrics
                    ) else p for p in item.get("panels", [])
                ]
                if item.get("title") == "TianShu runtime and log integrity":
                    item["title"] = "TianShu runtime and log pipeline"
                files[name] = item
    return files


def datasources(ca):
    return {
        "apiVersion": 1,
        "datasources": [
            {
                "uid": "obs-loki",
                "name": "TianShu safe logs",
                "type": "loki",
                "access": "proxy",
                "url": "https://obs-guard:8443",
                "editable": False,
                "jsonData": {
                    "httpHeaderName1": "Authorization",
                    "tlsAuthWithCACert": True,
                    "maxLines": 1000,
                },
                "secureJsonData": {
                    "httpHeaderValue1": "Bearer $OBS_QUERY_TOKEN",
                    "tlsCACert": ca,
                },
            },
            {
                "uid": "obs-prometheus",
                "name": "TianShu pipeline metrics",
                "type": "prometheus",
                "access": "proxy",
                "url": "https://obs-prometheus:9090",
                "editable": False,
                "jsonData": {"tlsAuthWithCACert": True},
                "secureJsonData": {"tlsCACert": ca},
            },
        ],
    }


def prometheus():
    configs = []
    for name, target, path in (
        ("guard", "obs-guard:8443", "/metrics"),
        ("vector", "obs-vector:9598", "/metrics"),
        ("loki", "obs-guard:8443", "/backend-metrics"),
    ):
        cfg = {
            "job_name": name,
            "scheme": "https",
            "metrics_path": path,
            "tls_config": {"ca_file": "/run/tls/ca.pem"},
            "static_configs": [{"targets": [target]}],
        }
        if name in {"guard", "loki"}:
            cfg["authorization"] = {"credentials_file": "/run/secrets/metrics_token"}
        configs.append(cfg)
    return {
        "global": {"scrape_interval": "15s", "evaluation_interval": "30s"},
        "scrape_configs": configs,
    }


def dashboard():
    panels = []
    queries = [
        ("Vector buffer bytes", 'sum(vector_buffer_size_bytes{component_id="loki"})'),
        ("Rejected pushes", "increase(tianshu_obs_push_rejected_total[5m])"),
        ("Monitor healthy", "tianshu_obs_monitor_success"),
        ("Lowest storage free bytes", 'min({__name__=~"tianshu_obs_storage_.*_free_bytes"})'),
    ]
    for n, (title, expr) in enumerate(queries):
        panels.append(
            {
                "id": n + 1,
                "type": "stat",
                "title": title,
                "gridPos": {"x": 6 * n, "y": 0, "w": 6, "h": 4},
                "datasource": {"uid": "obs-prometheus", "type": "prometheus"},
                "targets": [{"refId": "A", "expr": expr}],
            }
        )
    panels.append(
        {
            "id": 5,
            "type": "logs",
            "title": "All registered events (no sampling)",
            "gridPos": {"x": 0, "y": 4, "w": 24, "h": 16},
            "datasource": {"uid": "obs-loki", "type": "loki"},
            "targets": [{"refId": "A", "expr": '{stack="tianshu"} | json'}],
        }
    )
    return {
        "uid": "tianshu-logs",
        "title": "TianShu runtime and log pipeline",
        "schemaVersion": 39,
        "version": 1,
        "refresh": "30s",
        "time": {"from": "now-1h", "to": "now"},
        "panels": panels,
    }
