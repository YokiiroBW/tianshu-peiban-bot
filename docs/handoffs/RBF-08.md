# RBF-08 统计覆盖、事件归因与巡检摘要交接

- 工作树：`C:/Users/Administrator/.codex/worktrees/rbf-observability/tianshu-peiban-bot`
- 分支：`codex/rbf-observability-20261105`
- 基线：`33e5282f60fe4c803c3b16d498d1d924621ed1b9`
- 本卡代码与用例提交：`0ec7059f512db6001dce882edef3f28a1451afcd`
- 交付状态：本地实现、静态差异审阅及必要用例编写完成；尚未集成、执行测试或发布。

## 实现内容

1. 将渠道覆盖拆为入站事件 `channels.events`、出站调用 `channels.calls`、媒体补充 `channels.media_supplements` 三个真实生产者。实时投影和原有 `channels.statistics_reconcile` 作业共用同一账本；回填分页扫描并核对源记录与投影摘要，迟到或失败记录使覆盖保持未完成并从头继续。渠道事件和调用使用独立指标，不重复计为 Conversation 消息或送达；调用确认仅表示渠道调用确认。发送前从 `sending` 返回 `prepared` 时，投影根据持久事实变化递增版本。媒体重试在途不计为失败；事件被撤回后，媒体和恢复投影去除角色归属并标为未知。
2. 统计查询显示各生产者的来源、实时模式、回填状态和水位。旧 `life.planning` 的 `reason=live_producer` 且 `complete=false` 仅表示 `scanned_unverified`，不虚称已回填。业务投影、渠道传输、经领域验证的恢复、模型原始尝试和计费分别列示；未知单价/币种和缺少真实送达样本仍为未知或未证明。
3. 透传 Models 查询阶段、查得/缺失/不可用、原 attempt ID，以及数据库故障阶段和实际池名。仅 Models 明确标记的 `observe` 阶段 `attempt_not_found` 关联原 attempt 生成事件目标；`pre_submit` 的预期缺失不是事故，其他 404 不被全局屏蔽。已知数据库池/角色/阶段分别归因；无池证据的旧指纹保持不变。`job.deferred` 为非终态事件，不按失败制造事故。
4. 新增 `scripts/ops/rbf_monitor_summary.py`。脚本按源文件快照边界增量读取完整 JSONL 行，在独立 SQLite 派生索引保存字节位置、哈希和有限元数据；输出累计、滚动窗口、本次新增、最新问题及历史恢复证据，保留冲突证据，不由健康推断业务成功。轮转/截断、坏行、半行、源不可用降低输入覆盖；实际币种费用没有明确来源时保持未知。`--show-last` 可读取已提交摘要，不覆盖输入日志。
5. 根据协调者补充任务，仅对 `channels.ingest` 开启 `resource_recovery`。保留原持久 event key、Conversation input 与渠道只读 action 回执；Sources 抛出的 `RecoveryWait` 穿透 ingress、媒体和转发的降级捕获并在事务退出后交原任务恢复。发送任务未开启此机制，未知发送继续观察原回执，不自动重发。

## 集成接口和执行顺序

- 先集成 Runtime 的 `tianshu.runtime.recovery_policy.RecoveryWait` 和 `jobs.register(..., resource_recovery=True)`；本卡基线没有这些接口，单独检出本提交不可启动完整产品。Sources 的 typed wait 使用此同一类型。
- 集成 Models 的 HTTP 标记：`request['model_attempt_id']`、`request['model_lookup_phase']`（`pre_submit` / `observe`）、`request['model_lookup_state']`（`found` / `absent` / `unavailable`）。该接口对应 Models 窗口的提交及其最终后续提交，按协调者确认的依赖顺序合入。
- 共享日志文件由协调者单写：`runtime/rb_logs_output.py` 的 `SAFE_FIELDS` 与 `runtime/rb_logs_query.py` 的 `changes` 输出白名单均须增加 `lookup_phase`、`lookup_state`、`failure_phase`、`pool_name`。否则 Formatter 或 EventLog 的信息会在后续链路丢失；`tests/runtime/test_rbf_events.py` 中有 Formatter → Supervisor 输出 → LogQuery → incident 的回归用例。
- Life 实现窗口需要提供有界回填进度和完成证据。推荐 detail 形状为 `{'mode':'live','backfill':{'state':'pending|failed|complete','as_of':...}}`，只有该领域确认积压及失败投影已覆盖才设置生产者 `complete=true`；不能仅因服务持续运行或扫描过一次置真。
- 没有新增数据库表、列或迁移。新增的巡检 SQLite 是协调者在独立路径创建的可丢弃派生文件，并非产品数据库。不要将索引或摘要路径设为两个原 JSONL 日志中的任一文件；不改现有巡检定时配置，实际接线由协调者统一决定。
- 示例接口（仅示意，未执行）：`python scripts/ops/rbf_monitor_summary.py --observations <observations.jsonl> --issues <issues.jsonl> --state <separate-index.sqlite> --output <separate-summary.json> --window-hours 24`。恢复上次已提交摘要可用 `--state <separate-index.sqlite> --show-last`。

## 验证与限制

- 已静态审阅本卡完整 diff，`git diff --cached --check` 通过；对 18 个代码和回归文件做了 Python AST 静态解析，未导入或运行产品。
- 未运行 pytest、构建、烟测、真实模型/QQ、生产读写或部署。下列用例在 Runtime、Models 和日志白名单集成后由协调者集中执行：

  `python -m pytest tests/operations/test_rbf_statistics.py tests/operations/test_rbf_monitor_summary.py tests/runtime/test_rbf_events.py tests/channels/test_rbf_statistics_recovery.py -q`

  涉及 PostgreSQL 的用例沿用 `tests/conftest.py` 的隔离测试库要求；未配置明确的临时测试 PostgreSQL 管理 DSN 时会跳过，跳过不是通过。
- 入站资源等待保留原任务和原来源身份；业务是否最终完成、Models 真实回执状态和渠道真实送达仍需协调者在最终集成后按正常领域入口验证。不得用统计覆盖、容器健康或空队列代替实际业务证据，也不得直接清理源表或盲目重放未知外部副作用。

## HTTP 类型化等待补充

- 在本交接后的窄提交中，`runtime/api.py` 复用 Runtime 的 `recovery_wait_to_wire`，仅对真实 `RecoveryWait` 在 `error.recovery_wait` 放置版本化 envelope，保留既有 `error.code/message/details/request_id` 和 HTTP 409；Models 端负责按固定合同严格解码。普通 409、503 和相同错误码的普通 `AppError` 均不会得到此 envelope。
- 仅当 envelope 的 `rollback_confirmed` 明确为 true 时，HTTP 事件标记为 `deferred`；false 或证据不足仍标记为 `failed`，不伪称业务成功。无法序列化的无效等待元数据返回内部错误而非可重试包。Models lookup 与池归因字段沿用原有接线。
- `tests/runtime/test_rbf_events.py` 增加本地/远端回滚证据组合、普通错误与无效等待元数据的回归。该补充同样只做静态审阅与 AST 解析，测试留给协调者在 Runtime/Models 合同集成后执行。
