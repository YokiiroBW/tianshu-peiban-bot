# 媒体复用冷备兼容

基线 `48889ce`。本地隔离修改仅为 `resident_snapshot.py`、必需的 `safety.py` 可选参数、相关测试和本交接；未操作 NAS、改 guard、推送或更改其他产品。

`snapshot(..., external_media_binds=[{source,target,read_only}], media_staging_roots=[Path(...)])` 与 CLI expectation 同名可选字段。外部挂载名单与 guard 同形，必须完整匹配唯一 Platform bind 的 Source/Destination/RW；其他服务、重复或意外挂载拒绝。外部源要求现存普通目录、无链接别名且与应用根/操作目录分离；不枚举或复制媒体库内容，结束前复核其目录身份。manifest 和 receipt 均记录 `excluded_external_media`（精确名单）、`media_staging_roots` 与 `media_staging_restore="independent_content_files"`；此为应用状态备份和离线恢复校验，不是全媒体备份。

父级确认本次来源为 `/volume1/Video/存储库/短视频/bilibili` → `/media/Bilibili`，`read_only=false`；宿主 staging 为 `/volume2/tianshu-v2-resident/data/platform/platform.media-staging`。仅在目录实际存在时将该精确路径加入 `media_staging_roots`；不存在时传空列表正常。staging 必须为应用根严格子目录。原 `safe_path`、遍历、哈希及复制 helper 增加默认空的 `hardlink_roots` 范围参数，默认仍拒绝硬链接；仅已登记 staging 中的普通文件允许按内容复制。配置/凭据等范围外硬链接与 symlink/junction 保护保持，备份和离线恢复中的副本为普通独立文件，不保留硬链接拓扑。读前/读后路径与打开句柄均检测 inode/大小/mtime/ctime/link 数变化，原库存哈希变化检查继续执行；所有 owner 正常停止、身份、权限、SQLite/WAL/Memory guard 检查保持。

验证：`python -m unittest discover -s tests/deployment/recovery -p test_resident_snapshot.py -v` 最终 **17/17 通过**（9 既有、8 新增）；同入口 `-p test_recovery.py` **39/39 通过**，0 跳过。合成临时目录创建真实硬链接，覆盖默认拒绝、限定范围内容复制/独立恢复、空 staging、精确挂载/其他服务/重复/缺失拒绝、凭据范围外拒绝、hash/copy 期间别名修改及外部目录替换拒绝。首次窄测 Windows stat/fstat 的 ctime 语义差异造成误判，已改为各来源分别前后对照，并保留跨路径/句柄其他身份检查；修复后窄测全部通过。Python 语法与 `git diff --check` 通过；未做 NAS、Linux fsync 或真实 Docker 验证。

完整测试输出保存在任务检出 `.runtime/media-reuse-recovery/resident-snapshot-tests-fixed.txt` 和 `recovery-default-tests.txt`，首次失败输出亦保留。父级可从固定提交导出独立 recovery-code 包并逐文件核验 SHA，不覆盖历史恢复包；生产调用使用基线 owner 列表、精确外部媒体名单与实际存在的 staging 路径。
