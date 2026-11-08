# 插件在线安装、重载、升级、回退、卸载交付（PLUGIN-ONLINE-LIFECYCLE-20261108）

## 0. 提交

- 分支：`codex/plugin-online-lifecycle-20261108`
- 工作树：`C:\Users\Administrator\.codex\worktrees\plugin-online-lifecycle-20261108\tianshu-peiban-bot`
- 提交序列：实现 `0745c42c8d79a4ea52c955248ab19b45895a1834` → 用例补充 `4a88e2558f86673aac6e578ba4dcc7df0b485585` → 文档收尾提交（最终交接 SHA 见分支 HEAD，本文档自身不记录其自身提交号）
- 基线：`37d4cb50`（主协调目录 `codex/snowluma-interfaces-20261108`）

## 1. 结论摘要

本次在既有插件系统上演进，没有新建插件平台，没有改动 Memory/Life/Conversation 或六角色架构，也没有改动插件 SDK 合同、共享装配、迁移或部署根文件。

- 实现：在线安装、启用、停用、**显式在线重载**、升级、**兼容版本回退**、卸载（含可选清除实例自有数据）、动态贡献撤销、页面票据复检、包目录回收。
- 关键缺口修复：`reload` 后端控制接口与页面入口原先完全不存在；`uninstall` 原先既不撤销 catalog 贡献、也不回收包目录、且没有数据清除范围；`upgrade` 原先缺少 `plugin.start` 健康确认，新版本启动失败会被当成成功；`Repository.__init__` 的 `self.runtime` 赋值遮蔽了同名 `runtime()` 方法，导致运行代次投影在生产不可用（真实子进程路径才会暴露）。
- 验证：41 个插件相关用例通过（含 6 个使用真实插件子进程、真实不可变包目录与真实 PostgreSQL 账本的在线生命周期场景）。
- 未验证：容器内 `independent` 角色的实机热重载、真实 NAS 发布、浏览器视觉验收、真实 QQ/付费模型调用。

## 2. 生命周期状态

| 能力 | 状态 | 说明 |
| --- | --- | --- |
| 在线安装（内置/ZIP/Artifact/目录/Git） | 已实现（复用既有路径） | 独立 `tianshu-plugin-install` 执行线程；失败不成为可启用版本；同 `client_request_id` 幂等 |
| 启用 / 停用 | 已实现（补齐撤销） | 停用先把该实例的 catalog 入口撤下再停进程 |
| 显式在线重载 | 本次新增 | `plugins.reload` 控制操作 + 页面按钮；实例 identity/config/密钥/绑定/持久数据不变，只换代次 |
| 升级 A→B | 已实现（补齐健康确认） | 新包启动 → `migrate_config` → `initialize` → `migrate_data` → `plugin.start` 健康确认 → CAS 提交 |
| 失败恢复 | 已实现 | 任一环节失败恢复实例快照，操作记录 `rolled_back=true` 与 `restored_version`，如实返回失败 |
| 回退 B→A | 已实现 | 对仍安装且兼容的旧版本执行同一条升级路径（页面按版本序显示“回退到此版本”） |
| 卸载 | 本次补齐 | 停止实例 → 撤销 catalog/事件入口 → 清页面票据 → 标记包 `installed=false` → 回收无引用包目录 |
| 卸载清除数据 | 本次新增 | `purge_data=true` 仅删该实例 `plugin_state/plugin_secrets/plugin_logs/plugin_schedules` 与 `plugin_consumptions` |
| 动态贡献撤销 | 本次补齐 | 重载/升级/停用/卸载都会即时撤下该实例 handler；页面票据重新校验包仍安装且摘要一致 |
| 旧代次迟到退出 | 已实现（复用） | `watch_worker` 比对 `workers[id]` 中的实际进程对象 |

## 3. 变更清单

后端：

- `src/tianshu/capabilities/plugins/control.py`：新增 `reload`（`instance_id`+`expected_revision`）与 `uninstall.purge_data` schema，OpenAPI 自动同步。
- `src/tianshu/capabilities/plugins/manager.py`
  - `admission` 集合：按实例的准入闸门；
  - `restart()`：显式在线重载，`revision+1` 后停旧进程并重建同版本进程，返回 `reload.previous_generation/generation/pid`；
  - `stop_worker(..., process=None)`：只停止指定代次，避免误停替代进程；
  - `uninstall(package_id, purge_data=False)`：撤销入口、可选清除实例自有数据、回收无引用包目录、写 `uninstall` 操作记录；
  - `upgrade()`：`version` 不可等于当前版本；配置迁移先算 candidate 再整体提交；新增 `plugin.start` 健康确认；失败记录 `rolled_back`/`restored_version`；回退启动失败不再吞掉原始失败；
  - `state(disable)`：先撤入口再停进程。
- `src/tianshu/capabilities/plugins/execution.py`：`invoke/execute` 增加 `instance_reloading` 闸门（仅目标实例）。
- `src/tianshu/capabilities/plugins/service.py`：新增 `Plugins.revoke(instance_id)`。
- `src/tianshu/capabilities/plugins/web.py`：`reload` 路由；卸载前清理对应页面票据；页面票据重新校验包仍安装且摘要一致。
- `src/tianshu/capabilities/plugins/packages.py`：新增 `reclaim(roots)`，只能删除包根目录下已解析路径。
- `src/tianshu/capabilities/plugins/repository.py`：`purge(instance)`；注入 owner 改名 `self.owner` 以解除与 `runtime()` 方法的遮蔽。

前端：

- `web/src/features/plugins/InstancePanel.tsx`：启用状态下的“重新加载”按钮、运行进程/代次展示、“升级到此版本/回退到此版本”、卸载与“卸载并清除此插件数据”入口及中文说明。
- `web/src/features/plugins/PluginCenter.tsx`：重载/升级/卸载的差异化结果提示（区分已受理、执行成功、恢复成功、恢复失败）。
- `web/src/features/plugins/types.ts`：`plugin_runtime`、`kind/purged/reclaimed/rolled_back/restored_version` 字段。
- `web/src/api/operations.ts`：登记 `plugins/reload` 为命令操作。

既有的“列表每 5 秒轮询刷新、配置表单独立 state 且仅在 `instance.revision` 变化时重置”的行为保持不变：轮询不会覆盖用户正在编辑的配置。

## 4. 实际验证（本次集中运行）

环境：Windows、仓库自带 `.venv`（Python 3.12）、临时 PostgreSQL 17.2 + pgvector 0.8.0（本机隔离实例，未连接生产）、真实插件子进程。

| 验证 | 命令/范围 | 结果 |
| --- | --- | --- |
| 插件相关回归 | `pytest tests/capabilities/test_plugin_runtime.py test_plugin_model_timing.py test_rb_independent_plugins.py test_plugin_channel_port.py test_catalog_lifecycle.py test_plugin_lifecycle_control.py test_plugin_online_lifecycle.py -q` | 41 passed |
| 真实在线生命周期（含安装/重载/升级/回退/卸载/长任务隔离/坏版本恢复/失败安装幂等/重装/停用保留） | `tests/capabilities/test_plugin_online_lifecycle.py` | 6 passed |
| 契约与撤销（reload/uninstall schema、页面票据、admission、revoke） | `tests/capabilities/test_plugin_lifecycle_control.py` | 8 passed |
| 前端类型检查 | `tsc -p tsconfig.json --noEmit` | 通过（无输出、exit 0） |
| 前端构建 | `vite build` | 构建完成（exit 0） |
| Python 字节编译 | `compileall src/tianshu/capabilities` | 通过 |
| 新增文件静态检查 | `ruff check` 两个新测试文件 | All checks passed |

真实子进程场景断言摘要：

1. 在线安装此前不存在的插件并启用，`state=ready`，调用返回该版本代码结果；
2. 重载后 `runtime_id` 与 `pid` 均变化，配置与绑定不变，调用仍成功；
3. A→B 升级后调用得到 B 版本结果；**另一插件的 pid 与 generation 完全不变且可继续调用**；
4. B→A 回退成功且调用回到 A 版本结果；
5. 卸载后目标进程消失、该实例 catalog handler 全撤、包 `installed=false`、实例数据保留、另一插件仍可调用；
6. 一个插件执行 2.5 秒长任务期间，另一插件调用在 2 秒内完成；对该长任务实例重载后它被替换，另一插件继续正常；
7. 新版本 `plugin.start` 失败时操作如实失败并恢复 1.0.0，操作记录 `rolled_back=true`、`restored_version=1.0.0`，旧版本恢复后可调用；
8. SDK 窗口不兼容的包安装失败且不可启用，重复请求返回同一失败操作；
9. 卸载后重装同一包，原实例身份、配置与数据保留，重新启用后可调用。

## 5. 未验证项（明确如实）

- **容器内实机热重载**：本机没有可用的天枢容器/Supervisor 运行环境，未在真实容器中观察“容器与六个宿主角色不重启”的过程证据。代码路径上重载只操作 `PluginManager.workers[target]` 与目标实例记录，不触碰 `Capabilities` 的其它组件或 Supervisor，但这是代码事实而非运行证据。
- **NAS 实机发布与生产迁移**：未执行，生产由统一协调者串行发布。
- **浏览器视觉验收**：只做类型检查与构建，未在浏览器中点击重载/卸载按钮。
- **真实 QQ / 付费模型调用**：未发送任何真实消息或模型请求。
- **不可逆数据迁移后的回退**：`plugin.migrate_data` 默认拒绝跨 `data_version`，此时回退会得不到数据兼容保证；本次没有实现数据降级。页面与文档如实说明该限制，不伪称可安全回退。

## 6. 配置、迁移、SDK 与装配变化

- **迁移**：无。未新增表、列或迁移注册；`purge` 只删除既有表中该实例的行。
- **SDK**：无变化（`packages/tianshu-plugin` 未改动），既有 `migrate_config/migrate_data/start/stop` 语义不变。
- **装配**：无变化。`Capabilities._owners` 的接线保持，仅内部新增方法与集合；`Plugins.revoke` 通过既有 `catalog.handlers` 生效。
- **配置**：无新增配置项。
- **兼容性**：`uninstall` 请求新增可选 `purge_data`（缺省 `false`，旧调用不受影响）；新增独立 `reload` 操作，不改动既有 `enable/disable/upgrade` 合同。

## 7. 首次上线方式

本次管理能力沿用现有核心发布流程（一次性发布核心），上线后每次插件操作都通过既有 `/api/v1/plugins/*` 在线完成，不需要重新发布核心。

发布后建议的最小核实：

1. 插件中心能读到 `reload` 操作（`schema`/`view` 正常返回）；
2. 对一个已启用实例点击“重新加载”，`plugin_runtime.pid/generation` 变化而容器与其它实例不变；
3. 卸载一个测试插件后其入口消失、其它插件可继续调用。

## 8. 回退限制

- 本批未改变数据结构，核心回退到上一镜像不会丢失插件账本；但已执行“卸载并清除数据”的实例数据不可恢复。
- 已 `installed=false` 且目录被回收的包，重新安装必须重新提供来源（例如同一 Git 提交），不能依赖旧目录。
- 跨 `data_version` 的升级若迁移不可逆，回退到旧版本会出现数据不匹配；本实现不给虚假保证。
