# RBF-03 Memory 等待恢复与材料重复归因交接

## 范围和状态

- 工作树 `C:/Users/Administrator/.codex/worktrees/rbf-memory/tianshu-peiban-bot`，分支 `codex/rbf-memory-20261105`，基线 `33e5282f60fe4c803c3b16d498d1d924621ed1b9`。本卡提交 SHA 见本文件所在提交。
- 仅修改 Memory 模块、本卡测试和本交接；未运行测试、构建、烟测，未访问或修改生产、真实模型、QQ 或其他设备。本卡不是集成、部署或业务恢复验收。

## 改动和合同

- 将准备、纠正和上下文补充的原批次/请求先冻结，再使用原 `RecoveryPolicy` 申请物理尝试；统一 `RecoveryWait` 时事务内保存非终态原因、到期时间、原阶段和原批次唤醒，释放当前批次执行位，并继续处理其他新材料。纯计算的原 lineage、8 次额度、原 unknown、来源和回执不重置。远端或本地回滚明确未确认时保留原状态和否定证据，原 attempt 只读对账；无 batch/attempt 的不确定准备会停止自动新建工作，需先调查消除不确定性。重试恢复在同事务中取得可用 claim 再预约；`inspect_wait_on` 返回 `None` 本身不授予新尝试。
- `recovery_claim_lost` 不由旧 claimant 写等待状态、释放领域租约或排新领域任务；保留原异常交给资源恢复队列，租约自然过期后既有周期恢复扫描可再次发现。数据库确认回滚及 commit-unknown 按共享数据库合同向 Jobs 传播；后者只能检查现存批次/所选 compilation 和原回执后按领域 CAS 恢复，不能将未知提交伪称失败。
- 对准备或 `dispatching` 且本地仍无确认登记回执的模型请求，仅在原场景授权、全部来源、episode/读取集、当前代际、原业务期限及领域归属仍有效时，使用已存完整请求、相同 attempt ID 和相同 completion 幂等补准入；`None` 不是 not-started 证据。已记录 queued/running/unknown/终态回执后，即使查询暂时缺失也只观察。成功原结果调用 Models `reusable_result`，最终提交仍执行 Memory CAS；语义纠正保留旧 compilation 记录并撤销其当前选择，新的有效结果单独选择。
- 冻结 episode 增加 `content_digest`，只读 `POST /api/v1/memory/extraction/attribution` 根据版本、范围、来源、材料摘要、实际存档请求、场景和 attempt 类型区分新增、补上下文、纠正、独立重试以及证据充分的重复成功输入。返回身份、状态和摘要，不返回正文；历史未保存摘要或请求无法证明身份时返回 `input_identity_unproven`，分页截断/不可见依赖会标注 coverage。已冻结续步在配额等待期间复用，而非再次构造。
- 为 Growth 提供 `MemoryService.lock_episodes(context, refs, *, connection, extra_source_refs=())`：把 episode 当前来源、补充来源及额外来源并成一次完整来源闭包锁定，再按 ID 锁定 episode 并复核版本、来源和权限；原 `EpisodePage` 返回合同不变。
- `memory.consolidate`、`memory.index_object`、`memory.index_retry`、`memory.recover` 显式使用 `resource_recovery=True`。`remote_wait_unresolved` 会保留原批次阶段且禁止自动重新准入；对已有 batch 可通过原 `extraction/resume` 显式触发只读对账，未获得原回执继续保持未解决。若无 batch/attempt，则没有原 ID 可对账，当前代码不会猜测解除，需协调者排查。回滚未确认的活跃批保留原业务阶段和分区占用；该分区后续材料会等待对账，其他分区可继续。依赖 RBF Runtime 的 `RecoveryWait(reason,retry_at,dependency=...)`、`RecoveryPolicy.inspect_wait_on`、`classify_database_failure`、`recovery_wait_retryable` 和 `JobQueue.register(...,resource_recovery=True)`；依赖 RBF Models 的 `status(...,phase="pre_submit")`、`reusable_result(...,expected_data_generation=...)` 及 Broker 同请求/ID/completion 的并发幂等准入。未修改共享 runtime、types、Models、迁移主线或装配根；无新增数据库迁移。现有旧 FakeModels 若参与集中回归，需要适配新接口和 receipt 的 `data_generation`，不能把替身兼容性失败称为产品通过。

## 原两条材料的正常恢复

脱敏历史证据中，两条材料 `input:d53344c0-880f-5aae-8ccc-8d4e975a3315`（当时 revision 1、sequence 3）及 `input:5eca2593-0cd3-5bac-ab21-11425c759fee`（当时 revision 1、sequence 4）属于原批次 `memory_batch_5480d89054eb449f8179cced26560f48`，当时为 `recovery_dependency_probe_in_progress` 误判失败。该快照不包含模型回执或 lineage 详情，不保证当前状态仍相同。

协调者集成并验证后，在原作者/角色/会话授权场景下先对两条 episode 通过 `POST /api/v1/memory/extraction/status` 读取当前 revision、batch、来源和动作。确认同一批仍可恢复后，对其中**一条**使用既有 `POST /api/v1/memory/extraction/resume`，参数包括该 episode 的当前 `expected_revision`、唯一 `client_request_id` 以及正确的 actor/person/conversation 上下文；它会在一个事务中核对并恢复整批两条材料、冻结请求、原最新账本及权限。另一条随后只读状态即可，无须重建 episode 或直接 UPDATE。若返回权限/来源、代际、版本、原账本或材料不匹配等 403/409，应保留原记录和回执，重新诊断，不能猜测回放。恢复排队后分别核对两条状态、同一批次 ID、原 attempt/lineage 和最终实际应用，区分排队、模型结果和业务收敛。

## 编写的回归与集中验证

- `tests/memory/test_rbf_attribution.py`：无正文归因、版本/图文范围变化、权限与实际请求差异、未证明输入及已成功输入的区分。
- `tests/memory/test_rbf_wait_recovery.py`：准备等待和新材料让行、纠正等待冻结、旧失败批原 ledger/CAS 恢复及过期拒绝、丢失 claim、不确定提交、所选 compilation 复用、成长来源闭包、准备/dispatching 与已确认 Models 回执、RPC 中断/丢 ACK 同一请求恢复、远端 rollback=false 的隔离和回执出现后对账，以及向量索引对应行为。测试只使用显式配置的隔离 PostgreSQL/pgvector 和替身 Models；未在本窗口运行。
- 集中建议：先整合 Runtime、Models、Growth 的最终接口，再用项目隔离测试环境执行 `python -m pytest -q tests/memory/test_rbf_attribution.py tests/memory/test_rbf_wait_recovery.py`，按实际变更补相关既有 core、index、Models 和队列用例。`TIANSHU_TEST_POSTGRES_ADMIN_DSN` 必须指向允许创建并删除临时库的隔离 PostgreSQL/pgvector，不设置时已有 fixture 会跳过数据库测试；跳过不等于通过。Broker 并发同 attempt ID 单次 provider I/O 须结合 Models 侧真实 Broker 测试验证，Memory 替身只能验证请求及 completion 身份没有变化。
- 已做静态 diff 检查和 Python 源码 AST 解析；这不等于导入、测试或真实业务验收。其他未覆盖及集成时发现的问题由主协调者根据实际失败处理。
