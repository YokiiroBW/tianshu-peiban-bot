# 已有B站媒体原位登记

用户要求把已下载B站视频补入天枢数据库，并询问4K/HDR/杜比视界和高规格音轨的现状。基线为已部署Platform `9a32650cdc0bfbd0350ddf22a17b41e909206d7c`。沿用用户指定gpt-6.1-sol/xhigh及既有推送部署授权；当前只有TS-131一个实现写者，另一位只读审计格式能力，协调者负责旧数据证据、集成及实机登记。

真实旧文件在NAS `/volume1/Video/存储库/短视频/manual/bilibili`，正式Platform只读路径 `/media/observe/emby-video/短视频/manual/bilibili`，644个MP4。原 `/media/Bilibili` 只有已登记的三个测试成品。bili-sync的只读历史库保存BV/CID/PID及路径，但不保存实际QN；Easy-vdl现有14任务路径未匹配本批文件，不能拿其best/format_id配置伪装成这批文件的实际画质。原始目录、媒体、NFO和字幕均保留原位。

## TS-131边界

配置 `media.import_roots=[{root_id,label,directory}]` 明确只读来源；目录必须已有，不自动创建或写控制区。新增imports模块负责扫描、证据归一及成品源验证，使用原documents台账，schema2不变。维护入口 `await media.imports.reconcile(root_id,evidence_manifest=...)` 无需启动订阅工作者，不新增网页/HTTP或第二套队列。维护可分批并幂等恢复，中断保留已登记项和明确未完成状态。

每个普通视频保存media_import库存：根及相对路径、BV、CID、分P、文件身份/大小/mtime、实际ffprobe编码/分辨率/帧率/动态范围/音轨信息、实际QN或null、状态与缺失证据。未知QN不以文件名、分辨率、旧best偏好、NFO或单纯format_id猜测。未知库存不需要先完整读完154GiB计算无意义哈希；具备可复用证据才进行完整哈希核验。

证据输入为operator提供的逐文件manifest，版本1，entries数组。每项relative_path、bvid、cid(nullable)、part_index(nullable)、size_bytes、mtime_ns、identity_evidence及quality_evidence(nullable)。身份可来自精确旧path，或旧库同BV/PID唯一CID且旧文件名/NFO/时长一致；父级保留来源导出摘要。质量证据必须提供实际QN和绑定当前文件的sha256/size，不采信best、请求上限或任意字符串。后端可在此明确语义下整理字段并在实现前通知协调者最终字段名。

只有BV/CID/实际QN与文件完整哈希一致时，才向现有media_source精确索引写kind=import的可复用源，关联当前import记录及证据摘要。Retention按kind分派，原library分支保持真实operation/marker验证；import分支核对当前配置授权、记录修订/状态、普通文件、前后身份和hash，再复用既有DownloadCache/pin/组包入库链。撤销/重映射根或文件变化后旧指针不可继续用。只读根禁止library_lock写入；必要锁位于已有staging，遵循现有源锁次序及线程取消收口。所有SQL属于Repository，事务内不做文件/网络操作；并发维护不可覆盖更新后的已验证记录。

不伪造completed、file_transfer回执、target_head、订阅成员或集号完成事实；不创建下载任务、不复制/移动/删除旧媒体，不扩大高级音轨功能为本轮实施。完整音轨选择与动态范围保存能力本轮只给真实审计结论。旧库只读SELECT精确下载元数据，不读取账号/用户/token/cookie表或把真实数据提交Git。

## 验证与交付

隔离覆盖只读根零写入、未知质量不命中、完整证据精确命中、幂等/中断、文件变更/根撤销、证据冲突，以及原library来源回归。复用合适旧检查，不跑无关网页/全平台。固定Linux镜像验证后推送部署；只增加import_roots，保留全部旧设置/RO映射/守护与其他产品。数据库批量登记前有明确可恢复快照，实际结果区分库存登记数、CID确认数、可精确复用数和未知原因。缺QN不能宣称全量同画质去重完成。
