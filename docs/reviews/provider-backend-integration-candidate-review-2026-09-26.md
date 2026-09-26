# 模型供应商自助配置后端贯通候选：独立审查

审查日期：2026-09-26

## 固定基线与方法

| 范围 | 固定提交 |
| --- | --- |
| 根协调候选 | `c644b7bd5f557c1488b4289581d9f536c23cf32f` + `d4fdf5c94be3234b069237e936d59773acd36965` |
| 平台 P1 | `5b16d0e52f7851d4da6a22175ee5563591ebb999` |
| 网关 G1 | `294f117f2fdccb05efdf2f9c0e6afc68678d887c` |
| 陪伴 C1 | `87da6a02cfba02a65f495c1589af3ae75aa2d52e` |

只读核对了固定根候选的 `contracts/provider-self-service/v1`、后端交接、根联合测试，以及三个固定产品工作树中的相关实现与专项测试。未运行测试，也未改作者工作树、NAS、生产配置或真实供应商数据。产品交接记录的测试结果是执行者报告；本次不是独立复跑。

## 发现

### [P2] 已入队消息会在后台选择当时读取新默认

位置：C1 `src/tianshu_companion/core.py:779-813, 1023-1052, 1151-1173`，固定提交 `87da6a02cfba02a65f495c1589af3ae75aa2d52e`。

新消息持久化成 `phase="queued"` 的 turn 时，动态模式下 `config_version` 还是 `None`。后台 `tick()` 将它推进到 `preparing`，之后 `_process_turn()` 才调用 `resolve_selection()` 并固定返回的版本。于是消息已经被接收、仍在队列里时切换默认，消息随后会使用新默认。

现有 `tests/test_model_selection.py` 覆盖 selector 已启动后的切换和慢响应；根联合测试也在直接 selector 接线后运行 Core。它们没有覆盖“消息已入队、selector 尚未开始”这段窗口。合同 README 第 6、81-83 行把版本选择放在新 turn admission，并要求在 Memory/生成前持久固定；交接也称默认切换只影响新回合。当前实现的边界晚于消息接收。

建议在消息受理时保存可重放的版本或默认修订令牌，再由后台按令牌完成精确选择；并覆盖入队后切换默认的用例。

### [P2] 排队中的 provider view 可在管理员锁定后返回 URL

位置：平台 `services/platform/provider_management.py:82-92`、`services/platform/web_console.py:578-582`、`services/platform/web_models.py:231-234`、`services/platform/provider_catalog.py:536-553`，固定提交 `5b16d0e52f7851d4da6a22175ee5563591ebb999`。

`ProviderManagement.route()` 在入队前检查管理解锁，但 `view` 交给 `LocalWork` 的是裸 `catalog.view`，worker 开始时没有再次检查。`/api/web/models/lock` 会从同一 session 移除 `management` 字段；session 本身仍有效。provider 响应发出前，`WebConsole` 只验证 session 仍是当前且仍存活，不再检查管理租期。若 `view` 在 LocalWork 中排队，管理员可以先锁定，排队任务之后仍返回 `catalog.view()` 中的 `base_url`，违反合同中“URL 只对已解锁管理员可见”的约束。

同一队列边界还影响完整登录 authority：provider 写操作的 worker `_write()` 调用 `_gate()`，而 `_gate()` 只查 `session_live()` 和 `models._gate()`。它没有像 `web_models.py:243-255` 一样再次调用 `session_valid()`。后者会重新计算 authority 并经 `auth.authenticate()` 检查当前 operator principal 是否仍被撤销（`web_console.py:204-243`、`auth.py:222-240`）。因此，若请求入队后 administrator principal 被撤销，当前 provider worker 的管理动作检查仍可能通过静态配置的 action 列表并执行。

建议在 worker 开始读取/写入前重新验证完整 session authority 与管理租期；返回 provider view 前也再次确认 lease 仍有效。用锁定和 principal 撤销各覆盖一次排队窗口。

### [P2] paid test 的最终结果与幂等回执分两次提交

位置：平台 `services/platform/provider_management.py:189-210`、`services/platform/provider_catalog.py:423-446, 448-496`，固定提交 `5b16d0e52f7851d4da6a22175ee5563591ebb999`。

测试上游返回后，平台先通过一次 `LocalWork.run()` 调用 `record_test()`，其 `_mutate` 事务提交成功结果；随后再通过另一次 `LocalWork.run()` 调用 `settle_test()`，把同一个 `client_id` 的 replay receipt 从 `pending` 改成 `settled`。进程若在这两个事务之间退出，供应商测试结果已经写入，但 `test_attempts` 仍是 pending。重启后用相同 ID 重放会在 `claim_test()` 处返回 `result_unknown`，不会再次发出付费请求，却也无法返回合同要求的原始成功回执。

联合测试覆盖了完整 settle 后的相同 ID 重放，以及取消后 unknown 不重发；没有覆盖两次提交之间故障。建议把 provider test 结果和 attempt receipt 合入同一数据库事务，或实现可确定恢复原回执的恢复路径，且不重发上游调用。

### [P2，条件性] 公网 IPv6 检查未识别 NAT64 嵌入的内部 IPv4

位置：G1 `src/tianshu_gateway/provider_adapter.py:47-58`，固定提交 `294f117f2fdccb05efdf2f9c0e6afc68678d887c`。

策略拒绝 IPv4-mapped、6to4、Teredo 等地址后，其他 IPv6 直接以 `ip.is_global` 判定。`64:ff9b::a9fe:a9fe` 编码 `169.254.169.254`，在当前 Python `ipaddress` 属性核对中仍是 `is_global=True`，也不命中上述格式检查。因此，当部署使用相应 NAT64/地址翻译路由，且供应商域名解析到该地址时，目标分类可能放行到内部 IPv4 的连接尝试。RFC 6052 描述 IPv4 嵌入 IPv6 的格式；Python 文档说明 `is_global` 的地址分类语义。[RFC 6052](https://datatracker.ietf.org/doc/html/rfc6052) [Python `ipaddress`](https://docs.python.org/3/library/ipaddress.html)

这只说明目标检查及网络路由可能允许连接，不证明 API Key 泄露。网关仍以原供应商主机名进行 TLS 校验，只有 TLS 握手成功后才发送 Authorization；内部目标还需提供受信且主机名匹配的证书。本次没有目标部署的 NAT64 路由证据。建议纳入部署使用的 NAT64 前缀，或在出站网络策略中阻断指向内部 IPv4 的翻译目标，并增加隔离解析器覆盖。

## 已核对的边界与通过项

- **内部身份与 turn 绑定：** 平台 `ProviderAuthority` 分别要求 `config.select` 的 companion 身份和 `provider.runtime` 的 gateway 身份；runtime 请求只接受版本、调用服务、工作负载和 turn ID。grant 将 turn、scope digest、版本、调用服务和工作负载绑定。浏览器不能选择运行时 provider ID。网关在调度后、建立上游会话后重新读取并比较同一动态授权。旧版本的运行上下文还通过当前 provider revision 检查；编辑、停用、清密钥、删除都会使旧修订不可执行。默认切换保留仍有效的旧 turn grant。这部分实现与合同相符。
- **明文凭据边界：** 上游 key 仅由平台私有目录加密保存，runtime 私有响应才提供给网关；联合测试检查了浏览器 projection 和响应没有 key。内部平台与网关若在私网 HTTP 上传输服务凭据及 key，则仍依赖部署网络隔离或另配 TLS；交接将其列为待部署确认项，本报告没有把它写成固定代码缺陷。
- **HTTP 范围：** 本轮 provider `base_url` 是 HTTPS，网关 `models/test` 也按公共 HTTPS 策略执行。合同另行允许内部服务在私网 HTTP，但要求隔离或 TLS。`http://NAS:18446` 是浏览器访问网页的入口，不能据此推导本轮应支持 HTTP 上游；之前的 HTTP 范围疑点关闭，不列为发现。
- **联合测试边界：** 根联合测试确实启动平台 aiohttp 路由、网关路由和录制 TLS 上游，并用 `Harness` 贯通 Companion Core；但 Companion 侧用本地测试 `LocalServiceClient` 和手工构造的 `HttpDefaultModelSelector`，没有走生产 `build_runtime`/`JsonService` HTTPS 与 CA 装配。后端交接已明确这一限制，也说明无 NAS、真实供应商或部署验证，因此测试结果没有被本报告扩写为完整生产链证明。
- **前端证据：** 协调方补充，前端固定提交 `a23f1de` 的本地平台、网关与录制 TLS 上游浏览器链路为 4/4；当前仍没有完整 Companion 浏览器回复链的证据。本项是协调方提供的信息，本次未审查该前端提交，故应作为局部验收证据，不代表完整端到端验收。

## 本次结论

固定候选有四项需要修复或明确接受的发现：消息 admission 的模型版本固定过晚、provider 管理请求队列后的 authority 检查不足、paid test 回执不能跨进程故障原子重放，以及条件性的 NAT64 目标分类缺口。service identity、精确 grant、旧 turn revocation/lease 路径总体符合合同。生产配置、网络隔离、真实供应商和完整 Companion 浏览器链尚未验收。
