# RBH-02 Models 连接证据交接

- 基线：`ec245f9e30f935f7bcadbc441373a45c9f9d07a4`；工作树 `C:/Users/Administrator/.codex/worktrees/rbf-models/tianshu-peiban-bot`，分支 `codex/rbh-models-20261105`。最终 SHA 由本提交及协调回执确定。
- 仅修改 Models 域、Models 定向用例和本交接；无新 SQL、共享类型、配置、模型绑定、凭据或超时变更。保留原 attempt、冻结 request/completion、数据代际和原有回执查询；未修改历史行、创建后继或调整领域预算。

## 权威回执和安全边界

- 在本次真实 HTTP 请求发生异常时，将 `rbh.transport.v1` 加入原 `ModelReceipt.diagnostic.transport`：`phase`、安全 `error_class`、有界 `os_errno`、安全白名单 `tls_reason`、经校验的 host/port、`headers_sent`/`body_sent`（true/false/null）和 `evidence`。沿用原 `status/result`、client/codec 与 `http_timing`，不新增平行回执 API。
- 可产生 `confirmed_not_sent` 的类型是 aiohttp 精确类型 `ClientConnectorError`、`ClientConnectorDNSError`、`ClientConnectorSSLError`、`ClientConnectorCertificateError`，不把子类或同名异常当作可信类型。必须处在 HTTP context 进入阶段，已观察 `connect_start`、未观察 `connect_end`、非复用、header/body trace 可用，且没有 headers/body/response/first body/first output/read complete、collector 输出、原 provider_handle 或 first_output_ms。只在此条件成立时两个发送字段为 false；有对应轨迹时为 true，不能证明时为 null。
- 原实现仅凭 `isinstance(ClientConnectorError)` 判未发送，会把响应中或关闭时冒出的同类异常误写成 `dispatch=not_started`。现在对缺少或矛盾证据保持 `sent_or_unknown`，实际中断后的未知状态不被当作未发。`TimedResponse` 保持已有上下文行为，并单独标识 `__aexit__` 实际抛出的关闭错误；已完整读完结果后的既有失败/结果语义保持，不将关闭错误当成建连失败。HTTP 返回错误及网络前普通 AppError 沿用原错误与结算，不能凭其诊断扣减连接预算。
- 端点只保留连接异常中的实际 hostname/port，无法安全校验时返回 null；其他异常仅从目标 URL 提取 hostname/port。TLS reason 只接收代码白名单，异常类型使用受限类名；不会写异常 message、完整 URL、path/query/userinfo、凭据、请求正文、提示词、供应商错误正文或 TLS 指纹。非白名单 reason 与未知 errno 返回 null。TLS 证书失败虽能证明无发送，不因此自动判断为可恢复；永久性分类、连接预算、限额及探针由 Runtime 独占。
- 原历史回执不会被补写或改判；Runtime 可按冻结合同用 failed/not_started/transport_not_sent、finished_at、原 diagnostic.category、first_output_ms/provider_handle 和完整 http_timing 精确识别具备历史证据的样本。缺任一证据、已发送或 unknown 继续使用原预算与回执。

## 用例、装配与未验证项

- `tests/models/test_rbh_transport_evidence.py` 编写了合成 aiohttp DNS resolver + 真正 trace 信号、DNS/连接/TLS/证书失败、trace 缺失、复用/发送/响应/输出/handle 的反例、非可信类型、TLS reason 与 URL 敏感信息、response/close 例外、原 Models 回执和 HTTP RPC 往返、历史 receipt 读取及普通 AppError 边界。其 provider 和 resolver 全为隔离合成对象，未访问真实模型。
- 已按约定做完整 diff 静态审阅、`git diff --cached --check` 和变更 Python AST 解析；本窗口**未运行测试、构建、烟测、真实模型、生产或 QQ**。协调者应在集成 Runtime 唯一证据校验后集中执行本文件及受影响的 `tests/models/test_rb_http_timing.py`、Models broker/receipt 测试和 Runtime 跨合同用例；来源/撤回、旧失败恢复和真实业务收敛须由原领域与协调者另行验证。
- `diagnostic.transport.error_class` 的四个可信类名和严格轨迹约束已直接回报 Runtime 与当前协调聊天；无共享装配需求。DNS/连接/TLS/关闭故障仅代表原尝试的可观测事实，不等同供应商或本机责任归因，也不代表业务已恢复。
