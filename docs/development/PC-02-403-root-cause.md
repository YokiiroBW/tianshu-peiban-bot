# PC-02：403 取证记录（尚未修复）

状态：`in_progress`；household 用途已向用户询问，授权与配置尚未改动。不是全局 403 问题的最终结论。

## 已确认的分支

同一 Companion 服务凭据对两个正式角色可用，对 `actor:household` 的发现请求返回 403。生产 Platform 的角色运行账本仅有两个 active/enabled 的 `actor:role-*` 角色，**没有 household 登记**。settings 中出现某个 actor 字符串，不等于它在正式运行账本中登记并启用。

`ActorCapabilities.guard` 先验证服务身份，再由 `role` 要求已登记且 active/enabled 的角色；`ActorMemoryAuthority._role` 同样读取角色运行账本。现有 `is_placeholder_actor` 将没有角色记录的 household 定义为安装占位身份。没有证据证明上述拒绝错误，也没有证据把该分支归因为 10-27 凭据轮换。

因此不能通过白名单或跳过 `role()` 消除它。需由用户确定：保留历史并停用占位角色的自主生活，或通过已有正式角色流程为它指定人格、模型并启用。前者意味着原“household 必须成功一次”的验收项应由用户选择明确替换；后者涉及正式角色授权，不能静默决定。

## 五条真实请求

采集于 2026-10-30 03:13:39（Asia/Shanghai）。使用配置中既有服务凭据，凭据只在内存读取；同一 SHA-256 指纹贯穿五条请求。真实本机 TLS 入口保持原 CA、SNI 和 HTTP Host 校验。请求只做 discover 和 actor_check，不组装 Core、不调用模型或投递消息。

| correlation_id | actor | route / operation | HTTP |
|---|---|---|---|
| `1bc6e7f92e2eff66cada67ee68abfed0` | household | `/internal/v1/actor-capabilities/operate` / discover | 403 forbidden |
| `d6832f83696698dce78315ce61e4c30f` | role-7b6c | 同上 / discover | 200，role_version=6 |
| `c3ef87aac0ebfcb1f7674314b449de1c` | role-7b6c | `/internal/v1/actor-self-memory/authority` / actor_check | 200，包含 query/interests 权限 |
| `7e0210001a4147f2a59f5fcfa75977ce` | role-84e3 | discover | 200，role_version=1 |
| `524f3b43b536001f2a6729433fe3a53b` | role-84e3 | actor_check | 200，包含 query/interests 权限 |

原始安全事件与完整 actor 标识只放主工作区 `.runtime/proactive-companion-repair-20261030/PC-02-authority-traces.json`。账本只读证据为 `PC-02-role-registry.json`；线上源码与协调检出的逐文件校验为 `PC-02-source-proof.json`。两个授权模块完全一致，`role_runtime.py` 整体有差异，不能把本地整文件视为线上；占位语义另由线上该函数的直接只读提取核对，见 `PC-02-placeholder-proof.json`。

## 基线与不利项

PG 基线：历史决策 15/69；最近 24h 为 15/45。6 条 paused，5 条无恢复时间；主动八表全空。03:16 的五分钟日志窗中 Platform 拒绝率约 19.6/分钟，主服务 peer 属性覆盖率仍为 0%。与最初 193/分钟的窗口不同，**此间未执行修复，不能把差异称为修复效果**。

首次独立进程直连配置中的 SDK URL 得到三条 ConnectError，已保留为 `PC-02-isolated-direct-transport-failed.json`。统一宿主内部 SDK 使用进程内传输，旧 URL 不是可用的独立进程监听器；该失败不能证明生产链路网络故障。改用真实本机 TLS 入口后取得上表五条有效请求。

## 未验证 / 后续

- 尚未把所有 `http.auth.rejected` 归因到具体路由/主体；household 分支不能解释全部风暴。
- 主服务新日志尚未部署；五条探针来自候选观察代码的独立进程，不等于主服务新版本上线。
- 未改凭据、登记、权限或生产数据；没有 household 成功决策、30 分钟零拒绝或 24h ≥80% 的验收结论。
- 用户决定 household 的用途后走已有配置/角色管理能力；W0 生产部署后按真实归因处理剩余拒绝。PC-03/04 可继续本地修复，W3 仍保持未放行。
