# 644个历史视频NFO与Emby头像补全已完成

TS-135 Platform `fcac74594c7e013af88f2e5e66376b71af73174b` 已推送GitHub main并部署，2026-10-14（上海时间）完成实机验收。复用现有导入台账、来源解析、NFO技术信息、MID头像缓存及Emby连接，没有新建下载队列、网页接口或数据库版本。

644个视频的简介、UP昵称/UID/主页、标准导演以及实际文件流信息已补全。共更新778份原NFO、补建2份缺失NFO，形成644个视频和136个剧集父级的780份关联NFO。旧目录一个P4视频与原NFO不在同一位置，因此仅在视频实际目录补建episode/show；视频路径不变，原孤立NFO保留。原标题、集数、裸BV ProviderId、演员/封面及无关XML字段均保留。

171个BV中168使用当前B站元数据，3个使用逐CID核对过的原bili-sync台账：1个当前详情不可用，另外2个的4个旧CID已被上游替换。历史来源原因和摘要写入NFO，涉及7个视频/9份NFO。全部644个实际QN仍未知，不从分辨率猜编号；文件真实分辨率、编码、帧率、HDR观察及音轨已写入。原有1条时长异常仍保留invalid，不冒称完整成品或升级为精确复用源。

## 实机结果

- Emby4.9.5.0「Bilibili视频」库25136追加 `/video/存储库/短视频/manual/bilibili`，原路径、库ID和其他选项保持。NFO就位后普通扫描，[使用官方追加路径接口](https://dev.emby.media/reference/RestAPI/LibraryStructureService/postLibraryVirtualfoldersPaths.html)，没有重建媒体库或更改视频目录。
- 按准确路径和类型逐项核验36部Movie、608个Episode、136个Series；780项标题、简介、导演及视频/音轨技术观察全部通过。32个MID对应32个导演人物，头像已缓存并经人物图片API上传/读回比对。原有6个受管媒体条目的ID、路径、标题与简介保持。
- 644个视频dev/ino/size/mtime/ctime保持；另外1597个图片/字幕文件逐个哈希保持。原imports、media_sources、jobs、download_sources、订阅成员及发布回执摘要保持，没有新视频下载或文件搬动。
- 独立NFO核对780份全部通过，未知QN与时长异常标记保持。简介比较遵守XML将CRLF/CR归一为LF的规则，不把换行表示差异当作内容丢失。

## 部署与验证

首轮固定Linux镜像60/60通过；缺UP姓名可能产生空集合假成功的问题窄修后，最终固定镜像3条受影响核验用例全通过，其余结果复用（61个unique适用通过，不声称一次跑完61）。本地首轮57项、最终新模块18项通过。协调维护执行器6项Linux及mode/uid/gid/xattr保持通过。集中审查修正了备份阶段中断恢复和目录项持久化，最终增量无阻断。

37库冷备及离线恢复核验、五核心TLS就绪、十服务/guard ready、59网页资源、Dockge及原配置/供应商模型Key保持通过。此次从部署时实际最新的Memory/Companion基线出发，仅更新Platform，保留其余服务镜像和容器身份。

应用冷备：`/volume2/tianshu-v2-resident-updates/media-legacy-nfo-20261013-release-r1/cold-snapshot`。原NFO独立备份：`/volume2/tianshu-v2-resident-updates/media-legacy-nfo-20261013-release-r1/nfo-update/original-nfos`。容器历史映射保持只读，NAS执行器只应用明确计划中的.nfo，先备份再原子替换；新文件用不存在才发布的方式创建。`created_paths`是创建意图，恢复时只撤销存在且摘要仍等于计划after_sha256的文件，不能无条件删除后续编辑。维护执行器在协调分支 `codex/media-reuse-delivery-20261012`，固定代码提交 `981509c`。

实测基于Emby条目/People/图片API；没有逐页浏览器验收或完整播放/转码测试。未重算154GiB视频全文摘要，以全部视频文件身份和只写NFO的执行路径核验保持。高级音轨下载/FLAC封装策略不在本次修改内。[脱敏交付清单](media-legacy-nfo-deployment-2026-10-14.json)
