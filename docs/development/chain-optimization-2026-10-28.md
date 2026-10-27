# 2026-10-28 整体业务链路优化与双链路图

用户要求先解决正式单容器的503波动，继续完整梳理代码与业务链路，包括音乐重复查询；保留功能、配置、数据，在192.168.31.230实测后发布。用户已选择同时实现“账号→角色/人格→会话窗口”配置关系图和“消息→陪伴/记忆/日程→回执”实际运行链路图。本批是R7后续实现，不能以容器合并或仅方案替代交付。

## 基线与写者

线上R7为`d72f5febd4945ca5db8e51e2869f5720ab1ee86250ba76ede62b953bc3e1069b`；Platform `d9c1452c01f30350b2861afd3458e157a6a863af`，Companion `340cb78cf8bb6d9bb16c1b344fac8d85dc43e2d1`，Memory/Knowledge `abe78da4293ac679f38d41e13c346ad51c9a5fbf`，Gateway `29098f060d5055e69b079222d4de4d41c2ed2f42`。

| 任务 | 唯一职责与实际隔离分支 |
| --- | --- |
| TS-380 | Platform后端：503根因、原生只读探针、音乐维护/搜索/订阅/队列/导出重复工作；`codex/chain-platform-20261028`。 |
| TS-381 | Companion/Memory/Knowledge：消息准备、流式输出、记忆写回、生活日记、主动陪伴、提醒/回执、关系冻结；各产品`codex/chain-domains-20261028`。 |
| TS-382 | Platform前端与专属只读图投影：双视图、真实关联/事件、原登录和权限、浏览器验证；`codex/chain-graph-20261028`。只改graph新后端及WebConsole两处薄装配，不改TS-380的共享业务入口。 |
| TS-383 | 协调者：Gateway配置读取并发、统一宿主交换/取消/收尾、图查询注入、联合验证、唯一230部署写者；Gateway为`codex/chain-gateway-20261028`，协调库为`codex/chain-optimization-20261028`。 |

执行者均为gpt-6.1-sol/max。原根混合修改不动。现有`workspace.py start`只从可变项目HEAD分配且要求原协调树干净，不能表达本批精确线上候选基线；本批采用明确SHA的`git worktree add`，各任务保留`.runtime/workspace-context.json`，没有重置/暂存原修改或借此跳过隔离。产品内单一业务写者，UI专属文件与后端精确分开，最终由协调者串行合并。

## 已确认的问题与证据

230原生TLS只读就绪探针双读5轮：每轮一个200且九项检查全部ok（108–756ms），另一个503且九项全部not_verified（3–9ms）。`Probe.ready`在已有检查进行时立即拒绝重叠读者，构成可稳定复现的虚假降级；证据`.runtime/chain-optimization-20261028/ready-overlap-r7-r1.json`。修复共享当轮真实检查及其原始截止时间；不得加大预算或把真实失败判为成功。

其他确认点由各任务记录调用方、现行规则和集中修复：音乐逐条读/重复绑定及全局远端等待锁；回执窗口前部unknown阻塞后续；同请求相同文本embedding及不相关检索分支重复；网关全局配置锁跨版本等待。保留撤销、来源、角色/会话提交顺序、数据库事务与实际取消完成事实。

## 完成口径

按消息准入/角色、记忆资料、模型流式/回执、记忆写回、生活日记、日程主动任务、音乐媒体、插件接入、日志观测和前端管理逐块记录：既有实现、实际问题、修改或无需修改的证据、验证及限制。不通过删功能、跳权限校验、全局缓存业务事实或无界并发换取速度；没有问题的路径不强行重写。

图复用既有账号/角色/会话读投影和闭合去敏事件。配置关系与实际观测分开；事件只有时间/关联不冒充父子span；未观测环节明确标示，浏览器不获得内部token或任意LogQL。查询使用原guard只读角色，有限时间窗/数量和关闭收尾，不新增业务账本。

验证使用既有冻结R7合同、隔离夹具/数据库和实际Linux安装包；相关逻辑集中验证，保留失败及未覆盖，不重复累计通过数。230上验证并发探针、完整原生业务联合链路、数据库查询数/公平/取消恢复、图的登录边界与浏览器行为、日志及资源；真实QQ消息、模型付费调用或外部媒体发布若未执行必须明确，不能以本地替身冒称真实账号操作。

本批不再次清空R7之后的新日志，不覆盖原配置或其他服务。更新前取得匹配当前数据的备份，旧/新写者禁止并存；正式状态、镜像和实测结果完成后另补。

## 已实现与代码归属

| 链路 | 这次优化及保留的职责 |
| --- | --- |
| 准入、账号、人物、角色 | Platform保留账号/channel/群私策略与角色登记，Companion按实际准入落来源。人格、好感与关系不授予权限；同键异内容仍冲突，不缓存授权事实。 |
| 记忆与上下文 | Memory v2同请求、同设置、完全相同文本复用纯query vector；facts-only/episodes-only跳过对方的无关排序。每项身份、类型和来源证明仍分别验证；下一请求重新取设置与授权。 |
| 资料、原件与阅读 | 复用Knowledge原有capture→事务外I/O→重读配置/授权/revision→serve；Companion只在版本/hash/期限验证之后记读取成功，本地作品不冒充资料库读取。 |
| 模型选择、生成与流式 | Gateway配置读取改为Chat exact version、native(identity,requested)分锁；同键合并读取，无关慢查询互不阻塞。撤销复核、不可变摘要、深拷贝与同版本outage fallback保留；模型容量、公平调度和流式回执不改。 |
| 回复与实际投递 | 保留先持久投递意图再出站、Platform expression顺序及原send band；unknown只查原回执，取消不生成第二次发送。新的真实投递事件区分sent、partial、queued与unknown。 |
| 异步写回、关系与人物记忆 | 关系有界游标选择后执行exact row，暂停行不再挡住其他房间；同scope前序与前台屏障保持。phase按family/id重读实际持久终态，修复旧对象及actor-memory状态漏判；unknown不画成确定失败，冻结和Memory唯一账本不变。 |
| 生活、日记、写作 | 保留每actor/attempt/version串行，草稿/批准/发布及已发布日记只读权限；后台独立correlation，不借用已结束前台请求。背景模型原容量及聊天优先、公平机会保持，晚回包不写新稿。 |
| 日程、提醒与主动联系 | 前四条unknown的固定窗口改为轮转，每轮最多四个独立原回执查询；取消后drain完成才释放。恢复完回执才判新联系，原额度、静默、冷却、撤销及事务内重读保持，未扩大真实发送并发。 |
| 音乐 | Repository/Catalog事务内批读用于详情、目录、导出、排队、维护与扫描；登记后统一解析最终别名。远端元数据按完整键分锁，文件stat/摘要移出SQL事务；缺失写回前复核revision/canonical identity。保留音质、解码、库锁、任务版本及发布回执。 |
| 其他媒体和漫画插件 | 复用媒体类型分派、lease/generation/CAS及实例能力/绑定/invocation检查；本批未重写没有证据问题的视频、书籍、漫画发布和外部effect。原四线程/16准入、媒体后台最多三配额保持。 |
| 日志与健康 | 503共享真实当轮与原deadline；17个schema各保留只读binding核验，单schema多次往返合成一次SELECT；账户对象复用但状态每轮读取。五来源日志仍闭合12字段、批量持久写及原10000条/30天策略。 |
| 管理网页 | 新增配置关系与实际运行双图，复用账号/角色/会话读投影、主题、SVG和原保存API；Cookie/CSRF/Origin及读前后会话检查保持。固定五来源guard只读查询，最多两worker、四等待、四秒原传输期限，无浏览器任意LogQL。 |

300首合成歌单同结果SQL计数：详情/导出2702→13，目录604→8，维护1506→14，完整未变扫描905→9。它们是可复现调用数，不能当作真实账号/网络延迟的提速比例。大列表别名、重复成员顺序、已删除任务引用、artifact变更与缺失写回竞争已覆盖。

## 冻结候选与Linux验收

R9镜像`sha256:2b0d6ec8d4bddec06a0dc17af1590f05cf56a3809998be021abe637f703140ab`，Linux amd64，3388827804 bytes；四产品+shared storage及公开依赖共66个冻结wheel，无新增运行依赖。源码：Platform `5b7ea997b3c0e2e7bd3f015c4856ed29b67eb6fa`（独立合入UI并修正两个测试夹具）；Companion `225f62a9c201acfc283bc029562f118f16d5e524`；Memory/Knowledge `6ae7a1adfd8fb2ed5ee7d92a3ee5819e230678f9`；Gateway `0f491be3573af9257703b7871aaa746598d66b9f`；application `e3b60e5ef071d8ac623db7713c65b7a8e12ca370`。后续纯验收/文档提交不冒充该镜像的application源。

前端基于`e639b08b5a9b3c29da4d60ac4ef1e2380be36469`，357项输入全部与最终Platform精确Git archive逐字节一致；71文件/12617020 bytes，文件清单hash `667060c2ac22ab77740cea57a8de4a564a83df8f9d427ac7e086b7cff4e93328`。发布器现将已核验dist实际覆盖到`/srv/tianshu/web`，修复原基础镜像网页可能未更新的问题；构建者及集成提交分别记录。

230以最终安装wheel执行，测试与合成合同只读挂载，没有产品或deploy源码overlay：

- 真PG17独立容器/Unix socket、network=none：Platform音乐与schema/账户健康10 passed、4 subtests；Memory请求内向量/查询类型8 passed；Companion回执轮转/取消2 passed。显式断言backend=postgresql；生产PG/Redis未用于这些测试。
- Platform原预算/并发/真实TLS/批schema/双图HTTP：35 passed、5 subtests。
- Companion关系、学习、片段、人物记忆、审计、链路观测：183 passed、29 subtests；Gateway分锁/撤销/取消：5 passed、8 subtests。
- 音乐Linux解码、下载、目录与POSIX权限：51 passed、2 skipped、21 subtests。两项跳过明确需要另外的固定NCM decoder可执行文件，未将其声明为实测通过。
- 原生BusinessHost：六个不同owner线程，实际Platform配置→Gateway→本地录制provider的JSON/首个流式块/终态回执，OneBot真实本地WebSocket与原生命周期、Memory/Knowledge真实Assembly就绪及关闭通过。provider是隔离合成上游，无付费模型或真实QQ。
- 双图原生宿主：真实HTTP Cookie登录/CSRF/匿名边界、配置关系、真实本次安全日志投影、未配置/错误状态及全部71文件HTTP字节/hash通过；同一原生TLS app强制双读5轮，全部200，每轮只调用一次原evaluate。内部public app的404有原专项；HTTP WebAccess外层Ingress先403，二者分开记录。桌面/移动浏览器的原14适用节点验证与230真实HTTP验收分列，未声称230上执行了浏览器JS。
- 原生消息/记忆闭环：实际Platform登记及准入→Companion接收→Memory身份→Platform角色映射；Companion自动outbox材料回执一次投递成功，随后v2召回实际回调Companion来源事实、Platform原件/来源授权，继承同一correlation。角色错误与撤销均403；重新启用保留来源，撤销Platform入口后新召回、旧bundle校验及来源current均拒绝。六owner线程、正常关闭、源夹具库不变、provider零请求。生成环节特意未配置，记忆材料仍独立写回及召回；不能由此声称真实模型生成或QQ收发通过。

首次测试工具失败保留：PG URL省略authority hostname被原StorageOptions判无效，部分标PG节点实际回退SQLite，因此不计PG通过；改为显式hostname且query host仍指专属Unix socket后，以上20项才作为PG证据。TLS临时目录保持原Memory `.runtime`约束，nested只读挂载缺目录和缺原root测试helper均修正夹具。会话撤销新测试在query之后生成事件偶尔超出end_ns，现先捕获事件；产品时间窗/权限验证未放宽。native图初稿误期望HTTP404，按既有Ingress403修正，仅重跑受影响图工具。完整本地组件套件及旧失败的定位/定向修复见各TS-380/381/382/383交接，组间重合不重复累计。

上述原生双图、消息记忆闭环和正式guard只读查询工具保留在`tests/deployment/single_container/native_graph_check.py`、`native_memory_check.py`与`production_graph_read_check.py`，都是显式运行的验收工具，不进入普通单测自动执行。前两者只用独立合成状态，后者不创建第二个Platform或BusinessHost，只查询固定五来源并输出闭合计数/事件种类，不输出聊天或凭据。

## 当前数据备份兼容

正式更新前，R7正常退出（exit=0、非OOM、restart=no），现有容量守护的主动TERM记录为systemd failed/15，保留其armed/failure历史；只有进程PID归零、准确容器身份及停写时刻一致才接续备份。旧冻结备份器拒绝漫画插件标准Python环境的`data/platform-plugins/plugins/<64hex>/.environment/lib64 → lib`，首次备份失败已确认scratch/client清理完成，失败目录保留，另用新的独立stage。

`native_snapshot_alias.py`只对每次加载的临时冻结recovery namespace安装适配，复用现有容量guard的同一精确路径、UID/GID10001、字面目标lib及真实同级目录规则。safe_path和冻结bundle字节保持；记录独立directory_alias，普通文件仍走原copy/变更检查，重建链接后整树核对。230 Linux root以真实冻结模块验证两次复制、SQLite完整性、源不变、非法目标/UID/硬链接/复制中替换等十种拒绝场景通过；未unlink、改写或follow源链接。适配与guard/验证工具hash另列并放入备份controls，不冒称原冻结bundle已原生支持链接。

R7停写后的最新完整备份已通过：25逻辑源/6个PG命名空间、39个SQLite及辅助库1个、3个guard，9780个文件项/1644915330 bytes，1个精确alias。manifest为`0f7124e4c2fd9ac3ef5611ebfd9ad25670ec48491dd38fd90bc531d2f0aba3d3`，源不变、禁用恢复验证、临时PG/client清理均确认。旧失败stage保留；原r2容量历史独立复制，没有用新代覆盖旧armed/failure。

## 正式验收发现的日志核对分页问题

R9业务六模块和四个观测引擎已ready，实际obs-guard仍503，未据Docker初期healthy宣布完成。其monitor一次选512 pending+512旧confirmed，但`LokiClient.range`默认页1000；完整1024条触发对30天窗口的递归拆分，共用原10秒期限后失败。查询在prune/expire之前，失败又使满10000的释放流程推迟，放大pending/capacity/scan-behind。实际合约、鉴权、输入事件均有效，未靠放宽ready掩盖。

230对同一批真实ledger身份、同一只读Loki查询：1000页在10.003秒报`query_deadline_exceeded`；1025页7.877秒完整返回1024。512身份直接查询20134字节路径也200，排除了误把guard对外16KiB路由上限当成内部monitor根因的假设。

根提交`3c258be4dfb3a4d23cc9ee06dd95fe877ae59adb`令核对页为`max(1000,len(expected)+1)`；原10秒、哈希匹配、missing复核、重复与时间桶饱和拒绝保持。新增1000/1024完整批次、缺失/改动、饱和桶回归；原并发clear替身仅接收新limit参数。R10 Linux原生相关66个节点/20个subtests全部通过：首次31项因只读测试挂载缺可写`.runtime`而在setUp失败，增加独立tmpfs后只重跑这31项通过；其他35项结果复用。Windows原合同路径错用导致7项monitor初始化失败，改为冻结DEP_B_CONTRACT及明确PYTHONPATH后9项readiness通过，未改产品合同。

R10为不可变R9镜像上的增量发行，镜像`sha256:8155efd8f7c9358d75ae79842950f57641129d1a1406fe8d9a7e676f47b9ffbc`，3389302205 bytes。沿用同一66个安装wheel、前端71文件及原生引擎；归档完整3c258be运行文件，逐项确认与e3b60e5仅`deploy/observability/monitor.py`不同，并更新distribution回执及版本标签。未向运行容器热覆盖源码，产品功能验证复用同字节R9结果；最终正式状态另列。

## 连续补入下旧pending的重查公平性

R10正式探针、guard实际就绪和原10秒查询预算通过后，进一步观察发现595 pending里有83条旧Companion记录约2000秒未重查。对同一83身份执行原mTLS Loki只读查询，83条全部存在且hash一致、零缺失/冲突；没有修改ledger ACK。原因是`last_checked=0`的新pending不断先于已查未命中记录，满10000时每轮释放/补入512条，旧记录被长期排后。

根提交`804d0d529de92e71fbe96f4b0c43c84ca1040595`将未查事件的优先时间由0归一为`first_seen`，与旧记录真实`last_checked`比较；未命中后按实际检查时间排回，失败仍保留原证据、原deadline及pending/confirmed配额。两者均为Unix秒，无schema、索引、合同或业务配置迁移。pending过滤仍用部分索引，最多10000条作有界临时排序；本机合成10k全pending的SELECT+fetchall中位1.165ms/P95 1.291ms，仅为本机开销说明，不当作230实测。

新增10000条持续补入的83旧记录优先重查、未命中后old/new轮转、传输失败不改变核对证据回归；原1000/1024完整/缺失改动/饱和桶测试保持。最终Linux安装镜像上Storage+Readiness **24 passed、6 subtests**，无生产配置或产品源码overlay；Windows相同24项通过，Ruff与diff检查通过。其他未变文件复用R9/R10结果，不重复累计。

R11镜像`sha256:82dc69fd1b1a2404ac3c99c3bc9c8347a7fab52ec5e8b045c8effe4134795f11`，Linux amd64、3389776831 bytes，application归档SHA256 `751eccd4442ca749662ab46ecef4a8a797e25c2fbd87153292729a9aaa982659`。以不可变R10为父，完整runtime归档逐项确认仅monitor.py变化；66个安装wheel、71前端文件和各原生引擎同字节。发行回执和版本标签随精确804d0d5更新，无运行容器热覆盖。

## 230正式R11验收与资源

2026-10-28 04:33（Asia/Shanghai）正式启动R11，容器`3e635d956fede4f4f868960f607ab85f229a69110367dc2883610552a732ace8`，镜像与application为上节精确值。400秒生命周期复核healthy、非OOM，runtime/host及Platform/Gateway/Memory/Knowledge/Companion/OneBot六模块全ready，漫画inputs/publisher与五观测引擎ready。新r5容量守护active/enabled、heartbeat新鲜、无failure latch；旧legacy守护disabled，旧13全部exited=0/restart=no。R9、R10容器由Compose正常替换，保留其镜像、配置、inspect/stdout及备份；不能写成所有历史统一容器都仍保留。

这次收尾只改monitor读排序：R10正常停写（122秒、exit0、非OOM）后保留1126项配置/compose/unit与完整r4守护历史，当前日志root三文件3469312 bytes、两个SQLite作原源→备份→禁用恢复及完整性检查，源inventory保持。原S2完整25逻辑源PG备份、R10最新全物理39+1 SQLite备份保留。未恢复或迁移业务PG、未清空本轮新日志，数据继续使用原挂载。重发原同scope/owner/routes的300秒origin租约，零新增权限；1126个配置文件仅`private/gateway.env`的该租约引用变化，Compose仅镜像值变化、原37个mount身份/读写权限不变。

| 正式验收 | 结果及准确边界 |
| --- | --- |
| 并发就绪 | 启动后及跨300秒后各5轮双读，共20次HTTP200，原九项全ok；两窗口40–408ms、68–178ms，原预算保持。 |
| 数据与公开入口 | TS361正式app只读24逻辑库、actor独立SQL及六命名空间marker对原完整备份共25绑定一致；原样本/原件/漫画alias、HTTP18446=200、TLS18443匿名401且CA/hostname核验、OneBot18765健康200。未启第二个app。 |
| 跨租期 | 原origin签发后315秒Gateway/host仍ready、diagnostics.complete=true，五来源均有真实verified事件；不是只在首次启动通过。 |
| 双图 | 登录后入口`http://192.168.31.230:18446/#/settings/12`（任务与设置→链路图）。实际HTTP71文件/hash核验；两个匿名业务接口401。正式宿主原FlowEventReader用原mTLS只读角色查询固定五来源，实际窗口274条事件/107个关联，业务阶段0如实不推断。配置关系及完整业务观测/授权状态另有前述隔离原生与浏览器验收，未声称正式用户带session浏览器JS已操作。 |
| 日志分页/公平 | 原83条已存在且hash一致的长期pending，R11启动后全部重新核对并正常按策略退役，零遗漏/冲突，未手工ACK。最终guard ready200、monitor_success=1、query_failures=0；该时点pending595、最老约34秒，仍有有界历史来源扫描追赶，未宣称零积压或历史全部追完。 |
| 保留策略 | ledger10000、正式小时可见聚合10000，配置全局10000条/30天；既有writer CLI支持手动清理。raw/buffer/Loki物理回收另有界，不能把瞬时物理文件数与逻辑条数混称。R7以前按授权清旧约900MB的结果保留，本批未再次清新日志。 |

无合成流量、同一正式容器的两次60秒cgroup v2窗口：

| 窗口 | working set中位 | anonymous中位 | pids中位 | CPU单核口径 / 8核总量 |
| --- | --- | --- | --- | --- |
| 首次启动日志追赶期间 | 1077051392B（1.0031GiB） | 1047830528B（0.9759GiB） | 264 | 121.0473% / 15.1309% |
| 跨租期检查后 | 1071943680B（0.9983GiB） | 1042477056B（0.9709GiB） | 264 | 126.8269% / 15.8534% |

第二窗working set范围1069891584–1114513408B。working set为memory.current−inactive_file；CPU由计数差计算，两窗均仍有日志历史追赶和原后台工作，不能称完全空闲。此前R7为0.9182GiB/单核79.5667%、旧13另一窗口为1.737GiB；时间/负载不同，本轮未证明对R7的内存或CPU下降，不计算固定节省率。音乐SQL减少也不等于同倍端到端提速。不丢日志、业务/权限正确和后台公平优先于把数字改小。

只清理已核全部容器零引用、本批从未启用的R8候选镜像`66cd4f99…`，无force/prune；正式/R7/R9/R10回退镜像、配置、完整冷备、旧13、失败证据均保留。源写者停下后才能按实际新增事实选择回退，不承诺无损一键恢复。

协调树`.runtime/chain-optimization-20261028/`及230独立stage`/srv/tianshu-tooling/chain-release-20261028-456f2dda89f24fdbaa8788c18ed1cacc`保留最终`production-release-r11`、精确SHA回执、两轮ready-overlap、TS361/actor/graph/retention/fairness/config/lifecycle及两窗resource。Linux构建stage为`/srv/tianshu-tooling/unified-20261028-e5426e7b344240baa6ede87deb0ff52a`，相关安装测试在原独立validation stage；生产与测试资源分开。

本批已完成12条业务链路的代码复盘、确认问题的集中优化、双图实现和230正式验收。没有把未发现问题的既有路径强行重写，也未声称全代码无缺陷。真实QQ收发、真实付费模型生成、外部媒体发布和两项缺固定NCM decoder的测试没有执行为通过；现有配置/数据保留，未新建用户聊天、推送远程或合入产品main。候选产品/协调提交及原根混合修改各自保留。
