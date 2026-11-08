# UI 独立在线发布操作指南（第一期）

本文档定义天枢核心（`tianshu-core`）首次接入“UI 独立在线发布”能力后的完整操作规程，包括：
1. 首次接入（需要一次常规核心镜像升级与旧根路径资源快照保留）；
2. 日常 UI-only 独立构建、安装、激活与回退（**不重新构建核心镜像、不执行 `docker restart` 或 `docker compose up`、不重启六个职责进程或 Model Broker、不改变业务 `data_generation`**）；
3. 后端兼容合同演进时的前后端联合升级；
4. 发布失败恢复与历史版本人工回收边界。

---

## 一、目录结构与兼容要求

### 1.1 宿主与容器目录映射

复用现有 `deploy/core/compose.yaml` 中唯一的 `./data:/data:rw` 持久挂载，不新增容器或宿主机挂载点：

| 容器内路径 | 宿主机对应路径（以部署根 `<stack_root>` 为准） | 用途与权限 |
| :--- | :--- | :--- |
| `/opt/tianshu/web/dist` | 镜像只读层（只读容器根文件系统） | 镜像内置基线 UI（无外部激活版本或外部降级时兜底） |
| `/data/ui` | `<stack_root>/data/ui` | UI 独立发布根目录（UID/GID `10001:10001`, `0750`） |
| `/data/ui/releases/<build_id>/` | `<stack_root>/data/ui/releases/<build_id>/` | 不可变已安装 UI 发布包目录（含 `release.json`、`index.html` 及静态资源） |
| `/data/ui/current.json` | `<stack_root>/data/ui/current.json` | 当前激活版本原子指针文件（`tianshu.ui.current.v1`） |
| `/data/ui/.staging/` | `<stack_root>/data/ui/.staging/` | 解压校验与指针原子替换临时区 |
| `/data/ui/.lock` | `<stack_root>/data/ui/.lock` | 本机发布/切换操作互斥文件锁 |
| `/data/ui/legacy-root/` | `<stack_root>/data/ui/legacy-root/` | 首次接入前备份的旧版无版本前缀公开静态资源快照 |

### 1.2 兼容合同要求

- 发布包清单版本：`tianshu.ui.release.v1`。
- 当前后端权威兼容合同（`src/tianshu/runtime/ui_release.py:BACKEND_UI_CONTRACT`）：
  - `contract_id`: `"tianshu.web_api.v1"`
  - `api_prefix`: `"/api/v1"`
  - `contract_version`: `1`
  - `provided_capabilities`: `["command_recovery.v1", "data_generation_header.v1", "session.v1", "ui_version_status.v1", "function_bindings.v1"]`
- 校验原则：
  - 安装与激活时，由后端兼容规则独立核验 `contract_id`、`api_prefix`、`min_backend_contract_version <= contract_version <= max_backend_contract_version` 及 `required_capabilities` 子集关系；
  - 不凭前后端 `source_sha` 相同推定兼容，也不信任包内自报的 `compatible` 字段；
  - 静态资源固定挂载于 `/_ui/releases/<build_id>/...`，入口 `index.html` 必须实际引用该固定版本前缀。

---

## 二、首次接入操作步骤（需一次常规核心升级）

首次接入包含 `runtime/static.py`、`runtime/ui_release.py`、`runtime/process.py`、`runtime/supervisor.py` 与 `deploy/core/ui_release.py` 的代码变更，因此需要执行一次标准核心镜像升级。

### 2.1 升级前保存旧版根路径静态资源快照（关键步骤）

在替换旧核心容器前，先将当前正在运行的旧容器内 `/opt/tianshu/web/dist` 下的公开静态资源（`/assets/*`、`/room-illustration-assets/*`、`/life-evening.png` 等）保存到 `/data/ui/legacy-root/`。这样在核心升级并切换到新版版本化 UI 后，升级前已打开的旧页面请求旧根路径资源时仍能从 `legacy-root` 读取，不会 404，也绝不会映射为新版本内容：

```bash
# 1. 在宿主机或通过已检出新代码的工具保存当前运行容器的旧根路径静态资源
docker cp tianshu-core:/opt/tianshu/web/dist /tmp/tianshu-legacy-dist

python3 deploy/core/ui_release.py preserve-legacy \
  --source-dist /tmp/tianshu-legacy-dist \
  --ui-root <stack_root>/data/ui
```

### 2.2 构建并切换包含 UI 在线发布能力的核心镜像

```bash
# 2. 按既有不可变构建流程导出并构建核心镜像
python3 deploy/core/build.py \
  --checkout <repo_path> \
  --source-sha <commit_sha_40> \
  --owned-root <build_owned_root> \
  --output <build_owned_root>/core-<commit_sha_12> \
  --images deploy/core/images.json

# 3. 按既有标准流程加载镜像并更新容器（仅首次接入核心升级时执行一次）
docker load --input <build_owned_root>/core-<commit_sha_12>/image.tar
# 更新 .env 中 TIANSHU_IMAGE 与 TIANSHU_RELEASE_ID 后切换核心容器
docker compose -f <stack_root>/compose.yaml up -d
```

### 2.3 首次接入核验

```bash
# 核验健康状态与六角色就绪
python3 deploy/core/health.py --config <stack_root>/config/settings.json --probe ready

# 核验 UI 发布状态（此时尚未激活外部包，应显示 served_mode="builtin", degraded=false）
python3 deploy/core/ui_release.py status \
  --ui-root <stack_root>/data/ui \
  --builtin-root /opt/tianshu/web/dist \
  --verify-url http://127.0.0.1:18446
```

### 2.4 首次接入回退方法

若首次接入的核心镜像本身需要回退到接入前镜像：
1. 将 `.env` 中的 `TIANSHU_IMAGE` 与 `TIANSHU_RELEASE_ID` 恢复为上一版已核验镜像摘要；
2. 执行 `docker compose -f <stack_root>/compose.yaml up -d` 恢复原容器；
3. 保留 `<stack_root>/data/ui` 和新旧资源，便于恢复升级。首次核心回退遵循既有服务切换流程；
   旧核心不提供版本 URL，已打开的新界面需要主动刷新，不能将首次核心回退当作 UI-only 无中断回退。

---

## 三、日常 UI-Only 独立在线发布与回退（零重启）

首次接入完成后，所有兼容的 UI 更新、激活与回退均通过 `deploy/core/ui_release.py` 完成，**严禁调用 `docker build`（核心镜像）、`docker restart` 或 `docker compose up`**。

### 3.1 独立构建 UI 发布包（`build`）

首次常规升级后，先把镜像内置版本保存为可回退的已安装基线。内置版本已包含
`release.json` 和固定版本资源路径；在容器内以业务用户运行：

```bash
docker exec -u 10001:10001 tianshu-core python -c 'from pathlib import Path; import tarfile; p=Path("/data/ui/.staging/builtin-baseline.tar.gz"); t=tarfile.open(p,"w:gz"); t.add("/opt/tianshu/web/dist",arcname="release"); t.close()'
docker exec -u 10001:10001 tianshu-core python /opt/tianshu/deploy/core/ui_release.py install --package /data/ui/.staging/builtin-baseline.tar.gz --ui-root /data/ui
docker exec -u 10001:10001 tianshu-core python /opt/tianshu/deploy/core/ui_release.py activate --ui-root /data/ui --build-id <内置release.json中的build_id> --require-expected --verify-url http://127.0.0.1:18446
```

上述初始激活仅在 current.json 尚不存在时执行；已有外部版本时保留原入口，使用正常的
`--expected-current` 操作。先保存基线，再激活第一个独立包，才能通过同一 rollback 命令
完整恢复首次独立发布之前的界面。已安装的基线与旧版本不得随新版发布被删除。

从已提交的 Git commit SHA 和锁定依赖（`web/package-lock.json`）构建不可变 UI 发布包，工作区未提交修改不会混入：

```bash
python3 deploy/core/ui_release.py build \
  --checkout <repo_path> \
  --source-sha <ui_commit_sha_40> \
  --owned-root <ui_build_owned_root> \
  --output <ui_build_owned_root>/pkg-<build_id> \
  --build-id <build_id>
```

- 构建前确定 `--build-id`（如 `ui-20261108t020000z-37d4cb50dd34`；若省略则自动按 UTC 时间戳与 `source_sha` 前 12 位生成）。
- 产物：
  - `<output>/ui-release-<build_id>.tar.gz`（不可变发布归档）
  - `<output>/ui-build-receipt.json`（含 `build_id`、`source_sha`、`package_sha256`、`archive_sha256`）

### 3.2 安装发布包（`install`）

将归档安装到 `/data/ui/releases/<build_id>/`（安装过程在 `/data/ui/.staging/` 完成解压、路径安全检查、无符号链接检查、全文件 SHA256 校验、后端兼容合同校验和剩余磁盘空间检查，通过后原子发布为不可变目录，**不改变当前生效版本**）：

```bash
# 可在宿主机直接操作 <stack_root>/data/ui，或在容器内以 tianshu 用户执行：
docker exec -u 10001:10001 tianshu-core \
  python /opt/tianshu/deploy/core/ui_release.py install \
  --package /data/ui/.staging/ui-release-<build_id>.tar.gz \
  --ui-root /data/ui
```

- 核对回执字段：
  - `"package_installed": true`
  - `"pointer_switched": false`
  - `"http_verified": false`
- 幂等与冲突规则：
  - 相同 `build_id` 且 `package_sha256` 完全一致时安全复用（`"reused_existing": true`）；
  - 相同 `build_id` 但内容不同时立即拒绝（`build_id_content_conflict`），不覆盖既有目录。

### 3.3 在线激活新版本（`activate`）

使用本机文件锁、预期当前版本 CAS 校验（`--expected-current`）和同文件系统原子替换切换 `/data/ui/current.json`，并实时通过 HTTP 核验服务已实际提供新版本：

```bash
docker exec -u 10001:10001 tianshu-core \
  python /opt/tianshu/deploy/core/ui_release.py activate \
  --ui-root /data/ui \
  --build-id <new_build_id> \
  --expected-current <current_build_id> \
  --verify-url http://127.0.0.1:18446
```

- 核对三阶段回执字段（必须全部为 `true` 才算在线切换完成）：
  - `"package_installed": true`
  - `"pointer_switched": true`
  - `"http_verified": true`
- 若网络核验失败，工具将返回非零退出码且 `"http_verified": false`，如实报告 `verification_error`。

### 3.4 在线回退到旧版本（`rollback`）

无需重新构建或重启，直接通过同一原子切换能力回退到上一版本（或通过 `--to <old_build_id>` 指定任意仍兼容的已安装历史版本）：

```bash
docker exec -u 10001:10001 tianshu-core \
  python /opt/tianshu/deploy/core/ui_release.py rollback \
  --ui-root /data/ui \
  --expected-current <current_build_id> \
  --verify-url http://127.0.0.1:18446
```

- 新打开或主动刷新的浏览器页面立即获得回退后的版本 `A`；
- 已经打开的页面在刷新前继续加载各自版本固定路径 `/_ui/releases/<build_id>/...` 下的懒加载分块与图片资源；
- 右上角已出现的“发现新版本”提示在检测到服务端回退回当前页面版本后自动收起。

---

## 四、后端兼容合同变更时的联合升级规程

当未来某次核心升级修改了 `BACKEND_UI_CONTRACT`（例如 `contract_version` 升至 `2` 或新增破坏性契约变更）时：

1. **防呆保护机制**：
   - 旧版不兼容的外部 UI 包（`max_backend_contract_version: 1`）不会被新核心继续当作当前有效版本服务；
   - 若 `/data/ui/current.json` 仍指向不兼容的旧外部版本，新核心启动或重验时会判定 `compatibility_mismatch`，自动降级使用新镜像内置的兼容基线 UI（`/_ui/version` 报告 `served_mode: "builtin"`, `degraded: true`, `degraded_reason: "compatibility_mismatch"`）。
2. **标准联合升级步骤**：
   - 先使用新代码构建并安装兼容新合同的外部 UI 包 `<new_compatible_build_id>` 到 `/data/ui/releases/`（或在升级核心镜像前移除旧 `/data/ui/current.json` 以直接使用新镜像内置基线）；
   - 执行核心镜像升级；
   - 激活兼容新合同的 UI 版本 `<new_compatible_build_id>` 并通过 `--verify-url` 核验 `compatible: true, degraded: false`。

---

## 五、失败恢复与历史版本人工回收边界

### 5.1 失败恢复

- **构建或安装失败**：仅影响构建输出目录或 `/data/ui/.staging/`，不会改动 `/data/ui/releases/` 与 `/data/ui/current.json`；无需回退，直接修复后重试。
- **激活 CAS 冲突（`expected_current_build_id_mismatch`）**：说明存在并发发布或当前版本已变化，先执行 `status` 核对实际生效版本后再决定是否继续切换。
- **外部指针或版本目录意外损坏**：
  - 运行中的 `interactive` 进程会自动保持进程内最近一次已验证可用的版本快照（`served_mode: "fallback_last_known_good"`）或回退到镜像内置基线（`served_mode: "builtin"`），核心服务不会崩溃；
  - 运维可通过 `rollback --to <valid_build_id>` 重新写入完好的 `current.json`，或删除损坏的 `current.json` 恢复纯内置基线模式。

### 5.2 占用查看与历史版本人工回收边界

- **为什么不自动删除历史版本**：
  - 长期打开未刷新的浏览器标签页在用户切换到某个未加载过的懒加载页面（如日志、统计、小屋插画）时，仍会请求 `/_ui/releases/<old_build_id>/...`；保留历史版本目录可确保旧页面不断裂。
- **查看已安装版本与磁盘占用**：
  ```bash
  docker exec -u 10001:10001 tianshu-core \
    python /opt/tianshu/deploy/core/ui_release.py status \
    --ui-root /data/ui \
    --verify-url http://127.0.0.1:18446
  ```
  返回 JSON 中的 `installed_releases` 列出所有版本的 `build_id`、`disk_bytes`、`compatible` 以及汇总 `total_disk_bytes`。
- **人工回收安全边界**：
  1. **严禁删除**当前 `served_build_id`、`requested_build_id` 以及 `current.json` 中记录的 `previous_build_id`（直接回退目标）；
  2. 建议至少保留最近 **3～5 个**历史发布版本，且仅回收发布时间超过 **7 天**（确认无活跃旧浏览器会话引用）的历史版本目录；
  3. 人工回收仅限删除 `/data/ui/releases/<obsolete_build_id>/` 单个子目录，**严禁清理 `/data/files`、`/data/state`、`/data/channels`、`/data/capabilities`、`/data/logs` 或任何业务与媒体目录**。
