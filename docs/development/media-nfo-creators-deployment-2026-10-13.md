# B站 NFO 与 UP主导演头像已部署

TS-134 Platform `9a2dde37527ac313593ec5d06b2a82e93e324c58` 已推送 GitHub main 并部署 NAS。新下载与任务页“重新解析来源”沿同一链路写 NFO，简介保留原文及用户覆盖；UP主写为导演 `昵称（B站 UID …）`，同时保存原昵称、UID和主页，协作者信息保留。同名UP不会仅凭裸昵称覆盖其他人物头像。

每个视频/分P保存实际编码、分辨率、帧率、显示比例、时长、HDR/DV证据和全部音轨/字幕信息。NFO的 `tianshu/source` 与source.json记录BV/CID/实际QN/size/sha256；不以“最高”偏好替代实际结果。tvshow.nfo不使用最后一个P的画质代表整组。简介沿用plot，标准技术字段为fileinfo/streamdetails。[Kodi NFO规范](https://kodi.wiki/view/NFO_files/Movies#fileinfo)

UP头像按MID缓存于Platform数据目录 `/var/lib/tianshu/platform.media-avatars`，来源变更或24小时过期时更新，缓存失败可重试。成功入库清理下载暂存时不会删除头像。Emby不从NFO导入人物图片，因此通过原认证连接给当前视频中精确匹配的Director人物上传缓存图像，并GET图片比较；不写Emby内部DB或people目录。[Emby图片上传协议](https://dev.emby.media/reference/RestAPI/ImageService/postItemsByIdImagesByType.html)

## 验收

- 首轮固定Linux镜像 **141项全通过**；修正后最终镜像定向 **16项全通过**，复用未变检查。本地首轮140通过、1 Windows符号链接条件跳过由Linux补齐；修正后的16项通过。涵盖纯NFO、每P实际质量、HDR/SAR、缓存复用/变更、失败重试、真实图像HTTP协议、元数据刷新保持已下载质量及现有导入/复用回归。
- 集中审查修复旧任务只编辑标题时缺头像来源、None来源失败/取消后URL恢复、导演名英文分隔符解析三处窄问题；修后增量复核无阻断。实机先以原旧任务、无头像来源、冷缓存执行共享补源/缓存方法成功，旧job原样保留，再做正式刷新。
- 实机Emby4.9.5.0：三个受管任务刷新，六份NFO与六个Emby条目核验通过。简介、导演类型与同一人物ID一致，GET头像与持久缓存图像匹配。
- 三目录视频原hash、dev/ino、大小和mtime保持；仍共享同一inode，所有分P从库文件复用。原download_sources各画质generation不变，没有新视频下载。先刷新一个任务，再刷新另两个，同MID缓存fetched_at/revision保持。
- 37库冷备与恢复校验、五核心TLS就绪、十服务/guard ready、59网页资源、Dockge同步和原settings/模型配置/Key保留通过。冷备 `/volume2/tianshu-v2-resident-updates/media-nfo-20261013-release-r1/cold-snapshot`。

## 范围

实际验证使用当前受管视频的既有画质64；HDR/DV、多音轨的NFO字段通过合成验证，不冒称完成新的高规格视频下载。Emby播放技术信息来自真实文件探测，不能声称它会采用全部NFO streamdetails字段。本批未改高级选轨或FLAC合MP4策略。

旧644个只读历史文件及NFO保持原状，库存仍643质量待核验、1时长异常。实际网页人物详情未逐页打开，本批证据为Emby条目/People/图片API读回。真实媒体清单、头像与凭据不提交Git。[脱敏交付清单](media-nfo-creators-deployment-2026-10-13.json)
