# 2026-11-08 容量统计边界修复

## 线上恢复

天枢容器 `966869abb85f2c5761d1ef878871129f8dee16bbd5731cf44a3485c85c7ba546`，
应用源码 `8a0d6f452e5d187c59241aac4ca851d66592530e`，镜像
`sha256:3eea26da27156598d5ad01b3923fa5a1d43afdaf49958b9283d7563648d172f9`。
宿主守护变更源提交 `6219738b8fb6c217fc91d044a07104a3887a5540`，安装文件 SHA256
`b5c6f66764a01b608614be68626a133b5e2604301e3cd346135d84bad3ebd350`。
守护原文件、配置和锁存状态在宿主 `/srv/tianshu-core/capacity-boundary-6219738b`。
保留原容器、配置和挂载，清除原配额锁存，恢复 `unless-stopped` 后启动原容器。

守护仍统计部署树和镜像用于观察，但不再以其总和触发停机；旧配置残存的
`max_deployment_bytes` 不再生效，新冻结配置不再生成该字段。实际磁盘 20 GiB 空闲
下限及其停机行为仍保持，不是按业务类型暂停下载；后者尚未实现。原来源/身份/容器
所有权保护未改。未来发布若重新安装守护，必须包含本提交，避免恢复旧配额行为。

原下载临时文件保留在本机，约 10.74 GiB；关联任务
`0449b5f8-7328-4a69-9276-835f756ea975` 检查时为 `retry_wait/runtime_interrupted`，
源代次 1 为 `missing`，未取得归档回执。没有删除、改写或直接重放它。正常视频归档后的
回执与内容校验清理复用现有 `media.retention`，此时未以本任务完成证明清理成功。

`tests/deployment/test_core_capacity.py` 5 项在 Windows 本地通过；包括旧限额字段下业务
增长不会停机、实际空闲空间保护仍生效。安装后原容器 healthy、六角色 ready、两路 QQ
available，guard 和 host watcher active；无真实 QQ 消息测试、无业务完成或全链路收敛声明。
