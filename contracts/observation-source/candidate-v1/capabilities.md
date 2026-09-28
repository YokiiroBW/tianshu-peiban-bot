# 能力与身份矩阵

| 调用方 | 接收方 | 能力或端口 | 证明与权限 |
| --- | --- | --- | --- |
| Platform | 宿主 | `observation/capabilities`, `apply`, `status`, `poll`, `ack` | 既有插件独立 Bearer；能力明确返回 `tianshu.bot-observation/v2`，在线账号来自 SDK |
| 宿主 | Platform | 无推送 | 宿主只经被认证的轮询暴露持久队列 |
| Platform | Companion | `/internal/v2/observations/ingest` | 既有 Platform 服务凭据；账号和实例须与 Platform 管理账本一致 |
| Companion | Memory | `/internal/v2/memory/observations` | 独立 `observe_ingest` caller 权限；不能借 chat `consume` |
| Memory | Platform | `/internal/v2/observation-source/verify` | 固定服务凭据/TLS；由 Platform 当前来源账本给出有效性 |
| Platform | Companion | `/internal/v2/observations/query` | 既有 Platform 服务凭据；只返回本账号本会话的管理投影 |
| Companion | Memory | `/internal/v2/memory/observations/query` | 独立 `observe_query` caller 权限；每次查询当前范围复核 |

协议失败默认不回复；观察留在持久队列或 Companion inbox。仅观察模式不阻断其他宿主插件；许可回复的事件须持久入队并有当前策略和短期 Platform 轮询租约才被接管。服务不得把 `unsupported` 事件变成空文本模型请求。旧 v1 插件及精确范围连接维持原行为；新范围须管理员显式启用。
