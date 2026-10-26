# TS-319：AL11 群聊参与候选合同

用户已授权继续 AL08/AL11，并明确允许直接协调写权。记忆扩展协调者已在 `autonomous-life-boundary-2026-10-26.json` 的 `AL11_namespace_delegation` 把 **observation-participation/candidate-v1** 唯一作者权委托本聊天。既有 actor/user/Chat/group/archive 合同、原 extension-v7、Memory/Companion src 不在本卡。

依赖已交付 TS309、TS314、TS318。根协调者按原分配锁与容量检查登记独立 workspace worktree；从登记时的根精确 HEAD 起新分支，不提交根混合改动，不写当前根 index。

## 范围

只写：

- `contracts/observation-participation/candidate-v1/`
- `tests/contracts/test_observation_participation_contracts.py`
- `docs/handoffs/TS-319.md`

将根 `.runtime/autonomous-life-20261026/participation-proposal/` 修订 2 收敛为新候选包，复用既有 manifest 的 LF SHA256 约定和原 unittest/jsonschema 方式。不建立新的契约框架。七种内部 DTO：candidate、host ownership、claim request/result、semantic decision、pre-dispatch registration、delivery binding。给出生产者/消费者、真实原身份/受众/配置代际、字段含义、正反例、校验与运行时权威边界。查询目录、错误与认证沿既有 RPC/HTTP framing；不能把 schema 自检宣称认证、真实 CAS、当前权限或事务证明。

本地包仅为明确版本候选，不是生产发布。不改其他包；精确提交、规范化 manifest SHA256 和每文件 hash 交给后续两个消费者使用，消费者联合验证完成前不标生产冻结。

## 必须保留的运行时合同

候选、语义决定、宿主唯一 owner、投递分离。普通候选尚无 AL12 engage 时静默，明确 @/关键词/命令保原路径。候选的真实 person/entry 是原作者锚点，不能杜撰 BOT 人物或改原 mentioned。归档 ACK 独立于参与状态；ACK 后原行仍可读，删除后 unavailable，不重建。

原链实核后的身份阶段：候选可只有真实 native/account/source 的 observed 锚；engage 后原 owner 装 input/actor entry，pre-dispatch registration 至少为 registered，身份映射仍可能待 Core；Memory identity 及 Core 实际 admission、Platform `_confirm` 后才有 resolved 人物/会话。不得要求首次候选提前有人物/会话，也不以群号或 learning-frame 代替实际对话。跨阶段只补原 owner 已确认的信息，native/account/source/actor 不换。真实 reply 创建仍需 resolved 身份；老成员已有映射时，本次新 fanout 确认可晚于 reply 到达，所以 binding 另有 `source_confirmation=pending/confirmed`，从创建起唯一关联但 pending 不 eligible。身份 resolved 不等于本次来源已确认，不能放宽原 context/send owner。

领取先查同 request_id 原回执再判断 free/CAS；同参复用原不可变事实但 fresh 返回当前有效性，异参冲突，过期不续授。一个 native event 全宿主唯一槽，不能每 actor 一个槽。handled/unknown/claimed 不可迟到改 free；unknown 发送不换键重试。

派发前持久登记真实 admission/message/source/fanout/actor assertion；两侧准备记录核实后才沿原 Core 受理。原 Bot 队列本地事务创建真实 reply/attempt 时同时绑定 registration；claim 在排序和限量前排除无效参与回复，不占正常连接槽。不声称跨数据库事务，不另建观察或出站队列。

NoneBot 固定 2.5.0，只支持由天枢独立 onebot 入口构造、处理器集合受控的宿主；adapter_kind 字符串和插件列表自身都不算证据。原 `_observe`、`_claim_observation_reply`、`_scope` 本次实际执行须分别给出持久捕获、显式路径正常未接管、精确路由正常无 owner 收据，区分无路由/多路由/容量/存储失败。所有自有前后 hook/任务正常闭合、集合未变且成功收据齐全才可 free；permission/rule 异常可能被框架吞为 false，finally 到达不算成功。各 matcher 的 state.copy 不能充当共享收据。未知宿主图/命令 trie/脱离任务仍 unknown。其他 matcher 实际进入即永久 handled。AstrBot 固定 4.27.3 无全 pipeline 完成公开证明，普通参与保持 unknown，禁止自动 claim，但保留原明确触发能力。

固定源码依据和完整澄清见 `autonomous-life-wave2-execution-2026-10-26.md` 的 AL11 节；写入候选 README 以便消费者独立审阅。

## 验证与交付

整体候选稳定后集中验证 schema、封闭字段、正反例、字节/日期/摘要规格与 manifest；沿现有真实 jsonschema Draft2020-12、本地 Registry。只在缺实际约束时补反例，不写伪运行模拟器。旧提案的 schema 结果仅按其原 hash 复用，包路径/内容变化须验证新包。SDK/平台两边实现后另做真正联合，不重复未变检查。

使用已存在解释器只读执行，不安装或修改 TS311 环境。仅本卡临时数据，无模型/QQ/外站账号/PG/生产迁移/推送部署。按精确路径提交及交接，根任务板与候选运行快照由父级单写，交付后停止扩展。
