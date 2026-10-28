# 天枢质量修复与角色独立日常交付

**后续状态：已合入、推送 GitHub 并部署到 NAS。** 详见 [部署记录](quality-life-deployment-2026-10-03.md)。以下保留本地开发完成时的验证快照。

状态：**15 项均已本地开发完成并提交，相关隔离联合验证完成；尚未合入产品 main、推送或部署。** 精确提交、检查分类与合同清单见同名 JSON。

本轮按用户最新授权，由三个子智能体在同一对话并行实施，协调者处理适配器、CI、公共协议与交付整合；没有新建开发窗口。开发来源采用审查时与 Dot/GitHub/NAS 对齐的 rc.5 及已记录独立产品版本，保留原目录的未提交修改。

## 功能结果

角色启用即自动登记自己的世界、房间、基础作息和当日计划；关闭对话能力、没有 QQ 消息、网页未打开都不影响后台时钟。每天及到期阶段使用角色已选模型与真实人格内容生成计划和经历，写入原 Life 事件、获知和日记素材链。对话可以改变活动内容；日程是否存在和时钟是否推进不由对话决定。

计划与已发生经历分开展示，支持角色名称、当天安排、当前阶段、历史日期和分页。没有可用模型时继续基础作息并显示明确状态；失败或中断可通过现有管理权限重试计划或当前阶段。停用暂停，恢复对齐现在，停机缺口不补造经历。撤回影响立即使尚未发生的旧计划内容失效，已发生经历保持。

质量修复包括适配器不再把累计历史当永久容量上限、消息归档按稳定来源身份去重、停用重启后的绑定恢复、浏览器 storage 异常降级、任务列表分页与筛选一致性、异步路径中的 SQLite 工作迁出事件循环、来源完整范围分批与回执索引、文件新鲜度规则复用、模块公共边界整理、旧前端组件和重复 CI 步骤清理。

## 代码与版本

权威本地候选位于 `C:/YOKI/Codex/tianshu-peiban-bot/worktrees/quality-life-20261003/`，各产品分支均为 `codex/quality-life-20261003`。不要将原根目录 `projects/` 的旧检出或未经核对的 GitHub main 当成本轮候选。源码固定提交、基线和合同完整字节清单见 [机器清单](quality-life-delivery-2026-10-03.json)。

| 产品 | 本轮固定候选提交 |
| --- | --- |
| platform | `3f715b6e94c6552b30e03c17497f4dd56f32292e` |
| companion | `11e6cc37057d60e389c8837926fe61352065a605` |
| memory | `8415bc85bcad089a9e24461d5f3e88039754103c` |
| chat-audit | `66097a98e8613437f96a76ddae4e23bb7e1fd609` |
| assetlibrary | `54b2beb39568d8ccf954688e24dd18ab868d014c` |
| coordination | `8089ce0b48ec7a48475c26035dc3e4cca992fcae` |

协调 SHA 固定实现与合同；随后保存本交付文档的提交不做自引用。Gateway 未改，沿用 `e4f112f02d28a1ded134126feed33f15e003ec20`。

- [Companion 生活实现与部署接线](../../../companion/docs/handoffs/DQ-companion-life.md)
- [适配器容量修复](../../../companion/docs/handoffs/DQ-adapter.md)
- [Platform 界面、公共边界及实际联合验证](../../../platform/docs/handoffs/DQ-platform.md)
- [Memory 批量来源与文件读取复用](../../../memory/docs/handoffs/DQ-data.md)
- [Chat Audit 消息身份修复](../../../chat-audit/docs/handoffs/DQ-data.md)
- [AssetLibrary CI 验证](../../../assetlibrary/.codex/handoffs/DQ15/tests.md)

## 验证证据和限度

| 范围 | 实际证据 |
| --- | --- |
| 公共协议 | 4 项 schema/实例/哈希/一致性规则用例通过；两份消费者捕获的实际 257-selector / 2-batch 输入通过独立合同校验；四个必需协议目录通过现有打包哈希核验 |
| Memory | 分批归并有效结果 1132 通过、1 项固定基线失败、0 跳过；首次全量及环境修正后的窄补跑分别记录，不称首次全绿 |
| Companion | 原全量和环境/兼容修正的相关补跑，按节点归并 721 项有通过证据、4 项固定基线失败、15 项未执行；最终撤回补丁直接相关 114 项通过，新生活用例共 28 项；未重复整套 |
| Platform 后端 | 受影响后端 57 通过、40 subtests；共享 HTTPS 管理传输和相关入口 26 通过；重试授权撤回 1 通过，分批结果不相加为不重叠总数 |
| Platform 浏览器 | 定向三套桌面/手机 24 项通过；重试变化后两套 22 项通过；现役模型页面 2 项通过；任务中心 8 项经一次失败修正后各自通过 |
| 实际生活读取 | 真正 Platform → Companion HTTPS，23 条同秒经历分页 20+3；一个后端用例包含桌面/手机浏览器项目，通过；使用隔离持久数据，非 route mock |
| 实际新角色 | 浏览器创建角色、指定模型 B、关闭对话、后台自主登记、真实 Selector/Gateway/合成模型生成、计划失败后按钮重试、落库显示、重载与停用；四服务用例通过，没有预写角色/grant或手动 tick |
| 关系接口复用 | 固定 Memory/Companion 源码快照与新 Platform 的真实 HTTPS 联合 5 项通过；曾因测试源码目录布局未配置而无法启动，修正隔离指针后通过 |
| Chat Audit | 稳定来源身份、相同内容不同消息、重放及导入关联定向 10 项通过 |
| 适配器 | 累计超过 20000 历史、待处理上限、重放、unknown 与重启等 12 项通过；NoneBot wheel / AstrBot ZIP 本地构建成功 |
| AssetLibrary | 现有 verify_repository 通过；repository 94 通过、1 项在未修改基线中复现的目录断言失败；未执行远端 Actions |

Python 检查、受影响文件格式、前端类型与构建及 diff 检查结果见各产品交接。原有格式遗留、固定 peer 或 SDK 缺失均单独记录，未把抽样或分批结果称为全系统全量全绿。独立窄复核找到的计划撤回、模型 grant 请求身份、停启旧回包、租约在后置授权等待中到期，以及已存未来细节残留均已修复并有针对性验证。

Memory 513 来源的隔离 HTTPS 样例约 2.5–3.1 秒，索引避免逐 receipt 全表 JSON 扫描；该测量不能推算 NAS 或无限历史容量。真实模型生成内容的语义质量、线上长时间跨日、真实 QQ、生产数据历史影响均未在本轮验证。

[桌面生活页](../../../platform/.runtime/role-life-joint/results/role-life-desktop.png) · [手机生活页](../../../platform/.runtime/role-life-joint/results/role-life-mobile.png)。截图是忽略的本地合成运行产物，协调者已目视布局；不作为生产使用证明。

## 后续上线需要使用的既有流程

本轮状态只到本地候选提交和隔离联合验证，尚未合入产品 main、推送、更新 NAS 或迁移生产数据库。正式发布时使用本次固定产品 SHA，沿已有更新流程处理：

1. 将 `life-read/v1`、`source-sync-batch/v1` 加入既有部署 manifest 的合同清单；本清单还保存补回的 diagnostics/v1 和 model-origin-renewal/v1 原始字节。交付 JSON 是源码交付记录，不是可直接执行的完整部署 manifest。
2. 已有 Memory schema 3 按原 source-guard 规则停写、保留现状和新备份，再运行显式 `migrate-source-lookup`；新安装沿现有迁移链创建索引。没有执行生产迁移或历史消息补救。
3. 沿现有独立生活读凭据设置一次 `life_readers.runtime_roles=true`，新角色随后自动登记；既有显式读者撤回不被覆盖。复用角色模型选择服务；显式 `life_writing=false` 继续表示禁止生活 AI，未配置该项时自主日常默认允许生成。旧日记/长篇的显式配置和发布规则不变。
4. 更新后再做真实环境零对话、网页关闭、经过实际时间节点的验收。当前本地合成模型和固定时钟验证不能代替这一步。

完整优先级与每项范围保留在 [15 项开发队列](quality-life-development-queue-2026-10-03.md)。
