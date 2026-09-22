"""Single owner of generated Vector/Loki/Grafana/Prometheus configuration."""

import json

from policy import FIELDS, LEVELS, OUTCOMES

PRODUCTS = ("platform", "companion", "memory", "gateway")


def vector(snapshot, buffer_bytes=4 * 1024**3):
    sources, transforms = {}, {}
    for service in PRODUCTS:
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
        registry = snapshot["products"][service]
        allowed = [service] if service != "memory" else ["memory", "memory-knowledge"]
        code = [
            "raw = string!(.message)",
            'assert!(length(raw) <= 4095, "invalid_safe_event")',
            'assert!(!contains(raw, "\\r"), "invalid_safe_event")',
            ". = object!(parse_json!(raw))",
            "assert!(length(keys(.)) == 12 && "
            + " && ".join("exists(." + field + ")" for field in sorted(FIELDS))
            + ', "invalid_safe_event")',
            'assert!(.schema_version == "1.0.0", "invalid_safe_event")',
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
                "inputs": [p + "_safe" for p in PRODUCTS],
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


def loki(retention_hours=720):
    return {
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
            "max_entries_limit_per_query": 5000,
            "allow_structured_metadata": False,
            "max_line_size": "4KB",
            "max_line_size_truncate": False,
        },
        "analytics": {"reporting_enabled": False},
    }


ALERTS = {
    "PipelineTargetDown": ('max(1-up{job=~"guard|vector|loki"})', "1m"),
    "IntegrityMonitorStale": (
        "time()-tianshu_obs_monitor_last_success_timestamp > bool 120",
        "1m",
    ),
    "StorageQueryFailed": ("1-tianshu_obs_monitor_success", "1m"),
    "PendingDelivery": ("tianshu_obs_oldest_pending_seconds > bool 120", "1m"),
    "SequenceGap": ("tianshu_obs_sequence_gaps", "2m"),
    "InvalidOrChangedSource": (
        'sum({__name__=~"tianshu_obs_(invalid_source_lines|identity_conflicts|source_changed)_total"}) or vector(0)',
        "0s",
    ),
    "RetrievalConflict": (
        'sum({__name__=~"tianshu_obs_(retrieval_conflicts|invalid_retrieved_lines)_total"}) or vector(0)',
        "0s",
    ),
    "CollectorDiscardOrError": (
        "(sum(increase(vector_component_discarded_events_total[5m])) or vector(0)) + (sum(increase(vector_component_errors_total[5m])) or vector(0))",
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
        'min(predict_linear({__name__=~"tianshu_obs_storage_.*_free_bytes"}[1h],86400)) < bool 1073741824',
        "10m",
    ),
    "LedgerCapacity": ("tianshu_obs_landed_events > bool 900000", "1m"),
    "ScanBehind": ("tianshu_obs_scan_budget_exhausted", "5m"),
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
                "name": "TianShu log integrity",
                "folder": "TianShu",
                "interval": "30s",
                "rules": rules,
            }
        ],
    }


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
        ("Pending events", "tianshu_obs_pending_events"),
        ("Sequence gaps", "tianshu_obs_sequence_gaps"),
        ("Landed events", "tianshu_obs_landed_events"),
        ("Oldest pending (seconds)", "tianshu_obs_oldest_pending_seconds"),
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
        "title": "TianShu runtime and log integrity",
        "schemaVersion": 39,
        "version": 1,
        "refresh": "30s",
        "time": {"from": "now-1h", "to": "now"},
        "panels": panels,
    }
