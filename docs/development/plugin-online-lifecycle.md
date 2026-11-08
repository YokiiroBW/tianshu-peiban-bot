# 插件在线生命周期与热重载

## 一、对象与现状（PL-00）

插件系统区分四个对象，它们各有唯一账本，不新增平行业务状态机：

| 对象 | 账本 | 说明 |
| --- | --- | --- |
| 包 | `capability_plugins.plugin_packages` | 插件 ID + 版本 + 内容摘要；`root` 为不可变目录，`digest` 为内容 SHA-256 |
| 版本 | 包的 `manifest.version` / `config_version` / `data_version` | 已安装版本不可变；新源码必须以新版本重新安装 |
| 实例 | `capability_plugins.plugin_instances` | 身份、配置、密钥引用、绑定、期望状态与 `revision`（CAS） |
| 配置与数据 | `plugin_instances.document`、`plugin_secrets`、`plugin_state` | 实例自有；重载与升级保留，卸载默认保留 |
| 运行进程 | `workers[instance_id]` + `plugin_runtime` 观察 | 每个实例一个真实子进程，代次（`runtime_id`/`pid`）可观察 |

操作语义（每个操作归属、成功条件、失败后状态）：

- **安装（install）**：准备包（内置 / ZIP / Artifact / 服务器目录 / 固定提交 Git），在独立 `.environment` 创建虚拟环境，安装描述与 `contributions` 校验后写入包账本。成功条件：`operations.execution=succeeded` 且包可读；失败：`execution=failed` 且不会成为可启用版本；同一 `client_request_id` 幂等返回原操作。
- **启用（enable）**：CAS 写 `desired_state=enabled` 并启动该实例进程、播种定时任务、开放贡献。成功条件：`state=ready`；失败：`state=error`/`blocked`/`pending_configuration` 并保留期望状态供页面显示。
- **停用（disable）**：撤销入口（catalog handler、事件、定时任务停止触发）并停止目标实例进程；其他插件继续。
- **重载（reload）**：使用当前已安装版本重建该实例进程，实例身份、配置、密钥引用、绑定与持久数据不变；`revision` 递增，旧进程迟到退出不会误停新代次。
- **升级／回退（upgrade）**：切换目标实例版本，先以新包启动、迁移配置与数据、执行 `plugin.start` 健康确认，然后一次性 CAS 提交；任一环节失败恢复快照并如实报告 `rolled_back` 与 `restored_version`。回退到仍安装、兼容的旧版本即一次普通 `upgrade`。
- **卸载（uninstall）**：停止该包全部实例、撤销贡献与页面票据、标记包 `installed=false`、回收无引用的包目录；默认保留实例配置与业务数据；`purge_data=true` 时只清除该实例自有数据。

宿主进程（容器内的 `independent` 角色）在以上全部操作中持续运行；操作只影响目标实例与其子进程。

## 二、实现位置

- `src/tianshu/capabilities/plugins/manager.py`：安装、实例投影、启停、`restart`（在线重载）、`upgrade`（升级/回退）、`uninstall`（撤销与回收）
- `src/tianshu/capabilities/plugins/execution.py`：`admission` 准入闸门（按实例），原有 attempt/effect/unknown 语义不变
- `src/tianshu/capabilities/plugins/service.py`: `revoke(instance_id)` 立即撤下该实例的 catalog handler
- `src/tianshu/capabilities/plugins/web.py`：`reload` 路由、卸载清理页面票据、页面票据重新校验包仍安装且摘要未变
- `src/tianshu/capabilities/plugins/control.py`：`reload` 与 `purge_data` 请求 schema（同时生成 OpenAPI）
- `src/tianshu/capabilities/plugins/packages.py`：`reclaim` 回收不可变包目录
- `src/tianshu/capabilities/plugins/repository.py`：`purge`（实例自有数据）、注入 owner 不再遮蔽 `runtime()` 方法
- `web/src/features/plugins/`：重载按钮、运行进程/代次显示、升级/回退按钮、卸载与“卸载并清除数据”入口与说明

## 三、边界与证据

- 不重启容器、不重启 `independent` 角色、不中断其他插件；重载期间只允许目标插件短暂不可用。
- 新调用在切换窗口内对该实例返回 `instance_reloading`；已受理的调用保留原 Action/attempt，不重放外部写。
- 旧代次迟到退出由 `watch_worker` 比对 `workers[id]` 中的实际进程对象，不会停掉替代进程。
- 真实子进程、真实包目录与真实 PostgreSQL 账本验证见 `tests/capabilities/test_plugin_online_lifecycle.py`；契约级验证见 `tests/capabilities/test_plugin_lifecycle_control.py`。
- 已安装版本不可变：源码改动必须重新准备为同一 ID 的新版本；同一 digest 重复准备复用既有目录与环境。
- 迁移限制：`plugin.migrate_data` 若声明拒绝回退（默认实现即拒绝跨 data_version），页面如实提示“回退会出现数据不匹配”，系统不伪称可以安全回退。
