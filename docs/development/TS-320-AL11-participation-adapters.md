# TS-320：AL11 宿主归属与适配器接入

用户已授权继续 AL11 与同聊天子代理并行。`autonomous-life-boundary-2026-10-26.json/AL11_namespace_delegation` 已明确把 integrations-only 适配器交本聊天新卡单写，Companion 基线 `671deeadb04301b42f56a3a2020548117698bbc0`。TS314 已交付；不写 TS307、不写任何 `src/`、记忆、日程、模型或陪伴运行行为。

依赖 TS319 候选包和 TS314。分配前只读准备；根协调者登记、创建独立 worktree 并发布精确候选 pin 后才写产品。一个作者同时维护 canonical 与两个发行副本，不在不同检出并写。

## 精确范围

- `integrations/shared/`
- `integrations/nonebot/tianshu_nonebot/`
- `integrations/astrbot/astrbot_plugin_tianshu/` 的 Python 接入及 RPC 副本；不改页面/账号配置
- `integrations/build_adapter_releases.py`
- `integrations/nonebot/README.md`、`integrations/nonebot/ADAPTER.md`
- `integrations/astrbot/README.md`、`integrations/astrbot/ADAPTER.md`
- 新 `tests/test_participation*.py`
- 必要原 `tests/test_adapter_rpc.py`、`test_adapter_nonebot_host.py`、`test_adapter_astrbot_host.py`、`test_observation_adapter.py`、`test_adapter_capacity.py`
- `docs/handoffs/TS-320.md`

不改 dependencies/锁、Companion src、根合同/板、其他项目。需要精确扩路径先向父级报告，不借 root 全仓权限越界。

## 完整功能

复用 AdapterService 原观察 SQLite 行、RPC、配置代际、ACK/保留/清理和发送入口。普通非 @ 群事件有独立宿主归属状态；归档 ACK 不消费或重置参与状态。候选可以在 processing 被观察，不能为了 AL12 等模型阻塞原 poll/ACK。ACK 后能按原键读取、claim 同参回放；行被清理即 unavailable，不按复制候选重建。

宿主全 native event 唯一 CAS 槽，不按 actor 分槽。只允许 processing→free/handled_elsewhere/ownership_unknown，free→claimed；handled/unknown/claimed 不回 free。领取回执先于 free/CAS 查询，同参返回原不可变结果与 fresh current validity，异参 conflict；过期 grant 只保留历史事实，不续授。原回包丢失、重启、配置变化和 ACK 后回查都沿同一原行/receipt 恢复。新通信的记录遵循 TS319 精确候选 pin，认证仍用原 RPC 信任边界，不能接受模型自填身份。

NoneBot 2.5.0 的首版自动普通 claim 仅支持由原 `onebot.py` 独立入口真实构造、处理器/任务集合受控的天枢宿主；配置字符串本身不授予能力。所有其他宿主默认为 unknown。不得修改/猴子补丁框架核心，也不重复运行 filters/rules 猜 owner。若需读固定框架注册集确认封闭集合，明确版本和失败时 unknown，不能把插件列表当全部 hook 证明。

在原 `_observe`、`_claim_observation_reply`、`_scope` 的一次实际规则执行中，分别保存真实捕获成功、显式观察回复正常不接管、旧精确路由正常无 owner 的结构化内部收据。区分无路由、多路由、容量、存储、租约/代际失败；旧 False 不等于正常完成。matcher.state.copy 不能充当共享事件收据。只有已知集合未变、全部自有前后 hook/任务正常闭合、收据齐全且当前期限/策略/原行仍有效，才置 free。异常、取消、缺失或未知脱离任务仍 unknown。其他 matcher 实际进入前即持久 handled，失败或不发消息也不释放。

AstrBot 4.27.3 公开 hook 无全 pipeline 完成证明，普通候选 ownership_unknown，禁止自动签发 claim；原明确 @、关键词、命令与精确路由继续正常。不要为了声称双平台功能齐全伪造 free。

实际发送继续旧 messages/send/status 与 BotDelivery 原 reply/attempt 回执。参与 reply 必须检查同一原 host claim 当前有效，unknown 不自动换键重发；不得另建发送队列或 sender。

## 联合与交付

TS321 平台作者与本作者直接协作候选/归属/领取/发送核验操作名、原键和字段。根合同只有 TS319 owner 可改；发现合同缺口报告父级，不能私改 pin 或放宽 schema。普通参与无 AL12 决定时保持静默；本卡不实现 LLM 决策。

完整块稳定后集中审查、验证：真实 AdapterService 数据库的 CAS/回放/ACK/清理/跨角色竞争、异常/取消、代际/期限与重启；固定真实 NoneBot SDK 正向收据和负态、未知宿主图保持未知；AstrBot 普通 unknown 和原明确路径回归；canonical/release 字节一致和原构建脚本；与 TS321 实际 Platform→RPC→原发送回执联合。替身/源码核对/实际 SDK/真实 QQ 分别记，不用 schema 形状证明 CAS。依赖缺失只创建本卡私有环境，不装改别人的 venv。

不发真实 QQ、不调真实模型或外站账号、不用共享 PG/预算、不推送部署。精确提交及 handoff，保留实际失败、精准复验和未覆盖，未变成功不重复跑。父级审查通过后本地交付，停止扩展。
