# NAS-06 联合验收前置适配

基线 49cbaac。新增显式核心辅助网络（egress/frontend）及日志网络（observe/storage），均要求小型私网且彼此不重叠，默认配置保持 Docker 自动管理。不修改 Docker daemon 全局地址池。

真实进程身份检查改为在本地 Docker 主机读取 inspect 对应 PID 的 /proc 状态，并复核容器 ID、镜像、启动时间、PID 与进程 starttime，避免依赖 distroless 镜像内的 cat/shell。正式 UID/GID 要求不变。

本地：packaging 90 项通过；受影响 observability 19 项执行，18 通过、1 Linux flock 跳过；Ruff 通过。真实九服务、日志与恢复仍须执行，不以本地检查宣布通过。NAS 使用固定新提交导出的包与新候选，不覆盖既有验收现场。
