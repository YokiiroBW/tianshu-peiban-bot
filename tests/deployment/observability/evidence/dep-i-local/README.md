# DEP-I 本地证据（不是Linux全链证据）

`plan/`来自固定DEP-G planned例子的实际CLI调用，返回0且所有18个维度not_run。
`windows-refusal/`来自同输入加`--execute-synthetic`的实际调用，返回1，
`error_code=linux_host_required`，所有18个维度仍not_run，没有调用Docker。
两份report绑定本次执行时源码工作字节hash、固定身份hash和计划artifact；
`check_linux_evidence.py`均已核过。校验器不把文件hash自洽提升为实际运行通过。

`boundary-tests.txt`为最终13项边界测试真实输出，12通过，Linux flock 1跳过。
其他实际测试结果记在DEP-I handoff（工具输出），不补造未保存的原始转录：
21项既有对账/配置/原字节合同、4项真实TLS、12项总期限均通过；
生命周期11个不同用例中10个本地通过，外部POSIX SIGTERM 1跳过。
生命周期先完整8项7过1skip，再完整10项8过/1测试清理错误/1skip；
清理错误为测试自己的SQLite连接未close，改为closing后该用例单独通过；
最后新增main构造失败用例单独通过。没有宣称最终完整11项再次重跑。

合同来自协调根`C:/YOKI/Codex/tianshu-peiban-bot/contracts/diagnostics/v1`实际原字节，
先核manifest内部全部文件hash再复制私有`.runtime/depi-contract`；未用根Git旧合同。
manifest SHA256 `5d89f7a21637fd57cea4a236e17f8d8c4917799497ff44f87ee68ea614d4805f`。
依赖只安装在本任务忽略目录的专用venv，无宿主全局安装。

未执行：真实Linux Docker五组件链、PID1 SIGTERM、flock、Grafana/Prometheus API及规则投递、
持久卷容量/4GiB满额、可选64MiB tmpfs探针、正式30天/加速TTL、NAS。
历史DEP-B证据保持原字节、不重标为本次新实现全链证据。
