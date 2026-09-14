# TS-050 W5失败诊断：保持验收阻断

2026-09-14。协调者对 `479f45b` 的完整复跑为 **9通过、1失败，67.999秒**，未合入。旧Git内source-sync成功轨迹原样保留，不作为本次失败已消失的证据。本增量仅保存失败、改进成功断言/日志和复现传输关闭路径，**没有修复产品或解除验收阻断**。

## 三份不同证据

| 记录 | 实际结果 | 能证明什么 |
| --- | --- | --- |
| 协调原始W5失败 | 第二turn failed / dependency_unavailable / model_calls=0 / scope_version=null；没有第二模型请求，也没有相应Memory select入站记录。后台check修补事件范围后，Memory接受delivery=failed的输入候选。 | 原测试把consume接受误作对话成功，随后model_requests[1]触发IndexError。原日志未保存底层HTTP异常，不能事后补写成已抓到RemoteProtocolError。 |
| 固定交错复现 | **1项10.962秒**，通过表示成功复现缺陷。已复用连接无connect_tcp，发送头之前暂停5.2秒，让原Uvicorn实际5秒idle timer到期；恢复后receive_response_headers.failed=RemoteProtocolError，未有response_headers.complete，Memory未收到目标select。第二turn相同失败，第三轮新连接sent。 | 原Core客户端的过期复用连接可以触发同一失败路径；没有关闭keepalive、修改超时、替换server响应或patch产品。 |
| 被动W5单次观察 | **1项15.348秒正常**；第二轮初始select明确connect_tcp/start_tls，新连接收到HTTP200，两个turn均sent且各model_calls=1。 | 新日志及成功断言可用；这只是一次正对照，不覆盖协调原失败，也不证明竞态修复。其他9个成功场景没有重跑。 |

原件见 [协调W5原始轨迹](TS-050-w5-coordinator-trace.json)、[协调完整运行日志](TS-050-w5-coordinator.log)；复现异常、无头响应、连接事件、正对照及原始SHA见 [独立诊断数据](TS-050-w5-diagnostic.json)。

原协调数据的12个文件（10个场景结果、log、environment）在任何复验前按原字节复制到 `.runtime/ts050-source/diagnostics/coordinator-w5-failure-20260914/`，附 `sha256.json`，全部哈希复核一致。Git中原件只规范CRLF→LF，原字节SHA仍在诊断数据中；完整原字节保留在隔离档案。`--run-dir`令后续诊断不会覆盖该档案或协调失败的常规runtime结果。

## 根因判断与确定性复现

原失败的最后一次Memory identity/resolve结束约相对t=6.797秒；第二输入accepted_at为01:07:04.421，W5精确封存于01:07:09.421。两端默认值均为5秒：HTTPX池idle expiry从客户端释放响应计算，Uvicorn keepalive从服务端完成响应计算。两计时点和事件循环调度不完全一致，客户端检查/取出复用连接与服务端关闭之间存在交错窗口。

复现只用HTTPX正式request hook设置HTTPcore `request.extensions.trace`：在 `http11.send_request_headers.started`（已通过连接池取用检查、尚未发送头）等待真实server idle timeout+0.2秒。没有修改任何产品/依赖方法、socket、timer或HTTP响应。抓到的事件顺序：

1. 无connect_tcp，确认请求使用已有连接。
2. send_request_headers/body均在本地报告complete。
3. receive_response_headers.failed：`RemoteProtocolError: Server disconnected without sending a response.`，无头完成事件。
4. 实际Memory ASGI不存在该query的select，Core非bootstrap第二轮直接终结为failed。
5. 后台check与consume依然成功，事件aggregate_version=3、delivery_state=failed、reply_ids=[]、candidate accepted。

**原协调事件的具体底层异常未被记录。** 原W5表现与这个交错在时间边界、未进入Memory select、失败字段及后续候选行为一致，因而是高置信根因推断；确定性复现证明了客户端确实存在该失效路径，不将推断冒充原事件已直接捕获的事实。

Memory接受候选不是本问题：合同允许按真实输入提炼，failed/unknown回复不应取消已经受理的输入事实。需要分别检查“输入被消费”和“回复生成送达”。

## 产品归属与安全修复边界

固定Core `a759f1755c3e9b2afa6b6e5b3fd5e2b6b8072d79`：

- `src/tianshu_companion/clients.py:JsonService.call`把HTTPX传输异常统一转为dependency_unavailable并丢失类型。
- `src/tianshu_companion/core.py:Core._process`仅首次bootstrap对Memory 503做有限等待；普通后续轮次的查询传输失败直接failed，未生成任何回复。
- HTTPX默认keepalive=5，实际Memory Uvicorn keepalive=5；不是延长测试wait_commits可以解决的终态失败。

后续Core任务应评估**明确查询白名单与单次有界重试**，保留可诊断的传输错误分类/新连接行为、当前身份/范围/deadline检查；不将业务403/409、guard或owner水位503当作可绕过的缓存错误。避免关闭共享HTTPX客户端破坏T1/T2的其他在途调用。

没有响应头不代表生产环境请求一定未执行，本次“未进入Memory”的结论来自受控服务侧记录。因此不得统一重放Gateway生成、渠道send或其他可能执行的命令。register/ingest/revise/consume等写路径即使有幂等字段也要按各自契约另评，不应被通用网络重试顺带放行。实际修复由协调者分派，TS-050没有改产品快照。

## 测试与后续

W5先逐个断言Core `phase=sent`、`delivery_state=sent`、`model_calls=1`、committed_event.delivery=sent，再检查两个模型请求和prompt。失败时输出turn状态、timings、bootstrap/deadline及最近HTTP/传输事件；不改wait_commits本身的“真实输入消费”语义。被动HTTPcore日志仅记request_id/path/event/异常类型和响应状态，不记录凭据/请求头，不添加延迟或重试。

```powershell
& .runtime/ts050/venv/Scripts/python.exe -B scripts/integration/ts050.py run --pattern test_ts050_keepalive_diagnostic.py --run-dir .runtime/ts050-source/diagnostics/keepalive-controlled
& .runtime/ts050/venv/Scripts/python.exe -B scripts/integration/ts050.py run --pattern test_ts050_w5_observation.py --run-dir .runtime/ts050-source/diagnostics/w5-passive
```

以上两个诊断各运行一次，均完成清理；每个6次监听关闭检查无失败，Gateway进程停止。受控测试中的失败成功断言也被实际触发并验证含dependency_unavailable，避免再次只得到IndexError。没有重跑其他9个场景或篡改历史10/10轨迹；静态/格式/AST和本增量diff检查通过。

协调已启动Core TS-023。Memory TS-034新版本575941ee已另行集成，但本次仍固定原Memory ba0e50d，不提前升级或引用worker。待Core修复正式集成后，再由协调统一升级快照验W5与真实遗忘/画像；本窗口当前等待产品修复，完整矩阵仍partial且验收阻断。
