# 本地与 NAS 媒体存储交付

状态：前后端已集成到隔离本地候选，未推送或部署。用户选择首批仅本地/NAS 已挂载目录，不含 WebDAV。信息组织参考 [MoviePilot 配置](https://wiki.movie-pilot.org/zh/configuration)，视觉沿用天枢。

## 固定候选

- Platform 基线：`be98a630636ff610fb86de665967c467bf9f921a`，包含此前尚未部署的自主日程候选。
- 前端来源：`ab0afce1556abed38e679d83c699e7ece8b46a78`，集成为 `e961b99`。
- 后端来源：`1cc5a575ec11be51e12665c5c9c4fd3f2e1176c9`。
- 最终 Platform：`9667373c1800f7cb75b4f300efde2619e89c89ad`，位于 `worktrees/media-storage-20261012/platform`；串行 cherry-pick 无冲突、工作树干净、完整 diff 空白检查通过。

## 已完成

现有“订阅→账号与媒体库”中管理存储根和多个相对目录，支持浏览、创建子目录、用途选择、编辑及移除登记。展示实际读写能力与所在磁盘容量；移除配置不删除真实文件。NAS 路径必须已挂载到 Platform 容器内；网页不会自动挂载网络共享。

动态媒体目录和原 AssetLibrary 目标共存。订阅及链接下载可选媒体成品目录与独立暂存目录，复用原下载引擎、媒体 SQLite、队列、成品包和 NFO 生成器。仅监控不要求目标也不下载；启用下载处理之后新增，历史使用现有手动重算。旧请求省略模式仍按下载兼容。

本地发布持久记录意图和回执，校验目录身份、文件归属和完整内容，支持新增分 P、改标签、显式画质替换及重启恢复。任务冻结创建时保存位置，完成记录不随配置删除失效。未知同名目录及用户修改文件不会被覆盖；成功发布后清理本次已核实的旧成品备份。

## 验证与证据

- 后端媒体相关 31/31 通过，18.566 秒：原 runtime 14、overview 9、新存储 8。包含真实临时文件系统、Windows junction、发布中断和跨实例排他；日志 `backend/.runtime/media-regression.log`。
- 前端桌面/手机媒体检查 76/76，通过后最终改动另验证相关 20/20；类型、构建、改动范围格式通过。前端交接 `platform/docs/handoffs/media-storage-web-2026-10-12.md`，最终截图位于 `frontend/.runtime/media-storage-release`，协调者实际查看桌面浅色及手机暗色。
- Ruff services/platform 与 tests/backend 检查通过；改动范围格式通过。全仓格式检查仍有 11 个未改动基线文件失败，未顺手改动；构建原 renderer 大 chunk 提示仍存在。
- 最终固定集成 SHA 的独立 HTTP 联合 **2/2 通过，33.306 秒**，见[联合交接](../handoffs/media-storage-joint-2026-10-12.md)。实际 Platform 生命周期、Worker、文件系统和回执；B站及下载媒体内容为合成，非真实账号验收。日志 `.runtime/media-storage-20261012/joint/final.log` 位于工作区根，覆盖目录入库、追加分 P 与发布回执前重启恢复。

## 使用边界

本地目标以视频与 NFO 成功落盘为完成依据，不冒称 Emby 已扫描入库。需要让 Emby 可见同一成品目录并配置对应媒体库。首次落盘使用同卷 rename；已有成品更新采用可恢复的两次 rename，存在短暂可见间隙，不是原子交换。下载缓存及完整 staging 继续按原增量恢复需求保留。

未操作 NAS、真实 B站账号或媒体服务器；Linux/NAS 实际挂载和锁语义尚未实机验证。发布前需与其他对话最新产品版本协调，不能把本候选基线视作全部线上最新状态。根原有修改与独立服务均未触碰。
