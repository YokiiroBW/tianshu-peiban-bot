# 群聊四态发布收口（2026-10-27）

已正常推送、串行集成并部署，状态为 **deployed_runtime_verified_real_qq_deferred_by_user**。用户明确将实机配置和消息验收留待后续，本次不修改测试群/私聊策略、不发送 QQ 测试消息。维护窗口已结束，服务恢复运行。

## 已上线及运行验收

| 产品 | 精确提交 | 运行镜像 |
| --- | --- | --- |
| companion | `677fcb1f1392c134084d6e2be0d61f1a49f757b4` | `sha256:367ecafce3335852d01ad1a52d9d46ac9ccb50a0e7796a8f51e27d3168f75178` |
| memory | `110a41bf851163901f7fc4a8f13cc19817285a3a` | `sha256:e5d852ea6a8536807328ce8c9389ab3ec1c7d9f63b372a229dc163e23c6bd0f9` |
| onebot | `677fcb1f1392c134084d6e2be0d61f1a49f757b4` | `sha256:d212032ff6ff547a455d024c02db89a785ad6f784b0a5a6f8cfacf9b79b43005` |
| platform | `f4bbdb23795e0f9d89345aed98c09906fb24031f` | `sha256:657c89780410e4e4ac386a1ad5cee3d02696f56f11266ddd089006bf37c7e95e` |

原完整运行验收已通过：13 服务、4 Compose 项目、13 个持久 NAS 挂载、五核心 TLS live/ready、24 个原生逻辑源及独立 BOT 记忆域，共25源；3份保护标记与运行 guard ready。实际源码/导入副本核对通过，网页68文件随镜像校验，独立 LAN 首页及两个入口资源 hash 一致。OneBot 已连接，未以健康检查冒充真实 QQ 四态收发验收。

Companion 实际服务身份调用 processing 状态接口 HTTP200。指定群返回 observe / actor_unbound / processing_enabled=false；指定私聊返回 reply / processing_enabled=true。此为只读状态查询，未注入聊天事件；群需用户后续绑定真实角色才能整理。实际网页登录停在正常登录页，未代填密码或解锁 BOT 管理。

## 数据与配置保全

沿原停机边界，6 个 PG 数据库的25逻辑源、3份保护标记完成真实 dump/隔离恢复验证，39个 SQLite 核验；OneBot 单独1库冷备/禁用副本核验。生产未执行数据库恢复。冷备manifest SHA256 `75472ca26b42b842e8490cc7568987c9dc7f15b1b3db2844829ec583f75e4c42`。

Platform f4bb 显式事务修复已在隔离真实PG两项通过，原1F1E保留，临时库/角色均确认清理。实际初始化只移除1项旧tuple UNIQUE；历史 reply_connections、reply_authors、sources 指纹完全一致，PK及其他约束、原binding和3guard保持。未重初始化 Memory/BOT记忆。

基于轮换后的现场配置仅追加原 Companion principal 的 observation.verify、开启原合法 Memory reader 的 allow_runtime_groups；3原文件已备份。Gateway沿原同entry/routes/owner/TTL续租，配置其余字节/Env/mount/运行身份保留。Knowledge、观测服务保原容器和镜像；inputs/publisher未重启。共享PG/Redis/MySQL服务器未重启、服务配置未改；Chat/备份、媒体与SnowLuma未操作。根HEAD/index及原worktree改动保留。

## 耗时及保留的不利证据

主要耗时为原生冷备逐库dump及隔离恢复。原冷备末尾因工具自行生成一份pyc而报控制树差异；原失败保留，只接受已核定的缓存目录和该文件，复用完成的PG原始证明，再核39 SQLite/文件/标记。没有重跑整套PG恢复、删除失败或放宽任意控制差异。

维护中另确认旧guard生成器使用的shared前缀与现场停机脚本命名不符，已用支持的generic代次修正，旧latch保留。完整验收两处旧假设也已更正：现场原本4个Compose项目；旧持久群策略按产品原有方法投影为v2后才与API比较，其他归属/版本/epoch/私聊/read断言保留。最终full通过，旧失败均保留。精确私有收据hash见[结构化记录](group-modes-release-2026-10-27.json)。

## 尚未验收的边界

真实群聊四态切换、真实群/私聊入口拦截与QQ收发按用户要求延期；本批0策略更改、0QQ测试消息、0新增测试模型调用，总账17540/18000。已有本地入口/后台联合结果沿[交付记录](group-modes-delivery-2026-10-27.md)复用，保留其原失败与替身边界，不称全量测试全绿或语义满分。Chat历史/关系图/点云/热度仍依赖真实唯一Chat映射，未伪造绑定或群画像。
