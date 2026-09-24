# DEP-J 一次性合成副本许可 v1

本文件与 `drill-permit.schema.json`、`drill-permit.example.json` 固定窄输入，提交给协调核查后才实现执行器。不是根合同，不是生产放行或用户审批凭据。例子故意过期，不能直接执行。

调用者在独立步骤准备许可，以 `--permit-sha256` 绑定原字节；执行器不生成许可再自动启动。严格字段，未知字段拒绝。`issued_at`/`expires_at` 为 UTC Unix 秒，窗口至多 900 秒，执行预算不超过剩余有效期和 `max_runtime_seconds`。同一 `permit_id` 仅执行一次，失败/未知不重放。

`source_directory`、`restored_directory`、`drill_directory` 是同一显式 synthetic scope 的 deployments 直属、彼此不同的目录；drill 必须此前不存在。源 UUID 和 scope UUID 必须匹配已登记 authority；恢复目标必须 restored_disabled，备份原字节摘要及全事实/guard 校验摘要同时匹配。原 authority 丢失仍拒绝。

`inputs_directory` 是同一 scope 下 drill-inputs 直属、与三部署目录完全不重叠的人工准备目录。`inputs_sha256` 绑定其中 `inputs.json` 的原字节；其封闭 files 清单绑定专属 config/private/TLS/contracts/tools 及 core/obs Compose，不继承原秘密。`compose_sha256` 绑定两份 Compose 原字节，`config_sha256` 绑定全部 config/private/TLS 文件集合。`versions` 固定四产品完整提交，`image_ids` 固定九 owner 的本地 image ID；这不是 registry digest。

两个新项目必须使用隔离的 tianshu-qa-* 或本任务限定的 tianshu-accept-a1-* 前缀，obs=core+-obs，均不同于原项目。只允许副本内 bind，原 authority/恢复目标/inputs 均不挂载；无宿主socket、named volumes、额外owner、特权、devices、host网络、外部network、build、pull或隐式服务。network全为新的internal网络；loopback端口必须显式绑定且无自动随机外露。所有出站只在此网络内到合成录制端口；配置内出现外部URL/地址即拒绝，不把“localhost”当容器外真实服务的授权。

操作全程持有G固定的源 `.runtime-owner.lock` 同 inode 非阻塞 flock，并持有副本租约；不删除锁。复制前后复核原 authority 完整事实与九owner正常停止；原恢复目标从未启动。数据在新副本按原恢复包复制；锁仅在副本重新创建。启动后经公开HTTP功能断言，不以健康200替代撤销/遗忘/unknown语义。证据仅状态与hash，不记录敏感响应正文。

所有断言使用独立输入中的固定请求/预期，由许可绑定，不执行任意脚本、SQL、命令；若产品公开接口无法覆盖则报告 missing。正常/取消/到期均精确SIGTERM并确认退出0、非OOM及无残留；不强杀/全局kill/down/prune、不删除副本。失败保留证据、restart=no，不宣称停止完成。

输出恒有 `drill_only=true`、`release_ready=false`、`original_restore_activation=false`。原authority与禁用目标不变更；副本绝不提升为当前authority。更新版本选择和运行镜像切换分别记录。
