# 自主生活首批执行卡（2026-10-23）

用户已授权按 AL-01–18 开始开发：先核对 Q20 与正在进行的记忆升级，可独立推进的使用同聊天子代理并行；本批保留用户对话记忆逻辑，角色自身记忆独立域；功能稳定后集中审查和验证。本记录只放行下列精确切片，不表示整个队列已完成或已发布。

## 现状与写权

- Q20 本地交付：Memory `291721bdb01e852f4e65b84b151cfa1fbbb2230b`、Companion `b84265379566fdb07916949533bf28a2781e06eb`、Platform `95767b2b18c78eb9c503f5def34fd297b8a9fcc9`。候选未集成发布；百人压力 78 送达 / 21 失败 / 1 未准入，不能称压力验收通过。独立 88 为 87 送达、1 超时，不能替代完整 210。
- 活跃聊天“记忆系统优化”正在执行 Q21：TS-306 独占 Memory、TS-307 独占 Companion、TS-308 负责对照实验和该批共享记录。其 `src/`、测试、装配及迁移广泛重构仍在进行。不能只等待 Q20，也不能以新增文件为理由绕过同产品单写者。
- 本批仅拥有新增 `actor-self-memory/candidate-v1`、`actor-capabilities/candidate-v1` 合同命名空间、本批记录与 TS-310 平台切片；不改 Q21 的合同、CURRENT、模型账本、运行资源或测试数据库。
- AL-02/03/04/05/06/09/10/12/13 等依赖 Memory/Companion 的实现，待 Q21 写者释放或双方明确划分后再分派。接入时取 Q21 最终验收提交，不能把本批接在旧 Q20 上覆盖新记忆逻辑。
- 平台当前没有其他在执行写者。本批从完整 Q20 平台候选隔离开工，保留其 BotObservation 改动；这不是把 Q20 候选宣称已发布。最终产品合入依赖协调者串行审查。

根仓库已有混合修改和暂存，平台已有未跟踪 `build/`；全部保留。采用已有 `workspace.allocation_plan` 校验任务/并发后，以精确 SHA 创建隔离 worktree，登记 lease/context；不 reset/clean/stash、不暂存混合文件。全局并发 5，TS-309 验收释放后才分配 TS-310。

## TS-309 — AL-01 候选合同

基线 `ab93be4e91b824008d387d71c80e77dfac7341b3`；分支 `codex/ts-309-actor-contracts`，工作树 `worktrees/TS-309/workspace`。协调者提交，子代理定向审查并仅在委派文件内返修。

允许路径：

- `contracts/actor-self-memory/candidate-v1/`
- `contracts/actor-capabilities/candidate-v1/`
- `tests/contracts/test_actor_contracts.py`
- 本文件与 `docs/handoffs/TS-309.md`

交付独立 schema、正反消费样例、LF 规范化哈希与语义约束。自我记忆不伪造 person：写入请求引用真实来源，事实由来源权威确认；长期来源限制与当次披露凭据分开；部分阅读绑定具体素材版本；读取与发送前检查核对同一版本；撤回派生单向失效。来源/角色端口是待实现合同，不能以默认允许或管理员 token 冒充接通。

平台能力复用现有角色、character binding、插件执行台账、Vault 与回执；明确权威角色版本、实例版本、动作白名单、稳定内部 principal 及 unknown 不自动重发。候选仅用于隔离开发，不覆盖已发布用户合同，不修改产品路由。

集中验证：真实 Draft 2020-12 校验、正反例、请求/响应覆盖、manifest、来源范围/时间/版本/覆盖绑定等负例。复核旧合同相对基线字节未变。测试通过不等于来源生产者已实现或生产联通。

## TS-310 — AL-07 平台角色能力与现有绑定界面

依赖 TS-309 本地候选验收。基线 `95767b2b18c78eb9c503f5def34fd297b8a9fcc9`，分支 `codex/ts-310-actor-capabilities`，工作树 `worktrees/TS-310/tianshu-platform`；唯一产品写者 `research_agents`。使用同聊天协作，不创建用户新聊天。

实现完整调用路径：已配置 Companion 服务认证 → Platform 当前角色状态/版本 → 当前 character binding、实例版本、明确 entries → 实际可用动作/账号 → 既有执行器及同一意图台账 → 窄化回执。发现可不传初始角色版本，返回权威版本；invoke/get/reconcile/cancel 要求版本并核原调用归属。复用现有取消/对账/重启恢复，不建第二执行队列、账号库或权限表。

新增 `actor.capabilities` 传输动作，只供显式配置授权的 Companion 服务；生产凭据配置不在本卡。`binding_revision` 使用实例 revision；服务器 principal 固定为 `actor-capabilities:` + 原 actor_id。角色绑定缺 entries 不能沿用旧 workspace 隐式全允许。保留 `not_applied`、unknown 等真实效果状态；不把 accepted 当完成，不把找不到回执当无副作用。

现有插件绑定界面增加角色选择与动作选择，保留 workspace 行为；账号和凭据仍归既有实例/适配器。泛用桥仅声明当前有处理器的动作，不能将 B 站/小黑盒目录声明冒充真实互动已可用。AL-14/15 保留后续实现和真实验证。

允许路径（只按实际必要修改）：

- `services/platform/actor_capabilities/`、`services/platform/plugins/actor_bridge.py`
- `services/platform/plugins/{execution,repository,manager,web,control}.py`
- `services/platform/plugins/schemas/instance.schema.json`
- `services/platform/{auth,service,server,contracts}.py`（鉴权与薄装配）
- `apps/web/src/features/plugins/{BindingsPanel,InstancePanel}.tsx`
- `apps/web/src/features/plugins/{types,api}.ts`
- `tests/backend/{test_actor_capabilities,test_actor_capabilities_http,test_actor_capabilities_pg,test_plugins,test_plugins_web}.py`
- `tests/backend/run_plugins_fixture.py`（隔离浏览器夹具中的合成角色）
- `apps/web/tests/plugins.spec.ts`
- `docs/platform/actor-capabilities.md`、`docs/handoffs/TS-310.md`

`role_runtime.py`、媒体账号、服务凭据只读复用；若确实需要新窄接口，先向协调者说明归属与变更，再登记范围。不得修改 `bot_observation.py`、`bot_delivery.py`、`bots.py`、共享适配器、Memory/Companion/Gateway 或 Q21 文件。合同从完整 Q20 快照与 TS-309 精确候选消费，不能以根目录缺包为由改旧合同。

稳定后集中执行已有相关后端测试、角色跨账号/动作/版本/恢复等必要用例、前端类型/构建与真实浏览器绑定流程。只用隔离合成库/插件；PG 环境缺失则明确跳过，不连接现有生产 PG。无真实模型、QQ、外部账号动作、推送或部署。交接列实际命令、通过/失败/跳过、未覆盖、精确提交；本地候选、产品集成、生产发布分别记录。

## 后续推进顺序

TS-310 稳定后，平台写者串行推进 AL-08 内容发现与 AL-11 群参与基础；与 Q21 释放后的 Memory/Companion 独立切片并行。AL-02 的新 StorageRuntime/schema/guard 与旧用户域保持独立；复用现有基础设施，不给旧 Store 换 service 名就安装用户表。AL-03 的来源事件与待交付记录按既有事务边界接入。Q21 最终合同、测试与负载事实必须重新核对后才能共享装配。

没有用户对话记忆回归、自己的记忆可续存、来源撤回传播、次日兴趣延续、可靠外部回执和可用界面之前，不将 AL-17/18 标完成。18 项队列原范围保留；账号实测和发布按对应阶段授权执行。

## 本批本地交付回收

TS-309 已提交 `1d5523e7c968d1deccc569ef396d953b8c1f2241`，13 项合同检查通过。TS-310 已提交 `90c0d44fc5d244e91ea4665e59e18e18a607e3df`，工作树干净；独立审查发现的排队写取消终态已在原执行器修复并定向验证，没有其他阻塞项。16 项角色行为与 3 条浏览器流程通过；相关插件/HTTP/观察链和网页测试、类型与构建结果及全部失败/跳过见[精确交付记录](autonomous-life-first-wave-delivery-2026-10-23.json)和[产品交接](../../worktrees/TS-310/tianshu-platform/docs/handoffs/TS-310.md)。原生 PG、真实账号/模型及 Companion 消费链未实测；额外 installed smoke 因隔离环境媒体依赖不齐失败，不宣称全量安装检查通过。

最近再次核对“记忆系统优化”：Q21 已根据真实对照结果放行集中实现，要求规范当前事实与原话证据共同保留并区分计划/实际执行。TS306/307写权仍未释放。本批没有改动 Memory、Companion、Gateway 或 Q21 记录；根原有暂存区与其他任务行保持。两卡 done 均为本地候选交付，不是产品集成、推送或部署；角色自身记忆存储和自主生活闭环仍按剩余队列接入。
