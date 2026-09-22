# DEP-I：完整日志链执行入口与守卫正常关停

## R1关停证据窄修（本次最新交付）

返修基线 `345cdea93611b354ad29ea43584c3f3e5e533a19`，该首验未集成。
以下原交付记录保留为历史；本节覆盖原compose stop/force-recreate及非0退出判定。

新增统一 `deploy/observability/container_lifecycle.py`：初次up后（即使部分失败）固定真实ID，
每次按owner/ID/镜像/挂载复核；固定ID禁用自动重启并inspect确认，只发显式SIGTERM，
以同一30秒总预算执行命令和poll，不升级SIGKILL，不重复结果未知的信号。
严格仅exited/Running=false/ExitCode=0/无OOM或Error/restart=no确认正常，143/2/137均拒绝。
每次记录完整退出观察和信号尝试，异常/超时阻断后续start/recreate；最终guard exit2使CLI返回1、report failed。
Vector重建改为正常退出后无force且无-v的固定ID rm，再up --no-recreate；不删除源段/卷。
部分重建失败捕获新ID供同样的TERM清理，不能用compose隐式关停回退。
原守卫线程/请求期限实现未改，不重复未变的94秒期限回归。

实际R1验证：新增11项Docker协议替身0skip通过（0.154s），覆盖0、143/2/137、超时、
ID/镜像替换、部分启动/重建、未知信号不重发、禁止隐式kill命令及最终guard exit2不留partial。
相关原边界13项为12过/真实Linux flock 1skip（0.069s）；Ruff通过。
首轮新测试唯一失败是浮点截止0.050000000046秒与精确0.05比较，测试容差改1ms后通过；
没有放宽生产截止或退出码判断。真实Linux/Docker停写仍未执行，不冒充协议替身为容器证明。
新增R1证据见 `tests/deployment/observability/evidence/dep-i-r1/`，包括实际替身逐步commands/
signal/exit事实、正常与143/2/137/超时结果，以及新plan与Windows拒绝报告。
原345cdea及DEP-B所有证据不改写。固定返修HEAD另发协调，提交后再次停写。
交付前已逐字节核对原39个证据文件未变，新7个R1报告artifact hash全部校验通过；
完整diff与白名单已复核，未改产品、根板、G/J入口或共享合同。

## 目标、范围与固定依赖

根基线 `eced6de3ed0e2292d8c767d9f37fea36d0e04476`。
任务分支 `codex/dep-i-observability-wave4`，独立应用worktree。
仅修改deploy/observability、tests/deployment/observability及本handoff。
按wave4最新授权由Codex直接实施，无子代理/DSH、NAS/SSH/系统安装、真实业务数据或外发。
未修改根板/共享合同/产品/DEP-G/J目录，无合并或推送。
交付提交为包含本handoff的本地Codex身份提交，精确HEAD随最终协调消息提供，固定后停写。

消费协调转发的DEP-G固定Git对象：

- `880c2c33ffc4c12b9267d38267c2dacde074890d`：身份文档/schema/example/实现，只读取Git对象。
- `ceca645fbc60b68a28eb1fa4e942ecf8bab3ea1b`：镜像inspect与容器observed分离的补充文档。
- schema原字节SHA256 `4869bb1df94e5ac3457b244f812026948d22fc9091c477384c57032f07d4a8c2`，随包复制并强校验。
- diagnostics/v1只从协调根实际文件核manifest及全部成员hash后复制本任务私有运行目录；
  manifest `5d89f7a21637fd57cea4a236e17f8d8c4917799497ff44f87ee68ea614d4805f`。

没有读取其他工作者活动检出。产品词汇由实际G bundle携带的固定源码snapshot消费，
执行时要求与身份sources严格相等，本号未擅自改产品版本。

## 变更

入口和完整运行说明见 [LINUX-ACCEPTANCE.md](../../deploy/observability/LINUX-ACCEPTANCE.md)。
默认plan，显式execute才取本地Unix Docker、共享同inode非阻塞flock。
复核身份原字节、inventory、完整Compose及挂载、实际project/service标签、镜像ID/RepoDigests、
UID/GID、四应用停止、全新日志项目及空五持久卷，拒绝scope被其他容器挂载。
保留G已产生的合成启动日志作为baseline，新增4×100连续序号事件每批，保存三方实际全集与hash。

已接线而**尚未实际Linux执行**的场景：采集；Loki断开时真实buffer指标+磁盘文件；
Vector重建后的重连补传；rename轮转与再次重建；canary拒绝计数及中央无泄漏；
query/writer/匿名权限；Grafana datasource和dashboard API、Viewer读/拒写；Prometheus三个up；
实际告警规则到guard内部录制器；可选64MiB tmpfs真实ENOSPC/guard容量函数探针。
录制器无外部URL/发布端口，只持久化封闭告警字段，无Slack/邮件等外发。
每个未执行维度单列；“通过”必须有绑定artifact。证据检查CLI只校验自洽，不创造运行证明。

容量计算与应用回收门禁仍阻断；无采样、无应用段删除，查询命中/204不是回收授权。
真实持久卷ENOSPC/Vector满4GiB、磁盘高水位、正式30天、加速TTL单列未执行，
有界tmpfs结果不能提升这些维度。旧DEP-B全部固定证据保留，不引用旧8条或640条为新全链。

协调追加授权的必要守卫修复：
`guard_lifecycle.py`接SIGTERM/SIGINT标志，停止准入、取消网络等待、同一10秒预算等待listener、
monitor与全部在途HTTP；确认owner结束后才close backend。正常monitor自身finally关闭SQLite/告警库；
启动失败/超预算非0，未结束owner时不抢先close，不打印正常关闭成功。
网络取消与最终close分开，保留10秒请求总期限/8槽/去敏边界。configure复制新增模块。
G须将日志包source重绑本次固定提交并重新生成bundle；入口拒绝实际部署关键代码仍为旧版本。

## 实际验证

Python 3.12.14，专用venv安装cryptography50.0.1/jsonschema4.26.0；Ruff0.15.7。
独立不同方法合计**59项本地通过，2项Linux限定未运行**，分批记录，不把重复跑数累加：

| 实际命令/范围 | 结果 |
| --- | --- |
| `python -m unittest discover -s tests/deployment/observability -p test_linux_acceptance.py -v` | 最终13项，12过/真实Linux flock 1skip，0.067s；默认不调用Docker/取租约、远程拒绝、socket固定、五卷、身份篡改、证据、原字节合同、保留门禁 |
| `DEP_B_CONTRACT=<私有已核快照>` + `... -p test_observability.py -v` | 21项0skip，0.774s；首跑漏env的4skip已纠正后完整21通过 |
| `... -p test_guard_tls.py -v` | 4项真实loopback HTTPS/mTLS，最终1.604s，0skip |
| `... -p test_deadlines.py -v` | 12项总期限/槽回收/DNS/分页/关闭取消，94.632s，0skip |
| `... -p test_guard_lifecycle.py -v`及两项后续单测 | 11个不同方法：10过/外部POSIX SIGTERM 1skip；慢TLS、慢后端、HTTP/monitor超预算、重复信号、线程/构造启动失败、真实子进程SIGINT。真实monitor40记录和告警SQLite持久/完整性通过 |
| `ruff check deploy/observability tests/deployment/observability` | 通过 |
| `run_linux.py` planned例子及同例子显式execute | 计划返回0/all not_run；Windows执行返回1/linux_host_required/all not_run；无Docker |
| `check_linux_evidence.py <plan或windows-refusal>` | 两份实际artifact hashes验证通过，不升级Linux结论 |

生命周期中一次失败发生在测试检查用SQLite连接自身未close导致Windows临时目录清理，
改为contextlib.closing后该用例单跑0.259s通过；新增main构造失败单跑0.295s通过。
最终完整11生命周期项未重复整套跑；其余最终代码相关总期限与TLS均已通过。
新增guard模块后的配置生成用例另单跑0.331s通过。完整diff已审查，26文件均在白名单，
`git diff --cached --check`通过；33个既有证据文件与根基线原字节相等，两个G接口复制与固定Git原字节相等。
原始本地计划/拒绝报告及最终边界测试转录见
[本地证据](../../tests/deployment/observability/evidence/dep-i-local/README.md)。

## 未完成、限制与下一步

1. **本地完成不等于Linux/NAS验收**。本机无Linux Docker，所有实际组件/告警/权限容器场景仅实现未执行。
   Windows真实子进程SIGINT不替代Linux PID1 SIGTERM；真实flock也未跑。
2. 协调串行审查提交后，G重绑本日志包、准备五镜像与新scope身份；四core停止、五obs卷空且10001所属、
   TLS loopback SAN齐备后，按运行文档启动一次完整链。执行失败保留scope，不删除旧段/容器后重试。
3. 持久卷物理满额、4GiB缓冲满载、真实30天容量/保留和独立加速TTL未接入本短程入口的实际执行；
   不拿tmpfs或静态配置冒充这些维度。需协调安排独立有界持久文件系统和长程观察预算。
4. 应用持久确认/安全回收合同仍缺，引用已有RECLAMATION-PROPOSAL.md；不能宣布无限日常运行。
   在源预算前停止新工作准入、保留源段、扩容或完成合同是可操作阻断。
5. 关停10秒是应用线程/网络等待预算，不是不可取消内核磁盘I/O的硬实时保证。
   超预算退出2表示异常/未知持久状态；J不得将其当九writer正常备份许可。
   本执行器最终保存所有obs退出码/OOM，137/OOM明确失败。
6. Grafana验证为实际API/datasource权限接线，未实现浏览器视觉验收；通知只到隔离内部录制器。
   确认接收器进程只属于合成测试；不作为生产告警分发服务。

无自动激活、发布、推送、NAS修改或旧系统退役。固定HEAD交协调后停止写入。
