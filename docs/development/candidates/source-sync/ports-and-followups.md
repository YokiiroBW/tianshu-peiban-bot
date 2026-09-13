# 固定证据与最小后续任务

正式分工以[multi-actor.md](multi-actor.md)的candidate.2物理P/角色A分层为准。单actor限制仅是旧Core迁移保护，不作为正式契约架构。

以 Git 对象读取，证据清单记录完整提交、blob SHA256、函数锚点。此处函数位置均属于指定提交，不属于工作树 latest。所有路径均在各自独立产品仓库。

| Owner/固定版本 | 真正已具备 | 此候选需要新增/修正 |
| --- | --- | --- |
| Core 812019e | `core.py:ingest` 保存 inbox request/receipt/source；`store.py:latest_source` 取最高 revision；`_finish` 写真实 outbox，scope 未知为 blocked_scope；`short_context.py:sources_current` 本地修订检查；app 仅 ingest/cancel 业务 HTTP | 同一事务持久来源/轮次事实水位；SourceFacts 本地只读服务及单一 HTTP；撤回稳定 key 不再复活；来源分类证明及 event reality 汇总，不能沿用 `_finish` 的 real 常量；维护与 receiver 匹配的读取授权。 |
| Platform a5ee59f | `Origins.issue/resolve/revoke`；prepare/confirm_mapping 校验真实上游响应；observe_source、verify_current_sources 为内部 rehearsal；HTTP 只有 origins/resolve 和 model-config/snapshot | 受信转发适配器完成首入站映射；保留 admission 历史/时间与当时 entry；SourceAccess 当前主体/entry/route/映射与逐来源明确撤权检查，持久水位，单一 HTTP；不得在该服务复制 Core revision owner。 |
| Memory 69b29f3 | HTTP identity/select/revise/consume/profiles；source_authority 生产为空；sources/lineage、_invalidate、_invalidate_jobs、_bump、本地 BEGIN IMMEDIATE；fixture-only 确认/候选/来源加载；画像 epoch 隔离 | 真实 SourceSync 屏障及自身 ledger/suppression；失效与版本在冲突响应前持久；SourceAuthority 本地验证；consume 校验完整 owner event/快照缺口；分离 TrustedWorkflow；后台 check HTTP。 |
| Audit e6d8e21 | `app/evidence_api.py` POST `/internal/evidence/read`，独立读服务授权和 channel mapping；结果 `archive_observed`、`current_source_state=not_checked`，locator/内容hash/观察ID/原生external id | **本候选无 Audit 实施任务**。其 archive_observation_id 不是 Core receipt；其 API 不提供 Core revision/retract、Memory 版本、完整上下文证明。pending 足够先接来源链；未来 archived 升级要另发可信逐来源绑定合同并联合验证。 |

协调期间 Core 已集成 `17eba4f1d513073c3e1bad7823e2f7c3fc728754`。增量只读 `context.py/core.py` 确认 profile_check 的独立版本域、inherited_checks 的跨作者依赖、_verify_checks 的逐入口/绑定/域探针及 _preflight 的前后输入复核。上述来源/receipt/分类缺口仍存在；下一 Core 任务从此已集成代码延续，禁止丢弃派生依赖。70 tests/17 subtests 是协调者报告，本任务未重跑，不作为本任务验证数字。

## 串行发布后可并行的产品范围

协调者先审 schema/语义/合成负例，明确发布 ID 和依赖 hash；未发布前不得在产品中把 candidate.1 URL 当现成接口。后续任务编号由协调者分配，本包不更新任务板。

1. **Core producer**：仅Core Store/Core/app/部署客户端与其测试；按candidate.2拆物理inbox和actor admission/receipt，collector加入actor，保留单一channel conversation、全局turn序号、跨actor两轮上限/发送顺序。新增ingest-actors和精确source selector、物理变更广播、head与reality事实。旧路径保持单actor迁移保护并隔离混组，不从collection猜actor，不将其限制冻结为最终架构。不得改Memory/Platform数据库。验收群私的同P多角色、不同消息分collector、空target、越权冒领、跨角色edit/retract、旧来源/原始输入核验、blocked_scope、取消不撤输入及重启。
2. **Platform producer**：仅Origins/受信转发应用服务/server/auth/store与其测试；source-access增加精确物理source_input及逐actor授权、服务器默认路由，另保留current后台核验；补历史、撤权水位、批量回执下同person/channel可靠映射。验收真实HTTPS/receiver、只获准A不能冒领B、default非通配、物理撤回不要求全部旧actor当前权限、错author/binding/channel/actor、过期引用和映射响应丢失重试。不要扩大为通用ABAC或每消息审批。
3. **Memory consumer/workflow**：只改Memory自己的服务和迁移主线；按A选择器升级source rows/lineage/source_writes/suppression，P变化同事务失效所有本地已登记actor依赖，但A遗忘/授权撤销不删除B。保留两段事务屏障、双owner水位、完整owner event验证、TrustedWorkflow、后台check及两个既有版本域。correct只禁旧值不伪造新来源；群共享沿原精确批准，不因actor admission自动放行。迁移/备份/回滚由本产品负责人验收，归属不明旧ledger不自动开放。
4. **联合验收**：待三个具体提交固定后，扩展 TS-050 真实 HTTPS 组合，分别记录真实 owner 与合成模型/渠道替身。至少首账号W0→映射→首来源→零预算→Core scope_version→真实committed_event→Memory candidate→完整组读取；更正/遗忘/entry撤权/retract 后旧探针409、不可用503，候选不复活；owner head变动/缺响应/本地并发/崩溃重启；画像私密epoch隔离；T1/T2/T3、发送前复核及已知非原子窗口。

Core 与 Platform 可用本包合成 fixtures 各自实现；Memory 同时以固定 fixtures 实现消费，最终三方接口需真实接通验收。与已集成 TS-021 画像消费只共享已发布契约与固定17eba4f证据，不读取其可变目录；Core 合并顺序由协调者串行处理 app/Core 共享入口冲突。

**本包以外的具体后续项**：Memory/Platform完整更正接线需独立可信replacement来源、同一已确认主体/范围/版本绑定及原子可读新组，验收新值召回、旧A持续禁用、重放/撤销/重启不复活；未做前不得宣布完整更正。多角色分层已在本主候选明确，产品须据此实施后才解除迁移保护。256角色依赖仍是全scope硬容量，长期累积可能持续503；有容量证据再做固定快照分页或连续增量+缺口恢复，不在本任务堆机制。

## 退出条件和明确未完成

两个 producer 返回精确可核验事实、Memory 在本地持久失效后再读版本，且联合负例能拒绝旧来源，才算来源接线通过。仅 remote bool、SQLite 参考模型成功、schema 通过、TLS 成功或 owner 字段等于 companion 都不满足。

本候选没有真实渠道认证、真实产品端口实现、生产确认 UI、跨平台账号合并证明、原文删除、Audit/Core archived 绑定或混合单消息分段能力。无真实用户确认签发方时 revise 继续不可用；无已批准共享策略时 profile 发布继续不可用。它们是具体缺口，不以本包合成正例替代。
