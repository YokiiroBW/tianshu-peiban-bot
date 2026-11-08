# 视频订阅失效条目跳过与原队列恢复（2026-11-08）

本聊天 `01a1b920-2282-7ca0-a864-517eade2c159` 沿用本次媒体修复部署授权，按用户“失效视频直接跳过，如果本地有就保留本地，不再动”的要求，串行发布源码 `8a0d6f452e5d187c59241aac4ca851d66592530e`，镜像 `sha256:3eea26da27156598d5ad01b3923fa5a1d43afdaf49958b9283d7563648d172f9`。实现位于独立工作树 `video-unavailable-20261108/tianshu-peiban-bot`、分支 `codex/video-unavailable-20261108`。其他窗口的未提交修改保留。

## 实现

复用原 `Subscriptions.scan`、完整来源枚举、原子基线提交、Actions 和下载队列。仅在单个成员解析明确返回 `source_removed` 时跳过该视频，完整成员 ID 仍进入原基线；既有分 P 集合、本地文件、发布头、下载记录和原回执保持。跳过发生在分 P 登记、入队、音质及目录重评之前。

登录、限流、网络错误等不确定失败仍终止本轮扫描；整个收藏夹来源不可用也保持原错误，不提交虚假空基线。没有新增迁移、依赖或并行状态机。

## 验证及发布

8 个隔离 PostgreSQL / 真实 Actions 用例通过，覆盖历史同步、只同步未来、重复扫描不重复入队、账号及临时错误不提交部分基线、整个来源失效，以及重算/音质/目录重评时保留原本地文件、发布头、回执和分 P。临时数据库容器按精确 owner 和 ID 回收，临时凭据已删除。前端未变化，沿用音乐 UI 修复的有效结果。

从 Git archive 构建正式 Linux 镜像成功。北京时间 12:07:33，六角色 ready、HTTP200、0 重启、无 OOM；已安装 `capabilities/media/subscriptions.py` SHA256 与不可变源码一致。配置文件、媒体账号、模型绑定、代际、迁移登记、11 挂载和其他两个容器保持，容量守护 healthy，守护及巡检 active。当前外部 UI 仍为 `ui-20261108-music-476dda89d9b7`、激活序号 7，兼容且未降级。

## 实际订阅恢复

原订阅 `1cd8425d-639f-43fb-aa63-14339473ff0d`、收藏夹 `4061483034`、配置 revision 3 保持。B 站账号实际核验有效，来源完整枚举 24 个视频；其中 `BV1HbMX6kE95` 明确不可用。

上线后原正常定时扫描于北京时间约 12:07:50 完成：基线包含原 24 个成员，失效的 1 个视频没有下载任务，其余 23 个视频正常入队，扫描回执完成，原初始化提示解除。

随后实际下载暴露现场原配置的 `/data/media/staging` 尚不存在，UID 10001 无权在 root 所有的 `/data/media` 下创建它，23 个任务均在下载开始前报 `media_io_failure`。本批只在既有 `/data` 持久挂载下补齐该配置指定的临时目录，属主 `10001:10001`、模式 `0750`；未修改父目录、共享媒体库权限或配置。

通过正常 `media.jobs.control` Actions 提交这 23 个已确认未开始下载的原任务重试，使用当前 Identity 刷新的原作者权限、原来源和稳定请求标识，由现有生产执行者处理。23 份重试回执均 succeeded，原 23 个 job ID、父扫描 Action 和原失败记录保持；不是直接 SQL 清理或未知效果重放。北京时间 12:12:52，实际为 1 项 downloading、22 项 queued，临时媒体已有 182,816,403 字节，失效视频任务数仍为 0。该截面证明已恢复实际下载，不代表全部视频下载或 Emby 入库完成。

原 258 条 Models unknown 和 6 条 Action unknown 的关键事实逐项保持；切换观察窗口另新增 1 条 Models unknown，未改写或重放。不声称其他业务全部收敛，未发送模型或 QQ 测试。

证据位于原工作区 `.runtime/video-unavailable-20261108/`：`validation.xml`、`test-cleanup.json`、`build-result.json`、`release-status.json`、`final-health.json`、`video-after.json`、`staging-prepared.json`、`original-retry-actions.json`、`recovery-followup.json` 和 `preservation.json`。
