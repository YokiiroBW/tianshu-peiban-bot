# 插件在线安装、重载、升级、回退、卸载交付（PLUGIN-ONLINE-LIFECYCLE-20261108）

## 0. 提交

- 分支：`codex/plugin-online-lifecycle-20261108`
- 工作树：`C:\Users\Administrator\.codex\worktrees\plugin-online-lifecycle-20261108\tianshu-peiban-bot`
- 首版实现：`0745c42c8d79a4ea52c955248ab19b45895a1834`（实现）→ `4a88e2558f86673aac6e578ba4dcc7df0b485585`（用例）→ 文档收尾
- 本轮返修基线：`2a18fd17f591dbeb567f44d4ae7e577200d0b601`（返修提交号见分支 HEAD；本文档不记录自身提交）
- 基线分支源自 `37d4cb50`，**不含此后的网页在线发布与模型绑定交付**，因此本分支不用于覆盖现有部署基线。

## 1. 结论摘要

在既有插件系统上演进，没有新建插件平台，没有改动 Memory/Life/Conversation 或六角色架构，也没有改动插件 SDK 合同、共享装配、迁移或部署根文件。

本轮返修针对三个已确认缺陷：

1. **升级的"成功"必须等于最终服务进程**：暂存进程 `plugin.start` 后**直接接管为服务进程**（不再关闭后另起一个进程），只有它接管后才记账成功；失败一律不记成功并进入真实恢复，`rolled_back` 只在旧版本真的可用时为真。
2. **卸载与其它生命周期互斥**：引入包级生命周期锁 + 实例级锁的固定顺序，卸载全程关闭该包实例准入，分阶段如实报告，重复卸载幂等且不重复删目录。
3. **`purge_data` 与页面承诺一致**：在同一事务内清空该包下实例的配置、绑定、密钥引用、状态、日志与定时任务，保留执行证明（Action/Attempt/Receipt/unknown），页面文案与后端范围一致并写明"该包下所有实例"。

## 2. 三个缺陷的根因与修复位置

| 缺陷 | 原根因 | 修复位置（当前实现） |
| --- | --- | --- |
| A 升级成功≠服务进程 | 暂存进程只做健康检查，成功记账后 `finally` 关闭它；再新建 worker 承接调用，其失败被 `except (...): pass` 吞掉——于是"新进程起不来"仍显示 `execution=succeeded`，同时 `rolled_back` 无条件写 `True` | `manager._upgrade`：暂存进程 `plugin.start` 成功后 `worker(instance, prepared=process)` 直接注册，记账 `generation=process.runtime_id`；失败路径先停已接管的暂存进程，再 `repository.restore` 并重启旧版本，按实际结果写 `rolled_back/restored_version/restore_error`；`manager.raise_operation_failure` 把失败以 `AppError(details.operation_id/execution/error)` 返回 |
| A2 已受理调用会重启旧版本 | `executions.execute` 在实例锁内用**入参快照**的旧版本调 `host.worker(instance)`，切换后仍可能把旧版本进程拉起来 | `manager._upgrade` 先 `settle_accepted`（`repository.accepted` + `update_call`，写操作 `unknown`、读 `none`、`instance_upgraded`）；`executions.execute` 在锁内复读调用文档，非 `accepted` 或包版本已变则直接结算返回，不启动任何进程 |
| A3 升级没有准入 | `upgrade` 注释称关闭准入但代码没有 `admission.add`；`upgrade` 路径完全没有使用准入 | `manager._upgrade` 在 `try` 前后维护 `admission[id]="instance_upgrading"`；`execution.refusal` 让新调用与解冻后的旧调用返回同一拒绝码 |
| B 卸载与其他操作非互斥 | `uninstall` 逐个 `state(id, False, rev)` 各自拿放**实例锁**，释放期间另一请求可重新启用；随后仍标记 `installed=false` 并回收目录，产生"实例 enabled + 进程在跑 + 目录已删" | `manager.uninstall/_uninstall`：整个操作在 `package_locks[plugin_id]` 内；`create/configure/bindings/state/restart/upgrade/启动恢复` 均在该包级锁下；卸载期间 `admission[id]="instance_unloading"`；`_disable` 拆出供包锁内复用；失败写 `stopped_instances/remaining_instances/packages_marked`；回收前在 `installation_lock` 内重读"仍 installed 的包"的目录，避免删掉并发重装刚准备的同一目录；重复卸载读 `fingerprint` 幂等或 `already_uninstalled` 短路 |
| C purge 与页面承诺不一致 | `repository.purge` 只删 `plugin_state/plugin_secrets/plugin_logs/plugin_schedules/plugin_consumptions`，实例文档的 `config`/`bindings` 原样保留；页面却宣称"已清除插件自有配置" | `repository.purge` 在同一事务内清空实例文档 `config`/`bindings`、`revision+1`、`data_purged_at`，并停用该实例定时任务（`enabled=false`，清 `dispatch/previous_dispatch/pending/wake_generation`）；保留 Action/Attempt/Receipt/unknown；`web/src/features/plugins/InstancePanel.tsx` 文案改写为真实范围与"该包下所有实例" |

## 3. 生命周期状态

| 能力 | 状态 | 说明 |
| --- | --- | --- |
| 在线安装（内置/ZIP/Artifact/目录/Git） | 已实现（复用既有路径） | 独立 `tianshu-plugin-install` 执行线程；失败不成为可启用版本；同 `client_request_id` 幂等 |
| 启用 / 停用 | 已实现（补齐撤销） | 停用先把该实例 catalog 入口撤下再停进程；两者都在包级锁内 |
| 显式在线重载 | 已实现 | `plugins.reload` 控制操作 + 页面按钮；实例 identity/config/密钥/绑定/持久数据不变，只换代次 |
| 升级 A→B | 已实现（本轮返修） | 暂存进程 `migrate_config → initialize → migrate_data → plugin.start` → **同一进程接管** → CAS 提交 |
| 失败恢复 | 已实现（本轮返修） | 停接管进程 → 恢复快照 → 重启旧版本；旧版本可服务才 `rolled_back=true` |
| 回退 B→A | 已实现 | 对仍安装且兼容的旧版本执行同一条升级路径 |
| 卸载 | 已实现（本轮返修） | 包级互斥：停全部实例 → 撤入口 → 清票据 → `installed=false` → 回收无引用目录 |
| 卸载清除数据 | 已实现（本轮返修） | 单事务清配置/绑定/密钥/状态/日志/定时任务，保留身份与执行证明 |
| 动态贡献撤销 | 已实现 | 重载/升级/停用/卸载都会即时撤下该实例 handler；页面票据重新校验包仍安装且摘要一致 |
| 旧代次迟到退出 | 已实现 | `watch_worker` 比对 `workers[id]` 中的实际进程对象 |

## 4. 变更清单（本轮返修）

后端：

- `src/tianshu/capabilities/plugins/manager.py`
  - `package_locks`（按插件 ID）+ `admission` 由 set 变为"实例 → 拒绝码"字典；
  - `worker(instance, *, prepared=None)`：可直接接管已启动、已初始化、已 `start` 的暂存进程，避免二次 `plugin.start`；
  - `restart/configure/bindings/state/create/upgrade/uninstall/启动恢复` 全部纳入固定锁序；
  - `_uninstall` 分阶段执行、失败带阶段证据、幂等短路、回收前重读引用；
  - `_upgrade` 改为"暂存进程接管 + 真实恢复 + 准确 `rolled_back`"，并在失败时用 `raise_operation_failure` 返回 `AppError`；
  - `settle_accepted` 结算升级前已受理但未执行的调用。
- `src/tianshu/capabilities/plugins/execution.py`：`refusal(identifier)` 取代 `blocked`；`admit(...)` 支持 `defer`，`dispatch` 独立；`execute` 在流内复读文档并按原身份结算。
- `src/tianshu/capabilities/plugins/repository.py`：`purge` 扩展真实范围；新增 `accepted(instance)`。

前端：

- `web/src/features/plugins/InstancePanel.tsx`：升级说明改为"由新进程自己的启动确认成功后才算切换"；两条卸载说明分别写明保留与清除的真实范围、"该包下所有实例"、被拒绝的并发操作；
- `web/src/features/plugins/PluginCenter.tsx`：升级提示指向实际承接调用的运行进程；
- `web/src/features/plugins/types.ts`：`Installation` 补充 `from_version/to_version/generation/failed_version/restore_error/already_uninstalled` 等字段。

测试：

- `tests/capabilities/test_plugin_online_lifecycle.py`：新增 8 个场景（启动失败不记成功、恢复失败不谎报回退、已受理调用按原身份结算、升级成功代次等于服务进程、保留配置卸载、清除数据卸载、卸载与启用互斥、卸载与重装串行、接管后账本冲突）；
- `tests/capabilities/test_plugin_lifecycle_control.py`：新增准入拒绝码、卸载准入、以及带事件屏障的"卸载 vs 启用"确定性并发用例；
- `web/tests/plugin-lifecycle.spec.ts`：桌面+手机 3 条浏览器用例（重载提示/失败不显示成功、升级仅在接管后提示、卸载与清除范围）。

## 5. 实际验证（本轮运行）

环境：Windows、仓库自带 `.venv`（Python 3.12）、临时 PostgreSQL 17.2 + pgvector 0.8.0（隔离实例，未连接生产）、真实插件子进程。

| 验证 | 命令/范围 | 结果 |
| --- | --- | --- |
| 插件相关回归 | `pytest test_plugin_runtime.py test_plugin_model_timing.py test_rb_independent_plugins.py test_plugin_channel_port.py test_catalog_lifecycle.py test_plugin_lifecycle_control.py test_plugin_online_lifecycle.py -q` | **51 passed** |
| 真实在线生命周期 | `tests/capabilities/test_plugin_online_lifecycle.py` | **14 passed**（含 8 个新增场景） |
| 契约与并发编排 | `tests/capabilities/test_plugin_lifecycle_control.py` | **10 passed** |
| 新增用例在旧实现上的失败证明 | 同一测试文件在 `2a18fd17` 工作树上运行 | **11 failed / 13 passed**（旧实现缺少 `refusal`、不结算已受理调用、不接管暂存进程、purge 不清配置、卸载非互斥） |
| 前端类型检查 | `tsc -p tsconfig.json --noEmit` | exit 0 |
| 前端构建 | `vite build` | exit 0 |
| 浏览器用例（桌面 1440×1000 + 手机 390×844） | `playwright test plugin-lifecycle.spec.ts` | **6 passed**（3 条 × 2 视口） |
| 既有浏览器用例未回归 | `playwright test plugin-lifecycle.spec.ts rb-independent.spec.ts` | **10 passed** |
| Python 字节编译 | `compileall src/tianshu/capabilities/plugins` | 通过 |
| 静态检查 | `ruff check` 两个测试文件 | All checks passed（产品文件维持基线既有 7 项，本轮未新增） |

真实子进程场景断言摘要（新增部分）：

1. 新版本 `plugin.start` 抛 `plugin_start_failed`：操作记 `failed`、`rolled_back=true`、`restored_version=1.0.0`、`failed_version=2.0.0`，**不存在任何 `succeeded` 的 upgrade 记录**，旧版本恢复后可调用；
2. 旧版本此时也无法启动（人为破坏）：`rolled_back=false`、`restored_version=null`、`restore_error=plugin_start_failed`，原始失败码保留，账本版本仍为 1.0.0；
3. 升级前受理但未执行的调用被结算为 `interrupted`/`effect=none`/`error.code=instance_upgraded`/`retriable=false`，切换后新版本正常服务，旧包未被重新拉起；
4. 成功升级后 `plugin_runtime.generation == operations.generation`（记账代次即服务进程）；
5. 默认卸载：配置、绑定、密钥引用保留，重装同包回到同一实例且可再次启用、调用返回原结果；
6. `purge_data=true`：实例文档 `config={}`、`bindings=[]`、含 `data_purged_at`，密钥清空，定时任务 `enabled=false`，重装后配置不再复活，历史 `succeeded` 调用与结果仍在；
7. 卸载过程中的启用请求被包级锁挡在门外（事件屏障断言 `enabling.done() is False`），卸载后实例 `desired_state=disabled`、无进程、无残留 handler；重复卸载返回 `already_uninstalled=true` 且不重复删目录；旁观插件 pid/generation 不变且仍可调用；
8. 卸载过程中发起的重装串行执行，最终包重新 `installed=true`、可启用并调用；
9. 接管后账本提交冲突（注入 `revision_conflict`）：接口返回 `AppError`（`details.execution=failed`、`error.failed_version=2.0.0`），恢复后旧版本继续服务，没有任何 `succeeded` 记录。

失败场景对照（每条都对应缺陷描述中的具体现象）：

| 现象 | 现在的行为 |
| --- | --- |
| 升级后 UI 显示成功但调用失败 | 记账成功前服务进程已接管；任何失败都以 `AppError` 返回并显示失败原因 |
| 失败后被说成已回退 | 只有旧版本真的重启并可用才 `rolled_back=true`，否则给出 `restore_error` |
| 切换中旧调用把旧版本拉起来 | 已受理调用按原身份结算；`execute` 锁内复读文档，版本已变就返回 |
| 卸载后实例仍 enabled / 进程仍在 | 包级锁 + 全程关闭准入；失败报告阶段与剩余状态 |
| 重复卸载删两次目录 | 幂等短路；回收前重读仍 installed 的引用关系 |
| 清除数据后配置又回来了 | `purge` 清空实例文档 config/bindings，重装不复活 |
| 执行证明被当插件数据删掉 | 只删 state/secrets/logs/schedules/consumptions，`actions` 原样保留 |

## 6. 未验证项（明确如实）

- **容器内 / 六宿主角色实机证据**：本机没有可用的天枢容器与 Supervisor 运行环境，未在真实容器中观察"容器与六个宿主角色不重启"。代码路径上所有锁与准入都限于 `PluginManager` 的该包/该实例，不触碰 `Capabilities` 其他组件或 Supervisor，但这是代码事实而非运行证据。
- **NAS 发布与生产迁移**：未执行；本窗口不部署，生产由统一协调者串行发布。
- **真实 QQ / 付费模型调用**：未发送任何真实消息或模型请求。
- **不可逆数据迁移后的回退**：`plugin.migrate_data` 默认拒绝跨 `data_version`，回退得不到数据兼容保证；本次未实现数据降级，页面与本文档如实说明该限制。
- **既有的基线失败**（与本批无关，在 `2a18fd17` 上同样复现）：`tests/capabilities/test_model_timing.py` 两个插件 LLM 端口用例（`SimpleNamespace` 缺 `load_for_execution`）、`tests/capabilities` 目录整体运行时的 4 失败/116 错误（订阅与媒体夹具的环境问题）。本轮未修改这些模块。

## 7. 配置、迁移、SDK 与装配变化

- **迁移**：无。未新增表、列或迁移注册。`purge` 只删除/改写既有表中该实例的行；`data_purged_at` 为实例文档内的 JSON 字段，不需要 DDL。
- **SDK**：无变化（`packages/tianshu-plugin` 未改动），既有 `migrate_config/migrate_data/start/stop` 语义不变；本轮只是保证 `start` 只执行一次且该进程即服务进程。
- **装配**：无变化，仅 `PluginManager` 内部新增锁与方法；`Plugins.revoke` 通过既有 `catalog.handlers` 生效。
- **配置**：无新增配置项。
- **兼容性**：`uninstall` 请求新增可选 `purge_data`（缺省 `false`）；新增独立 `reload` 操作；升级失败现在以 `AppError`（409）返回并携带 `details.operation_id/execution/error`，原先是抛内部 `RemoteError`/`Fault`。前端已按新返回体展示。

## 8. 首次上线方式

沿用现有核心发布流程（一次性发布核心），上线后每次插件操作都通过既有 `/api/v1/plugins/*` 在线完成，不需要重新发布核心。

发布后建议的最小核实：

1. 插件中心能读到 `reload` 操作（`schema`/`view` 正常返回）；
2. 对一个已启用实例点击"重新加载"，`plugin_runtime.pid/generation` 变化而容器与其它实例不变；
3. 对一个测试插件执行升级，确认提示中的代次就是随后调用所用的进程；构造启动失败版本，确认页面显示失败而非成功；
4. 卸载一个测试插件后其入口消失、其它插件可继续调用；对含配置的实例执行"卸载并清除数据"，重装后配置不再出现。

## 9. 回退限制与集成限制

- 本批未改变数据结构，核心回退到上一镜像不会丢失插件账本；但已执行"卸载并清除数据"的插件自有配置/密钥/状态/日志/定时任务不可恢复（执行证明仍保留）。
- 已 `installed=false` 且目录被回收的包，重新安装必须重新提供来源，不能依赖旧目录。
- 跨 `data_version` 的升级若迁移不可逆，回退到旧版本会出现数据不匹配；本实现不给虚假保证。
- **集成限制（重要）**：本分支基线为 `37d4cb50`，**不包含**此后的网页在线发布与模型绑定交付。合并时不得用本分支覆盖部署基线，必须在最新发布之上重放本批改动并复核这些特性；本窗口不部署。
