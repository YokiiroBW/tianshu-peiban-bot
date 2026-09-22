# 报告 dep-d/1

本格式是 DEP-D 输出，非根共享发布清单 schema。`report.json` 的核心字段：

| 字段 | 含义 |
|---|---|
| report_version / kind | `dep-d/1`；主套件 `release_acceptance`；短观察 `readiness_observation`、单产品 probe `platform_component_probe` 均不能冒充主套件 |
| run_id | 此次运行 UUID，不复用旧执行 |
| mode / runtime_kind | `local|container` 与 `product|synthetic` 两个独立维度；preflight 无有效输入时 unknown/unverified |
| started_at / finished_at / duration_seconds | 实际 UTC 时间及 monotonic 实际秒数，不是计划时长 |
| binding | 输入 manifest/mapping 原字节 SHA256；release_id/status；四产品 repo/40位commit/image/digest；合同逐文件原字节 SHA256；feature/blocker原发布投影；identity_basis=static_input_only |
| input_sha256 | CLI运行输入的原字节摘要；直接调用的 selftest/component probe可能不具该字段 |
| results | `{case,status,code,duration_seconds,facts}`；status 为 pass/fail/dependency_missing/skipped/not_run；facts 是白名单有限投影，不是原 HTTP 正文 |
| claims | real_model_quality/browser_rendering/nas_acceptance/observation_24h 分别列明，不能由 mode 或别项 pass 推导 |
| verdict | 有 fail→failed；有非pass/空结果→incomplete；全pass但synthetic→synthetic_only；全pass的product→passed_with_limits。后者仍不是部署发布审批 |
| content_sha256 | 删除本字段后按以下算法编码并求 SHA256；不是数字签名 |

`runtime_binding` case 中的静态版本相符与适配器陈述不等价于镜像实际身份。DEP-A `evidence` 如需引用本报告，必须由协调核实际运行来源和环境，核文件原字节 SHA256/用例缺失/绑定，并使用 `kind=release_acceptance`。不得把它充作 linux_images、log_recovery、restore_drill；本目录没有这些授权能力。

hash 精确定义是 Python 3.12：

```python
json.dumps(payload, ensure_ascii=False, sort_keys=True,
           separators=(",", ":"), allow_nan=False).encode("utf-8")
```

无 BOM、无尾换行；对象键递归按 Python 字符串字典序排序，数组原顺序；NaN/Infinity/-Infinity拒绝，重复 JSON 键在读取时拒绝。Unicode不另做规范化。数字使用 Python JSON 编码（含 `1.0`），**不称 RFC 8785 canonical JSON**；跨语言消费者须复现此编码，或者调用本目录 `verify-report`。文件原字节摘要与 content_sha256不同：最终漂亮排版JSON有末尾 LF，DEP-A引用应 hash整文件原字节。

测试向量：输入对象 `{"z":1,"a":"天枢"}` 的编码字节应是 UTF-8 `{"a":"天枢","z":1}`。`verify-report` 只判内容是否被改，重算 hash 能制造新内容，因此不证明 passed 真实。

观察 journal 每行包含 run_id/sequence/timestamp/elapsed_seconds/gap_seconds/health/previous_sha256/sha256。sha256算法同上，删除本行 sha256 字段，首行 previous_sha256为64个0。观察报告保存整个文件原字节 SHA256和最后链头；实际不足86400秒、任一健康失败/中断或采样最大间隔超过计划+30秒不得声称24h通过。即使ready观察24h通过，observation_workload仍未执行。

固定样例是 [synthetic/suite/report.json](evidence/synthetic/suite/report.json)，其17个pass是运行器合成驱动验证，runtime_kind=synthetic、verdict=incomplete；[platform-login-final/report.json](evidence/platform-login-final/report.json) 是固定提交Platform单产品的实际TLS会话探针；都不构成当前四产品正式联合验收。

清单证据自引用限制：报告绑定**被测试的不可变清单**；将报告摘要加入清单 evidence 后新清单字节摘要发生变化。保留原清单与报告之间的绑定，不将改后的清单反填旧报告，最终verified由协调者审查，不从自填标签自动推导。
