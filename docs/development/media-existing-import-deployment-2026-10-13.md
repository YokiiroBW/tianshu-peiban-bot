# 已有 B站媒体登记与高规格能力核验

TS-131 已推送 GitHub main 并部署 NAS：Platform `33f0ce09b43361862e644d162a34f2716d2c8286`，镜像 `sha256:aefe17ee99aa971b71fc85718eae9da62763ee2a2eb0784ed2cc5a3ce90fe289`。在正式容器既有只读映射内，将旧 Bilibili 目录的 **644 个视频、171 个 BV、约 154.44 GiB** 原位补入媒体数据库。644 项具有 BV/CID/分 P 身份来源，实际 ffprobe 视频/音轨信息已记录；643 项通过身份和时长核验，1 项时长异常已记录。第二次登记幂等。没有移动或复制媒体文件，没有生成下载任务。

## 登记与复用边界

旧目录为 `/volume1/Video/存储库/短视频/manual/bilibili`，Platform 读取 `/media/observe/emby-video/短视频/manual/bilibili`。配置只新增 `media.import_roots`，复用原只读映射；原 `/media/Bilibili` 及其三个已验证成品源保持。

446 项与 bili-sync 历史路径精确对应，198 项以唯一 BV/分 P、来源元数据和实际时长对应。旧库没有保存实际画质 QN，因此 **644 项 QN 均未知，状态为643待核验、1异常，新增精确复用源为0**。不从标题、分辨率或“best”偏好猜 QN。完整证据项可经现有 `media_source → Retention → DownloadCache/pin` 链路复用，缺证据项不能承诺自动跳过以后同画质下载。

同一次 import_commit 事务写库存与合格源，沿用 documents/schema2。原 library 的操作回执和发布标记验证保持；导入来源每次复用核对授权根、记录修订、文件身份和摘要。只读根不创建控制区；未知质量不整文件读取约154GiB计算无效摘要。调用与 manifest 见产品 `docs/platform/media-existing-import.md`。

## 实际验证

- 本地相关 57 项最终56通过、1 Windows符号链接条件跳过；固定 Linux 镜像57全部通过，已补齐该项。完整固定 diff 审查无阻断发现。
- 首次正式登记 122.278 秒，重复登记 13.9 秒。643项unknown/QN缺失，1项invalid，conflict为0。
- 异常文件实际386.878秒，历史台账和当前B站同CID元数据均为5820秒；没有将这个旧文件冒认为完整成品。原文件保留，不自动覆盖或补下载。逐项身份仅保留在私有运行证据。
- 原文件 dev/ino/size/mtime、原非导入 documents/jobs/download_sources/members/member_parts/episodes 逐项摘要保持。原媒体源仍 3 条，新增下载0。
- 37 个数据库冷备校验、五核心 TLS 就绪、十服务/guard ready、网页资源与 Dockge 同步核验通过。冷备 `/volume2/tianshu-v2-resident-updates/media-adopt-20261013-release-r1/cold-snapshot`；外部媒体不在应用冷备内，原文件保留。
- 仅增加 import_roots；其他设置、媒体挂载、原供应商模型配置和加密Key保持。未新建权限、未调用模型或发送QQ消息。

## 4K、HDR、杜比视界与音轨现状

使用现有 B站账号做了真实格式元数据读取，已读到并成功选择 4K QN120 和杜比视界 QN126。固定 yt-dlp 解析器包含 HDR10/DV 与 Dolby/FLAC 来源，但本次没有下载完整高规格视频，也未验证具体 HDR10 样本或 Emby 播放保真。[yt-dlp B站解析器](https://github.com/yt-dlp/yt-dlp/blob/2026.08.19/yt_dlp/extractor/bilibili.py#L65)

当前下载只选一条 bestaudio，没有 DTS/Atmos 专项音轨选择或保真校验，去重键仍为 BV/CID/QN。实际样本选择了 FLAC，当前 FFmpeg 5.1.9 将其以默认参数合入 MP4 会失败；同机一秒合成素材复现，MKV流复制通过。本批不修改高级格式下载策略，不能宣称这些组合已全部可用。[yt-dlp 合并器](https://github.com/yt-dlp/yt-dlp/blob/2026.08.19/yt_dlp/postprocessor/ffmpeg.py#L741) · [FFmpeg FLAC/MP4 检查](https://github.com/FFmpeg/FFmpeg/blob/n5.1.9/libavformat/movenc.c#L6697)

真实旧库导出、逐文件清单、实际格式结果和运行回执仅保留在忽略目录 `.runtime/media-adopt-20261013`，不提交凭据或私有媒体信息。[脱敏交付清单](media-existing-import-deployment-2026-10-13.json)
