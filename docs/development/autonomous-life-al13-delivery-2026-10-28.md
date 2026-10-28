# AL13 本地交付（2026-10-28，主场景与scope通过、已本地集成）

本批候选审查及声明范围内验收已完成：candidate2 SQLite/PG主场景和scope r2四次原生运行均成功，十份原始报告已收取并校验。协调者已串行快进三个本地产品main到下列精确提交，保留原Platform build/。**AL13未推送、未部署；本记录为正式本地交接；线上版本仍为R15。**

此前 R15 已推送并部署，根记录 `0f63c63`；这不表示本 AL13 候选已上线。正式任务板及18项队列已同步本地集成状态，未把AL13记为已上线。

## 精确候选

| 所属 | 提交 |
| --- | --- |
| Memory / TS-390 | `9459711d9eb0d12a2b93a0041d62712e8794b021` |
| Companion / TS-391 | `bba45091f504a1534d52343134b0fa59f39cfdd1` |
| Platform / TS-392 | `da70adf28ea20f4bf913114896092c4ec101e9aa` |
| 根共同合同（仅本地） | `6b7c7aeed602124601af906b02a77b71a7f5a3ed` |

## 实现与原有归属

Memory 在原独立角色经历域增加显式 timeline：真实 occurred_at UTC 半开窗在 LIMIT 前过滤，稳定时间/id游标绑定角色、受众、窗口与版本；遗漏、未知时间和预算不足不会被当作“那天没有经历”。旧 query 及衍生操作保留。Knowledge 原件 owner 从实际匿名 GET 链产生版本化披露资格，复用原件 provenance，不做 DDL，不以补资格改变旧 read access_version，也不升级旧 actor_only 历史。

Companion 继续使用 Life 的 materials/diaries/revisions、配方、私稿和发布指针。日记来自已核来源；反思进入原 append_derived，问题回原 concern/activity。公开与订阅受众版本从各自获准材料生成，不把私稿全文交给模型删敏。分享继续走 Proactive 的订阅、真实受众、quiet/cooldown/quota、发送及 unknown 回执。普通对话按需使用原 ActorMemorySkill，没有新增逐轮规划模型。

Platform 只扩原 LifeRead 投影和直接调用者，保留旧虚构日记含义与授权。用户记忆提取、分段、画像、摘要、召回算法、旧 API/schema 未改变；角色认识不写进用户记忆。

## 独立审查的三项修复

1. PG 对超过六位的小数原本四舍五入，游标却截断，可能重复分页或跨日。现于 timestamptz 转换前截断，与 SQLite 和游标统一；有对应单测及 SQLite/PG 原生证明。
2. 来源撤回后，日记派生问题可能通过 concern 再进规划模型。现过滤并核验 concern/current/previous activity 的来源，在模型槽内和输出后重核；恢复缓存保留对应 proof。
3. 管理员 revision 同步入口原可读撤回后的缓存正文。现对来源化日记拒绝同步读取，异步 verified 入口核来源；旧虚构日记兼容保留。

最终只读复核已关闭以上问题，并追踪独立受众材料→日记草稿→motive→表达模型→send_expression 的来源与受众核验。静态复核与下方已完成scope原生场景分别记录。

## 已有验证

组件结果分别记录，不重复累计：Memory 82 个不同节点；Companion 213 passed / 3 skipped（管理员同一节点复跑不增加计数）；Platform 后端16、浏览器4、build/typecheck通过。不声称各产品全套全绿。

candidate2 的 `sqlite-main` / `pg-main2` 均 exit 0。独立 R15 镜像测试容器安装同三份候选 wheel，pip check、安装 payload 校验通过，降至 uid 10001 执行六 owner。主场景已实际证明：UTC/微秒分页、来源化私密日记、反思与 concern、公开原件资格和干净投影、独立公开日记、原 LifeRead HTTP、私稿不可见，以及撤回后的 admin/read 拒绝和规划模型不回灌。PG 实际绑定路径亦通过。

主场景刻意没有用户 turn 或用户记忆路由；这证明本主场景未混写用户域，**不能替代普通对话和用户记忆回流验收**。

原始报告已收至 `.runtime/al13-verification/evidence/candidate2-main/`。协调者与独立复核者已逐份重算并核对六份 SHA-256，确认各报告 ok=true；精确索引见同名 JSON 的 `native_main.evidence`，不复制合成正文。两个 AL13 主报告：

- SQLite：`02a99a68985fd4d42b7d60a5b185b1b2ceef084a269135ac0299a22952cf0b23`
- PostgreSQL：`095099e8966227143faa117ed2c05e476843615673e27856a372e3897550b8c6`

模型 provider、身份、时间/资料均为声明的隔离替身；公开原件使用声明的单 host DNS/connect seam，字节读取和 Knowledge HTTP/存储是真实路径。未验证真实付费模型、QQ实号发送、外站账号或 AL13 生产运行。保留此前 candidate 失败/修正记录，不以本次成功抹除。

## Scope验收与严格边界

`sqlite-scope2` / `pg-scope2` 均exit0，报告ok=true、closed=true、close_task_done=true、owner_threads_remaining=[]；独立重新核对四份原始报告SHA。脚本源码和两份报告的source_sha256一致：`9cdb2e0ed815d78d52ffb46340d494b59bf10b84f0d7c09edfa30fe1b3bf03bc`。

原用户网页输入沿原Memory写入/召回取得actual material receipt；真实已受理群turn沿实际registry选中的ActorMemorySkill执行日期timeline与偏好召回；未手写turn或伪造owner返回。原群assertion、独立受众日记、原motive、quiet deferred及撤回后presend/admin拒绝和零新attempt均有断言。

两个scope validation SHA：SQLite `dfcd0dee889fb0d5bcc343e067b7dffd7fb48f3f274a0bfd18cbd193a117decf`；PG `53519b30b2f60a7ba0fe72782f8988aa864b4844122a9b46cfcd26ccb64a4c29`。安装报告两者均为 `9f7e9036a1a9d4b70b8d3ea1999dcb09453b230b569d59d08900363cfe96e676`。原主六份回执继续复用，不重跑或重复计数；主报告原有pending文字由此scope补证关闭，不修改原始回执。

限制仍明确：用户与角色证据来自不同scope，未验证同一prompt内的矛盾偏好自然表达；工具由fixture调用原registry，不是模型自主选择工具；群turn实际终态为closed_unknown，不当作成功送达。日记候选在原quiet门禁处deferred、零发送attempt，未实号发送；unknown发送恢复复用原未变证明，本scope未重演。真实QQ、付费模型、外站账号及AL13生产验收不在本次证明内。

## 夹具修正与日志观察

最早控制器vendor嵌套挂载缺空目录，产品未执行，仅清两个created容器后补挂载点；PG初次start早于socket ready，产品未启动，随后增加final PID1=postgres与pg_isready有界等候。candidate1主场景未设置既有life_writing，两backend日记unavailable；candidate2仅显式开启测试配置后主场景通过，产品wheel不变。

Scope r1脚本SHA `367eb42793e600895fd01f5fcc365232512fa704185bc80f1ceeeca6735ef161` 的失败回执保留：SQLite在额外工具检查报dependency_unavailable；PG业务检查到撤回，但关闭未完成，ok=false，均不计通过。r2等真实group turn自然终态后再做额外工具检查，复用原read_retry有界恢复，并明确观测退出。两次成功r2的bounded_read_recovery均为空，不声称本轮实际用重试消除了问题。

Platform事件seq1534于09:34:09.417Z记录logging.recovered成功；seq1535于09:34:09.420Z的logging.unavailable/log_flush_timeout是恢复后补记历史故障，**不是第二次故障**。不从两条时间差推算中断时长，不猜CPU/I/O原因，不宣称修改产品日志系统。原失败报告及platform-logging-events.json留存。

协调者已按label清理本批全部自有测试容器及PG服务/tmpfs，stage证据保留；正式R15仍healthy、0 restart。此清理/生产状态由协调者回传，没有因此重跑未变化的生产业务验收。

## 可重建合同与配置

`contracts/runtime-profiles/unified-r15-al13` 已纳入 Git，可直接使用 `materialize.py`、manifest 和最小 overlays：C/P 276 文件、Memory 103 文件，共享100条映射，仅19个历史 overlay（886,641 LF字节）。新空目录实际重建并校验通过。AL13三包直接引用当前root；Memory Knowledge主manifest指向root `profiles/memory.manifest.json`，保留其R15窄依赖。不能用当前根legacy memory-context替代不同owner冻结树，也不能改无关pin来绕过。测试树未消费的旧嵌套profile仅按当前root归一化。

使用既有 `life_writing` 配置，本次只在隔离测试fixture中开启。生产值须保留并按用户实际启用选择另行记录，不新增功能门禁，也不由测试配置推断生产已启用。

本批 TS-389–393 与前置依赖闭包10卡验证通过。根完整taskvalidate仍因12个历史任务引用缺失依赖（涉及9个编号）失败（如TS-265引用缺失TS-256/257）；本批未修改历史任务绕过。

三个本地产品main已串行集成；正式交接、队列与任务状态已集中更新。发布、推送、部署与实机配置验收另记，保留现有配置和数据。
