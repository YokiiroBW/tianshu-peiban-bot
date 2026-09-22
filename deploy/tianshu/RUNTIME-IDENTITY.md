# DEP-G runtime identity 1.0.0（固定消费者接口）

输出 `reports/runtime-identity.json`，JSON 对象 `schema_version=dep-g-runtime/1.0.0`。
这是一次隔离部署的观测身份，不是发布许可；卷合同仍为 release-manifest 1.1.0。
DEP-I/J 必须校验本文件绑定的固定 Git 提交及报告原字节 SHA256，不执行报告里的任意命令。

字段：

- `scope`: `synthetic_only=true`, `release_ready=false`, `nas=false`。
- `release_id`、`deployment_root`（Linux 绝对路径）、`project_name`（tianshu-qa-*）。
- `projects.core/observability`: `name`, `directory`, `compose_file`, `lifecycle_owner`。
  core 名字等于 project_name、目录 deployment_root、文件 compose.json；observability 名字
  为 project_name 加 -obs、目录 deployment_root/observability、文件 compose.yaml。
  lifecycle_owner 为 `coordinator`；任何执行器必须先独占租约并确认 Compose project 标签，
  不得互相 start/stop。DEP-G 只在自己本次执行租约内启动核心栈，结束停止并保留容器/卷/证据。
- `integrity`: `release_manifest_sha256`, `bundle_integrity_sha256`,
  `compose_sha256`, `observability_compose_sha256`（未配置时 null）。
  消费者使用前重新核对应文件及 bundle inventory，不仅相信报告字符串。
- `services`: 以四产品及五个 obs-* owner 为键。每项 `project`, `image_reference`,
  `image_id`, `repo_digests`, `platform`, `uid`, `gid`, `container_id`, `status`。
  未观测值 null，repo_digests 未观测为 []；status 是 `not_observed` 或 `observed`。
  image_id 是 Docker 本地配置 ID `sha256:...`，绝不能填入 release image digest。
  repo_digests 只接受 Docker inspect 实际返回的 repository@sha256:...，空数组合法且明确
  表示无 registry digest 证据；platform 观测值必须 linux/amd64。uid/gid 是实际容器进程验证值。
- `mounts`: 原清单所有 mount=true 项，每项 `id`, `host_path`（绝对）、`container_path`,
  `owner_service`, `backup_group`, `uid`, `gid`。uid/gid 为 Linux stat 观测；未执行为 null。
  五个观测卷及其 owner/backup_group 完全沿用 VOLUME-INTERFACE.md；只读消费者不是 owner。
- `state`: `planned`、`stopped` 或 `stop_unconfirmed`；`activation_authorized=false`。
  stopped 仅表示本执行器观察到核心容器停止，不是九 writer 正常停写/备份许可。
  J 仍须独立验证九 owner 的退出码、正常关闭和 authority；恢复后禁止自动激活。

计划输出不能当 Linux 证据。观测服务仍不代表 readiness 成功；执行报告分别记录构建、
架构、正式依赖、运行 UID/GID、鉴权 readiness 和停止结果。身份文件不包含 env、token、
来源 ref、密码、数据库或原始 Docker inspect。失败和未知结果保留证据，不重签、不重放，
没有 down -v、prune、无归属 kill 或删除。
