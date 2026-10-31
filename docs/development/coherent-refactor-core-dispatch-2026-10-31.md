# 天枢统一重构 核心域后续分派

本说明是当前完整队列的施工分组，不新增平行产品或替代研究方案。用户已授权全量实施，仍全部由 `gpt-6.1-sol / xhigh` 子智能体实现。当前三位实现者先完成已分配部分，主协调者收取提交后再明确放行本说明对应分组；所有产品测试、typecheck、build 和浏览器验证仍留到完整集成后的 CR19。

## 共同接口

已发布类型源提交 `ec2fc7c27ca7d648a263f3d7f1d09138ad2ea70f`，集成为 `0370ddc`。沿用 TrustedContext、RequestBudget、Action/Attempt/Receipt、SourceRef、MediaRef、ModelPort、Database、JobQueue、DomainModule。后续正式扩展由基础所有者发布。

HTTP 读取返回实际领域 projection；分页返回 Page。写入一律 CommandResult，业务结果放 data。保持 client_request_id、expected_revision、未知回执和实际缺失信息，不以空对象/0/成功默认值掩盖未配置或未执行。前端只保留一个客户端，不用跨域兼容拆包。

## A 身份 来源 记忆 与统一上下文

施工块 CR04、05、06、07、08。拟在 foundation_models 完成 CR01–03 后由该实现者接续，独占 `src/tianshu/identity/**`、`sources/**`、`memory/**`、`conversation/context/**` 和相应用例；继续由其维护根迁移总注册、基础资源和最小共享类型。不得写 `web/**`、其他 conversation 文件、life、companion、capabilities、channels 协议实现。

1. Identity 是可信会话、账号关联、角色/人物标识、授权范围和接入策略的唯一入口。沿用现有有效 cookie/CSRF、口令散列和初装语义，去掉 SQLite 存储、旧服务凭据桥与按正文猜身份。初装的部署设置口令只写且不进仓库；空库不能令已封存部署意外重新开放任意注册。事件 producer/event_id/revision、原作者、提及、引用和关于谁要分别建模。群 reply/observe/ignore、命令回复负责人在这里形成决定，channels 提交真实输入而不再复制这些规则。
2. 既有 `/api/v1/session`、`setup`、`auth/login`、`auth/logout` 使用 SessionView 和 CommandResult。初装 body 最少 `client_request_id, username, password, display_name`，如部署初始化受 setup_token 保护则真实校验；登录 body 为 `client_request_id, username, password`。创建/登录 CommandResult.data 为新 SessionView，HTTP 设置 cookie。请求中的权限、scope、actor 身份不能被直接当可信。应用账号和角色权限分别处理。
3. Source 是原件、版本、受众、精确片段和依赖关系的唯一权威；保留必要支持组、独立替代支持、suppression 和 intake epoch。撤回/更正/遗忘形成有界变更，实时读/动作提交查选中依赖；后台只沿依赖边传播，不全库正文扫描或全局写屏障。来源撤回后旧模型结果不能重新写回。
4. Memory 原生 PG 保存正式 ClaimRevision/Episode/Matter、人物和群投影、关系背景与风格证据。保留 speaker/subject、转述/本人确认、否定、条件、有效时间、当前状态和矛盾支持。提醒/改期/取消提供当轮用例，普通认识增量后台抽取；同一原事件的文本、音频转写和摘要不重复计证据。Memory 不自行宣称 life 承诺完成。
5. 固定一个 1024 维 pgvector profile；检索先按范围、主体、当前事项、时间过滤候选，再加载正文。中文词法/专名与向量融合，MMR 到 k 停止，保留来源复核与 rerank 的有限预算。完整列表独立分页，不由 top-k 冒充。人格、条件和证据单位不能被随意截断。
6. ContextAssembler 位于 conversation/context，但仅拥有选材和预算，不复制事实存储、人格状态、联系策略。提供 chat/plan/extract/research 场景；使用实际 tokenizer/供应商计量可用信息，未知媒体成本有明确估算来源。私聊材料不能混进群投影，模型内容不能成为 system 权威。
7. 关系是角色—人物之间的明确设置、认识、边界和共同经历；删除 score/stage/签到/离线扣分。冻结阻止该人物自动关系/相处风格变化，不挡事实纠正、短期情绪或合法新私聊；access/content/expression/plan revision 分清。关系类型不授予工具权。

前端已明确需要关系 catalog/view/manage、claims/claim/correct/evidence/matters、人物群投影及账号关联。实现者发布实际操作与字段给前端，UI 可调整，不反向强迫领域保留旧信封。关系必要投影包括 pair、revision、relationship_type、display_label、frozen、summary、boundaries、shared_experiences、preferences，并带允许展示的证据；这些是显示需求，不要求把所有状态存成一个 JSON blob。

与 capabilities 的正式消费端口由其早期 ports/types 提交提供：Authority.check，Sources.resolve/capture/read，以及同事务 ResultEvents。新 Source.resolve 必须真实取得授权媒体；不可仅返回 URL/空字节。能力结果由来源和 Memory 按稳定 action 身份消费一次。

交接 `docs/handoffs/CR04-08.md`，列出真实 API、schema、任务 handler、已接消费者及尚待 B/C 装配项。实现完成只标 implemented_not_tested。

## B 人格 生活 对话 表达 与成长

施工块 CR09、10、12、13、16。拟在前端首次可集成提交后分配，保留该实现者 web 归属；后端独占 `src/tianshu/companion/**`、`life/**`、`conversation/**`（不含 context）、相应用例和 schema。不写 identity/sources/memory/channels/capabilities 或根迁移注册。

1. 稳定作者人格完整保留，和后天认识、连续心境、事件评价、短 ReflectionProposal 分离。重评相同事件不叠加；带范围的主观感受不被记为用户事实，也不通过全局 mood 泄露。无强制每轮二次 LLM、私密正文正则或每秒情绪 tick。
2. life 连接目标、Matter、承诺、日程、活动、真实动作和等待原因。使用唯一 Procrastinate worker 和有变更水位的合并派工；没有变化不规划。重启后不补发过期候选，改期/取消使旧触发失效，保留 R67 暂停保护期行为。明确命令/提醒不被普通分享新奇度过滤。
3. AttentionPolicy 统一用户当前可用、免打扰、当前会话、内容理由和联系预算。普通来话不因早睡/心情被吞；允许不说话和自主创作。自主 PhotoIntent 支持 create_only/share_candidate/requested_delivery；成图后按当前时机再决定发送/延后/归档/取消，关联原拍摄快照，不从成图事件循环生图。
4. Conversation 是唯一轮次编排：来源登记、context、模型、工具、表达、结果回流。网页/QQ/TG/主动候选共用；两轮次限制、合并防抖和直接命令回复负责人实际生效。工具等待释放 Broker 执行位，长后台任务释放对话位。模型可选记忆失败时允许正常回复，但授权和来源未知不能当许可。
5. 插件命令/模型工具/日程都用同一 Action 目录；一次结果回同一角色/关联轮次。表达为有序 ExpressionPlan，文字/图包/图片/音视频/引用/提及保留顺序、唯一逻辑表达 ID；未确认投递不得盲重发。调用能力的 TTS/图包/照片产物后由这里唯一提交投递，不让引擎自发消息。
6. 原始 episode 水位驱动日记/手记/兴趣/认识/风格整理；主观手记不可自证，私密素材不直接进入可分享日记。风格有作用范围、反例、撤回和冻结消费者；无新依据不编变化。下次对话和计划确实读取新投影。
7. 保留衣柜、小屋、创作书架、活动/目标/承诺和日记阅读已有有效能力，迁入真实领域对象；不能用“未实现”删除已完成旧功能。前端 life/runtime 旧结构改接新投影，承诺保存/改期/取消和 growth view/revoke/restore 均有真实消费者。

实现 capabilities 所需 LifeSnapshot.scene/sharing 与 Expression.present 端口，按其正式类型连接；由装配根注入明确领域 service，不构造包含所有私有方法的 Core 对象或隐藏 service locator。

交接 `docs/handoffs/CR09-16.md`，列出一条从真实输入到下一次选择的完整装配路径，以及真正外部引擎配置要求；不以定义类/handler 名称代替接线。

## C OneBot 与渠道连接

施工块 CR04A 及 CR04/12/13 的渠道端。拟在 capabilities 首次完成范围提交后分配，独占 `src/tianshu/channels/**` 与相应用例、schema；保留能力域归属。不复制 identity、conversation、表达或通用 Action 账本。

固定 v11 23 个消息段、38 个公开 API、隐藏快速操作、消息/通知/请求/元事件、HTTP/反向HTTP/WS/反向WS四种传输。provider 扩展依据 NapCat/SnowLuma 实际能力，群公告/群文件不是伪造标准 API。有序片段携带引用原作者、@与@全体，未知段保留有界原生数据且不丢其他段；音视频/GIF取得真实原件并做真实格式转换。

请求 echo、重连、事件去重、异步 accepted、未知发送和晚到结果保留身份；通道发送权来自可信身份，不能从正文猜目标。会话协议层和业务 reply/observe/ignore 分开。TG 和已有外部 AstrBot/NoneBot 桥保留实际宿主边界；已有宿主命令回复不被天枢再发一次。

向 UI 提供实际可配置连接、标准/扩展支持矩阵和验证状态，未配置/对端不支持/实现缺失区分。对外消息最终由 conversation/expression 统一发起。集中验收不向真实群擅发公告/@全体，不把合成协议通过当真实 QQ 收发成功。

交接 `docs/handoffs/CR04A-channels.md`，列出固定协议表与每种传输入口、实际消费者、保留扩展和未知回执恢复方式。

## 装配与最终验证

三个分组各自提供 DomainModule、schema Migration 和注册函数，基础所有者串入明确的装配根；模块间只用公开类型/小端口，禁止直接读对方表或私有对象。所有网页消费端点、worker 持久任务、Broker 许可、渠道投递和回流都装配后，主协调者才启动 CR19 集中验证。

部署代码同样交子智能体。CR20 的新版 PG/vector 独立服务与旧数据清理边界见执行说明；先验证完整构建，再冻结镜像与配置、停旧写者、清专属旧数据、启新版。共享 PG/Redis、其他产品和共享媒体不属于清理整库/整盘的授权。
