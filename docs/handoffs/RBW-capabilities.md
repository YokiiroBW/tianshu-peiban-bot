# RBW-capabilities 交接

## 基线与提交

- 工作分支：`codex/rbw-capabilities-20261105`；基线：`e2529c95a92857f368111d9ae5b23ee66800b636`。
- 本卡代码及必要回归用例：`3142803576791253fbedd7b1e53b599702c04fae`。本文件另行提交，避免交接记录自引用提交哈希。
- 本窗口保留了工作树原有的 `.codex/config.toml` 速度设置修改，未纳入提交。

## 实现范围

- 独立进程负责媒体、插件及基础设施工作；独立项目和工作区插件绑定允许无角色上下文，并保留原主体和授权范围。插件定时任务保留原派发身份，结果未知或效果未知时观察原动作，不创建重复动作；配置或绑定变化只唤醒相应实例。
- 规划目录读取本地声明及有时效的后台可用性观察；未知或过期不视为可执行。真实调用和外部提交前仍按原权限、来源、能力可用性及行为依赖复核。配置版本变化使相关观察失效。
- Action 在既有 document 中冻结行为依赖和安装代际，派生任务、插件回调、媒体和基础设施续接沿用原身份及代际。模型和表达请求携带原依赖供各自真正出站时复检；已有未知外部效果仍保持未知。
- 视频、漫画、书籍分段和音乐的实际物理下载接入共享恢复预算，缓存命中及已提交发布的观察不消费下载次数。同一材料切换账户或来源沿用原预算；音乐原候选计划中的不同材料各自记账，已确认候选文件复用；书籍真实分段变化使用同一 lineage 的容量重分段续接。
- 媒体统计从原任务账本投影，按任务和版本记录状态；提供持久分页回填。来源撤回按角色及独立工作空间分片处理，保留原动作、尝试、未知状态和不含内容的发送进度。
- 系统日志转发日志检索和任务定位过滤项。可选漫画文件适配器、插件及基础设施的配置错误隔离到所属组件，状态明确报告失败。

## 共享装配与接口

1. 集成包含 `current_execution_generation()` 的 Runtime 后续实现；已核对该接口存在于 Runtime 提交 `040017fce7fb3ddf421a5a3e36a73248b7caccf7`。安装代际、任务路由、恢复策略及相关 Runtime 迁移均需随主集成版本装配。
2. 在 capabilities 初始化前注入 `capabilities.bind_recovery_policy(RecoveryPolicy())` 和 `capabilities.bind_statistics(statistics)`。统计服务需提供 `record_safely_on(connection, event)`、`mark_producer_on(...)`；回填只证明扫描时可见的当前任务账本，不能重建未留存的历史版本。
3. 在所有会执行 Action 的进程接入 `capabilities.actions.bind_behavior_validator(validator)`；签名为异步 `validator(context, refs, *, purpose)`，返回 `None`/`True` 表示通过，失效时抛错。`Actions.invoke(..., behavior_refs=(), behavior_purpose="conversation", generation=None)`、`saved_behaviors(action, attempt)`、`check_saved_behaviors(action, attempt)`、`saved_generation(action, attempt=None)` 供可信调用方使用。
4. Models 的 `ModelRequest` 需提供 `behavior_refs`、`behavior_purpose`、`data_generation`，并在 Broker 排队结束、真实供应商派发前复核；Conversation 的 `ExpressionPlan.behavior_refs` 及实际发送前复核需一同集成。Sources 的 `capture`、`publish_media`、`register_media` 需接受原 `generation` 并在各自写入边界校验。
5. `catalog.planning(context)` 返回声明、`availability`、`can_execute`、`freshness` 及观察时间；`catalog.on_change(context, capability_ids)` 是异步定向唤醒端口。真正执行使用 `catalog.availability(context, handler)` 实时复检。主窗口将观察变化接到 Life 原有唤醒链。
6. 来源撤回协调者使用 `actors_for_source(connection, refs, kind)` 和 `invalidate_actor(connection, refs, kind, *, actor_id)`；`actor_id=None` 是独立工作空间分片。两者使用同一来源范围和类型，不以其他主体替代原主体。
7. 运维恢复使用 `context_for_action(action_id)` 获取原授权上下文并刷新；`inspect_recovery`/`observe_recovery` 只读原身份和回执。`invoke_registered_recovery(..., operation="reroute_pending", client_request_id, expected_revision, expected_attempt_id, expected_job_id, guard_on, guard_attempt_id, guard_owner_token)` 仅在同一锁事务中通过 `await guard_on(connection, recovery_attempt_id, owner_token)` 后改派已证明未提交的原任务，不重放未知副作用。
8. 日志端口需接受本卡转发的 `start_ns`、`end_ns`、`role`、`level`、`module`、`correlation_id`、`trace_id`、`job_id`、`attempt_id`、`keyword`、`exclude_conversation`、`cursor` 和 `limit`，任务查询支持 `job_id`。Action 的失败事件仅含原动作/尝试标识、错误码和能力标识。

## 迁移与集中验证

- 本域新增 `capabilities-projects-0002-independent`，由 `capabilities.module` 在 `capabilities-projects-0001` 后声明；主协调者还须在共享 `product_migrations` 的同一顺序注册。它仅放宽项目角色/会话及经历/续接角色列的非空约束，独立路径仍以主体和系统授权范围隔离。
- 需要主集成版本的 Runtime、Sources、Models、Growth、Conversation、Life、Logs、Operations 接口及迁移。不能仅合本卡就视为可运行产品。
- 已做静态差异审阅和 `git diff --cached --check`，通过。按本批统一执行约定，本窗口未运行测试、构建、烟测，未连接外部设备或真实服务。新增 `tests/capabilities/test_rb_*.py` 交主窗口与集成后的相关调用方集中验证，重点覆盖独立无角色任务、原回执/未知结果、跨来源重试、代际、撤回、插件定时任务和装配。
- 真实媒体下载、设备、模型、语音、QQ 及完整部署和业务验收均未在本窗口验证。用户授权的天枢业务清理、配置导出与生产部署由主协调者串行处理，本窗口未执行。
