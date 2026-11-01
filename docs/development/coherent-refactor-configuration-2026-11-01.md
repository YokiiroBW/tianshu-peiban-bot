# 统一重构配置迁移与发布记录

更新：2026-11-01。生产写者：root。实现者：原三个 gpt-6.1-sol/xhigh 子代理。

## 当前实际状态

正式入口为 `http://192.168.31.230:18446`。最新源码 `dcdf729b5d22f451093e3658d131fd6757a00dde`，整镜像 `sha256:679e08db330d32973e40d32f07007ee5b0b3442a1f6e8fc86af550ed39aaa40c`，无源码覆盖挂载；前端已随同一镜像发布。完整重构初次发布基线为 `08ef6cb303f7b054cbbdb34e3d16ff9c584fe024` / `sha256:559ef862da33ee81456b017c14d51ef66bf404e47cf9a3a3ec168428dbeb48bc`，后述原验收证据保留。

应用位于 `.230:/srv/tianshu-core/app`，专属 PG 17/pgvector 0.8.7 位于 NAS `/volume2/Dockers/tianshu-core-postgres`、端口 25018。最终清理后应用 ready、PG healthy、容量守护 active，20 GiB 空间下限保持。11 个挂载包含原媒体目录 7 项和原专用 SSH 材料 2 项。

两个 QQ 已正常热切换，最终镜像之后账号身份查询再次通过。SnowLuma 自 2026-10-27 的原进程未重启。旧 R67、专属业务库和本轮验收环境已退役。用户授权后，NAS 原专用公钥已恢复并限定来源，新版实际采集通过。30 块实现、前端、集中验收、配置迁移、正式部署和旧业务退役均已完成；下文单独记录真实外部验证的覆盖范围。

## 用户配置保留结果

用户最新要求覆盖此前“全清零”的配置处理：业务历史仍重置，但作者配置必须迁移。
首次经新版公开领域 API 完成 108 项读取及配置操作，并以独立读取核对最终结果。
一次性操作材料在协调者私有 `.runtime/coherent-refactor-20261031/` 中，不进入产品包。

| 配置 | 实际迁移与核对 |
| --- | --- |
| 作者人格 | 7 份；逐份比较完整正文 SHA-256 及独立字段。已发布人格与未发布草稿分开保留，不把澄汐较长草稿替换为正在生效的人格 |
| 角色 | 澄汐、智乃保持启用；小爱保留为未启用角色，不虚构 QQ 连接；household 人格保留为未绑定档案 |
| 模型供应商 | 保留全部 5 个原来源和 7 个原模型条目。原密钥通过新版 CredentialStore 重新加密，未复制旧密钥库或旧加密主密钥 |
| 原生模型路由 | 8 provider / 8 route：同一来源必要的 embedding/rerank 协议入口和本轮实测 Gemini 原生多模态入口分开登记，配额仍按实际共享资源归组 |
| 功能选择 | 33 条系统绑定及智乃实际聊天覆盖 3 条，共 36 条；澄汐、小爱继续继承系统配置，没有复制冗余角色覆盖 |
| 检索模型 | Qwen3-Embedding-8B 的实际 1024 维/l2 配置和重排路由保留 |
| QQ 机器人 | 一个 QQ 对应一个角色。两个私聊默认仅观察，原指定私聊允许回复；智乃原四群仅观察，其他群忽略；澄汐原有效群策略继续忽略 |
| 接入凭据 | 两个 OneBot token 均已和 SnowLuma 当前天枢端点逐项比较一致；其他 Chat Audit/AstrBot 端点未修改 |
| 自拍 | 澄汐原冻结 ComfyUI graph、bindings、outputs、主模型、实际角色外观字面量及 1024×1536 参数逐项保留；不重新生成或推测外观 |
| 本地资源并发 | `comfyui-local` 独立资源组：并发 1、后台上限 1；不借用付费聊天模型配额 |
| 主动联系 | 智乃原目标映射至新版已核验私聊，保持 22:00–08:00 静默、冷却 3600 秒、每日 1 次、未回复上限 1 次、内容有效期 3600 秒 |

未导入旧聊天、记忆、好感账本、成长结果、日程执行历史、旧任务、旧发送回执或旧权限票据。
真实人物映射不会自动赋予 QQ 用户 Web 管理权限。没有旧数据库双读、旧算法回退或长期兼容入口。

## 补充作者配置与实际接入

清理前按精确表补查，另迁入 9 类 19 条媒体作者配置：B站账号 1、音乐账号 3、书源订阅 4、书源 6、书库设置 1、音乐设置 1、漫画设置 1、Emby 连接 1、Navidrome 连接 1。一次性管理员脚本调用现有原生领域方法，未直接灌表；19 条凭据重新加密后全部读回一致，6 个外部账号/连接只读核验均成功。feed 关联、启停和间隔保留，远端 Emby/Navidrome/Komga 路径不误当本地路径改写。媒体业务 job 在导入前后均为 0，后台 worker 已恢复。

QWeather 原 host/key 经旧 seal、5 行 MAC 与 AES-GCM 验证后迁入，使用独立 qweather-primary 资源组；澄汐位置原生写入 Life。household 尚无绑定角色，其原位置保存在私有作者存档，没有猜绑其他角色。Memory 的 vector 与 rerank 均启用，原模型路由不重复创建。

补查确认：没有持久保存的朗读配置；角色技能覆盖和插件秘密均为 0；漫画插件只是无额外作者配置的内置实例。household-assets 为未设置 endpoint/token 的占位；漫画自身保存的 AssetLibrary 连接另行完整迁入。

两个 QQ 只修改天枢 WS URL，分别为 `/channels/onebot/v11/qq-1449801200/ws` 与 `/channels/onebot/v11/qq-1365939091/ws`。初次切换的身份、版本、在线、图片和语音能力只读查询通过；最终镜像后两个身份查询又通过，实际页面天枢连接正常，智乃 Chat Audit 旁路正常。其余 QQ、token 和登录目录不变。澄汐原 AstrBot ops 端点在切换前已经重连，本批未改。

最后补齐的 NAS node/relay/remote helper 进入独立部署包；4 项定向用例通过，包括 launcher/schema、unknown 原 ID 查回、SSH/YAML 边界。未重复全套检查。一次最终只读查询漏选 actor，被权限正确拒绝且 effect=none；修正操作工具作用域后用新的明确请求完成，未放宽权限。

## 本次最终修复与验证

- 插件管理页此前只看前台进程，导致真实 worker 插件已运行仍显示失败。现在读取绑定原实例修订、实际子进程代次及原 worker 租约的持久观察；退出后失效。5 项定向用例通过，实际 Linux 上完成 ready → stopped → 新代次 ready；未另起插件宿主。
- 原主动联系配置的有效期已成为 Life 原生字段，前端对应可编辑。0033 是独立增量迁移，默认 4 小时，人物覆盖可为 1 小时。候选创建和上限使用同一 PostgreSQL 时钟；缩短策略可提前过期，延长策略或重试不延长旧候选。实际 PG 故事 1 项通过，显式自拍约定的 2 项已通过结果复用。
- 其余集中验收证据复用：真实 PG 的后端行为、前端桌面/手机页面、日志中断后的持久缓冲补传、独立库备份恢复、原动作身份恢复，以及 Gemini 文本/图片/音频/视频/tools、DeepSeek、Qwen embedding/rerank 的既有实际探测。
- 配置导入中一次 routine CAS 冲突确认未提交后，暂挂新版 worker，读取当前修订后用新的明确配置命令完成；随后恢复 worker。没有把未知结果当作失败重发，没有覆盖竞争写入。
- 最终整镜像及新生产配置读回均通过；没有向真实 QQ/TG 用户发送测试消息，也没有把配置保存、目录可见或容器健康当作所有外部平台的实际业务验证。

原始适用检查、首次失败与受影响复验日志保留在协调者私有验收目录。最后一轮无需重复整套测试。

## 已完成的退役和清理

- 删除专属旧 PG schema 25 个；删除前重新计算依赖闭包，外部依赖为 0，共享库其他 schema 保持。
- 删除旧 Redis 精确前缀 6 组中的 5 个实际 key，没有 FLUSH 数据库。
- `.230` 旧应用及冷停组件 14 个容器退役。另 2 个匿名容器经命令及挂载核实为旧 R56/R27a 构建步骤后清理，连同 4 个独占匿名卷。
- 删除 12 项旧数据路径；随后移除 `/srv/tianshu`、`/srv/tianshu-tooling` 和旧两套 systemd 容量守护。3 个旧冷备份 NFS 挂载正常卸载，未递归进入 NAS 离线归档。
- NAS 14 个已停旧注册表容器清理；其原数据目录已不存在。两个专属验收 PG 容器、NAS 与 `.230` 验收目录、临时明文导入文件、过时控制 helper/venv 及确认不用的旧镜像清理完成。没有全局 prune；有依赖的 Docker 中间层按依赖保留。
- 新 NAS helper 位于 `/volume2/Dockers/tianshu-core-infrastructure`。共享 Python、媒体、QQ HOME、独立 control-hub、AssetLibrary、Chat Audit、AstrBot 及先前划定的保护目录保留。

最终实测 `.230` 可用 72,624,775,168 字节（约 67.6 GiB），该主机只剩正式 `tianshu-core` 容器；NAS 可用 348,651,053,056 字节。这些是实测时点，不是固定容量保证。

Windows 三个生成构建目录的递归清理被自动审批返回 `blocked by policy`，没有换方式绕过；仍保留且不属于镜像输入。人工清理脚本位于原工作区 `.runtime/coherent-refactor-20261031/cleanup-local-build.ps1`。

## NAS 授权恢复与最终只读核验

原 NAS `agent` 授权列表缺失天枢专用公钥，首次实机返回 `ssh_auth_failed`，采集曾暂挂。用户随后明确回复“允许恢复这把专用公钥，并限定来源主机”。已重新核对当前应用私钥对应的公钥指纹 `SHA256:2CXMNkyNKiT+F7HnLGIaCBqhHtpXBI996oHAEEgvm8k`，仅追加该原公钥，并设置 `restrict,from="192.168.31.230"`。其他授权条目、文件 inode、所有者及权限保持；没有生成替代密钥或解除来源限制。

通过原生配置 API 将 `nas-main` 启用至 revision 3。2026-11-01 12:31（Asia/Shanghai）实际读取：主机 snapshot 为 `ok/current`，Docker inventory 为 `ok/current`，完整返回 112 个容器；专属 PG healthy，SnowLuma 原容器保持运行。Compose 返回 72 个栈（21 完整、40 部分、11 不可用），聚合状态为 `partial/current`，实际原因包括部分外部项目文件不可用、权限不足、未解析的绑定/镜像与不支持的字段。指标同为 `partial/current`，明确保留首次采样、43 个未运行容器及底层缺失的 I/O/网络计数，不把未知值填零，也不称全部外部项目已完整核验。

基础设施实现子代理仅复核既有回执和源码，确认没有本次迁移造成的实质缺项。Compose 扫描仍限原 `/volume1/Download/dockge/stacks`；新版 PG 的 Compose 项目位于该根外，因此未列入 72 个栈，但其容器、Compose labels 和健康状态已在 Docker inventory 正确可见。没有为了消除部分状态去扩大扫描范围或修改其他项目权限。

本次只恢复既有访问并读取状态，NAS 容器控制动作提交数为 0。原公钥恢复收据 `nas-ssh-restoration.json` 和原生读取摘要 `nas-final-readonly.json` 已保存；此前缺少授权的记录保留为历史，不再是未完成项。应用代码和镜像未变，复用已通过的集中验收，不重复构建或全套测试。

原始回执、截图、私有配置存档和错误记录位于协调者原工作区 `.runtime/coherent-refactor-20261031/acceptance/`。真实发送、付费生成、所有外部平台及长期陪伴质量没有被这些配置/健康核验覆盖。

## 发布后修复：LAN HTTP 登录

用户实际网页登录报告 `crypto.randomUUID is not a function`。登录请求在前端生成请求编号时抛错，未到达账号校验；此前 API 登录成功没有覆盖这一浏览器差异。该接口只在安全上下文提供，局域网普通 HTTP 不具备它；既有 `web/src/app/requestId.ts` 使用可在该环境运行的 `crypto.getRandomValues()`，本次将登录、通用命令、intent、ProviderManager 和 Matters 的 5 处遗漏统一接入，不新增编号机制。

前端子代理交付 `214387a8b3c4f7c66e69e7360cf46e07be6441bb`。build（含 typecheck）通过，`session-transport.spec.ts` 定向 5 项通过，其中新增真实非 loopback HTTP origin 的登录和 unknown 后原 intent ID 保留验证。协调者核对 diff 后快进集成，无装配差异，不重复已通过的测试。

按精确源码构建整镜像并串行部署，正式容器为 `d62af5ed598909f5e48188c150dd215f035413680687d8a7f41a099fbd92de83`。应用 ready、守护 active，11 个挂载和全部 config 文件摘要保持；未改后端、数据库迁移或 QQ 配置。正式 Chrome HTTP 标签页刷新后，现有账号提交登录成功并到达工作台；截图与发布收据位于原工作区 `.runtime/coherent-refactor-20261031/http-login-fix-20261101/`。只核验本次受影响行为，没有发送聊天或调用模型。

## 发布后修复：持续生活与真实经历

用户实际页面暴露了原集中验收没有覆盖的持续运行缺陷。作息保存旧 principal access_epoch，账号或其他角色修改后 life.clock 报 access_changed，4 次重试耗尽；原恢复复用了已失败队列 key。当前只对持久时钟重新核验原作者、固定场景和当前权限，在原授权上界内刷新；实际撤销则暂停。恢复控制任务可新建 key，原 revision/due CAS 保证只推进一次；不重置模型或外部 Action。纯时钟改入既有 DUE 队列，模型规划保持 ELASTIC。

真实旧规划请求不含 life_plan 和活动 JSON 合同：整个能力目录和唯一格式要求放在可选材料中，被预算裁掉。现在任务约定进入固定系统指令，必要材料有界保留；从现有 Catalog 动态挑选可执行、已曝光的业务能力，管理判定共享一个实现，未保留固定八项白名单。超预算省略有明确覆盖记录，原请求冻结、unknown 原身份查回和本地参数校验保持。

images.profiles.save 与 channels.api 的 get_login_info 曾被记录为“原任务已执行”。生产者现在按实际能力元数据及真实子 API 区分管理和生活结果；两条已核实的澄汐误分类经历仅归档，原 Action/Receipt/消费记录保持。原真人输入的覆盖只表示已收到的 parts，不冒称理解所有媒体。JSON/XML 原件保留文本及原始字节，不可解码材料保持原媒体。天气覆盖仅说明实际取得的响应。

修复原件后，真实天气抽取又暴露模型自造 location:101120301 主体：原权限拒绝正确，未写入人物事实。新的抽取合同直接使用 ClaimService.visible_subjects，并复用既有谓词正则；禁止为满足枚举把天气改绑给用户/角色，无相应人物事实可返回空数组。旧失败不改为成功、不重发旧模型。14:29 和 14:34 两次自然天气观测均在模型调用中出现 TypeError，被记为 transport_unknown；原响应未留存。随后用协议允许的 tool_calls:null 在本地直接复现同类错误，修复只覆盖 openai.chat 明确可空字段，损坏响应与缺少完成标志仍拒绝，原工具 ID/参数片段及未知 usage 保持。不能断言这是两次实机错误的唯一原因。14:44 取得的真实天气原件已完成整理，claims/matters 为空；此前 unknown 请求保持原状态、未重发。14:49/14:52 的更新查询超时，没有取得新原件，页面保留旧读数；不能据此称当前天气刷新成功。

验证复用：经历语义/结构化材料 PG 7 个有效案例；时钟及原作息 PG 9 个有效案例；规划原 16 个案例及返修受影响 5 个案例（重叠结果不累加）；到期队列纯检查 3 项；主体/谓词合同 13 项；生活页面 3 项；Chat nullable 21 个新例及原相邻协议 6 例，共 27 个不同案例，最终 typecheck 与实际镜像构建通过。首次 Windows Proactor/临时 DSN fixture 设置失败、一个列表断言差异及协议新树 .runtime 父目录缺失导致的 3 个 setup error 保留，纠正后仅运行受影响案例；没有使用生产库做测试。临时 PG 25019、目录及测试凭据已退役。

可空字段的规范依据是 OpenAI 官方生成类型：[ChatCompletionMessage](https://github.com/openai/openai-python/blob/main/src/openai/types/chat/chat_completion_message.py) 与 [ChatCompletionChunk](https://github.com/openai/openai-python/blob/main/src/openai/types/chat/chat_completion_chunk.py)。只依照声明允许空的字段处理；choices/message/delta 等必需结构没有宽松回退。

实际应用源码 `dcdf729b5d22f451093e3658d131fd6757a00dde`，镜像 `sha256:679e08db330d32973e40d32f07007ee5b0b3442a1f6e8fc86af550ed39aaa40c`，容器 `992376aca440bcb3605b501a8e18758e33028b3195c2b57ec67a1b0b161734d5`。14:12 恢复两角色时钟，14:14 两次真实 life.plan 均带完整合同并各保存一项 running 想象日常；没有注入虚构测试活动。最终发布后原活动仍在、两条尚未开始的纯时钟保持原 job ID/due 时间，改到 due/priority 100。发布 ready/guard active，配置和 11 挂载保持，无 schema 迁移。Chrome 生活页显示“想象中的日常活动”和下次生活时间，技术状态使用中文。活动首次出现后实机截图又发现三节点仍套四列 CSS，最后仅把桌面列模板改为三列并直接验证标题可读，没有重跑不相关测试。

没有向真实 QQ/TG 发测试消息，没有强制补造心情或主观手记；本轮不能证明数日稳定性或全部外部业务。操作收据与必要截图在原工作区 `.runtime/coherent-refactor-20261031/life-repair-20261101/`、`life-repair-release-20261101/` 和 `life-response-20261101/`。中间 8de3c18、05bbdc9、291f29b 候选未部署，当前以最终精确镜像为准。
