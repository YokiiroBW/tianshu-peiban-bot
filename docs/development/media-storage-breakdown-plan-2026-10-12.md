# 存储磁盘四类占用统计

用户要求把存储卡改为“订阅占用、Emby占用、其他占用、未使用”，用不同颜色线段展示。基线为已部署Platform `5cc7876be03c98c461b0690441f2aefbfba9bce6`。沿用已授权的Sol/xhigh并行开发和NAS部署；不改现有媒体文件。

TS-129唯一后端写者负责media storage/config/runtime及相关测试；TS-130唯一前端写者负责media存储卡及相关测试。各自独立工作树，根协调者负责内部API约定、部署配置、精确挂载及验证；第三位只读审查。

## 统计范围

订阅占用为已配置天枢媒体根内普通文件的磁盘分配量，先统计并按st_dev/st_ino去重，包含共享控制池/元数据，硬链接只算一次。Emby占用为现有Emby连接通过VirtualFolders返回的所有可映射本地媒体目录，排除已计入订阅的inode；重叠库/路径/硬链接继续去重。其他占用=total-free-subscription-emby，包含其余文件、文件系统开销与预留；未使用为statvfs可用量。不得用视频码率/目录表象大小猜物理占用，不读取文件内容；无st_blocks平台明确logical口径，NAS按allocated。reflink/压缩等导致与磁盘占用不一致时不能静默裁剪成假结果。

当前Emby视频路径位于/video/存储库，音乐路径位于/music/音乐存档。天枢目前只见/media/Bilibili；本轮增加只读观察映射：NAS /volume1/Video/存储库到/media/observe/emby-video，NAS /volume1/Music/音乐存档到/media/observe/emby-music。保留原读写Bilibili映射。后端配置media.storage_mappings为[{server_id,server_prefix,local_prefix}]，只匹配当前已配置Emby server并对VirtualFolders路径做完整边界匹配，不能用任意远端路径读取未映射本机目录。已有target.server.media_root到target.directory可作为直接映射；最长明确前缀优先。路径缺失/权限不足/远端失败/未映射需标未知，不能计0或全归入其他。

统计异步、合并相同扫描，不阻塞页面/服务启动；使用缓存（约5分钟）和取消协作，目录不跟随符号链接、不越入另一设备。配置变化使旧范围快照失效。首次显示扫描中，正常页面刷新能取得完成快照；不能每次view递归整库。多个卷保持分组，不把不同卷容量相加混成一个饼。

## 内部接口

保留overview.storage/volumes现有字段。每个volume增加breakdown：

`{state:"ready"|"scanning"|"partial"|"unavailable",subscription_bytes:number|null,emby_bytes:number|null,other_bytes:number|null,unused_bytes:number|null,measured_at:number|null,basis:"allocated"|"logical",code:string|null,refreshing:boolean}`。

ready时四项非负且和等于total。未就绪可以显示明确已知项，未知项为null；前端不得把null当0。code采用后端实际固定码再同步前端文案。初始scanning至少unused_bytes可据磁盘读数填充，旧快照刷新用refreshing和measured_at准确标明。Emby连接为0时该分类可明确为0；空VirtualFolder无本地路径不算未映射。后台只读取文件元数据。

网页大数字改总容量，四色分段+四项图例/容量；沿用视觉令牌，标签与数值辅助颜色。极小正占用可以用最细线作提示但不可冒称精确比例；未知状态展示中性待统计区域和解释。保持桌面/手机可用，不新增设置弹窗。

## 运维与验收

已有/media/前缀规则及read_only支持可直接复用guard/recovery，不改代码。先用旧挂载/旧配置冷备，再为Platform Compose/CAP加入两条RO bind和唯一media.storage_mappings改动；create_host_path=false，目录已存在，不mkdir/chown。其余镜像/挂载/settings不动；重建Platform并新state_dir重新arm，Dockge同步。现有精确外部媒体排除扩为三条，备份报告区分before和after。

专项覆盖跨目录硬链接、订阅与Emby重叠、同卷多target、跨卷、未映射/不可读/远端失败、异步缓存/并发/配置变化及取消。前端验证四色/四数、总量守恒、tiny/zero/null/扫描/部分失败、桌面手机；最终固定Linux镜像和NAS真实目录统计。实际验收不以合成图例或Emby逻辑文件总长代替NAS目录分配量。

## 2026-10-13 实机范围校正

NAS 的 Video 与 Music 是同一 Btrfs 容量池的不同子卷，实际 stat.st_dev 和 statvfs.f_fsid 均不同；/proc/self/mountinfo 却明确给出同一 filesystem、superblock device 和 source。因此容量分组与明确根的所属卷统一使用 storage_filesystem：Linux Btrfs 按最长完整挂载点的 mountinfo(device, source) 归组，不能要求它等于 stat.st_dev，也不能凭容量相等合盘。其他平台/文件系统沿用 device。文件身份仍为原始(dev,ino)，不同子卷同 inode 编号分别统计；每个明确根内仍以自身 device 限定递归，不越入意外挂载。补充实际 NAS 条目及异源同容量、转义与路径边界回归，再做正式容器全媒体根独立计数比对。
