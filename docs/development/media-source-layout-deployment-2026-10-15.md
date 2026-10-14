# B站来源分类与媒体目录迁移 — 2026-10-15

已按用户新位置完成推送、部署和实机迁移：`\\YokiiroNAS\Video\存储库\bilibili`。

| 层级 | 当前根目录 |
| --- | --- |
| NAS | `/volume1/Video/存储库/bilibili` |
| 天枢容器 | `/media/Bilibili` |
| Emby | `/video/存储库/bilibili` |

新订阅默认按来源归入 `UP主/<名称+UID>`、`收藏夹/<名称+FID>`、`合集/<名称+来源ID>`、`稍后再看`。保留自定义目录，网页路径预览与后台共用规则；订阅改名不会悄悄移动旧目录。稍后再看使用账号绑定来源，已读取现有账号40项。

复用现有 destination、Subscriptions、Bilibili.scan、Repository、Imports、DownloadCache/Retention 和 FileOrganizer。新增独立的 ImportRelocation 做精确路径重绑定；未增加数据库 schema、下载器、凭据或前端依赖。源目录策略参考 [bili-sync](https://github.com/amtoaer/bili-sync)，按天枢现有产品边界实现。

644 个历史视频中641个恢复到原UP主/收藏夹/合集来源，3个按NFO里的UP主归类。644视频使用原inode硬链接，2378附属文件独立复制；3022个旧文件只在目标文件、数据库及Emby核验后逐个移除。两个原有同BV但分P不同的包保留独立分P目录，避免覆盖各自NFO。

三个旧测试成品通过已有媒体来源重新发布到分类目录，三个新成品仍共用一个视频inode。新任务全部completed、Emby verified、缓存cleaned，旧任务隐藏但原始回执保留；现有六个订阅仍暂停，并采用来源目录。下载台账与冷备完全相同，实际复用画质64的generation仍为1，三条下载台账均未变，未重新下载视频。

Emby原库25136已改为一个新路径，其余14库和原选项保留。647视频、139个Series的简介/名称/UP信息/实际流信息均按新旧精确路径核对，32位导演头像保留；三条播放/收藏状态恢复后再次核对一致。移除旧临时重复目录后总条目为976，两处旧根已无普通文件。旧管理标记和控制记录37份独立归档，未当作新操作回执改写。原路径下420份群晖生成的索引信息也先校验归档，再逐个清理。

平台提交 `c886c22d5949bbcb51d5cd0173b68909d5aa61f8` 已非强制推送GitHub main并部署；NAS挂载、容量守护白名单和Dockge均同步。37库冷备与离线恢复验证通过，十服务运行、五核心TLS ready、guard ready、59网页资源哈希一致。其他九个容器ID、角色和供应商模型/Key保持；也保留另一对话刚启用的自动记忆配置。

验证使用固定部署镜像：Linux相关测试105/105、文件执行器Linux9/9；桌面/手机108场景、真实本地Platform HTTP 4场景通过。受管整理使用临时真实SQLite/文件系统/Organizer/Retention验证，再以现有B站/Emby配置核验实际结果。已通过且代码未变的检查不重复运行。

历史记录仍为643条unknown和1条时长异常，迁移没有猜测实际QN或消除异常。精确镜像、路径、计数、验证与私密证据位置见[交付清单](media-source-layout-deployment-2026-10-15.json)。
