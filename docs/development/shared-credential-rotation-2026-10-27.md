# 共享 PostgreSQL / Redis 轮换与实际验收

2026-10-27 用户授权后已实际完成 PostgreSQL 共享角色、Redis 应用用户和 Redis default 三个身份的独立换密。恢复后再次以全新连接证明三个新值接受、三个旧值明确认证拒绝。共享服务器没有重启，没有删除卷、恢复覆盖生产数据或执行业务 schema 迁移。本聊天协调者单写生产；子代理准备精确候选、只读诊断与集中审查。

AL08、AL11 与记忆扩展的原发布版本保留。当前原完整只读运行验收已通过，尾号 9091 的 QQ 账号已由最终 metadata 确认就绪；此前“QQ 待登录”已被新实证覆盖。本轮验收没有发送 QQ 消息或调用模型。逐项结果和私有证据 hash 见[结构化收据](shared-credential-rotation-2026-10-27.json)。

## 实际范围

- PostgreSQL：核对并更新 13 个容器的配置引用，包含五 Core、Chat app/backup-worker、MoviePilot、telegram-files、Forgejo、Joplin、mt-photos、Synctv。引用存在不等于每个应用均已实际使用该外部数据库；数据库级连接观察单独记录。
- Redis 应用身份：五 Core 和 control-api/control-events；default：requirepass 及 mt-photos 引用。MoviePilot、Synctv 的独立 Redis 身份保持。
- 21 个文件候选由协调者合并：5 Core settings、15 NAS 配置/seed/image pin、1 活跃本地维护 binding。五 Core 双 URL、mt-photos 双字段一次合并；原 actor 配置、已发布 token、权限、合同及数据路径保留。历史私有备份不全局替换。
- 6 个浮动镜像标签已与原运行镜像不一致，因此只把对应 Compose image 固定到原实际 Image ID。没有借重建引入新版镜像。

## 验收结果

| 范围 | 实际结果 |
| --- | --- |
| PostgreSQL 权威 | fresh TCP 新值接受、旧值 password-authentication-failed；角色 OID、flags、cluster identity 保持，SCRAM-SHA-256；14 个数据库所有权计数保持；未来重建用 seed 已更新 |
| Redis 两身份 | fresh AUTH 新值接受、旧值 WRONGPASS；原 ACL 权限/选择器保持；redis.conf 同 inode 写入并由原运行容器读回；运行 requirepass/ACL 与持久声明一致 |
| Core | 13 实例、5 TLS ready、25 逻辑源、3 guard、LAN Web200、实际运行源码和镜像核验通过；新 guard ready |
| 记忆隔离 | actor schema2、独立 installation/schema 和用户域不重叠，实际 guard 与当前 PG 匹配；停机期间四份用户/actor binding/guard 未变 |
| QQ | 原完整只读 profile 通过；原账号尾号 9091 已连接，0 测试发送/0 模型调用 |
| MoviePilot、Chat app/worker、control-api、Synctv | 原 healthcheck healthy；Synctv 新容器实际挂载的 URL 文件逐字节匹配本轮候选 |
| telegram-files、Forgejo、Joplin、mt-photos | 实际只读 HTTP200；Joplin 使用实际容器地址及原 APP_BASE_URL Host 的 `/api/ping`，没有把错误 loopback404 当成功 |
| control-events | 原进程运行；没有独立健康接口，只记进程状态及相关 Redis 权威验证，不冒称端到端事件业务测试 |

PG 在切换前已清空真实客户端连接；内部 logical replication launcher 保持运行。恢复后的连接计数按数据库观察，同库的 Core、Chat app/idle worker 不能据此逐进程归属。Redis NAT 同样不能逐进程归属；只报告身份级连接。mt-photos 外部 PG 常驻连接观察为0；随后用当前容器已有 pg 驱动和实际环境建立只读连接，SELECT 1 及当前数据库/角色匹配均通过，原 `/api-info` 返回200且报告PG/Redis启用，共享Redis实际有6条连接。容器内PG实际未运行。没有触发照片业务，也未观察应用常驻PG连接，不推测为已证明懒连接池。

本次复用此前已完成的 Core 25 源/3 guard、Chat 22 表原生恢复证明；只新增本轮配置/权威元数据备份，没有把历史证明写成再次完成全量恢复。PostgreSQL ALTER ROLE 使用客户端生成的 SCRAM verifier，不把明文口令放入 SQL、argv、公开输出或 Git。

## 保留的失败与限制

1. 初次 PG drain 发现遗漏的 Synctv 和服务器内部 launcher，切换尚未执行时即停止；补入 Synctv，内部进程未终止。未知 backend type 仍会阻止切换，没有盲 kill 连接。
2. Chat backup-worker、control-events 的停机各等原 90 秒宽限后 exit137；备份队列在停机前为 57 succeeded、0 在途/claimed。恢复后两者状态按真实检查记录，没有把超时退出写成正常退出。
3. 初次重建的严格检查拒绝了 Docker 的同网络名称/ID、空 DNS、Bind 顺序、Compose 版本标签差异；保留原失败和原工具，按精确等价证明续接，已恢复实例没有重复重建。
4. Chat 主连接字段从原 secondary network ID 变成声明 default network，单独对固定新实例验两张完整网络的身份/IP/网关等及当前三条内核路由。旧内核路由未采，不能声称旧新路由逐字节相同。其余配置、权限、资源、挂载和固定镜像仍按原检查。
5. 首次 Platform readiness 超时；之后不改产品代码或预算，原 backend 和 full profile 均通过。已定位到 2 秒 evaluate/inflight 预算，具体慢子检查未计时，未把 PG 冷缓存假设冒称根因。
6. NAS 新私有目录最初呈 mode000，已对本轮目录明确设置0700、备份0600后继续；此失败时尚未切换认证。原始操作收据均保留。

## 事件尚未全部关闭

此次关闭的是三个共享身份。Chat APP_SECRET_KEY、独立入站/管理 token，以及 MySQL、其他应用/网页/VNC 等复用旧值的独立身份仍按未解决暴露记录。Chat 历史备份 HMAC 密钥未盲改，以免使原备份无法验签。不能将本轮成功写成整个凭据事件已解决，或宣称已撤回历史工具输出。

真实群聊表达、模型质量、外部搜索/账号动作和 AL12 没有借本轮上线验收升级为已完成。普通候选继续按既有静默边界，原 @/关键词逻辑保留。用户记忆与角色自身记忆的产品代码没有在本轮凭据操作中并写。
