# R21 核心链路集中审查、修复与发布

本轮按用户授权审查并修复陪伴、日程、回复、记忆，以及它们依赖的平台、网关和单容器运行链路。四产品修复已合入 main、正常推送并部署 .230 的 R21。核心功能可以使用；本结论依据静态链路追踪、缺陷定向验证和只读实机验收，不等于真实 QQ、付费模型自然表达或长期运行全部验收。

冻结来源：Memory `57a1a1468382641ea1d33cae9498c9ed170f6a7a`；Companion `5b83948c507fba97a496cfda1acff882816aff7f`；Platform `c591a5ddda34b31cd0e9e55f46cd7d50fae36dae`；Gateway `ed631d8a6f26890628a9df833792210e9a09e31c`。精确归档见本目录 `integrated.json`。

## 覆盖范围

| 用户链路 | 实际追踪范围 |
| --- | --- |
| 陪伴与入口 | 群 disabled/observe/reply/active、@及关键词保留、私聊与 Web；身份、actor/channel/scope、角色切换与撤权、参与判断及原渠道所有权。 |
| 回复 | collector/source → 上下文、会话窗口与摘要 → 模型准入/生成 → 来源前后复核 → 原 sender → 回执、unknown 与取消恢复；Chat/Responses 共用容量、前后台让位、单次上游发送。 |
| 日程与自主生活 | 订阅、目标、活动检查点、重启与并发取消；Life 按角色/阶段轮转、阅读旁听、真实经历 outbox、日记反思与分享；平台管理操作和状态呈现。 |
| 用户记忆 | 原分段学习、作业/批次/回执恢复、来源权威、画像和关系投影、完整组召回、更正遗忘、迟到作业失效及群私隔离。 |
| 角色记忆 | 独立 actor 账本、来源、长期兴趣、生命周期、遗忘依赖闭包及旧证据抑制；不混入用户记忆语义。Knowledge 仅核原件版本、授权与披露边界。 |
| 运行依赖 | 平台网页/API、权限与临时 origin、内部 client；网关 scheduler/slot、provider/续期/检索模型；根 host、owner 线程、Exchange/线程桥、入口、健康及分阶段停止。 |

这是指定链路及第一方依赖的静态追踪和缺陷定向验证，不是全库逐行安全审计。媒体、外站、资产仅核接入边界；AL14/15仍延期。未因审查范围完整而扩大为全量测试。

## 已确认的问题与修复

| 触发与影响 | 修复及证据 |
| --- | --- |
| 角色解释 A→B→C，中间 B 到期清理时丢失替换边；遗忘 C 无法追到 A，旧正文残留。 | 清正文前在原 actor metadata 保留无正文替换 ID；遗忘/影响闭包读取这些边，重开 Store 后仍有效。基线定向复现失败，修复后通过。 |
| 分别遗忘由 A、B 支撑的同类解释，迟到提案将 A+B 拼在一起，互把旧证据当新证据而复活。 | 按同 actor/kind 的已遗忘 episode 与真实源 identity 并集判断；真正新增独立来源仍可形成新解释。基线复现失败，修复后通过。 |
| 主动表达等待模型期间被管理端取消，随后异常用旧快照覆盖取消；重启遗留 generating 又自动生成。 | 原 owner 重新读取同次尝试结算，保留取消/终态；每轮最多处理一个中断意图为 unknown，先于图片准备，不自动重生成。覆盖任务取消、重启、图片意图和显式重试。 |
| 主动表达采用收件人记忆后，只复核动机来源，未复核这次采用的记忆；等待模型或延迟发送期间撤回可能漏检。 | 仅非空实际采用记忆保存 scope 与原版本证明，不存新增正文；复用零预算 known 校验，在生成前后、发送前及原图片边界核验。生成中和发送前撤回均被阻止。 |
| 平台普通生活管理写超时被表示为普通不可用，owner 的 result_unknown 被转成上游错误；用户难以区分未执行和未知。 | 原写端口保留 execution_state/unknown 与原 request_id；不自动重试或换键。真实本地 HTTP peer 证明 unknown/not_started、写读超时差异及不重发。 |
| PeopleLife 等待 control 读取期间 session/目标 scope 被撤销，随后仍可能先执行订阅写。 | 写前和返回前复用原授权复核；定向覆盖撤销阻止 manage 及合法同键写，无新权限门禁。 |
| 网关 acquire 登记后等待 pump 锁被取消，残留无人接收的 waiting；release 先标 done 后等待锁被取消，容量不能归还，重复触发可长期堵住前后台。 | 取消覆盖登记后的全部阶段；复用原幂等回收，只有取消异常路径创建有强引用的清理任务，结束后继续传播取消。两个基线复现分别残留 waiting=1/inflight=1，修复后原竞争断言通过。 |

## 性能与可维护性

- Memory checkpoint 从遍历全部 metadata 改为按既有主键读取 schema/instance/revision/recovery；替换闭包用邻接表一次遍历，避免逐深度重复扫边。仍读取需要的既有历史，不以截断闭包换速度。
- Platform 普通成功 JSON 不再由共享 webFetch 克隆、解析后又由调用方解析；错误及 session 响应继续触发会话复核。浏览器定向证明普通成功体未被读取/克隆。
- Gateway 消除取消遗留的排队和容量占用；正常 release 保留直接协程快路，不新增任务调度或让出点。

这些是代码路径与复杂度收益，没有生产延迟或吞吐测量，不宣称具体提速比例。没有扩大槽位/超时、增加后台工作者或平行账本；没有 wire、DDL、依赖升级或本轮迁移。

## 未确认原因与退役判断

历次约 120 秒排空停服是实际可用性线索，但现有静态证据尚不能确定具体等待方。Companion 停止信号可唤醒 producer，drain 等待已调度工作；SDK pending 跟踪 RPC/事件而非 OneBot WebSocket 连接；根检查 host/线程桥和停止依赖顺序后也未确认新冲突。不能据时间相似推断死锁，未强杀、加大超时或清除守护状态来掩盖它。

没有确认可安全退役的无调用业务入口：群策略旧版 normalization 仍兼容配置，HTTP/TLS 路径仍用于独立部署，旧 adapter/投递仍有调用，迁移/CLI 仍有兼容责任，网关 counter 模式仍服务显式 reserve=0。故不为清理数量删除；本轮仅合并实际重复的原权限复核。

## 已有必要验证与边界

| 产品 | 本轮已有结果（本汇总未重跑） |
| --- | --- |
| Memory | 12 passed、11 deselected；覆盖遗忘、替换链重启、旧证据组合、真实新证据、来源撤回、维护及迁移/checkpoint 失败。 |
| Companion | 10 个定向场景通过，另复用 1 个空 recall/后台等待与权威重检用例；覆盖取消、unknown 重启、前台不阻塞及记忆撤回。 |
| Platform | 2 个 unittest 节点、1 个 Chromium wrapper 节点通过；TypeScript 与 Vite build 通过，既有大 chunk 提示保留。未重复无变化布局验证。 |
| Gateway | SchedulerCoreTests 20 通过（含新增 2），2 个实际 loopback HTTP 节点通过，覆盖 Chat/Responses 共池与取消不转发。 |

各产品交接记录了实际格式/静态/diff 检查及测试夹具或首版修复失败过程；最终定向通过不抹去这些过程。未执行全量、双后端矩阵、压力、真实 QQ 或付费模型；本轮没有 PG 特有改动。自然表达质量、长期连续使用与真实渠道效果不能由替身或静态审查证明。

两项历史数据限制必须保留：旧版本若已擦除过期中间节点的替换边，当前数据无法精确恢复，未猜测或读取旧备份补造；既存已生成主动候选若没有采用记忆的证明，不能追溯其当时的 recall，本修复只为新增生成保存准确证明。用户记忆原有 tombstone/历史保留规则不被改成 actor 域擦除语义。请求发送后取消或网络中断也不能证明 owner 回滚，仍按原幂等与回执恢复。

依据：`docs/development/chain-quality-execution-2026-10-29.md`、冻结归档中的 `memory-source/docs/handoffs/TS-402.md`、`companion-source/docs/handoffs/TS-403.md`、`platform-source/docs/handoffs/TS-404.md`、`gateway-source/docs/handoffs/TS-405.md`。最终运行版本、启动健康和受影响入口验收由根发布记录补充。

## 发现归属、优先级与实施

上述问题依次编号 Q01–Q07，均已修复并发布。Q01/Q02属于actor遗忘残留/复活，Q03属于取消和恢复重复执行，Q04属于撤回后继续采用记忆，Q07属于网关取消造成容量泄漏，优先修复；Q05/Q06属于管理写结果和授权时序，随本批完成。没有通过新权限、额外队列或放宽守护解决。

| 编号 | 冻结实现位置 | 现有能力与必要验证 |
| --- | --- | --- |
| Q01/Q02 | Memory `src/tianshu_memory/actor_memory/lifecycle.py::erase_derived/dependents/ensure_unsuppressed` | 原actor元数据、来源身份、同域事务；旧版失败复现和新替换链/组合旧证据用例。 |
| Q03/Q04 | Companion `proactive.py::_expression_unknown`、`runtime_execution.py` | 原单worker锁、候选终态与Memory.select版本协议；取消、重启、生成及发送前撤回定向用例。 |
| Q05/Q06 | Platform `web_life_management.py::_call`、`web_people_life.py` | 原请求ID、execution_state和session/qq.admin/scope复核；真实loopback写超时与权限竞态。 |
| Q07 | Gateway `scheduler.py::acquire/release/_complete_cleanup` | 原幂等discard/release；两个基线复现及原竞争测试、HTTP共享池/排队取消。 |

实现为四个独立完整功能块；集中核对M/C版本合同和根运行装配后串行合入。没有公开wire、依赖或DDL变更，复用检查后不再由根重复跑产品测试。静态覆盖的全部入口不冒称运行覆盖；动态覆盖严格限于上表定向场景和下列正式就绪/静态资源读取。产品完整覆盖表及命令见对应仓库handoff：

- [memory TS-402](https://github.com/YokiiroBW/tianshu-memory/blob/57a1a1468382641ea1d33cae9498c9ed170f6a7a/docs/handoffs/TS-402.md)。
- [companion TS-403](https://github.com/YokiiroBW/tianshu-companion/blob/5b83948c507fba97a496cfda1acff882816aff7f/docs/handoffs/TS-403.md)。
- [platform TS-404](https://github.com/YokiiroBW/tianshu-platform/blob/c591a5ddda34b31cd0e9e55f46cd7d50fae36dae/docs/handoffs/TS-404.md)。
- [gateway TS-405](https://github.com/YokiiroBW/tianshu-model-gateway/blob/ed631d8a6f26890628a9df833792210e9a09e31c/docs/handoffs/TS-405.md)。

## 正式发布与实机结果

R20 `a88f34c384a9` → R21 `sha256:1850b49ac5a489f4d8d2994446fcc1176c158319a8ccc1557220e6874bef97fe`；当前owner `2b420a404ffc81970bdd27deefb1482f3258a709fc7bd101553d1df42f8b8c94`，容量守护r14。完成时间 2026-10-29T02:27:54.159416+08:00。四产品628个已安装源码文件、83个前端产物、56个未变应用文件与全部66个wheel逐hash核对；新镜像继承R20的根文件层和运行入口/用户/环境/健康配置。检查候选镜像的容器未挂生产卷、无网络、未启动业务应用。

正式healthy、0 restart、无OOM；6业务模块、5观测引擎ready，原CA与hostname校验的鉴权ready为200（9项），18446首页及9项受影响入口静态资源按发布hash通过。未登录管理浏览器或发送真实QQ/模型业务请求。

37项挂载逐项保持；1133份配置逐hash保持，另1份Gateway env仅在原owner/entry/scope续租引用行变化，零新权限。Companion/host设置不变、20GiB floor与原容量/挂载授权保持；其他13个容器ID、镜像、运行态和重启次数不变。数据库无迁移、无全库备份恢复，仅保存本次配置/守护/租约的小型回退输入，没有复制媒体或业务库。

旧容器退出码0、无OOM，排空流程耗时123.968秒（systemctl返回0），旧结束至本次验收约71.322秒。后者是中断窗口的上界记录，含启动及实际验收开销，不冒称无中断或精确不可用时长。保留本轮旧守护故障/日志，未清除失败记录或强杀旧owner来绕过检查。

发布回退须先停止新写者，核对新产生事实及来源/租约，再串行切换至保留的旧镜像；不承诺无损一键倒库。此次actor元数据可被旧代码忽略，但降级会失去本轮修正的遗忘保护，因此不建议长期回退。

AL01–13和AL16/17非外站核心保持已发布，AL14/15继续延期，AL18真实1～2周连续使用仍待实际时间。正式物理验收仅这一轮必要检查，没有重复20次探针或等待300秒。完整机器可读证据见[发布记录](chain-quality-review-release-2026-10-29.json)。
