# 逐群四态与入口分流本地交付（2026-10-27）

2026-10-27发布阶段已收口：已推送部署、完整运行验收通过；真实QQ配置与业务验收由用户延期。最新见[发布记录](group-modes-release-2026-10-27.md)。下文保留原本地阶段约定/结果。

群聊、私聊先检查当前状态，停用后直接返回，不再进入正文加工、排队、归档、Core 准备、自动整理或模型。后台在选取下一阶段前批读处理资格，执行中切换状态再做轻量复核。三个产品沿既有 worktree 新分支实现并提交，状态为 **local_verified_not_integrated**；未合产品 main、未推送或部署。

| 产品 / 任务 | 本地提交 | 基线 |
| --- | --- | --- |
| Platform / TS-325 | `1bb4d69c0b06db2faa972ecca1c23fef4307f721` | `620b1e0b0a6c76ac69f538a13e00c9c6d53b112d` |
| Companion、SDK / TS-326 | `677fcb1f1392c134084d6e2be0d61f1a49f757b4` | `d416b68c2bf47f6a5675c8f170c5185062e66688` |
| Memory / TS-327 | `110a41bf851163901f7fc4a8f13cc19817285a3a` | `a2de620c676a24d7c08bb33ef3f1be2488b71666` |

产品分支均为 `codex/group-modes-20261027`，分别复用 TS-311、TS-307、TS-306 的物理 worktree；原分支、根 index 和原改动保留。精确行为及接点见[执行约定](group-modes-execution-2026-10-27.md)，测试来源见[结构化交付](group-modes-delivery-2026-10-27.json)。

## 功能与复用位置

Platform 原观察配置负责逐群模式、角色和字面关键词。停用不采集；仅观察继续原归档和整理；启用回复只有真实 @ 或配置关键词才进入原回复链；主动模式才允许后台根据角色兴趣和有限上下文决定参与或沉默。旧配置确定迁移，不会自动变主动。界面展示宿主实际能力及应用状态，不支持普通消息 claim 的宿主保持静默。

SDK 的主要状态门在实际消息入口，位于正文规范化之前，NoneBot 的普通消息所有权 graph 也仅在 active 群运行。私聊沿原策略结构执行，observe=false 不能再被名单回复许可绕过。A 群模式变化不使 B 群的连接或在办事项失效；停用再启用不会复活旧回复 generation。

Companion 沿原 MemoryLearning、ModelSlots、后台模型选择和 AL11 candidate/decision/claim/register/发送账本实现参与，不建新持久队列或发送器，不加语义命中词规则。停用正常暂停未发阶段，不消耗重试；已发请求和原 output/receipt 保留，unknown 按原参数对账。完成 task 在刷新前清理，修复另一个群持续繁忙时，重新启用群可能长期吃到旧暂停提示的问题。

Memory 只小范围扩展原 browser 登记：明确 `allow_runtime_groups: true` 后，既有受信 Platform 可登记真实观察群，继续核账号、operator person、角色、fresh origin 和来源证据。实际 Core 生成 canonical CID，暂停不变更历史读取权或删除证明。原群画像页可列出真实观察群并读群话题；Chat 统计/关系/历史仍要求真实 Chat 配置和唯一归档映射，不能把观察 channel 当 Chat binding。

## 集中验证

| 范围 | 有效结果 |
| --- | --- |
| Platform 后端，逐父合并 R1/R2/R3 | 141 父：132 通过、9 范围跳过 |
| 四态配置和群页面，桌面及手机 | 14 通过；类型检查、构建通过 |
| Companion、SDK，完整 R1 与受影响 R2/R3 合并 | 1771 父：1723 通过、48 范围跳过 |
| Memory browser 登记与原读取回归 | 48 项：41 通过、7 范围跳过 |
| 实际 SDK/Platform/Companion/Memory 入口联合 | 5 父通过，停用下游为零、跨群隔离、私聊、触发回复和恢复历史 |
| 实际动态群 WEB 来源链 | 1 父通过，原 Core CID、缺字段拒绝、启用后读取、暂停保历史、换角色拒绝 |
| AL12 实际消费与原受控 SDK | engage / silent 两脚本成功；silent 无注册、发送 |
| 新 participation 功能实际 Platform/Gateway 路由 | 1 父通过，后台路由及默认模型继承成立 |

有效合并按 `(classname, name)` 对应最新适用结果，不是第二次完整全绿。Companion 原全量 19 个失败父及同父 teardown 错误、R2 的一个测试误改，Platform 原 22 个失败及联合夹具装配失败均保留；其相关节点已定向关闭。Companion 原 image 时序断言首轮失败、定向通过，未改图像代码，不声称已确证其根因。

Platform 早期浏览器宽选曾部分运行并出现 6 个失败；其中高级适配器向导第一叶在原基线也复现，另外 5 个未逐项归因，不宣称整个前端套件通过。受改功能的最终 14 项通过。原四项全格式失败、既有工作区 51 个旧路径缺失仍保留；未扩展为无关修复。每轮输入来源和实际提交绑定已独立核对，未变化检查不重复执行。

## 仍需区分的边界

- 全部为隔离 SQLite、loopback/TLS 和固定本地模型。入口联合的 Core 回复 dispatch 是计数替身；AL12 联合的 Core 管理/归档及 QQ 回调是替身；实际 Gateway 路由、实际 Core/Memory WEB 来源链分别独立验证，不能拼称一个真实 QQ 端到端验收。
- WEB 群话题及原摘要正文、memory refs 已与真实完成回执核对。原 `records(subject=group)` 读取的是显式发布的群画像，本父没有发布此类画像，返回 200、空列表正确；schema6 不会自动发布这种群画像，非空已发布群画像未在该联合父中覆盖。
- Memory 动态群字段、Companion 服务 `observation.verify` 授权、更新宿主 SDK 和真实 Chat 映射仍需发布协调者实际配线。生产 PostgreSQL 的回复连接约束迁移本批未执行，发布前需在获准的隔离/生产窗口验证。
- 旧 opaque 材料没有唯一公开物理映射时保留暂停并显示 mapping_required，未读生产库清点数量。真实模型语义、真实 QQ 主动参与和生产配置未验收。

本批真实模型新增 **0**，原账本 **17540 / 18000**、余 460。生产与凭据维护由另一统一协调者单写，本批不探测凭据、不写生产、不推送或部署。到此停止追加全量、压力或真实模型测试。
