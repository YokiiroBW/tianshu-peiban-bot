# 2026-10-29 真实运行故障恢复

用户授权修复自动记忆阻塞、窗口重复重置、自主任务失败和日志积压，正常推送并串行部署，保留配置、数据及用户对话记忆逻辑。用户新配置的嵌入/重排序沿现有绑定核对，不替换凭据或模型。

记忆系统线程“记忆系统隔离测试接续”已确认无 R21 后新候选、活动写者、待迁移或生产动作，并释放必要 Memory/Companion 写入边界。原 TS306/307 工作树和根混合暂存区保留。本窗口是唯一生产写者。

基线：根 d9e645a；M57a1a14、C5b83948、Pc591a5d、Ged631d8；生产 R21 image1850b49、application48eee53。守护r14、20GiB容量下限、37挂载及其他服务保持。新任务不复活已完成AL卡。

| 任务 | 范围 | 状态 |
| --- | --- | --- |
| TS407 | 窗口重建使用独立材料核验，避免校验旧窗口generation造成循环 | C9272bad已集成，28项定向通过 |
| TS408 | 原记忆job/batch恢复、自主执行诊断及幂等请求身份 | Ccc00312已集成，10个不同定向节点通过 |
| TS409 | 日志确认查询有界分片、失败原因可见及安全追赶 | 根f6055e2已集成，22个不同定向节点通过 |
| TS410 | 现有检索绑定与后台调度配置 | 核对中 |
| TS411 | 集中审查、正常推送、串行发布及受影响入口实机确认 | 待依赖 |
| TS412 | 原turn过期模型授权按同provider/revision精确续期 | 独立实现，必要定向验证中 |

三项独立实现使用独立分支/worktree。本次既有 workspace start 无法在保留根混合状态下启动（隔离协调检出无 projects、历史任务依赖不完整），因此在原根与隔离根同时持有 allocation.lock 时登记唯一 TS407–411、以 git worktree 创建；没有改原根任务板、索引或产品脏工作树。记录位于私有 .runtime/companion-recovery-20261029/allocations.json。

新发现原turn的四份模型授权均已过期，追加TS412，在同锁下登记独立P worktree。仅复验原config.select身份、scope_digest、provider/revision后为该turn发新publication；不延长旧publication、不切换模型、不修改其他turn。Companion按原SelectionRequest重取租约。旧授权撤销、模型修订或禁用仍拒绝。

用户新加入Qwen3-Embedding-8B与Qwen3-Reranker-8B已在目录；实际读取external revision4时retrieval绑定为空，M/G通道均存在。沿原配置owner的CAS及幂等回执绑定用户模型、启用hybrid与rerank；不复制密钥或因test unknown盲目重测。

已确认后台根因：Gateway每供应商上限4、前台保留4，后台容量为0；实际后台admission全为取消/期限耗尽，未进入供应商执行。修正同一调度配置的保留数为3，保留总并发16、单供应商4和前台优先。原记忆6次失败历史保留，只在确认原执行已关闭并核原Memory回执后，以原job/batch/turn显式恢复一次；不清计数、不重复入库。

日志根因：30天/1024ID查询在原10秒内未收到HTTP响应，阻塞确认→来源证明→退役→补入。缩小索引和时间页，但不抬10000条/30天上限、不伪造确认、不丢原始待确认记录或跳水位。

只做必要的现有定向验证并复用子代理结果。普通发布不做全量/双后端矩阵、完整浏览器、固定20探针/300秒等待或全库备份；无DDL则不运行迁移。部署健康、原任务准确一次写入、窗口稳定、日志确认追赶和既有检索绑定分别给出实际证据；不把健康当QQ或自然效果验收。

## 实机装配发现与后续收口

R22已正常推送并运行 imagee12b932。旧进程正常退出约124秒，无强杀/迁移。六业务、五观测与原鉴权ready200，37挂载、1132配置文件和其他13容器保持，另两配置变化仅同scope网关租约与reserve4→3，原20GiB容量守护r15有效。窗口generation9080在两次相隔约6分钟的读取保持，原10轮/22消息已重新装回；原输入未清理。

四个原模型请求实查为三个排队cancelled、一个排队deadline_exceeded，均无upstream requests行。恢复前拒绝了仅按cancelled分类的维护命令，没有重建job或盲重发；恢复证据包含这两种明确排队终态。原C恢复入口在单容器内部exchange可达，公共TLS统一代理P，因此追加TS413受信operator/role.manage原actor写权限下的固定平台转发入口，沿原platform→C客户端与原request_id，不开旁路进程。真实隔离HTTP4项通过，无模型/QQ。

新嵌入测试的unknown记录触发另一个真实故障：ProviderCatalog.view查询tested_at浮点参数，PG词汇未将该JSON路径视为数值，实际SQLSTATE42883；整个供应商目录及角色启动核验因此失败，角色停pending/verify，独立角色记忆的query正确拒绝403。TS414只补tested_at数值映射；原生产只读对照修前失败、仅本进程映射修正后matches1，定向1项通过。无权限扩张、手改角色状态或删除失败记录。

日志R22查询已恢复，但旧confirmed缺collection位置的部分页miss会错误降pending；后续修复保留旧确认直到完整窗口确证缺失。实机Vector仅2488B缓冲且正常送达，Loki最新事件不在尚满的历史台账，积压来自旧回填。原pending的occurred小时查询174ms、HTTP200、原hash完全一致；优先该小时仅作查找提示，miss继续完整原30天，不缩短证明范围。相关两次修正各3项必要定向通过（含重合，不重复计数），待R23实际追赶。

用户另窗口已授权交接Platform 5c529d7，根已合47b503e：检索设置复用现requestId()，修LAN HTTP缺crypto.randomUUID导致保存前报错。复用该窗口TypeScript/4个浏览器定向有效结果；完整集成P a35eb0d已重新Vite构建成功，未重跑全UI。当前绑定实读rev7，hybrid/rerank及用户两模型已存在，维护命令rev7→7、无新增写；重排已有成功测试，嵌入旧unknown/upstream_invalid502不冒称成功或直接归为上游502。
