# Emby 共享媒体目录挂载

用户明确要求把尚未添加的 Docker 媒体目录配置补入天枢。已在 NAS `192.168.31.210` 完成挂载、生效及实机文件核验。

| 位置 | 路径 |
| --- | --- |
| NAS 实际目录 | `/volume1/Video/存储库/短视频/bilibili` |
| 天枢 Platform 整理目录 | `/media/Bilibili` |
| Emby 容器对应目录 | `/video/存储库/短视频/bilibili` |

从现有 `embyserver-1` 的 `/volume1/Video` → `/video` 挂载解析实际路径。只创建原来不存在的 `bilibili` 末级目录，所有者 `10001:10001`、权限 `0755`，未递归修改已有媒体。Platform 新增该目录的读写 bind，`create_host_path=false`；五条原有挂载保留。网页里的整理目录和 Emby 映射无需修改。

已更新现有运行源 `/volume2/tianshu-v2-resident-updates/bots-20260928/{core,platform}.compose.json`，以及 Dockge `/volume1/Download/dockge/stacks/tianshu-v2-resident/compose.yaml`。复用既有快照同步器同步两份 Dockge 配置。Platform 仍为 `fdd4be0b3b4139eaf87889d2178bc13de607804a`，镜像 `sha256:3754c9d7cade06301a7557ac5f4f14d3175da47eed5d5bee82f47411b3dcdb44`，没有重建应用镜像或迁移数据库。

原容量守护只接受部署根内的挂载，故需同时登记精确的外部媒体目录。本地隔离协调提交 `d556257727de6f30f0e63bc30cbd080826a25c4f` 已安装至 NAS 工具目录的 guard/schema；guard SHA256 为 `9a2aa40e7ed4072abb57a3b1e7148feadd2bb26b6da7164cca83a9097c4ed7a4`。`external_media_binds` 与 `binds.platform` 同时登记上述 source/target/read_only；仅 Platform 接受该白名单。媒体盘纳入空闲容量检查，媒体内容不计入应用部署额度；原 fail-close 流程保留。该协调代码尚未推送远端，后续部署工具更新必须保留此提交和配置。

执行时通过原守护停止流程短暂停止十个天枢服务，重建 Platform 后恢复其余九个容器。新 Platform ID 为 `30780b7f7cb1bcc07b40c85c2b515d2d9cfca9cf823e1f206e5633306829d6da`；其余九个 ID/镜像保持，Emby 容器未重启。新守护状态目录 `/volume2/tianshu-v2-resident-updates/media-mount-20261012-r1/capacity-state` 已重新 arm 并运行，所有原 settings 文件哈希保持。

实际核验：Platform 默认用户 `10001:10001` 写入临时探针，NAS 读取同内容及同 inode，Emby 容器读取同一文件；探针随后删除。五核心 `/health/live`、`/health/ready` 均为 200 且 TLS 校验通过，十服务运行、guard ready，网页入口 HTTP 200。守护专项本地 40/40 通过、0 跳过；Windows 的 Linux 身份与 symlink 分支使用明确元数据夹具，NAS 候选配置加载及实际挂载为真实验证。未执行真实 B 站下载、媒体整理任务、Emby 扫库或元数据导入，不将文件互通等同于这些业务验收。

NAS 执行目录与回执：`/volume2/tianshu-v2-resident-updates/media-mount-20261012-r1/`。原 Compose、guard/schema、容量配置、systemd unit 与 Dockge 配置保留于其 `backup/`；另有 Dockge 同步备份。未触发回滚。若回退，应按当前容器 ID 重新建立守护状态，不能复用锁定旧容器 ID 的 armed 文件，更不应恢复旧数据库来回退挂载。去敏结果见同名 JSON，本地执行材料位于 `.runtime/media-mount-20261012/`。
