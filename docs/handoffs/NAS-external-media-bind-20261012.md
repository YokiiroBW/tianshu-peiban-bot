# NAS 外部媒体挂载守护兼容

隔离协调检出基线 `ac55b96`；修改前 guard LF SHA256 为 `06afd37e70afc61f8f785cf25d660d736b4e064482a779a7500bce230becd459`，由父级确认与线上相同。本地只修改 guard、配置 schema、相关测试和本交接；未操作 NAS、真实 Docker 或其他产品。

新增可选 `external_media_binds` 列表，条目格式为 `{source, target, read_only}`。条目必须同时精确出现在 `binds.platform`，且 source 为部署根外的现存普通目录、路径链无符号链接，target 为规范的 `/media/` 子路径。此次目标条目为 `{"source":"/volume1/Video/存储库/短视频/bilibili","target":"/media/Bilibili","read_only":false}`；目录由父级创建后配置。默认与空列表保留原部署根约束，其他服务不接受外部源。schema 同步可选字段及默认约束；运行时负责逐条核验白名单与实际挂载。

复用 `_safe_path`、挂载签名和 `capacity`：Platform 的 armed 签名登记精确白名单；`_owned` 仍逐项匹配 source/target/读写位。外部媒体源自动加入 `free_paths`，媒体内容不纳入 `max_deployment_bytes`。Emby 共享该媒体源不会被视为占用天枢部署根；原 `foreign_deployment_mount` 规则、锁定容器 ID、镜像、watchdog 与 fail-close 流程保持。

实际验证：bundled Python 运行 `python -m unittest discover -s tests/resident_capacity -v`，最终 **40/40 通过，0 跳过**（31 项既有、9 项新增）。覆盖旧配置及九/十服务、外部目录/空闲盘、非 Platform、错误 target、source/读写位替换、符号链接元数据和 Emby 共享源。Windows 无创建真实 symlink 权限，最终源/父级 symlink 用例使用明确的 `lstat` 元数据夹具；Linux/root/config-mode gate 也为测试夹具，其余目录元数据检查使用真实临时文件系统。本地 Python/JSON 语法及 `git diff --check` 通过；本机未发现 ruff/jsonschema，未安装工具或宣称执行这两项检查。

最终 guard LF SHA256：`9a2aa40e7ed4072abb57a3b1e7148feadd2bb26b6da7164cca83a9097c4ed7a4`。验证摘要在任务检出的 `.runtime/media-mount-guard-validation.json`。部署由父级按既有停止、Platform 重建、其余九容器恢复、新状态 arm 流程执行，本提交不表示 NAS 已安装或实机验收。
