# DEP-G runtime identity 1.0.0（固定消费者接口）

输出 `reports/runtime-identity.json`，JSON 对象 `schema_version=dep-g-runtime/1.0.0`。
这是一次隔离部署的观测身份，不是发布许可；卷合同仍为 release-manifest 1.1.0。
DEP-I/J 必须校验本文件绑定的固定 Git 提交及报告原字节 SHA256，不执行报告里的任意命令。

字段：

- `scope`: `synthetic_only=true`, `release_ready=false`, `nas_acceptance=false`。
  `host.system` 为实际执行宿主系统；未执行为 null，不以该 scope 字段否认宿主事实。
- `release_id`、`deployment_root`（Linux 绝对路径）、`project_name`（tianshu-qa-*）。
- `projects.core/observability`: `name`, `directory`, `compose_file`, `lifecycle_owner`。
  core 名字等于 project_name、目录 deployment_root、文件 compose.json；observability 名字
  为 project_name 加 -obs、目录 deployment_root/observability、文件 compose.yaml。
  lifecycle_owner 为 `coordinator`；任何执行器必须先独占租约并确认 Compose project 标签，
  不得互相 start/stop。DEP-G 只在自己本次执行租约内启动核心栈，结束停止并保留容器/卷/证据。
  每项目另带原文件 `compose_sha256`、解析后 `compose_json`（全部挂载包括只读绑定）、
  `compose_canonical_sha256`；规范化算法为 Python JSON sort_keys=True,
  separators=(',', ':'), ensure_ascii=False, allow_nan=False 后 UTF-8，无末尾换行。
- `integrity`: `release_manifest_sha256`, `bundle_integrity_sha256`,
  `compose_sha256`, `observability_compose_sha256`（未配置时 null）。
  消费者使用前重新核对应文件及 bundle inventory，不仅相信报告字符串。
  `config_sha256` 覆盖 config/contracts/tools 原字节；`sources` 为四产品 repo/固定commit。
- `recovery_inventory`: null 或 `{path, sha256}`；`authority`: null 或
  `{identity, evidence_path, evidence_sha256}`。本生成器不知道恢复库存及 authority 证明，
  故明确 null，J 必须拒绝据此放行，不能用配置中主体名猜造当前 authority。
- `services`: 以四产品及五个 obs-* owner 为键。每项 `project`, `image_reference`,
  `image_id`, `repo_digests`, `platform`, `uid`, `gid`, `container_id`, `status`。
  未观测值 null，repo_digests 未观测为 []；status 是 `not_observed` 或 `observed`。
  image_id 是 Docker 本地配置 ID `sha256:...`，绝不能填入 release image digest。
  repo_digests 只接受 Docker inspect 实际返回的 repository@sha256:...，空数组合法且明确
  表示无 registry digest 证据；platform 观测值必须 linux/amd64。uid/gid 是实际容器进程验证值。
  `status` 专指容器运行身份：允许 not_observed 且 image_id/repo_digests/platform 已有值，
  表示只执行了本机 Docker image inspect，此时 uid/gid/container_id 仍为 null。
  执行报告 `image_<service>` 步骤记录成功与否，并以 `runtime_identity_sha256` 绑定本文件原字节。
  配置了日志栈时 G 仅 inspect 五个日志镜像，不启动这些容器、不隐式拉取；本机无镜像则失败，
  协调者可在明确隔离执行计划内先准备镜像。I 不得猜补 ID。
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

机器输入以 `runtime-identity.schema.json` 为准；全部顶层字段必填，未知值明确 null，
不得用缺字段表示成功。`runtime-identity.example.json` 由实际生成器生成、替换合成根路径，
用于理解 planned 形状，不是运行证据。Python 消费者可复用 `runtime_identity.validate`，
此外仍须对实际文件、所有挂载、九 owner 作独立核验。

G/I/J 共同锁只有 `<deployment_root>/.runtime-owner.lock`。调用
`with runtime_identity.lifecycle_lease(root):`，Linux `flock(LOCK_EX|LOCK_NB)`，
不可阻塞等待、不可 unlink/替换锁文件；退出/崩溃由内核释放。锁覆盖检查、写入、启动、
停止及证据落盘整个操作。取得锁不代表启动许可；I 必须先确认四应用 owner 均已停止
才可写合成日志，J 必须确认九 owner 正常停写。不得为每个执行器另取不同锁。

为配合 J，可将新部署直接初始化到显式新 synthetic scope 的 `deployments/<source>`。
root 必须是全新目录，project_name 仍为 tianshu-qa-*。G 不移动现有部署；J 的独立
prepare 登记负责恢复库存和 authority 证据，本身份生成器继续保留 null。
