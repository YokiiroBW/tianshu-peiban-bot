# DEP-B 首验返修：总期限与资源回收

返修基线`8f1e0b3bf1b4423355891975ebd61bbeaa7226b6`。协调首验发现原10秒只为空闲超时，0/6/12秒三个字节会在12秒仍成功；原始反例`review-slow-result.json`不改写。该目录只保存本轮新增证据，旧640/TTL/8条证据留在原目录，不能给它们换成返修后源码标签。

守卫准入使用共享单调10秒总期限，覆盖TLS/头/体/后端/响应；查询完整请求与range全部分页同样受共享10秒约束。`transport.py`用非阻塞socket/TLS及最多50ms取消观察间隔，没有后台socket读线程。8槽/4MiB写入/8MiB响应、TLS验证、角色隔离和无自动HTTP重发保持。

DNS只能保证调用方等候有界：系统getaddrinfo无法强制取消，最多一个daemon解析线程和一个队列项，缓存30秒/64项，相同目标合并；卡死后未缓存请求超时/容量拒绝。IP无需DNS。OS调度、有限TLS/JSON CPU工作、本地挂死文件系统不能承诺硬实时中断，1.5秒测试容差不是额外运行预算。

## 真实执行

```powershell
python -B tests/deployment/observability/test_deadlines.py --output tests/deployment/observability/.runtime/deadline-report-first.json
python -B tests/deployment/observability/test_deadlines.py --output tests/deployment/observability/.runtime/deadline-report-final-query.json --case test_query_slow_response --case test_range_pages_share_deadline --case test_query_slow_response_headers --case test_query_stalled_tls_handshake
python -B tests/deployment/observability/run_final_smoke.py --loki tests/deployment/observability/.runtime/loki.exe --contract C:/YOKI/Codex/tianshu-peiban-bot/contracts/diagnostics/v1 --output tests/deployment/observability/evidence/2026-09-22-r1/final-guard-query-smoke.json
```

首条当时运行10项，73.930秒、0skip全部通过；随后仅添加range返回前期限复核及两个出站探针，按受影响路径跑第二条4项，41.387秒、0skip全部通过。当前完整入口包含12个不同用例，**没有声称最后一次整套12项执行**。两个报告分别保留真实源码hash；guard/transport与最终源码相同，首轮query hash与最终差异是range返回前复核。报告的源码运行前后均未变化。index另列最终工作字节与Git LF blob比较hash，明确Windows字节差异。

| 场景 | 实际观测 | 判据 |
|---|---:|---|
| 未鉴权慢头，0/6/12秒滴流 | 10.000秒 | 关闭，槽恢复，后续正常请求成功 |
| 认证慢体，0/6/12秒滴流 | 10.000秒 | 关闭，后端push数0，槽恢复 |
| 守卫TLS握手不发送数据 | 10.000秒 | 关闭并释放槽 |
| 6秒body + 6秒后端 | 10.000秒 | 共用父预算，没有等到12秒成功 |
| 响应8MiB但客户端不读 | 10.015秒 | 写出到期、释放槽并恢复 |
| 查询响应体持续滴流 | 10.000秒 | query_deadline_exceeded，active连接0 |
| 查询响应头不闭合 | 10.000秒 | query_deadline_exceeded |
| 查询对端停滞TLS握手 | 10.015秒 | query_deadline_exceeded，连接清理 |
| range每页延迟6秒 | 10.000秒 | 仅发两次，没有给第二页重置10秒 |
| 客户端close取消响应读取 | <1毫秒（报告四舍五入0.000） | 0.5秒测试容差内结束，active0 |
| 守卫close取消握手 | 0.062秒 | 0.5秒测试容差内释放槽 |
| 卡住的合成DNS解析 | 0.203秒 | 显式父预算0.2秒，线程数量1，未声称杀掉OS调用 |

网络全部真实loopback TLS；上游为明确合成HTTP替身，慢响应不伪称Loki行为。生产10秒未在这些慢网络用例中调小；1.5秒调度容差单独记录。只有DNS用更短显式父预算验证预算传播。

返修后同一`run_final_smoke.py`再次验证真实guard→Loki3.7.8→query：8产生/8落地/8检索、全部差异0；报告保留完整输入/实际检索响应、配置、源码及二进制hash。角色401、4MiB+1写入413、8槽满后第9拒绝且恢复查询、8MiB响应边界亦通过。最后大响应仍来自明确HTTPS替身，不冒充Loki大响应；不包含Vector/后台Ledger，不代替640恢复或Linux栈。

受影响原测试：TLS4项1.751秒通过；ReconciliationTests+ConfigurationTests共9项0.591秒通过，含新模块随生成包复制、原字节合同、权限/TLS、分页饱和；Ruff和完整diff检查通过。没有重复640或修改产品/合同/DEP-D实现。

Linux/Grafana装载与权限、物理满盘/满buffer、通知/栈外探活、生产720h/2h默认查询路径保留、24h/30天、NAS及应用安全回收仍未通过，应用回收授权false。
