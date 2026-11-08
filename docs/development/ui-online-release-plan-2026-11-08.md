# UI 独立在线发布（第一期）执行计划与冻结合同

> 2026-11-08 收口：UI-00～UI-06 已完成，原 UI 分支缺项经独立集成修复。
> 合并另行授权的功能模型绑定统一修复后，产品 `34dfba6314d869c3a15c18b5e63db4d151ff83e3`
> 已推送部署；55 个 Python/PG、10 个桌面/手机用例通过，正式 UI A→B→A→B
> 切换期间容器和六角色实例不变。实际实现、迁移、原配置保存及未验证边界以
> `docs/handoffs/UI-ONLINE-RELEASE-20261108.md` 为准；下文保留原冻结合同与阶段队列。

## 1. 基线与已知起点核对

- **当前实际基线 HEAD**：`37d4cb50dd3456a2b73dee61946dab71d105ef61`（分支 `codex/snowluma-interfaces-20261108`，包含管理员页 GET 读取补修 `b6095012156e303636f17b62b09cdc9ac5196ba2` 与 SnowLuma 接口上线记录 `40e32ff1cc5c35e6710622940664ed4ab7cf416b`）。
- **工作区保护**：原协调检出 `C:\YOKI\Codex\tianshu-peiban-bot` 中未跟踪的 `docs/wiki-requirements.txt`、`docs/wiki/`、`mkdocs.yml`、`scripts/wiki_reference.py` 及其他既有 worktree 完整保留，不执行 reset、clean 或 stash。
- **本期主协调分支与工作树**：
  - 主协调集成：`codex/ui-online-release-20261108`（`C:\Users\Administrator\.codex\worktrees\ui-online-release-20261108\tianshu-peiban-bot`）
  - UI-01 子工作树：`codex/ui-online-release-01-static-20261108`
  - UI-02 子工作树：`codex/ui-online-release-02-tools-20261108`
  - UI-03 子工作树：`codex/ui-online-release-03-web-20261108`
- **源码已知起点确认**：
  1. `src/tianshu/runtime/static.py`：`register_static(app, directory)` 在进程启动时将 `root = Path(directory).resolve(strict=True)` 固定在闭包内，无法感知运行期版本切换。
  2. `src/tianshu/runtime/process.py`：`interactive` 角色在启动时调用 `register_static(app, settings["http"]["static_directory"])`。
  3. `deploy/core/Dockerfile`：构建 `web/dist` 并复制到核心镜像 `/opt/tianshu/web/dist`，`deploy/core/image_inventory.py` 记录 `static_sha256`。
  4. 前端已有 `CommandMonitor`（`web/src/components/CommandMonitor.tsx`）、悬浮通知系统 `NotificationProvider` / `Notification` / `Notice`（`web/src/components/Notifications.tsx`）、`PageBoundary`（`web/src/components/PageBoundary.tsx`）以及 `data_generation` 处理（`web/src/api/client.ts`）。
  5. 静态路径拼接问题：
     - `web/src/features/room/illustration/compositor.ts` 第 276 行硬编码 `image.src = \`/room-illustration-assets/${file}\``；
     - `web/src/features/life/DailyLife.tsx` 第 200 行硬编码 `src="/life-evening.png"`。

---

## 2. 冻结合同（UI-00）

### 2.1 唯一合同与校验实现归属

- `src/tianshu/runtime/ui_release.py`（由 **UI-01** 单写）是 UI 发布合同、兼容规则、版本标识与路径安全校验、清单摘要计算、不可变版本目录校验、本机文件锁、当前版本原子切换与状态查询投影的**唯一实现归属**。
- `deploy/core/ui_release.py`（由 **UI-02** 单写）必须直接导入并复用 `tianshu.runtime.ui_release` 中的常量、数据模型与校验/切换函数，严禁在部署脚本中复制第二套 schema、摘要或兼容判定规则。

### 2.2 最小冻结合同 9 项细则

1. **发布包身份与清单合同（`release.json`）**：
   - 包格式版本常量：`UI_PACKAGE_SCHEMA_VERSION = "tianshu.ui.release.v1"`。
   - `build_id`：构建前确定，匹配正则 `^ui-[a-z0-9][a-z0-9._-]{3,60}$`（且不含 `..`），默认生成格式为 `ui-<YYYYMMDDTHHMMSSZ>-<source_sha[:12]>`。
   - `source_sha`：完整 40 位小写十六进制 Git commit SHA（`^[0-9a-f]{40}$`）。
   - `vite_base`：固定为 `/_ui/releases/<build_id>/`。
   - `entrypoint`：固定为 `"index.html"`，且 `index.html` 必须存在于 `files` 清单中并实际包含 `/_ui/releases/<build_id>/` 资源引用。
   - `compatibility`：声明所需后端合同：
     ```json
     {
       "contract_id": "tianshu.web_api.v1",
       "api_prefix": "/api/v1",
       "required_capabilities": [
         "session.v1",
         "command_recovery.v1",
         "data_generation_header.v1",
         "ui_version_status.v1"
       ],
       "min_backend_contract_version": 1,
       "max_backend_contract_version": 1
     }
     ```
   - `files`：字典，键为相对版本根目录的公开资源 POSIX 相对路径（不含 `release.json` 本身），值为 `{"sha256": "<64-hex>", "size": <int>, "content_type": "<mime>"}`。
   - `package_sha256`：对 `files` 按路径字典序计算的确定性 SHA256 摘要（`path + "\0" + sha256 + "\0" + str(size) + "\n"`）。

2. **版本标识正交性**：
   - `build_id`（UI 构建包标识）、`activation_sequence`（`current.json` 中的单调递增切换序号）与业务 `data_generation`（后端数据库/运行时代际）三者完全独立。
   - UI 发布、激活与回退绝不修改或触发 `data_generation` 变更。

3. **后端权威兼容合同与独立校验**：
   - 后端在 `src/tianshu/runtime/ui_release.py` 中声明 `BACKEND_UI_CONTRACT`：
     - `contract_id = "tianshu.web_api.v1"`
     - `api_prefix = "/api/v1"`
     - `contract_version = 1`
     - `supported_package_schemas = ("tianshu.ui.release.v1",)`
     - `provided_capabilities = frozenset({"session.v1", "command_recovery.v1", "data_generation_header.v1", "ui_version_status.v1"})`
   - 兼容性由 `validate_compatibility(manifest, backend_contract=BACKEND_UI_CONTRACT)` 独立判定：
     - 检验 `schema_version`、`contract_id`、`api_prefix`、`min_backend_contract_version <= contract_version <= max_backend_contract_version` 以及 `required_capabilities <= provided_capabilities`；
     - 绝不凭 `source_sha` 相同推定兼容（不同 SHA 只要合同满足即兼容；相同 SHA 若合同不满足同样拒绝）；
     - 绝不采信包内自报的布尔 `compatible` 字段。

4. **外部发布目录布局（复用既有 `/data` 挂载）**：
   - 优先使用 `settings["http"].get("ui_release_directory")`；未显式配置时，若存在 `settings.artifact_directory`（容器内 `/data/files`），默认使用 `settings.artifact_directory.parent / "ui"`（即 `/data/ui`）。不新增容器或额外宿主机挂载。
   - 目录结构冻结如下：
     - `/data/ui/releases/<build_id>/`：不可变已安装版本目录（内含 `release.json`、`index.html`、`assets/`、`room-illustration-assets/`、`life-evening.png` 等）。
     - `/data/ui/current.json`：当前激活入口指针文件（`schema_version = "tianshu.ui.current.v1"`）。
     - `/data/ui/.staging/`：安装校验与指针写入的临时目录。
     - `/data/ui/.lock`：本机发布操作互斥锁文件。
     - `/data/ui/legacy-root/`：首次接入前保存的旧版无版本前缀公开静态资源快照目录（仅服务旧页面遗留的根路径静态资源请求，不映射为新版本内容）。

5. **固定版本资源 URL 与路由边界**：
   - 版本资源 URL 固定为 `/_ui/releases/<build_id>/<path>`。
   - 根路径 `/` 及非保留路径的 SPA HTML 导航请求返回当前有效版本的 `index.html`。
   - 保留路径边界：`api`、`health`、`internal`、`channels`、`v1`、`v1beta`、`_ui` 绝不回退为 `index.html`；任何带文件扩展名或 `Accept` 不含 `text/html` 的缺失路径一律返回真实 404。
   - 仅允许读取对应版本 `files` 清单中的公开普通文件，阻止 `..` 目录穿越、符号链接逃逸，禁止通过静态路由读取 `release.json`、`current.json`、`.staging`、`.lock`。

6. **当前版本查询接口（`GET /_ui/version`）**：
   - 路径：`GET /_ui/version`（同时支持 `HEAD /_ui/version`），同源公开只读，响应头 `Cache-Control: no-cache, no-store, must-revalidate`。
   - 响应体固定结构（不含任何本地路径、环境变量、凭据或业务信息）：
     ```json
     {
       "schema_version": "tianshu.ui.status.v1",
       "served_build_id": "ui-...",
       "served_source_sha": "37d4cb50...",
       "served_mode": "external",
       "requested_build_id": "ui-...",
       "activation_sequence": 1,
       "compatible": true,
       "degraded": false,
       "degraded_reason": null,
       "contract_id": "tianshu.web_api.v1",
       "contract_version": 1
     }
     ```
   - `served_mode` 取值为 `"external"` | `"fallback_last_known_good"` | `"builtin"`。

7. **文件锁、预期切换版本校验（CAS）与同文件系统原子替换**：
   - 安装、激活、回退均通过 `ui_release_lock(ui_root)` 获取 `/data/ui/.lock` 独占文件锁。
   - 切换函数 `switch_current_release(ui_root, target_build_id, *, expected_current_build_id=None, require_expected=False, builtin_root=None)` 支持校验当前激活版本是否匹配 `expected_current_build_id`，不匹配时拒绝切换并抛出 ` ValueError("expected_current_build_id_mismatch")`。
   - `current.json` 更新先写入 `/data/ui/.staging/current-<uuid>.tmp` 并 `fsync`，再通过 `os.replace` 同文件系统原子替换 `/data/ui/current.json`。

8. **三阶段回执区分**：
   - 发布操作工具回执必须明确包含并区分：
     - `package_installed`（包已安装并通过离线完整性与兼容性校验）
     - `pointer_switched`（`current.json` 已原子切换到目标 `build_id`）
     - `http_verified`（通过 HTTP 实际请求 `/_ui/version` 与 `/` 核验服务已提供目标 `build_id`；未核验或核验失败时必须为 `false`，网络核验失败时返回非零退出码并记录 `verification_error`）。

9. **内置基线保留与升级恢复语义**：
   - 镜像内置 `/opt/tianshu/web/dist` 作为无外部发布版时的默认基线（构建镜像时同步生成内置 `release.json`，若为开发态普通 `dist` 目录则自动派生内置基线快照）。
   - 核心升级不得覆盖或清理 `/data/ui/current.json` 与 `/data/ui/releases/`：
     - 若外部 `current.json` 指向的版本与新核心 `BACKEND_UI_CONTRACT` 兼容且完整，继续服务该外部版本；
     - 若外部版本损坏或与新核心 `BACKEND_UI_CONTRACT` 不兼容，自动降级为内置基线（或运行期内最近可用兼容快照），记录 `degraded: true` 与 `degraded_reason`，不拖垮核心进程。

---

## 3. 共享模块 `src/tianshu/runtime/ui_release.py` 冻结接口清单

为保证 UI-01 与 UI-02 无缝协作，`src/tianshu/runtime/ui_release.py` 必须提供以下公开常量、类与函数：

- 常量：
  - `UI_PACKAGE_SCHEMA_VERSION = "tianshu.ui.release.v1"`
  - `UI_CURRENT_SCHEMA_VERSION = "tianshu.ui.current.v1"`
  - `UI_STATUS_SCHEMA_VERSION = "tianshu.ui.status.v1"`
  - `BACKEND_UI_CONTRACT: dict[str, Any]`
- 校验与构建辅助函数：
  - `validate_build_id(build_id: str) -> str`
  - `validate_source_sha(source_sha: str) -> str`
  - `release_base_url(build_id: str) -> str`（返回 `f"/_ui/releases/{build_id}/"`）
  - `file_sha256(path: Path) -> str`
  - `compute_package_sha256(files_manifest: Mapping[str, Mapping[str, Any]]) -> str`
  - `build_release_manifest(dist_dir: Path, *, build_id: str, source_sha: str, created_at: str | None = None, compatibility: Mapping[str, Any] | None = None) -> dict[str, Any]`
  - `validate_compatibility(manifest: Mapping[str, Any], backend_contract: Mapping[str, Any] = BACKEND_UI_CONTRACT) -> None`
  - `verify_release_directory(release_dir: Path, *, expected_build_id: str | None = None, backend_contract: Mapping[str, Any] = BACKEND_UI_CONTRACT) -> VerifiedReleaseSnapshot`
- 运行期管理与原子操作：
  - `ui_release_lock(ui_root: Path)`（上下文管理器，获取 `ui_root / ".lock"` 独占锁）
  - `install_release_directory(ui_root: Path, source_dir: Path, *, min_free_bytes: int = 64 * 1024 * 1024, backend_contract: Mapping[str, Any] = BACKEND_UI_CONTRACT) -> dict[str, Any]`
  - `switch_current_release(ui_root: Path, target_build_id: str, *, expected_current_build_id: str | None = None, require_expected: bool = False, backend_contract: Mapping[str, Any] = BACKEND_UI_CONTRACT) -> dict[str, Any]`
  - `read_ui_release_status(ui_root: Path | None, *, builtin_root: Path | None = None, backend_contract: Mapping[str, Any] = BACKEND_UI_CONTRACT) -> dict[str, Any]`
  - `UiReleaseManager`（供 `static.py` 在运行期按需重验 `current.json`、缓存已校验 `VerifiedReleaseSnapshot`、保持 `last_known_good` 及提供 `/_ui/version` 状态）。

---

## 4. 任务归属与单写边界

| 任务编号 | 负责人 | 单写范围 | 依赖 | 状态 |
| :--- | :--- | :--- | :--- | :--- |
| **UI-00** | 主协调者 | `docs/development/ui-online-release-plan-2026-11-08.md` | 无 | 已完成冻结合同 |
| **UI-01** | 子智能体 1 | `src/tianshu/runtime/ui_release.py`、`src/tianshu/runtime/static.py`、`tests/runtime/test_ui_static_release.py` | UI-00 | 待派发 |
| **UI-02** | 子智能体 2 | `deploy/core/ui_release.py`、`tests/deployment/test_ui_release_tools.py` | UI-00、复用 UI-01 合同签名 | 待派发 |
| **UI-03** | 子智能体 3 | `web/vite.config.ts`、`web/src/app/uiRelease.ts`、`web/src/app/App.tsx`、`web/src/features/room/illustration/compositor.ts`、`web/src/features/life/DailyLife.tsx`、`web/tests/ui-online-release.spec.ts` | UI-00 | 待派发 |
| **UI-04** | 主协调者 | `src/tianshu/runtime/process.py`、`src/tianshu/runtime/supervisor.py`、`src/tianshu/runtime/config.py`、`src/tianshu/runtime/config.schema.json`、`deploy/core/Dockerfile`、`deploy/core/settings.example.json` | UI-01, UI-02, UI-03 | 待执行 |
| **UI-05** | 主协调者 | 集中验证（pytest + 真实 aiohttp 静态服务双版本构建 A→B→A 在线切换 + Playwright 桌面/手机验收） | UI-04 | 待执行 |
| **UI-06** | 主协调者 | `docs/development/ui-online-release-operations.md`、`docs/handoffs/UI-ONLINE-RELEASE-20261108.md`、计划终态更新与本地提交 | UI-05 | 待执行 |

---

## 5. 验收条件（UI-05 / UI-06）

1. 同一实际 `aiohttp` 静态服务实例在运行期完成 `A -> B -> A` 切换，进程实例与 PID 不变，期间 `/api/v1/session` 等 API 请求与后台模拟任务持续运行且 `data_generation` 不变。
2. 已打开的 A 页面在服务端切换到 B 后，首次打开懒加载功能页及请求房间插画/生活场景图片时，仍从 `/_ui/releases/<build_id_A>/...` 成功加载 A 的资源。
3. 新打开或主动刷新的页面加载 B；回退后新页面加载 A；入口 HTML 与引用的资源无版本混用。
4. 房间插画、懒加载功能页和主要入口在桌面、手机视口均可用。
5. 发布和回退仅通过 `Notifications` 显示非阻断更新提示，不触发自动刷新，不打断当前表单输入和进行中的命令提交；`CommandMonitor` 与 `unknown` 恢复语义保持。
6. 原登录 Cookie、CSRF 和 `data_generation` 语义保持，UI 更新不触发业务代际切换。
7. 不兼容包、摘要错误、缺件、路径越界、符号链接、相同 `build_id` 不同内容覆盖、并发激活冲突、安装/切换中断等异常场景均被拒绝且不破坏当前可用版本。
8. 缓存头（HTML/`/_ui/version` 及时重验、固定版本资源 `immutable`）、`HEAD`/`Range`、缺失资源真实 404、SPA 导航回退、保留路径（`api`/`health`/`internal`/`channels`/`v1`/`v1beta`/`_ui`）符合合同。
9. 首次接入的旧根路径静态资源兼容（`legacy-root`）及无外部发布版时的内置基线启动均有自动化用例验证。
