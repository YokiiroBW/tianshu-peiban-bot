# 群聊四态发布与实机验收

用户已明确授权本批推送、部署，保留原配置与数据，不影响其他服务；另明确授权联系现有生产协调任务。真实验收只使用用户指定的一个私聊账号和一个测试群，精确标识保存在本批私有 runtime 收据，不随发布文档入库。当前真实模型账本 17540/18000，余460；本批准备新增0。不得把本地替身通过写成真实 QQ 收发通过。

## 当前执行状态

已完成正常推送、四镜像串行部署和完整运行验收。真实QQ配置及群聊四态/私聊入口业务验收由用户明确延后，0测试策略变更；下文真实消息步骤保留为以后执行的计划，不表示本次已执行。最终事实见[发布记录](group-modes-release-2026-10-27.md)。生产窗口已结束，模型17540/18000、本批0新增。

## 固定发布输入

| 产品 | 精确提交 | 远端 main 已核父提交 |
| --- | --- | --- |
| Platform | `f4bbdb23795e0f9d89345aed98c09906fb24031f` | `620b1e0b0a6c76ac69f538a13e00c9c6d53b112d` |
| Companion、SDK | `677fcb1f1392c134084d6e2be0d61f1a49f757b4` | `d416b68c2bf47f6a5675c8f170c5185062e66688` |
| Memory | `110a41bf851163901f7fc4a8f13cc19817285a3a` | `a2de620c676a24d7c08bb33ef3f1be2488b71666` |

均可正常快进，禁止 force；正式推送前 fresh 核对。沿原 worktree 与分支，保留根原 HEAD/index/混合改动。功能验收及限制见[本地交付](group-modes-delivery-2026-10-27.md)。

离线产物均由精确 Git 导出，根协调者独立核对四个 wheel 的598个运行文件均等于提交原字节，另核68个 Web 文件。完整路径、SHA、原不利构建记录见 `.runtime/group-modes-release-20261027/artifact-verification.json` 及所引子收据。Companion 和 NoneBot 需要同时更新，运行镜像中 `/app/src`、`/app/integrations` 或 `/app/tianshu_nonebot` 等实际 shadow 副本按最新实读 import 来源更新并验证；不能只安装 wheel 后假定生效。AstrBot发行一并保留，实际生产仍按当前 NoneBot 宿主更新，不切宿主。

Memory本次仅 browser.py，未改 Store、DDL、合同或依赖，无需 Memory 数据迁移；Knowledge 不使用该路径，无需随其换镜像。Gateway代码不变，participation 功能沿原默认模型继承，不扩模型配置或凭据。

## 配置与数据最小变更

- 保留账号、角色、存储、所有原群/私聊策略和绑定。旧策略确定迁移，绝不自动启用主动。只为指定验收群和私聊临时保存测试意图；验收结束沿正常 API、新 client_id 和当前 expected_revision 恢复原意图，不回写旧 revision/generation。
- 给 Companion 实际 platform_sender 所认证的原 Companion service principal 增加 `observation.verify`；不能使用反向 Core 入站 token 或 actor-memory 专用 principal。已有后台 selector/Gateway 路由保留。
- 原 Memory Platform browser reader 有合法 account/operator person 模板时，才增加布尔 `allow_runtime_groups: true`。保原 actor/scopes 和运行角色授权；没有模板如实记录，不伪造人物/CID。Chat 群统计/历史只在已有真实唯一映射时可用，不猜 binding。
- Platform 启动构造 BotObservation 会自动移除 reply_connections 旧 tuple UNIQUE，保 PK connection_id 和原行。PG 分支没有自身备份；必须先在隔离 PG 验证，再沿原 guard 停机和25逻辑源/3guard冷备流程保全必要数据，静止窗口核迁移前后行与非目标约束。不得删旧代来恢复 UNIQUE；产生新写入后不自动用冷备覆盖新数据。

## 串行执行与验收顺序

1. 获得现生产协调者明确释放记录；重新核当前部署版本、配置/Env、运行身份、挂载、guard及其他服务基线。保存原配置字节/元数据和镜像回退材料，敏感值不出工具输出或入库。
2. 精确提交正常推送、协调检出串行快进；采用已绑定离线产物，基于届时真实生产镜像构建新镜像。只更新本批三产品及实际 SDK；其他服务按原 guard 必要停启范围控制，不覆盖无关资源。
3. 运行隔离 PG 迁移用例；完成必要冷备与隔离恢复校验，再在原维护边界切换。首次新版 Platform 启动前后核目标约束、PK、旧行和相关源映射；失败保留证据、不自动 SQL 回退。
4. 沿原 full runtime profile 检查实际镜像/源码/Web chunks、原挂载与配置、五服务 live/ready、25逻辑源/3guard、QQ9091就绪和其他服务未受影响。原验收脚本的旧镜像/旧stage/旧token pin必须更新为本批实际输入，不直接运行旧发布器。
5. 使用用户指定真实群和私聊验收。disabled 对普通/@及私聊均先拦截，按同一 native event 核后续无入队/归档/Core/source/候选/模型/发送；observe仅实际归档/材料/整理；reply普通只观察，真实@与配置字面关键词走原回复链；active才进入真实后台参与判断，允许 silent，engage 时核原 claim/register/实际 QQ receipt。指定群关闭不影响其他群；重新启用不补采停用期间或复活旧回复。
6. 在原已受理材料上核后台暂停/恢复，保 output/receipt、不重复模型；确认历史仍可读。恢复原测试策略和绑定，记录真实 API 回执及新的当前代，不抹测试证据或删除其他人的聊天/记忆。

每个业务验收块核真实 Gateway usage/request IDs，首次实机验收内部计划最多32次真实测试模型请求；达到该批次上限即停止新增并报告，绝不越过总18000。重试、后台整理和参与判断都计入实际测试增量，不只数用户消息。不得为逼出主动回复反复调用或强制模型 engage。

验收由真实输入触发；需要指定账号/群成员发消息时，在服务就绪后给出具体步骤。没有可控真实入站时只记录真实服务受控链路已验，保留真实 QQ 入站未验，不伪造人类发言、人物或原生事件。

原本地测试、未覆盖、构建失败与部署期间新增不利证据均保留。最终区分已推送、已部署、运行验收、真实四态业务验收与未覆盖；模型账本和原配置恢复结果统一记录后收口。
