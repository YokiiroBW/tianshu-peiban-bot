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
| `package_locks[plugin_id]` | `PluginManager` | 同一插件的全部生命周期写操作：create、configure、bindings、enable/disable、reload、upgrade、uninstall、启动恢复，以及**安装的发布提交** |
| `locks[instance_id]` | `PluginManager` | 单实例的进程换代与调用执行 |
| `installation_lock` | `PluginManager` | 包内容准备（源码获取、依赖环境、检查子进程）与目录回收；**只在本级锁内做阻塞工作** |
| `operations_lock` | `PluginManager` | 操作账本的接受与写入；从不反向持有上述任一锁 |

统一锁序只有两条，且都单向：

1. **发布路径**：`installation_lock`（准备）→ 释放 → `package_locks[plugin_id] → installation_lock`（提交）。提交阶段重新取 `installation_lock` 是为了和回收互斥，不是把准备阶段嵌进包锁，所以慢速 git/pip 不会阻塞该插件的其它生命周期操作。
2. **移除路径**：`package_locks[plugin_id]` → `installation_lock`（回收）。与发布路径的第二段完全同序，因此不可能互相等待。

`create` 是 `package_locks → operations_lock`，与上面两条不交叉。所有等待都在同一包内，其它插件始终用自己的锁与进程。

- **安装与卸载互斥**：只有"提交（把包写成 `installed=true`）"这一步在包锁内执行，因此卸载进行中同一插件的任何版本都无法变成已安装；反之，卸载也不会在某个版本提交到一半时删目录。用例用事件屏障断言"卸载期间没有任何 `installed=true` 的包记录写入"。
- **准备完成的目录受保护**：准备阶段把已生成的不可变目录登记到 `preparing`（键为目录路径、计数式），回收只删除 `packages 引用之外且不在 preparing` 的目录；提交前再校验目录仍存在。因此不会出现"准备成功、目录被并发回收、随后提交成已安装"。
- 卸载期间该包任何实例都关闭准入（`admission[id] = "instance_unloading"`，调用返回 409 `instance_unloading`），因此不存在"卸载中又被重新启用"的窗口。
- 卸载分阶段执行并可如实报告：`operations.execution=failed` 时携带 `stopped_instances`、`remaining_instances`、`packages_marked`，绝不返回 `uninstalled:true`。
- 重复卸载命中 `fingerprint` 幂等（同 `client_request_id`）或 `already_uninstalled` 短路，不会重复删目录、不会误伤随后新建的实例。
- 回收只删除"该包目录、且不被任何仍 `installed` 的包引用、也不在 `preparing` 中"的路径，并在 `installation_lock` 内重读引用关系。
- 串行结果与真实顺序一致：安装先完成、卸载后完成时，卸载覆盖它列出的范围（含刚提交的包）；卸载先完成、安装后完成时，新包完整可用（用例同时验证包记录、目录存在与进程引用）。

## 三、上下文归属（按进程代次）

`Executions.context()` 铸造的每个句柄都记录 `instance` 与 `generation`（该子进程的 `runtime_id`），回调按二者校验：

- 子进程的回调闭包在创建时绑定它自己的 `runtime_id`（`PluginManager.child()`），因此**迟到回调只能在自己代次的句柄上成立**；旧进程退出后即使还拿着引用，也无法使用新进程的句柄。
- `revoke(identifier, generation)` 只撤该代次；`retain(identifier, generation)` 只保留现任代次。停止、退出清理、重复接管、升级失败一律按代次撤销。
- 因此正常启动、重载、升级接管、升级失败、回退、进程异常退出、取消、卸载各路径的撤销范围是：
  - 启动/重载/回退：停旧代次 → 撤旧代次句柄 → 新进程用自己的句柄初始化；新进程注册后 `retain` 清掉历史代次残留。
  - 升级接管：暂存进程在 `plugin.initialize` 时拿到的句柄属于它自己的代次；接管时不撤销它，只撤旧代次，记账的 `generation` 与它一致。
  - 升级失败：先停候选进程并撤它的句柄，再恢复快照与旧进程，候选进程无法继续写已恢复的数据。
  - 进程异常退出：`watch_worker` 只撤该进程代次的句柄；未注册的候选进程退出时同样按自身代次撤销。
  - 取消：调用自身的 `finally` 撤该调用句柄；需要停进程时按其代次撤销。
  - 卸载：先撤贡献与页面票据，再按代次停进程与撤销句柄。
- 不放宽任何校验：`callback` 仍要求实例匹配、句柄未过期、期望状态与授权有效；所有权检查只是附加的、更严格的条件。

## 四、失败候选的关闭与退出确认

升级失败时，候选子进程可能**从未注册**到 `workers[instance_id]`，但它仍是一个持有 RPC 通道、句柄和插件自有任务的活动操作系统进程。因此失败分支的顺序是：

1. `stop_worker(identifier, "upgrade_abandoned", process)`：撤销该候选**自身代次**的句柄并 `process.close()`（`plugin.stop` → `terminate` → `kill` 后 `await process.wait()`）；
2. `process.exited`（`returncode is not None`）确认操作系统子进程已经退出；
3. 恢复实例快照；
4. 撤销该实例残留句柄；
5. 需要时重启旧版本，并按真实结果写 `rolled_back`/`restored_version`/`restore_error`。

- `stop_worker(..., process)` 只处理**指定**进程：若 `workers` 中当前登记的是另一台替代进程，它不会被关闭、其句柄不会被撤销、其资源不会被释放。
- `Process.close()` 带 `closed` 幂等标记：失败分支与 `finally` 都会清理同一子进程，第二次调用是安全的空操作。
- 候选关闭失败或无法确认退出时：`candidate_closed=false`、`candidate_error` 记录 `candidate_still_running` 或清理异常码，`rolled_back` 与 `restored_version` 不再声称安全恢复；原始失败码仍在 `error.code`，原始 `failed_version` 不变，请求/回执/unknown、实例身份、配置、版本与 CAS 均不受影响。

## 四之二、未确认退出时停止恢复

失败分支是**两条互斥路径**，由"指定失败候选是否确认退出"决定，不再汇合到无条件恢复：

- **已确认退出** → 撤销该候选自身代次句柄 → 关闭 → `candidate_gone` → 恢复快照 → 撤销残留句柄 → 必要时启动旧版本 → 按真实结果记账。
- **未确认退出** → 记录原升级失败与清理失败 → **不调用 `repository.restore`** → **不启动旧版本** → 不报告恢复完成 → 返回 `instance_cleanup_pending`。

退出证明只接受真实进程事实：`Process.exited`（`returncode is not None`），或在句柄已回收/跨宿主时对**记录在账本的候选 pid** 做只读探测且仅接受"不存在"结论。`close()` 返回、RPC 断开、`closed` 标记、句柄撤销都不构成退出证明；关闭抛错但进程确实已退出时按真实退出事实处理，`candidate_error` 只记录清理异常、不改写原失败码。

限制通过既有准入统一生效：

| 入口 | 行为 |
| --- | --- |
| `Executions.admit` | `admission[instance]=instance_cleanup_pending` 时拒绝新调用 |
| `worker()` | 同样拒绝，覆盖任何间接启动路径 |
| `state`/`configure`/`bindings`/`upgrade`/`restart`/`uninstall` | 先 `settle_pending`；候选未退保持拒绝，退出了走同一条受控恢复 |
| `start()` | 从操作账本读回 `candidate_closed=false`/`candidate_pid`，恢复限制与待清理记录，**不在启动时假设恢复** |
| `finally` | 只做幂等清理（`Process.close` 的 `closed` 标记），不解除限制、不恢复数据、不覆盖原失败 |

影响范围只限该实例：不持有全局锁，其它插件照常服务。

## 五、升级的真实成功条件

- `plugin.start` 成功且 RPC 通道仍在（`process.alive`）之后，该暂存进程直接注册为 `workers[instance_id]` 的服务进程；记账的 `generation` 就是这台进程（用例断言 `plugin_runtime.generation == operations.generation`）。
- 该进程注册失败、或提交账本失败时一律记 `execution=failed`：先停掉已接管的暂存进程，恢复实例快照并重启旧版本；`rolled_back`/`restored_version` 只在旧版本**真的恢复可用**时为真，否则 `rolled_back=false`、`restored_version=null` 并给出 `restore_error`，原始失败码保留在 `error.code`。
- 升级接口把该操作的失败以 `AppError`（`details.operation_id/execution/error`）返回，页面据此显示失败原因，不会把失败呈现成成功。
- 升级窗口内该实例拒绝新调用（409 `instance_upgrading`）。**升级前已受理但尚未执行的调用**按原身份结算为 `interrupted`（写操作 `effect=unknown`、读操作 `none`，`error.code=instance_upgraded`，`retriable=false`），绝不重新启动旧版本、也绝不重放外部写；已在旧进程里执行的调用按自身结果收尾，且`execute()` 在锁内复读调用文档，包版本已变更时不再启动任何进程。

## 六、`purge_data` 的真实范围

在单个事务内：

1. 实例文档的 `config` 清空、`bindings` 清空、`revision+1`、写入 `data_purged_at`；**实例身份（id/package/name/version）保留**，因此重装同包仍然回到同一实例，但不会把已清除的配置或绑定带回来；
2. 删除 `plugin_state`、`plugin_secrets`、`plugin_logs` 中该实例的行；
3. 该实例的 `plugin_schedules` 置 `enabled=false`、`state=removed`、`code=plugin_data_purged`，并清除 `dispatch`/`previous_dispatch`/`pending`/`wake_generation`，不再有后续触发；
4. 删除 `capabilities.plugin_consumptions` 中该实例的行。

不删除：`capabilities.actions`（Action/Attempt/Receipt/`unknown` 等历史执行证明）、请求、回执、宿主配置、共享文件、外部资源、其他插件或包的数据。页面文案与上述范围一致，并写明"该插件包下所有实例"。

## 七、实现位置

- `src/tianshu/capabilities/plugins/manager.py`：`child()`（回调绑定自身代次）、`worker(prepared=...)`（只撤旧代次）、`stop_worker`（按代次撤销，并对未注册的指定进程关闭）、包级锁与准入、安装的"准备/提交"两段、实例投影、启停、`restart`、`upgrade`、`uninstall`（互斥撤销与回收）、`settle_accepted`
- `src/tianshu/capabilities/plugins/execution.py`：`context(..., generation=)`、`revoke(identifier, generation=None)`、`retain(identifier, generation)`、`callback(..., generation=)`、按实例的准入拒绝码（`refusal`）、已受理调用的原身份结算
- `src/tianshu/capabilities/plugins/service.py`: `revoke(instance_id)` 立即撤下该实例的 catalog handler
- `src/tianshu/capabilities/plugins/web.py`：`reload` 路由、卸载清理页面票据、页面票据重新校验包仍安装且摘要未变
- `src/tianshu/capabilities/plugins/control.py`：`reload` 与 `purge_data` 请求 schema（同时生成 OpenAPI）
- `src/tianshu/capabilities/plugins/packages.py`：`reclaim` 回收不可变包目录（跳过仍 installed 与 `preparing` 中的目录）
- `src/tianshu/capabilities/plugins/process.py`：`Process.exited`（`returncode` 判定操作系统退出）、`close()` 的 `closed` 幂等标记、`os_process_exited(pid)` 只读探测（仅证明不存在，不杀进程）
- `src/tianshu/capabilities/plugins/repository.py`：`restore` 兼容从操作账本读回的行（JSON 对象按列名读取）
- `src/tianshu/capabilities/plugins/repository.py`：`purge`（实例自有数据）、`accepted`（未执行调用）、注入 owner 不再遮蔽 `runtime()` 方法
- `web/src/features/plugins/`：重载按钮、运行进程/代次显示、升级/回退按钮、卸载与"卸载并清除数据"入口及与后端一致的说明

## 八、边界与证据

- 不重启容器、不重启 `independent` 角色、不中断其他插件；重载/升级期间只允许目标插件短暂不可用。
- 旧代次迟到退出由 `watch_worker` 比对 `workers[id]` 中的实际进程对象，不会停掉替代进程；未注册的候选进程退出时也只撤它自己的句柄。
- 插件从 `initialize`/`start` 起使用 `self.ctx`（启动上下文）调用 `state.kv` 等宿主能力，接管后该句柄仍属于同一子进程并继续有效；用例在真实子进程上验证。
- 真实子进程、真实包目录与真实 PostgreSQL 账本验证见 `tests/capabilities/test_plugin_online_lifecycle.py`；契约级与并发编排验证见 `tests/capabilities/test_plugin_lifecycle_control.py`；页面行为见 `web/tests/plugin-lifecycle.spec.ts`。
- 已安装版本不可变：源码改动必须重新准备为同一 ID 的新版本；同一 digest 重复准备复用既有目录与环境。
- 迁移限制：`plugin.migrate_data` 若声明拒绝回退（默认实现即拒绝跨 data_version），页面如实提示"回退会出现数据不匹配"，系统不伪称可以安全回退。
