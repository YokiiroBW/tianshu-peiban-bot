# 多人上下文记忆核心发布

用户于2026-10-26明确授权推送、部署当前已验收核心；此前“不推送/不部署”是本地阶段限制，已被本次授权覆盖。Chat全历史查询、群关系图、群点云图、群热度和BOT自主生活记忆接入仅规划，不在本批实现或启用。

当前状态：三个产品已正常推送，四镜像已部署到现有生产，完整只读实机验收通过。实际生产为 `.230` 计算、`.210` PG/Redis/媒体，不重建NAS旧天枢计算栈。

## 版本与已完成检查

| 服务 | 已正常推送的产品提交 | 线上镜像 |
| --- | --- | --- |
| Memory | `62206f2a58be08e4032ce32ddf2c90219f417877` | `sha256:6f46be21570e8f9f425b7d344b9879c51ec203300e005b6751e341f5f6209306` |
| Knowledge | 安装同一Memory wheel，保独立原绑定 | `sha256:579051ba6e2c18b7ab7e2ce7e177f1b88767f47b064207d29f05cd2c4d28474f` |
| Companion | `68b85d0d59e2cdbe93a5240e4d7771d4f7a31840` | `sha256:46973fdbb913e5cd04aa70142b53312e7e53875953b58d2ea6e56cf7d2b08c3b` |
| Platform | `506a82062166cef457974ef9b95789010e3435d0` | `sha256:478cab2687908bfe02c4163e58e7295bed4014f6d532d95a7e61c63e0787633f` |

三产品远端main均为已核旧main的正常快进，没有force；协调检出同步快进，Platform原未跟踪build目录44文件逐hash保持。既有任务worktree、原分支和根混合改动保留。

本地、PG、TLS、原固定110与真实145/删除补验沿原验收结果复用，具体分母和限制见[核心设计第10节](memory-contextual-consolidation-design-2026-10-25.md#10-2026-10-26-本地实施与集中验收状态)。不追加整轮压力或真实模型测试。

四镜像以各自实际当前生产镜像为基础，离线、无生产挂载构建，只替换产品wheel；Memory/Knowledge各132、Companion99、Platform304运行文件逐字匹配提交。实际import来源、Companion两处`/app`副本、四CLI help及所有原镜像运行配置继承均已核；依赖版本不变。Platform Web沿原镜像保留，既有树SHA256 `861544f6822c876416b9e15cde1a2e54d74645014ca69b39c0f11d70ecf147a4`。

首构建尝试发现Memory虚拟环境无pip，没有触及生产；按原发布工具的外部pip指向目标解释器方式完成四镜像，原失败和其自有临时容器清理记录保留。独立审读发现维护容器在客户端超时后可能存活，已在维护调用归属按本次唯一名称、标签、镜像精确停止并确认退出，再进入原失败收尾；不自动重跑迁移。

## 合同、迁移与操作顺序

沿每个owner现有的完整`/contracts`宿主目录，完整更新`memory-extraction/v1`、`memory-context/v2`、`source-sync-batch/v1`三包；其他线上包保持原字节。各实际目录的隔离副本叠加这三包后，四个候选真实loader均通过，每目录16文件变化。不得将root未采用rev7或无关候选覆盖上线。

extraction manifest为`205239730bf6aa5ff320cf9f300ae9c301c15559c0147aecd1fe001e8bfe5b82`，context-v2为`ec39155651f4c2d09183ccba5735cc9b9fc8ab9854de546223a89a46ff020ac9`，source-batch为`7f4c4587f1acea34f8af79f3792280b1532953bec018ab646532d670aa81c091`。

停机前经现`SelectedDatabase.readonly/open_existing`读取生产Memory：schema3、automatic_fact2、context1、observation1，尚无consolidation/segment标记。一次性维护程序据此先调用原`migrate_memory`，再`migrate_segments`，每步各有新备份，保存独立来源guard；不构造configured_app或worker。Companion用原共享StorageRuntime绑定main及原已启用observations，再finalize/close；不启动Core或模型。Knowledge保持独立绑定，不要求安装Memory的segments。

实际执行：旧guard正常停止并核十个受管实际退出/重启策略no → 五服务24个PG源/两个guard与文件冷备及临时库恢复校验 → 三包合同装配 → 原Memory显式迁移/Companion离线初始化 → 新guard代次与四镜像替换 → Gateway仅在旧引用确已过期时沿原owner/entry/routes/TTL300重新签发 → 按实际身份arm并核ready → 原13服务/五TLS/PG/Redis/NFS/Web/QQ连接只读验收。

OneBot、两个漫画辅助保留原身份和运行；其他服务不替换镜像。媒体原件、Books/Ebooks、漫画、听书、陪读、Gemini设置和存储后端不在修改范围。不得用保留SQLite覆盖PG。发布不发送真实QQ测试消息或主动调用真实模型；启动后的正常业务另属日常运行。

## 实机结果与发布修正

完整只读验收通过：13个实际服务、五服务live/ready的验证TLS、五套既有PG/Redis及24逻辑源、NFS挂载与依赖、Web静态资源/会话以及OneBot原连接元数据。协调电脑访问 `http://192.168.31.230:18446/` 为200，首页hash与服务内部相同。四个运行镜像源码逐文件再次匹配，Memory六项marker完整相等，Companion extraction_schema为6；automatic_memory_candidates与observations均按原配置启用。新guard ready；原三个辅助进程的身份和启动时间保持。未发送测试消息、未新增测试模型调用，没有新增历史回填。

冷备首次因辅助脚本导入在受检目录生成单一PYC而停止；四项控制目录/文件的实际差异仅此缓存，未发生业务配置变化。严格核源码、缓存hash/编译对应后将缓存保全到control外，重核副本、原文件谓词与SQLite，并执行原snapshot尾部；没有重dump/restore、没有伪造原媒体初始inode，媒体目录identity只对续接窗口前后可证。原失败及PG恢复证据保留。

第一次发布续接已装配合同，维护容器因只加入原primary网络而不能访问NAS，失败在首次只读marker查询，未执行迁移。第二次只从迁移阶段接续：维护容器先create、按服务补齐原全部网络并核Env相等，再start；Memory为core+egress，Companion为core+frontend。独立备份/收据与自有容器退出校验完成，原错误保留，不重复安装合同。实际Memory依次完成migrate_memory/migrate_segments，旧记录、旧任务代际和每步备份保留，未回填历史。

首次完整健康检查未通过：Companion继承日志目录已达1073741776字节、距离原1GiB预算只余48字节，触发原日志容量保护；Platform先超出就绪预算，后续单次观察为其余八项正常、媒体后台runtime恢复中。17源独立只读检查合计0.253秒，preflight ready；没有据此推断业务池耗尽，也没有改池、超时或媒体源码。按原guard正常停启路径，将161份闭合日志完整保全到同stage的`logging-recovery-v1/closed-companion-logs`，逐hash/bytes/uid/gid/mode核对并fsync，无删除/截断，原日志目录及配置保留；恢复同一批镜像与owner，新guard代次ready，三个aux进程不动。之后完整实机验收五服务live/ready均200。未单独宣称已定位媒体先前恢复的全部原因，原失败收据保留。

## 备份及回退边界

本次私有stage为`/srv/tianshu-tooling/memory-contextual-20261026-62206f2-81e73582d53b4305a4f479414a94bc7a`。冷备目标为其中`cold-snapshot`；已完成离线恢复校验：3270193759字节/7545条文件、39个SQLite、五服务24个PG源、两个guard；manifest SHA256 `66a48b16a604f4d7d79e1b01f9ce9618b1282c52a938db3a2889a27bfd163719`。PG归档16125536字节，各源在原备份中均已确认before=临时恢复=after且scratch删除。

schema6任务、canonical别名及新回执不能交给旧代际读者；整批不承诺仅换旧镜像即可回退。失败保留证据、确认本次维护写者退出，沿原流程保持相关业务停住后精确处理，不自动重跑或还原SQL。备份的隔离恢复验证不等于覆盖生产恢复演练。旧镜像、PG、guard、文件与配置共同保留；回退须针对实际已经执行的阶段判断。

## 质量与预算

本轮已观察的实际保存未见硬串人，仍有少量修饰/动机推断、前台时间理解和极端突发延迟问题；原真实145中两次上游502不改为通过。部署健康通过也不等于语义满分或生产业务吞吐验收。

原模型账本17540/18000、余460；本次构建/部署/只读验收及扩展规划新增测试模型调用0。未来扩展仍须按批准预算，本文没有新增调用授权或自动化。

[五项扩展实施规划](memory-extensions-plan-2026-10-26.md)。本次不启动该规划的开发、历史回填或自主生活接入。
