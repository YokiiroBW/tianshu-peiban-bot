# RBW-experience：日记创作与语义经历交接

- 基线：`e2529c95a92857f368111d9ae5b23ee66800b636`。
- 本包代码与回归用例提交：`17cbf01a8005740c2fcd5d137fb9657b335de492`；本交接记录在其后的独立提交中。
- Identity 场景合同对齐及同群成员变更回归代码：`95878517bf85e3e60345ff2d106af71e2b6ef0f5`，请在 Sources 的 `IdentityService.scene_id` 合入后接线并集中验证。
- 按 2026-11-05 最终观察合同接入共同恢复、统计和数据代次的代码与用例：`170f1d57808a9095e6baa8d2043b2be94b247f6d`。需要先装配新版 Runtime、Models 和 Operations，不能仅挑选本提交即当作可运行发布。
- 分支：`codex/rbw-experience-20261105`。只修改本包白名单；没有操作生产、清理数据、推送或部署。

## 完成的实现

- 日记对象及自动闭日按角色、当地日期和 Identity 的 `scene_id(context)` 区分。群聊人员、scope、audience 或 epoch 变化不改变场景；不同场景不会按日期任取正文。场景归属由 Identity 统一定义，调用方仍须检查实际授权和来源。
- 当前日的所有可用经历分批收集，原始回执/来源留在审计配方中；模型只收语义观察及覆盖信息。区分发现、实际读取、想象、失败和未知结果；未知图片不把计划描述当成实际内容，技术补取和日记/反思派生材料不作为新经历。去掉固定最近 64 条截断，结果应用及重试通过 Memory 公共端口按 128 条分批核验版本。
- 日记仍使用独立 intent、当天材料和 200,000 输入预算；没有材料时正常进入空状态。原成功正文的自动状态从 Models 原回执、原配方、来源及版本对账；材料或原回执不再可证实时保留正文并报告未对账，不猜其他场景、不伪造新成功。手动收集相同材料时恢复原成功 attempt 和上下文，不再次调用模型或改写正文。
- 日记和章节复用 `submit_frozen`：先冻结原意图、CAS 检查及登记，再以 `Models.submit(..., completion=JobSpec(...))` 提交，后台工作立即返回；完成通知或延时观察按原 attempt 查询 Models 账本。显式取消、来源失效、内容不完整、章节作品版本变化和迟到结果继续受原状态/版本约束。
- 保留作品、章节与日记的生成、取消、对账、修订和发布路径；日记新增按正文版本绑定的审阅，新的正文版本清除旧审阅。发布仍通过 Sources disclosure 做读者授权，日记审阅不增加发布强制门槛。
- 新增持久通知消费端口 `DiaryService.consume_episode_change`；通知消费脱离 Memory 写入事务后再查公共 Memory 端口和本域状态。
- 真实日记/章节 Models 提交前由 `GenerationRecovery` 注册和预留实际物理 attempt；相同对象自上一次保存的模型正文以来共享最多三次物理尝试。原 attempt 的 unknown 只观察，Models 明确失败后才按共同策略的 due 和冷却领取新 attempt；切换备选 route 时在同一 lineage 上更新实际 quota 依赖。显式取消不自动恢复，物理成功和正文保存分开记账。
- 正文实际保存或作者修订时，按正文对象与版本投递 `family=life` 的 `business_success`；失败时在 `life.experience_statistics_pending` 留下无正文的持久指针，从原不可变正文版本补投。统计服务故障不回滚正文。章节在提交、重试和保存时还复核已使用的前文章节版本。
- 安装代次在原日记/章节请求和各后续任务中冻结；写入事务、来源通知及补投检查原代次。回调 JobSpec 与 payload 都携带该值，不从任务恢复时的新安装代次重绑。

## 公开接口与需要装配

1. 注册 Life 的日记审阅路由：`route("diaries/review", diaries.review, write=True)`。请求 `{"id":..., "expected_revision":..., "client_request_id":..., "state":"approved"|"changes_requested", "note":"可选"}`；返回 `CommandResult.data={"id":..., "review_state":..., "content_revision":...}`，`revision` 是命令后的日记对象版本。作者读 DTO 中新增 `review_state`、`review_note`、`review_content_revision`；已发布读者投影不暴露审阅意见。
2. 由主协调者在 Memory 原事务中持久发出独立下游通知，不同步调用 Growth 或 Diary 的业务逻辑。注册 `queue.register("life.diary_episode_changed", diaries.consume_episode_change, retryable=True, execution_lane=ExecutionLane.DIARY)`，并纳入 `bot-background`。JobSpec payload：`{"context":wire(context), "episode_id":episode.episode_id, "revision":episode.revision, "watermark":watermark, "data_generation":原安装代次}`；JobSpec 的 `data_generation` 使用相同值，`subject_id=context.actor_id`。逻辑去重键包括 actor、`identity.scene_id(context)`、episode ID 和 revision。消费返回 `observed` 或 `unavailable_or_revised`；真实读取异常继续抛出供队列恢复，不能吞掉失败。
3. Models 的 `submit(request, *, completion: JobSpec | None = None)` 和原 `status`/`result`/`cancel(reason=...)` 必须完成装配。完成任务分别为 `life.diary_generate`、`life.chapter_generate`，payload 除各自 `diary_id`/`chapter_id` 和 `generation_id` 外还包含冻结的 `data_generation`，运行于 `bot-background`。Models 必须在接受及实际 dispatch 的事务边界核对 completion 的原代次；本窗口不能单独关闭跨进程 RPC 在 Life 本地提交后到 Models 接受前的重建竞态。该需求已通知主协调者并由其协调 Models。
4. 创建两个服务后，对 `diaries` 和 `writing` 分别调用 `bind_observability(recovery_policy=同一个 RecoveryPolicy 实例, statistics=同一个 Statistics 实例)`。装配后的 Models `generation_profile` 必须提供实际 `quota_group`，固定的 `route_id`/`route_revision` 不得暗中更换。Runtime 必须具备本批观察合同中的 `claim_lineage_due_on`、`update_dependency_on`、data generation 及 JobSpec 接口；统计 `record_safely_on` 对相同或已被更新修订取代的事件返回 True，对实际投影故障返回 False。
5. 在 `generation_control.recover()` 之前注册 `queue.register("life.experience_statistics_backfill", generation_control.backfill_statistics, retryable=True, execution_lane=ExecutionLane.DIARY)`，并将其纳入 `bot-background`。该任务 payload 为 `{"data_generation":原安装代次}`。重启恢复会主动排队补投；失败时保留指针并延后重排，补齐后以生产者 `life.experience` 标记覆盖。Life 调度的 `life.planning` 是不同生产者，由协调者在展示层聚合。
6. `life.diary_days.scope_key` 字段名称兼容现有队列参数，但新库含义是 `identity.scene_id(context)`，不是 scope 哈希；`life.diaries.scene_key` 使用相同值。原 HTTP 路由和手动命令语义保持；内部 `review_day`、`GenerationControl.work` 以及补投任务现在要求 payload 提供冻结代次。
7. 本包没有新增可自行派发物理请求的 Operations RecoveryAction。若 Operations 只观察或唤醒已有域工作，必须按合同使用 `observation_only/domain_managed`；物理预留和结算仍由本域负责，不能对同一次 Models 调用再扣一份预算，也不能把队列唤醒当作正文已保存。

## DDL 与注册顺序

`src/tianshu/life/rb_experience.sql` 在空业务库、Life 基础 schema 及 `0033`、`0035`、`0038` 后登记：删除 `life.diaries` 旧 `(actor_id,day)` 唯一约束，新增 `scene_key`、三个作者审阅字段和 `(actor_id,day,scene_key)` 唯一约束；扩展 `life.generation_recipes` 的 recovery 字段并新建补投指针表 `life.experience_statistics_pending`。它不迁移旧日记；这与本轮清空全部天枢业务数据的授权一致。若旧版该脚本已经在集成库登记/执行，只追加后两块新 DDL 或压平进空库 schema，不能重复执行日记 ALTER，也不能把无补投指针的历史数据宣称已经覆盖。迁移登记和共享入口本窗口均未修改。

## 集中验收和待其他包完成

- 本窗口**未运行测试、构建、烟测或真实模型**。静态审阅改动、`git diff --cached --check` 及 7 个本轮变更 Python 文件的 AST 语法解析通过；新增用例在 `tests/life/test_rb_diary_experience.py` 和 `tests/life/test_rb_writing_generation.py`，结果须由协调者集中验证。
- 旧 `tests/life/test_diary_automation.py` 等假定 scope 哈希即日身份，公共测试模型替身仍无 `submit`、quota profile；旧 Jobs 替身也没有安装代次方法，旧直接调用没有任务代次参数。`test_writing_completion.py` 的写入次数和 connection 替身也需要适配新的版本复检/统计指针。这些文件不在本包白名单，集中验收时需按新合同更新夹具/预期；旧 unknown 可立即重试的假设已被本批最终观察合同覆盖，不能据此伪造失败或声明已通过。
- Life 包负责首个深夜启动及跨日闭日的 clock 语义、路由和角色接线；Growth/Memory/主协调者负责持久独立通知、消费失败隔离、反思不递归和低价值想象活动的学习处理。本包不声称这些跨域行为已验证。
- 同群不同 `person_id` 的闭日及手动绑定、范围/权限变化、保存正文后的材料变化、Model 接受后确认丢失、并发取消、读者授权和首次空库闭日应在统一装配后使用真实持久端口做定向验证；不能将测试替身或队列接受等同于业务完成。
- 新增用例还覆盖原 attempt unknown 后的确定失败、备选 quota 与预算继承、作者修订不能重置预算、前文章节变化、统计故障补投及旧代次任务隔离。跨进程 Models 代次 fence、真实统计实现与恢复观察动作仍须在集成后验证。
