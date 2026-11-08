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
- **升级／回退（upgrade）**：先用新包起一个暂存进程，依次执行 `migrate_config` / `initialize` / `migrate_data` / `plugin.start`；**该进程同时是之后承接调用的服务进程**（不重复 `plugin.start`），只有它真的接管后账本才提交成功。
- **卸载（uninstall）**：在该插件包的包级生命周期锁内停止全部实例、撤销贡献与页面票据、标记包 `installed=false`、回收无引用的包目录；默认保留实例配置与业务数据；`purge_data=true` 时清除该包下全部实例的插件自有数据。

宿主进程（容器内的 `independent` 角色）在以上全部操作中持续运行；操作只影响目标实例与其子进程。

## 二、生命周期协调（返修后）

三级锁，顺序固定为 **包级锁 → 实例锁 → （执行中的调用自行持有实例锁）**，任何持有者都不申请反向顺序，因此不会互相等待：

| 锁 | 位置 | 作用域 |
| --- | --- | --- |
| `package_locks[plugin_id]` | `PluginManager` | 同一插件的 install 之后的所有生命周期写操作：create、configure、bindings、enable/disable、reload、upgrade、uninstall、启动恢复 |
| `locks[instance_id]` | `PluginManager` | 单实例的进程换代与调用执行 |
| `operations_lock` / `installation_lock` | `PluginManager` | 操作账本写入与包准备/回收（不与包级锁反向嵌套：`create` 是 `package_locks → operations_lock` 单向前进） |

- 卸载期间该包任何实例都关闭准入（`admission[id] = "instance_unloading"`，调用返回 409 `instance_unloading`），因此不存在"卸载中又被重新启用"的窗口。
- 卸载分阶段执行并可如实报告：`operations.execution=failed` 时携带 `stopped_instances`、`remaining_instances`、`packages_marked`，绝不返回 `uninstalled:true`。
- 重复卸载命中 `fingerprint` 幂等（同 `client_request_id`）或 `already_uninstalled` 短路，不会重复删目录、不会误伤随后新建的实例。
- 回收只删除"该包目录、且不被任何仍 `installed` 的包引用"的路径，并在 `installation_lock` 内重读引用关系，避免与并发安装竞争。

## 三、升级的真实成功条件

- `plugin.start` 成功且 RPC 通道仍在（`process.alive`）之后，该暂存进程直接注册为 `workers[instance_id]` 的服务进程；记账的 `generation` 就是这台进程（用例断言 `plugin_runtime.generation == operations.generation`）。
- 该进程注册失败、或提交账本失败时一律记 `execution=failed`：先停掉已接管的暂存进程，恢复实例快照并重启旧版本；`rolled_back`/`restored_version` 只在旧版本**真的恢复可用**时为真，否则 `rolled_back=false`、`restored_version=null` 并给出 `restore_error`，原始失败码保留在 `error.code`。
- 升级接口把该操作的失败以 `AppError`（`details.operation_id/execution/error`）返回，页面据此显示失败原因，不会把失败呈现成成功。
- 升级窗口内该实例拒绝新调用（409 `instance_upgrading`）。**升级前已受理但尚未执行的调用**按原身份结算为 `interrupted`（写操作 `effect=unknown`、读操作 `none`，`error.code=instance_upgraded`，`retriable=false`），绝不重新启动旧版本、也绝不重放外部写；已在旧进程里执行的调用按自身结果收尾，且`execute()` 在锁内复读调用文档，包版本已变更时不再启动任何进程。

## 四、`purge_data` 的真实范围

在单个事务内：

1. 实例文档的 `config` 清空、`bindings` 清空、`revision+1`、写入 `data_purged_at`；**实例身份（id/package/name/version）保留**，因此重装同包仍然回到同一实例，但不会把已清除的配置或绑定带回来；
2. 删除 `plugin_state`、`plugin_secrets`、`plugin_logs` 中该实例的行；
3. 该实例的 `plugin_schedules` 置 `enabled=false`、`state=removed`、`code=plugin_data_purged`，并清除 `dispatch`/`previous_dispatch`/`pending`/`wake_generation`，不再有后续触发；
4. 删除 `capabilities.plugin_consumptions` 中该实例的行。

不删除：`capabilities.actions`（Action/Attempt/Receipt/`unknown` 等历史执行证明）、请求、回执、宿主配置、共享文件、外部资源、其他插件或包的数据。页面文案与上述范围一致，并写明"该插件包下所有实例"。

## 五、实现位置

- `src/tianshu/capabilities/plugins/manager.py`：包级锁与准入、安装、实例投影、启停、`restart`（在线重载）、`upgrade`（升级/回退与真实恢复）、`uninstall`（互斥撤销与回收）、`settle_accepted`
- `src/tianshu/capabilities/plugins/execution.py`：按实例的准入拒绝码（`refusal`）、已受理调用的原身份结算
- `src/tianshu/capabilities/plugins/service.py`: `revoke(instance_id)` 立即撤下该实例的 catalog handler
- `src/tianshu/capabilities/plugins/web.py`：`reload` 路由、卸载清理页面票据、页面票据重新校验包仍安装且摘要未变
- `src/tianshu/capabilities/plugins/control.py`：`reload` 与 `purge_data` 请求 schema（同时生成 OpenAPI）
- `src/tianshu/capabilities/plugins/packages.py`：`reclaim` 回收不可变包目录
- `src/tianshu/capabilities/plugins/repository.py`：`purge`（实例自有数据）、`accepted`（未执行调用）、注入 owner 不再遮蔽 `runtime()` 方法
- `web/src/features/plugins/`：重载按钮、运行进程/代次显示、升级/回退按钮、卸载与"卸载并清除数据"入口及与后端一致的说明

## 六、边界与证据

- 不重启容器、不重启 `independent` 角色、不中断其他插件；重载/升级期间只允许目标插件短暂不可用。
- 旧代次迟到退出由 `watch_worker` 比对 `workers[id]` 中的实际进程对象，不会停掉替代进程。
- 真实子进程、真实包目录与真实 PostgreSQL 账本验证见 `tests/capabilities/test_plugin_online_lifecycle.py`；契约级与并发编排验证见 `tests/capabilities/test_plugin_lifecycle_control.py`；页面行为见 `web/tests/plugin-lifecycle.spec.ts`。
- 已安装版本不可变：源码改动必须重新准备为同一 ID 的新版本；同一 digest 重复准备复用既有目录与环境。
- 迁移限制：`plugin.migrate_data` 若声明拒绝回退（默认实现即拒绝跨 data_version），页面如实提示"回退会出现数据不匹配"，系统不伪称可以安全回退。
