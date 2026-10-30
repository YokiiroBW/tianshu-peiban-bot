# PC-02：403 根因与发布后观察

状态：`in_progress`。用户已明确要求删除安装占位角色，R38 已实施，R43 正常运行；历史和授权检查保持。24 小时成功率及全部拒绝归因尚未验收。下文五条探针保留原采集时点，不代表当前配置。

## 已确认的分支

同一 Companion 服务凭据对两个正式角色可用，对 `actor:household` 的发现请求返回 403。生产 Platform 的角色运行账本仅有两个 active/enabled 的 `actor:role-*` 角色，**没有 household 登记**。settings 中出现某个 actor 字符串，不等于它在正式运行账本中登记并启用。

`ActorCapabilities.guard` 先验证服务身份，再由 `role` 要求已登记且 active/enabled 的角色；`ActorMemoryAuthority._role` 同样读取角色运行账本。现有 `is_placeholder_actor` 将没有角色记录的 household 定义为安装占位身份。没有证据证明上述拒绝错误，也没有证据把该分支归因为 10-27 凭据轮换。

因此没有通过白名单或跳过 `role()` 消除它。用户随后明确选择“删除这个安装占位角色吧”，R38 通过原配置/运行管理移除占位生活身份，保留历史；该决定替换原 household 成功决策/cursor 验收项，两个正式角色的成功率、日记、主动闭环等要求保持。没有正式启用第三角色或扩大权限。

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

上述五条请求已按 correlation_id 与生产 Platform 原生日志逐条关联，得到 15 条 started/finished/auth 事件。household 的同一角色发现拒绝被记录为 `http.auth.rejected`；另外四条请求记录为 `http.auth.succeeded`。线上 `_terminal_events` 也明确将拒绝或无鉴权处置标记的 401/403 计入拒绝。因此这个计数不能单独证明 token 失效，不能仅凭计数去轮换或放宽服务身份。此核对没有新增 HTTP 请求，证据为主工作区 `PC-02-auth-classification.json`。

PG 基线：历史决策 15/69；最近 24h 为 15/45。6 条 paused，5 条无恢复时间；主动八表全空。03:16 的五分钟日志窗中 Platform 拒绝率约 19.6/分钟，主服务 peer 属性覆盖率仍为 0%。与最初 193/分钟的窗口不同，**此间未执行修复，不能把差异称为修复效果**。

首次独立进程直连配置中的 SDK URL 得到三条 ConnectError，已保留为 `PC-02-isolated-direct-transport-failed.json`。统一宿主内部 SDK 使用进程内传输，旧 URL 不是可用的独立进程监听器；该失败不能证明生产链路网络故障。改用真实本机 TLS 入口后取得上表五条有效请求。

## 发布后观察与未验证项

原观测代码已随 R38 部署，当前 R43 继承；真实出站故障已有 peer/route/principal/attempt 等安全属性。R38 与后续早期窗口曾取得零拒绝，不能据此宣称所有后续时段为零。

截至 2026-10-30 14:09，R43 排空前完整 30 分钟有 1 次 unauthorized（0.0333/分钟），日志字节预算完整、坏行 0。关联号 `5577d254f4354300821c53b2b623c938` 的 Platform started/finished/auth 三事件一致；本次有界 Companion/Gateway 记录没有同关联调用，旧 Platform 日志未记录该请求路由与主体，归因仍未知，不猜为浏览器会话或服务令牌。保留这次不利观察。

修复后新决策 cohort 固定为 R38 启动 epoch 1793326100.1223843 至 1793412500.1223843，跨发布不重置。已观察 13 条：completed 8、failed 2、unknown 2、interrupted 1，成功率 61.538%，未满 24 小时。两条 failed 的原模型回执 succeeded，但 application/result_unknown 仍失败；一个 unknown 由原 30 秒模型超时确认，重启时另有 interrupted/unknown。保留原状态，不重放、不从分母移除，也不将历史全部决策混入新 cohort。

R43 已有真实主动送达、v2 来源证明和原命令幂等，日记当天两角色各完整取到 2 条素材，仍分别在模型流 generation 阶段 unavailable/unknown，没有 draft。这说明不能再把全部故障归为 403，也不能把取消或上游超时当授权错误处理。详见 [R43 业务验收](proactive-companion-repair-r43-acceptance-2026-10-30.md)。

未验证：当前版本完整 30 分钟零拒绝；原 24 小时 cohort ≥80%；其他历史拒绝的逐请求主体/路由；10-27 凭据轮换因果。既有单写者、配置数据、容量与鉴权保护保持，W1 未完成，W3 禁止。

14:32 更新：R43 当前真实完整30分钟窗口（14:02:26–14:32:26）拒绝0，started/finished各6012、auth succeeded5972，完整字节预算及坏行0；此前有1次unauthorized的窗口不变。固定新决策cohort现为9/15（60%）：failed2、unknown3、interrupted1仍纳入，24小时与80%继续未验。当前30分钟零拒绝一项已有实证，不等于整卡完成。
