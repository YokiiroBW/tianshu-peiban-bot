# RB并行实现公共合同 2026-11-05

本合同是实施接口，不是测试门禁。基线c98e23c；现有公开入口尽量演进复用，删除被取代且无调用方的旧实现。允许必要新增领域文件，不另建平行队列、记忆或回执权威。

## 固定归属与跨域接口

- Runtime持久队列仍用JobQueue/JobSpec和同事务enqueue；稳定逻辑键/dirty version/due、恢复lineage与短租约由runtime包实现。通知不是事实，当前状态总可补查。新增唤醒/分类方法首先回报协调者供消费者统一采用。
- Models新增`async submit(request: ModelRequest, *, completion=None) -> ModelReceipt`：同attempt同摘要返回原接受/终态，不同摘要冲突。`completion`为程序提供的可信JobSpec可序列化记录（task/key/payload/service_class/scheduled_at/subject_id）；最终结果与通知可靠关联。保留`status(attempt_id)`、`result(attempt_id, context=...)`、`cancel(...reason=...)`及stream。结果只能由Models账本证明。后台领域持久等待引用后返回worker，不继续长await generate。Models包如需调整completion字段尽早给主聊天一个精确示例。
- Growth保留`material(context, *, purpose="conversation", query=None, budget=None)`，返回tuple[ContextContribution,...]；一规则一贡献，不能再读管理view。新增`resolve_behaviors(context, refs, *, purpose)`，返回按rule_id排列的当前revision、state、semantic_revision/兼容性与reason；支持新增不视为语义失效。历史/正文由Companion公开端口提供，Memory只建typed引用。
- `ContextContribution`新增可选`material_id`、`semantic_role`、`behavior_ref`；`ContextPack`新增`selection`。所有字段都有兼容默认值，避免隔离开发期间旧构造器破坏；最终清理无调用方旧路径。这些共享类型仅协调者写。来源完整闭包继续使用source_refs，不将support子集替代它。
- Memory写入与检索边界沿既有MemoryService/MemoryRetrieval。检索返回typed learning refs，由上层注入Growth解析器解释用途，不让Memory私表反查Companion。Memory写入负责群词义、语义历史、整批原子提交；检索负责新投影/profile和晚到索引CAS。DDL各自文件，Memory schema.sql仅写入包可改。
- Life日程apply返回逐候选结果（scheduled/intentional_idle/deferred_no_slot/stale_state/invalid_proposal及版本/原因/next_due）；具体dataclass归Life。目标、intent、step和角色焦点分开。Life包保留给Diary/Writing使用的公开服务能力；经验包新增需求交主聊天，不直接改Life核心文件。
- 消息原event/revision/digest不可变，附件补充单独revision并关联原part/source/turn；Conversation/Channels包负责贯穿接收/理解/记忆，不重新ingest伪造消息。Sources提供正式补充来源与失效契约。
- Capabilities提供轻量本地目录和有时效的后台可用性视图；真正选中的能力执行前复核。一个QQ连接/API owner；其他角色经现有调用端口使用，不重复建立连接。非BOT工作在independent角色承接。

## 运行角色

API、Models、BOT前台、BOT后台、独立能力（含媒体）、Operations六类，仍是同一产品镜像。runtime包定义实际role常量与启动参数，并回报主聊天；product.py的角色感知构图由主聊天装配。每域module提供roles/tasks/start/stop所需切片，不让未配置可选能力成为全产品ready前置。Sources失效先同事务生效，下游清理按域任务异步。

## 新HTTP约定（复用身份/命令/来源体系）

- 日志：继续POST `/api/v1/system/logs`，扩展filter/cursor，返回entries、next_cursor、coverage、freshness；具体兼容旧字段由日志包声明，不能只造理想后端。
- 统计：POST `/api/v1/system/statistics`，time range/timezone/module/actor/route过滤；返回overview、series、breakdowns、coverage、as_of，known/estimated/unknown不混用。
- 运维：POST `/api/v1/operations/incidents`、`incidents/get`、`settings`、`settings/save`、`recover`。写接口使用原client_request_id/expected_revision命令习惯；settings保存credential refs，不返回秘密明文。未配置诊断模型/通知目标时明确状态。
- Frontend先完成全部页面和状态，再与后端精确DTO联调。实现者发给主聊天公开响应样例，主聊天统一通知消费者；不自行另开接口同名变体。

## 配置、清空和迁移

保留当前天枢配置及必要凭据/主密钥/绑定；全部天枢自有业务记录、任务、日志和自有业务文件在最终切换时归零。独立模块功能保留但数据不迁入新安装。外部NAS/服务、非天枢拥有的共享库不删除。配置引用的文件按实际归属核清，不把外部路径当可删目标。实现窗口不执行任何清理。

域窗口可追加/改本域DDL，公共迁移注册由主聊天整合；空库基线最终由主聊天压平，并保留新版未来正常升级能力。已有配置字段不擅自改名或换模型。所有schema/API变化写在handoff，给出创建/使用顺序，无占位成功。

## 验证和回收

本轮先完整实现、编写必要行为用例、不运行逐卡测试/构建/烟测。主聊天收齐后按影响集中执行一次适用验证；接口调整与失败修复仅补对应范围。实际模型/生产/QQ/设备动作只能主聊天按用户范围处理。功能未完不能因最终未跑测试而停在半成品；真实依赖接口缺失时先用明确合同完成自己的部分并回报。
