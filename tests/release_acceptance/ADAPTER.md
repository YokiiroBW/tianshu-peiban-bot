# 测试控制接口 dep-d/1

本接口仅属于 DEP-D 测试驱动，不发布到根 contracts，不在产品服务器注册 `/control`。必须只控制此轮专属隔离合成资源。生产/NAS/外部账号/真实模型不在实现范围。

输入中的 `adapter` 二选一：

```json
{"http":{"url":"https://127.0.0.1:19000","ca_file":"/isolated/adapter-ca.pem"}}
```

```json
{"command":["/isolated/python","/isolated/control.py"],"cwd":"/isolated/deployment","env_names":["SYNTHETIC_CONTROL_TOKEN"]}
```

HTTP POST `/control`，或者 argv 命令 stdin，收到一个 UTF-8 JSON object：`adapter_version=dep-d/1`、`operation`、下表参数。命令 stdout 只给一个 JSON object，退出码必须 0；stderr 丢弃，不落验收报告。响应不超过 1 MiB。所有成功响应都有 `adapter_version=dep-d/1`；不支持的操作返回 `status=unsupported`。未知结构/错误退出/超时是 fail。命令是执行者审阅的显式配置，运行器不把任意 command 视为沙箱。

| operation | 参数 | 必需返回事实 |
|---|---|---|
| identity | release_sha256 | scope=synthetic_isolated, runtime_kind=product/synthetic, release_sha256, products.{role}.{commit,digest}；容器还有 container_os=linux |
| configuration | 无 | basis=runtime_loaded, loaded_config_sha256四产品原配置文件摘要；必须从实际运行进程/装配确认取值，不能由请求参数/清单/CLI help推断 |
| state | run_id | scope=synthetic_isolated；非负整数 model_calls/send_calls/candidate_count/pending_candidates，计数从本次隔离环境启动持续，重启不能重置 |
| memory_candidate | turn_id | 原 turn_id, candidate_id, state=accepted/queued/consumed；从受理回执或公开读端口取得 |
| memory_finalized | candidate_id | 原 candidate_id, state=committed, memory_id, revision>0, readback_id=memory_id；真实消费结果与授权读回，queued 绝不算 |
| archive | turn_id | 原 turn_id, state=archived, archive_id, readback_id=archive_id |
| fault | name, enabled | 设置/撤销特定测试故障；撤销时必须 restored=true |
| restart | services | 只重启四列出服务；restarted 列表、before_instances/after_instances 四服务不同实例身份；等待服务可用后返回 |
| revoke_source | message_id | 公开来源撤销回执确认后 revoked=true；只撤本轮成功对话来源 |
| recall_revoked_source | message_id | 用公开授权召回观察 excluded=true, source_revision>0 |
| cancel_observation | turn_id, timeout_turn_id | 第一个轮次实际等到超时终结、第二个在途轮次公开取消；timeout_observed=true, late_reply_deliveries=0, duplicate_model_calls=0；须观察迟到窗口，不在请求取消当刻推断 |
| logs | correlation_id | lines 数组，源日志/DEP-B查询得到的原 JSONL 字符串（各含 LF）；不可重建伪造成功事件 |

故障 name：`model_disconnect_after_accept`（已受理后断流，必须产生 unknown）、`model_revoked`（公开撤销该测试版本，恢复使用安全的新版本，不复活旧授权）、`model_timeout`（保证足够的在途窗口供公开 cancel，随后观察迟到返回）、`logs_unavailable:platform|companion|memory|gateway`（该服务日志持久性失败，在副作用前拒绝）。

四产品并非都存在安全的运行时故障注入、候选消费/归档读回或在线撤销入口。适配器必须使用专属测试边界/录制服务器及公开运维命令，缺失返回 unsupported；不得用返回常量、跨库 SQL 或修改产品实现补齐。恢复失败后运行器停止后续变更；操作负责人仍需根据隔离部署状态完成清理。

identity 是适配器陈述，不能单凭请求携带的 release_sha256 原样回显就证明运行来源。实际适配器应核进程启动快照/容器 inspect 的镜像 ID，独立附不可变启动记录，再由协调者核对；报告会保留 `basis=test_adapter_attestation`。本目录 `fixtures.py` 的 identity/counters/restart 都是测试替身，模拟重启也不能声称真的重启了四产品。
