# NR-06B：Growth 失败让位与持久重试

本卡为 `local_implemented_not_tested_not_deployed`。用户本轮明确授权：纯计算发生可恢复失败后保留原请求/回执，创建有历史链接的新 attempt；失败批次让位，不再永久挡住后续未冻结材料。未访问生产、凭据或真实模型，未运行测试、类型检查或构建。

## 基线与提交

- 工作树：`C:\Users\Administrator\.codex\worktrees\tianshu-growth-retry-20261103\tianshu-peiban-bot`
- 分支：`codex/nr06b-growth-retry-20261103`，基线 `c218515c`。
- 共享纯 helper：`249326745a76e5cfb9875a8847aa6450069c968b`。
- 明确的消费者/传输中断分类：`afd44032d3da516179816038b5bd8271edc4113a`。
- Growth 产品、迁移、界面及相关用例：`a1cbac4e47f54cf2d0f3a0564c269087755041d6`。
- 集成依赖 A 的 `d6139830` provider session 隔离与 `b8ee1ce5233a0beb7cb1dab8ec0d384980c593ea` cancellation reason（根已装配到 `95c2ef65`）。本卡没有修改这些共享字段或旧 request codec。

## 实现与归属

`models/retry.py` 只有纯错误分类和退避计算，无调度、存储或外部效果重放。`transient_model_failure(error_code, cancelled=False)` 对 timeout、连接断开、已知 transport interruption、Broker 中断、429/5xx 等返回可恢复；权限、来源/撤回、业务到期、明确取消、配置与身份错误优先排除。`cancelled_unknown` 与未识别错误保持保守。可信的新 `consumer_interrupted`、`transport_interrupted`、`broker_stopping` 可恢复。`retry_delay_seconds(1..)` 为 30、60、120、240、480、900 秒，之后封顶 900 秒。

`companion/growth_retry.py` 持有本域重试编排。失败事务释放 `organize_state.active_batch`，用独立批次行和 `growth_inputs.retry_key` 预留原 Episode 精确版本；普通整理只选择未预留的新材料。暂时运行失败进入 waiting 并排 due job；永久或未知错误进入 blocked，无自动重试。成功但 JSON/schema/条件校验失败也只阻塞自己的批次，保留显式复核入口。旧 hard identity conflict 和尚未完成授权的 legacy inventory 防护保持原有行为，见局限。

到期任务先读取原 Models 回执：queued/running 继续观察；已成功则消费同一原结果；确认暂时失败才冻结新的 intent/attempt，保留旧 GenerationRequest 和 Models 回执。重新安排前再次读取最新 cancellation_reason，`explicit_cancel` 优先于原 consumer_interrupted，停止创建后继。单个逻辑重试每次只占一个 Growth 执行位，失败再次释放 owner 回到队尾。占位冲突、提前唤醒和重启通过既有 JobQueue coalescing 与本域 revision/CAS 继续，不另建 scheduler，不把通用 job retry 打开。

新 reflect 请求仍为 execution 180 秒，普通排队不扣执行时间，无默认队列 TTL；保留原真实 business deadline。新后继使用隔离的 `provider_session_scope='intent'`；不改旧 request 的 scope、deadline、消息、来源、参数或原始回执。原请求已成功而仅本地应用/读取暂时失败时，只消费原成功结果，不再调用模型。

应用时按当前 Sources、Episode 版本、owner 与 retry revision 复核，事务中保留现有 Memory 写入及最终 CAS；晚到旧结果不能重复应用。cursor 和 `reviewed_material_at` 单调。若较新的材料已经更新状态，较旧重试只补原发生时间的历史 reflection，不激活旧 style/interest/affect，记录 `projection_superseded_by_newer_material` 及 `historical_only`。旧身份转换先核验原完整输出，再作历史投影筛选；让位的 legacy manifest 只有通过来源/版本与 claim CAS 后，才可临时恢复 pending 消费确切原结果，最终成功才 reviewed。

## 迁移与根装配

本域 SQL：`src/tianshu/companion/growth_retry.sql`，注册 ID **`0037_companion_growth_retry`**。module 本域 migration tuple 已注册；共享 `runtime/migrations.py` 由根统一登记。

- `companion.growth_inputs.retry_key text`：仅预留当前失败版本；更高 Episode revision 到达时清空，允许新版本正常处理。
- `companion.organize_state.reviewed_material_at timestamptz`：已应用材料最大发生时间，防止较旧重试覆盖新投影。
- 新 `companion.growth_retry_batches`：逻辑 retry_key、当前/前一 material key、批次来源及版本引用、attempt_number、state、due_at、last_error、观察标记、append-only history、outcome 和 revision。原内容与 provider 回执仍保留在原账本，不复制一份平行执行账本。

module 新任务 **`companion.growth.retry`**，处理签名 `(actor_id, scope_key, retry_key)`，`retryable=False`。请根在共享执行映射登记到既有 **GROWTH** lane。物理 job key 唯一，queueing_lock 为逻辑重试根，payload 稳定；模型尝试和状态 CAS 由持久重试行控制。没有修改 runtime/jobs.py、runtime/execution.py 或全局迁移主线。

## 界面增量合同

仅 GrowthView 增加可选字段，原界面字段和 Experience trace 入口保留：

- `retry_summary.waiting/active/blocked`：本 scope 各状态总数；`next_retry_at` 为 waiting 的最早 due 时间。
- `retry_batches`：最新/优先待办最多 20 条。`attempt_number` 是已安排的模型尝试次数（首个为 1），不是自动任务唤醒次数。`attempt_id` 对应 current_material_key；previous_material_key 与 retry_key 保留链路。
- `waiting_on_original=true` 表示等待原排队/执行/读取结果，不代表已经发起新请求。
- `outcome=historical_only` 表示只补历史手记，未覆盖较新的当前状态；`applied` 为正常完成。
- `can_request_review` 只有 blocked 且现有同场景授权允许显式复核时为 true。按钮仍调用既有 `companion/growth/review`，添加 `retry_key`，保留 scope revision CAS；waiting 不显示重复提交按钮。

界面展示下次时间、次数、原执行 ID、停止原因及“后续经历可以继续整理”。没有修改全局 web api/contracts.ts 或整体页面布局。

## 已做静态检查与集中验证 targets

实际仅完成 `git diff --check` 和 bundled Python `-B` 的 `ast.parse`：8 个修改/新增 Python 文件语法可解析；没有应用导入、测试运行、SQL 执行或浏览器运行。这不构成运行时通过证据。

根统一执行（复用既有隔离 PostgreSQL+pgvector fixture，不使用生产 DSN）：

- `tests/models/test_retry.py`：共享暂时错误白名单、永久/显式取消优先级、30/60/120 至 900 退避。
- `tests/companion/test_growth_retry.py`：unknown 让位、新旧版本隔离、队尾推进、第二次失败退避、live/成功原结果观察、源/版本/权限复核、重启、晚到结果、显式取消竞争、旧 active unknown 正常 recover、成功结果应用阶段中断与历史投影保护。
- `tests/companion/test_growth_output.py`：整份输出校验、原 fenced JSON 复核、RPC 原结果观察、失败隔离/显式复核、版本覆盖与无部分写入。
- `tests/companion/test_growth_legacy_recovery.py`：原 context/请求/source/身份转换兼容、失效授权、legacy manifest 与新版 retry 衔接。按根既定方案可与其他 Companion 定向用例合为一次 `tests/companion` 运行，无需重复跑本文件。
- 前端复用根的 `npm run typecheck` / `npm run build`（build 自带 typecheck，可合并一次），浏览器仅 `web/tests/growth-coverage.spec.ts`：原 2 场景加 1 个 waiting+blocked 混合场景，验证 due/次数/不阻挡文案/唯一显式复核按钮及 retry_key+revision 请求。

## 部署恢复建议与局限

不需新增作者配置。由根仅备份/迁移受影响 Companion 表，串行登记迁移及 GROWTH 任务后，正常 module startup 的 `growth.recover()` 会为现有 active/待处理材料及持久 retry 行排既有任务。智乃原 transport_unknown attempt 可由正常 organize 原结果读取转入 waiting，释放其原批次；21 条积压中不属于该冻结批次的版本随后继续。到期重试再次复核原结果及 Sources，才决定是否创建链接的新 attempt。无需手工改原 Models、GenerationRequests、cursor 或回执，不启动第二套 worker。

自动重试只覆盖已知暂时运行失败；JSON/schema/条件等语义错误显式 blocked，需要原复核入口。旧 `reflection_identity_conflict` 仍是原身份账本一致性 hard blocker；未完成授权的 legacy inventory 仍需授权恢复。本卡不将身份冲突、撤回或权限拒绝误分类成自动重试。外发、发布、工具副作用、Life 日记与 Memory 重试不在本域实现内。

参考：`docs/development/nr06-diary-and-computation-retry-2026-11-03.md`。提交只是实现候选，集中验证、根串行集成、迁移及真实运行验收由根完成。
