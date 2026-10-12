# 存储磁盘四类容量已部署

2026-10-13，Platform `9a32650cdc0bfbd0350ddf22a17b41e909206d7c` 已推送GitHub main并部署NAS，镜像 `sha256:acfef25805a230cd2dabdc2c8f5f89a7b261d5d597c5c586bf01b6fa54bacaae`。TS-129/130由用户指定的gpt-6.1-sol/xhigh分别在独立工作树实现后端和前端，协调者完成集成、精确挂载和实机验收，第三位对固定后端和部署改动做只读审查。

## 实际结果

页面以四色线段和数值显示订阅占用、Emby占用、其他占用、未使用，大数字为总容量。Emby范围来自现有连接的真实库目录；订阅优先，同设备/inode全局排重，硬链接只计一次。统计时间及后台刷新状态可见，未知保持空值而不是0。沿用原视觉令牌，桌面和手机均可用。

UID10001在正式Platform容器中读取 14 个映射媒体根，全部属于当前媒体卷；Video和Music子卷的stat设备号不同，容量已按实际Btrfs挂载归同池。四类已取得allocated/ready结果：

| 分类 | 字节 |
| --- | ---: |
| 订阅占用 | 3,751,936 |
| Emby占用（排除订阅） | 7,028,009,209,856 |
| 其他占用 | 8,726,506,717,184 |
| 未使用 | 51,394,341,203,968 |
| 总容量 | 67,148,860,882,944 |

四项之和等于总容量。独立订阅目录计数与产品统计一致，共 23 个普通文件入口、20 个独立inode，排除重复硬链接分配块 5,050,368 字节。独立遍历所有映射根的Emby结果也一致，音乐贡献 59,228,647,424 字节。首次view耗时 0.0084 秒，返回后异步完成统计；再次读取复用同一快照，没有启动下载工作者或读取媒体内容。

## 实现与部署

复用原media_storage卷容量读取和MediaServers.libraries；storage_scan拥有元数据遍历与身份去重，storage_breakdown拥有五分钟单飞缓存/取消/配置范围失效，runtime仅装配。无新依赖、无数据库迁移。订阅目录含共享池及元数据；Emby类排除订阅inode。其他=total-free-subscription-emby，包含文件系统开销和预留。没有st_blocks的平台明确logical估算；超出盘占用的分类保持partial而不裁成假精确值。

保留 `/media/Bilibili` 读写挂载，新增 `/volume1/Video/存储库` → `/media/observe/emby-video`、`/volume1/Music/音乐存档` → `/media/observe/emby-music` 两条只读挂载及唯一 `media.storage_mappings` 设置。Compose、容量守护精确清单和Dockge均同步；守护/恢复代码未改。

音乐根原ACL阻止UID10001读目录，先隔离验证数字UID、动态继承及精确撤销，再仅在音乐根增加 `user:10001:allow:r-x---a------:-d--` 一条目录列出/遍历/属性读取ACE。原ACE、owner及mode700保持，文件ACL变更0，既有子目录继承和实际UID读取通过；未使用会覆盖显式规则的enforce-inherit。原ACL及回滚证据保留在 `/volume2/tianshu-v2-resident-updates/media-storage-breakdown-20261012-acl-r2`。第一次准备因错误检查getace的archive头提前退出，仅留下空stage，未改媒体权限；修正后上述精确追加一次完成。

## 验证

首发Linux镜像49项通过/0失败/0跳过；随后实机发现Music虽同一容量池、stat设备号却独立，旧分类把它判成其他盘跳过，因此未据首发结果宣称完成。修复由共享storage_filesystem统一容量分组，Linux Btrfs按/proc/self/mountinfo的最长完整挂载点及superblock设备/源判断，不以相等容量或statvfs.f_fsid猜测。文件身份仍为原(dev,ino)，不同子卷同inode不会误去重；明确根分别遍历，子树不越意外挂载。原生依据见[Btrfs子卷说明](https://btrfs.readthedocs.io/en/latest/Subvolumes.html)，正式NAS观察见私有media-mountinfo/storage-devices证据。

最终固定Linux镜像受影响40项通过/0失败/0跳过，未变的runtime结果复用首轮；Windows首轮32通过/2平台条件跳过，补修结果见清单，Linux已覆盖跳过项。前端四色18项、受影响概览8项、首个固定候选真实本地HTTP桌面/手机2项通过；补修不改接口字段或网页，复用适用结果。类型、构建及截图检查通过。实际NAS统计与本地合成Emby响应明确区分。

37个SQLite冷备及未激活离线恢复、五核心TLS、十服务和guard ready、59网页资源哈希、Dockge同步均通过。原供应商/Key、角色权限、账号、订阅与其余九容器保留。冷备先使用旧owners/旧媒体排除清单，新增只读映射后守护登记三条外部媒体映射；应用冷备仍不包含外部媒体。备份：`/volume2/tianshu-v2-resident-updates/media-storage-subvolumes-20261013-release-r1/cold-snapshot`。

按文件系统报告的分配块计量，没有扫描共享物理extent；reflink/压缩等布局不保证等同底层物理块唯一性，硬链接去重已实测。此次NAS验证是正式容器存储读链和网页资源，浏览器操作在本地真实HTTP验证，没有重复B站下载或触发Emby扫库。

精确证据见 [验收清单](media-storage-breakdown-deployment-2026-10-13.json)。产品交接为 `docs/handoffs/TS-129.md`、`TS-130.md`；根混合修改保持，文档仅在干净协调分支提交。
