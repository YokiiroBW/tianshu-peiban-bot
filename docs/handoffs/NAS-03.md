# NAS-03：容器首次授权输入不再等待 EOF

基线 `4be5f5f1cb76c4af47bb600cbb51d5f56eda5bc0`，独立分支 `codex/nas-bootstrap-framing`。仅修改 linux_bootstrap.py、新增真实管道回归测试及本交接。

c 轮四镜像/四依赖检查通过，platform_issue 在 35 秒超时。收尾对登记容器关闭 restart 后 SIGTERM，退出 143，因此 stop_confirmed=false、abnormal_product_exit，不能记作正常停写通过。容器 `65a286410ca6…` 保留、已停止；未重新执行原授权，也未读取/假定原数据库内容。

实际容器 Config 为 OpenStdin=true / StdinOnce=false / AttachStdin=false。原 CLI 用 stdin.buffer.read(1048577) 等待 EOF，Docker 连接的输入保持打开造成等待。修复把私有请求编码为单行 JSON 加换行，CLI readline 有界读取该行；仍保持 1 MiB 字节限额、原 25 秒产品 CLI/35 秒调用期限、原非重放标记。配置文件用的 pretty JSON 编码不变。

验证：87 项 packaging 全部通过，0 skip，35.170 秒；真实进程管道测试在 stdin 保持打开时完成，包含 Unicode/内容换行与按字节超限拒绝。Ruff/diff 检查通过。

真实 NAS 两步验证：先用 Compose 同构无产品挂载探针验证开放输入上的单行帧成功；再用当前平台镜像，临时 tmpfs 数据库/日志/设置、独立合成凭据、既有合成证书与镜像内合同调用真实平台 local issue，退出 0，得到有效 receipt。实际 attach 仍为空，stdout 容器日志有 126 字节回执；不在交接/报告记录 assertion_ref 原文。结果见协调 `.runtime/nas-wave1/nas03-real-public-cli.json`。两探针核固定 ID/归属/正常退出/无 OOM/无重启后删除；原失败 c 容器不删除、不重放。

真实探针的设置/数据放在独立 tmpfs，未验证完整四核心网络、持久存储就绪/停写或日志恢复。后续使用新 d scope/标签和固定工具快照，管理员启动完整 liveness；不修改 c 现场，不将本次局部真实 issue 成功称完整部署成功。
