# 天枢完整产品设计

当前有效总稿：[整体架构 V2](../architecture/tianshu-system-architecture-v2.md) · [100 项需求映射](requirements-v2.json) · [系统衔接图](../../output/architecture/v2/system-overview.html)。2026-09-14 已按视觉方向重整；实时通话与共同观影移出网页，下文为历次设计记录。

最初需求记录为 [完整产品与架构蓝图](tianshu-complete-product-v1.md)，逐项追踪见 [需求清单](requirements-v1.json)。

完整功能目标来自 2026-09-12 的产品设想；架构、引擎选型和接口是研究建议，尚未实施或作为已接受 ADR。后续讨论先维护完整目标，再安排实现顺序。

2026-09-13 新增明确约束：[画像分层与最小充分召回](../architecture/profile-memory-granularity-design.md)。画像按字段和原子项取用，禁止默认注入完整人物简介；固定上限、必要条件保留及跨轮去重作为后续验收要求，具体预算数值仍是待评测草案。

2026-09-13 新增明确需求：[连续短句防抖与结构化消息组](../architecture/message-debounce-and-bundling-design.md)。静默窗口可配置，有效续句重新计时；封存后继续处理，普通新消息独立收集为下一轮，可提前并行处理并按序发送。防抖、处理排队与发送等待均计入实际延迟。

用户已确认同会话陪伴处理最多两个轮次。[功能路径与职责](../architecture/functional-routing-and-ownership.md) 进一步澄清：直接群管理/GsCore 命令可独立分流，自然语言从陪伴核心调用同一执行者；两个轮次上限不覆盖所有功能任务。

2026-09-13 新增明确需求：[多模型接入商与功能分工](../architecture/model-providers-and-workloads-design.md)。统一添加服务地址与 Key、发现或手工添加模型，为不同角色及对话、记忆整理、写作等功能选择模型；专用能力、参数透传和向量索引迁移分别处理。

2026-09-13 重申并细化：[Codex、Hermes 与项目上下文接入](../architecture/project-context-codex-hermes-design.md)。复用旧 MCP、Hermes MemoryProvider 和项目版本归档能力，补齐按项目/分支的文档增量同步及公共工具，让不同编码体通过短恢复包和按需查询接续工作。

2026-09-13 新增资料沉淀要求：[搜索、自动研究与资料库](../architecture/research-and-knowledge-library-design.md)。手动网址/文件和 AI 查阅来源共用整理入库流程，保留原始来源、版本、研究笔记及项目用途；初始来源整理见 [研究资料目录](../knowledge/README.md)。

2026-09-13 新增协作需求：[天枢与 AssetLibrary 的 NAS 文件整理](../architecture/assetlibrary-nas-organization-design.md)。天枢提出目标和元数据候选，AssetLibrary 负责音乐专辑归组、文件操作计划及资产索引；物理归档、标签写回和知识入库分别处理。

此前研究与跨项目统筹稿保留作历史参考。与新的完整需求不一致的旧约束不作为新产品功能上限；现有部署及数据在正式迁移之前保持原状。

2026-09-13 细化图片资源整理：[照片、动漫图与表情包整理及成本](../architecture/image-library-curation-design.md)。待整理源到新资源库共用查重、分类候选、优选和归档计划；本地处理优先，疑难项按需使用视觉模型，缓存和增量更新控制用量。优选不等于删除，相似不等于重复；示例成本是计算演示，尚无真实图片评测。

2026-09-12 已通过本机已有 HTTPS 凭据访问正确地址 forgejo.yokiirobw.top，核对相关远端固定提交；下载器、NAS 运维、开发交接与模型网关的复用判断已更新。[Forgejo 补充核对](forgejo-source-review-2026-09-12.md) 记录已实现能力和剩余缺口。

尚待核对：具体 reader 和 ZCode 产品、开发交接 controller、独立移动客户端源码、设备与手环型号，以及实际模型线路能力。这些不阻塞完整需求记录，但不能被描述为已完成集成。

2026-09-13 管理界面先做视觉评审。用户认可首版信息密度，要求增强设计感，并偏好苹果式材质、卡片、低饱和渐变和动效。[第二版视觉与动效提案](../../output/ui/management-v2/README.md) 包含三张效果图及使用示例数据的可操作稿，尚待确认，未进入业务功能实现。

后续用户认可 [紧凑记忆星图与日记阅读](../../output/ui/memory-diary-v2/README.md) 的方向，并要求继续设计其他页面；小屋、衣橱、家庭设备和资源库扩展见 [视觉目录](../../output/ui/README.md)。页面视觉认可与业务能力交付分别记录。

2026-09-13 小屋环境新增明确要求：[时间、光照、窗户与窗帘联动](../architecture/room-environment-and-lighting-design.md)。这些属于小屋基础体验；时间与环境持续求值、物件状态共享、手动与自动协调，渲染技术按局部照明及遮挡效果另行验证。

2026-09-13 智能家居接入细化：[天枢与 Home Assistant](../architecture/home-assistant-bridge-design.md)。天枢提供自己的界面和对话入口，HA 负责设备集成、状态与动作；服务回执与硬件反馈分别处理，米家设备按型号与网关验证。

2026-09-13 按用户要求补齐其余视觉：新增 [25 张代表页面与流程状态图](../../output/ui/remaining-pages-v1/README.md)，覆盖项目、资料、创作、智能家居、订阅下载、任务用量、角色记忆、生活扩展及关键失败/授权流程。已提供分类浏览页，业务交互与接口实现仍是后续阶段。
