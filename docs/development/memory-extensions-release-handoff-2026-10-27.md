# 记忆扩展统一发布交接

2026-10-27。自主生活聊天报告用户已明确要求：完成其当前任务后，连同记忆更新一起推送、部署。
统一发布协调者为 `01a15a96-9c82-7102-b512-9b592e1f4dff`；等待 AL08/AL11 审查及实际联合完成后，
由该协调者串行集成、推送、部署并实机验收。本聊天只补交接，五卡源码继续冻结，未新增生产操作。
下文是待发布依赖，不表示已部署。原阶段记录里的“不推送部署”保留为当时状态，不要求再次确认。

## 最终源码与共享合同

| 产品 | 本聊天最终提交 | 统一集成时的要求 |
| --- | --- | --- |
| Memory / TS313 | `a2de620c676a24d7c08bb33ef3f1be2488b71666` | 完整承接已发布 `62206f2` |
| Companion / TS314 | `671deeadb04301b42f56a3a2020548117698bbc0` | TS320 最终适配器提交应完整承接此版本，保留 src 行为 |
| Chat / TS315 | `1c2ee12c42d466bd9b48d1dd67d491429753f395` | 保留原独立 Chat 服务与数据库，不以新空实例冒充现网归档 |
| Platform / TS316 | `3e2cc412c676450a90dc0cbd3c3387d1c945e5e2` | 已由 TS318 `6d8dbaf29979c210c027f36a8333b5a605f7c3e6` 直接承接；TS321 从后者串行继续 |
| Gateway / TS317 | `5ff98947f7eaf82384d359ca84ad581a5cf113f0` | 保留原 TS261 完整 `d658367` 候选历史及 Q17 改动；核清与远端 main/现网的完整差异 |

五个原交付 worktree 的 HEAD 和 clean 状态在本次交接又已核对。TS316 已包含 AL07 整合
`209df023f223027880d597191ac3a49dcca1459c`，不要重复合并 TS310/AL07。
Gateway 本批新增虽仅三路径，其父版本含未集成的原 Q17；不能把三路径当成整个发布范围。
若协调者根据实际主线选择性集成而产生新运行版本，按其实际差异补验相关后台路由，不改写旧候选。

根共享交付分支是 `codex/memory-extensions-shared-20261026`，交接前 tip 为
`a72ebca818e0016775dc7957d734b43b9bd4a75c`；本交接随同一分支继续保存。
它承接已发布根记录 `2a43d3706d4cbde71318f87785d95350783f7764`，只含本批候选合同及交接。
不要整份复制根 checkout 的混合 contracts：其中未采用的用户 rev7 工作稿不属于本批。
实际受测完整包集是 `.runtime/memory-extensions-20261026/contracts-v7`，即已接受用户 rev6
加本批扩展；这里的 extensions-v7 不是用户协议 rev7。五个 manifest pin 见同名 JSON。
TS319 的新 `observation-participation/candidate-v1` 由另一协调者单写，与这五个包分别核 pin、装配。

## Actor 存储与双向来源链

Memory 的 `actor_memory` 配置至少含独立绝对 `database_path`、`contract_directory`、`authorities`。
`state_directory` / `guard_path` 默认分别为 actor 路径加 `.storage` / `.guard.json`，均须持久保存，
与用户库、用户 source guard 分开。复用原 storage backend/namespace 设置，逻辑身份为
`service=actor-memory, logical=main`；运行时自动登记，不新增注册命令。

在 actor 写者停止时，沿原安装命令初始化：

```console
tianshu-memory --config <private-config.json> migrate-actor-memory --backup <new-local-file>
```

等价入口为 `python -m tianshu_memory.cli`，不是 `python -m tianshu_memory`。初始 schema=2。
该 backup 记录此前 actor 域未安装，不是用户库或整个 PG 的冷备；原用户 Store 不在此分支迁移。
已有安装只验证原 guard/schema，不重建；缺失/不符不能靠重跑初始化修复。已有 SQLite actor
库没有自动导入 PG 的迁移。备份/恢复须把 actor binding、guard、实际数据库作为同一恢复点；
新清单纳入 actor-memory.main，原发布的“24 逻辑源”只是上一轮盘点数量。

`authorities.actor_check` 使用固定 Platform HTTPS authority URL、实际 `token` 和可选绝对
`ca_file`；此处 token 字段不是 token_env。`authorities.producers.companion` 使用固定
`source_facts_url` / `source_check_url`、token、可选 CA，两 URL 可相同。配置和真实凭据留在
原私有配置管理中，不把 token 放进本交接或仓库。

需要 Memory→Platform、Memory→Companion、Companion→Platform/Memory/Knowledge 的真实 TLS
及服务鉴权链。Comp source 固定为 `/internal/v1/actor-self-memory/source`，仅接受 `callers.memory`；
实际原件仍由 Knowledge 掌管。Memory 的 `callers.companion` 明确登记所需 `actor_memory_operations`
及 `allowed_actors` 或原 `allow_runtime_roles`。Platform 对真实 Memory/Comp 服务提供
`actor.memory.authorize`；Comp discover 另需 `actor.capabilities`。角色须当前启用，具备实际
`memory.read` / `memory.write`；使用 discover 得到的 Platform role_version，不能拿 Comp 本地版本替代。
实际阅读还沿原 Life/Reading 角色和 runtime_epoch 校验，不回填虚构事件或人工进度为真实经历。

Comp `services.actor_memory_authority` 使用原 `url/token_env/ca_file` 配置格式；Memory 仍走
`services.memory`。未显式配置 `services.knowledge` 时，最终版本已用同 Memory 配置独立构造、
登记关闭 Knowledge 客户端，避免来源回调等待外层客户端自己的容量；保原每客户端 3/4 门与 15 秒。
用户 health 正常不代表 actor 端口已初始化，须在统一实机验收中走真实 AL01/来源/回执链。

## Chat、群页面与历史遗忘过滤

Chat 实际 `EVIDENCE_READ_CONFIG` 登记 mappings 和 readers；reader 用独立 Bearer 的
`token_sha256`、service_id、mapping_ids，不借管理员或 OneBot token。APP_SECRET_KEY 须持久且非默认，
稳定游标依赖它和当前 reader/mapping/filter。固定映射包括完整 channel、robot_id、room_id、
message_type、id_kind 和 import_source_id；external 的 import_source_id=null，QQNT 必须精确 source。
部署其既有 evidence router，并用 HTTPS 连接真实归档。具体形状见 Chat 两份候选运行文档。

Comp 增加 `services.chat_history` 和 `chat_history_mappings:[{channel,mapping_id}]`。
Platform 保原 `web_memory`，增加 `web_group_memory.chat` 及 groups：id/label/entry_id/
conversation_id/chat_channel/archive_mapping_id。group entry 为当前操作员真实 WEB local_operator，
audience=group，actor 匹配且仅有一条 platform→memory/dialogue 路由。不要把操作员伪成 QQ 身份。
Origins 的 read_channels 由 groups 装配，不另造配置项；QQ channel 当前 canonical conversation
必须存在且等于登记 CID，缺失不能以配置值代替。操作员保原 memory.read、session/CSRF 和 origin.issue。

Memory browser reader 明确允许真实 WEB account、角色、完整群 scope，并在同 reader 的
archive_mappings 中登记对应 scope、chat_channel、platform_id、self_id、room_id、物理 conversation。
先核 Chat locator/hash，再逐全部 native aliases 核 Memory 已知遗忘；任一 withdrawn 过滤，全部
unsupported 过滤，Memory 不可用不返回未检查原话。未映射且受信 external-ID 导入可作归档证据，
不能冒称 Core-current；当前 Memory 不支持的 QQNT aliases 不得当成 current。

同时生成并发布最终 Platform 前端产物；现网若外置挂载静态文件，单更新后端镜像不会更新群图页面。
详细配置沿原 `docs/platform/group-memory.md`、Memory `docs/browser-extensions.md`，不另造管理入口。

## 后台身份

Comp 动态后台同时配 `services.gateway_background`、`services.provider_selector_background`，
固定真实 caller `companion_background`。Platform selector 凭据实际服务同名并具备原 `config.select`；
Gateway runtime 凭据沿原 `provider.runtime`，grant 保实际 caller/turn/version/workload。
Gateway 登记独立、internal=True 的 ClientGrant，启用现有 provider self service，并在原
`workload_bindings.bindings` 中显式设 `companion_background: background`。不改原容量、保留槽或超时。
请求头/body 不授予后台类别。只有后台 Gateway 而没有 selector 时，仅可使用明确配置的独立
`life_config_version`；不借前台动态版本。仅有后台 selector 的配置无效。

## 验收复用与限制

本批集中结果、原始 XML/hash 和实际服务联调收据见
[本地交付](memory-extensions-delivery-2026-10-26.json)：Comp 1680P/48S，PG 70 适用通过；
Chat→Platform→Memory1P、actor Comp→Memory→Platform2P、后台 Comp→Platform→Gateway2P。
原不利记录保留，不重跑已过完整组件/旧压力/145轮。默认客户端补充回归为 MockTransport，
其他联合的 Knowledge 原文 peer、用户对话锚点也有明确替身边界，不能称新生产配置已接通。

最后实机拓扑仅已知上一轮为 `.230` 计算、`.210` PG/Redis/媒体；当前状态由统一发布协调者只读核对。
新 actor 域、Chat真实 scope、群 UI、后台身份需要在最终整合版本上实机确认。保持原配置/数据/
其他服务及回滚点；同服务串行重建，不并发生产写入。

真实素材公开日记仍未接入；actor_only 不授对外披露。群图仅表示互动，二维坐标不写回事实。
当前模型测试账本仍 17540/18000、余460；本次交接0模型/0QQ/0PG/0生产操作。后续如实机验收
使用模型，仍由统一协调者记入原上限，不另起预算。
