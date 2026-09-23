# NAS-04 日志组件资源限制适配

基线 1f9d7d5。五日志 Compose 显式消费既有 nas-cpuset-qa-v1；保留内存、非 root、只读文件系统和网络隔离，CPU 改为固定集合，明确 PID 控制器不支持。默认可移植配置不变。

生产者绑定核心 deployment.json 中的资源声明，仅 tianshu-qa-*、loopback 和完整 isolated_test TLS。主协调配置拒绝资源覆盖，并检查固定版本日志包确实生成一致配置。Linux 消费者独立核验配置、真实 daemon 控制器与九容器的实际 HostConfig，拒绝不匹配。

验证：packaging 88 项通过、0 skip；observability 76 项执行，其中 74 通过、2 项 Linux flock 在 Windows 跳过；新增 4 项含多个资源/边界子用例。Ruff 通过。真实五日志链、备份恢复及正式发布仍未通过；这一提交不解除发布门禁。执行应消费本提交固定的日志源版本，而非旧包。

NAS Docker 自动地址池已紧张；后续联合验收须先指定并检查新网络分配方案，不清理其他产品网络或改 daemon 全局配置。
