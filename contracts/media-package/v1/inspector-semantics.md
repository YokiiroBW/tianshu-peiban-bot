# TS-099 候选清单语义

## 输入与字节

应用输入为 `(caller, manifest_bytes, expected_manifest_digest, cancellation)`。caller 是独立受信上下文，不在 manifest 内自称权限。manifest_bytes 是 1..1MiB 的 UTF-8 无 BOM JSON 对象；禁止非法 UTF-8、重复键（含嵌套）、未知键、注释、尾逗号、JSON 后的其他内容。可有合法 JSON 空白/尾换行，不要求跨语言重序列化。digest 为小写 64 位 SHA256，对**完全相同原始字节**验证，正文不包含自身摘要。

所有计数字段只接受十进制整数词法，不接受 bool、小数、指数或字符串。UUID 使用小写 D 格式、不能全零。BV 为 ASCII 大写 BV+10字母数字；CID 为 1..20 位正十进制字符串，无前导零；staging_ref 为 `[A-Za-z0-9][A-Za-z0-9_-]{0,63}`。

唯一键：schema_version=1、package_id、staging_ref、library_id、provider=bilibili、bvid、layout、media_extension、selected_parts、files。没有绝对路径、URL、凭据、客户端权限标志或任意 raw。

## 文件集与身份

selected_parts 为 1..128 个 `{cid,episode_number}`，CID 不重复。single 恰好一项、集号必须 null；multipart 集号是 1..999999 唯一正整数，来源只选一个分 P 也允许 multipart。files 为 3..512 个 `{path,kind,cid,size_bytes,sha256}`，各字段必填；cid 在根文件为 null。

固定布局：

| 布局/类型 | 路径 | cid |
| --- | --- | --- |
| single video | video.mp4 或 video.mkv，与 media_extension 一致 | 唯一所选CID |
| single nfo | movie.nfo | null |
| multipart 根 nfo | tvshow.nfo | null |
| multipart video | Season 01/S01E{集号至少2位}-cid-{CID}.{media_extension} | 对应CID |
| multipart nfo | 与对应video同stem的.nfo | 对应CID |
| source | source.json | null |
| 可选根 poster | poster.jpg 或 poster.png，最多一张 | null |
| 可选 multipart episode_thumb | 与对应video同stem的-thumb.jpg/png，每CID最多一张 | 对应CID |

恰好一份 source.json；每个所选 CID 恰好一份 video，multipart 每 CID 恰好一份 nfo 并有一个 tvshow.nfo；single 恰好一份 movie.nfo。其他组合、未选 CID、额外/重复文件、错误集号映射均拒绝。无封面/缩略图可以通过字节预检，报告元数据/图片验证仍未完成；不得造空文件。

目标包目录由服务端按 single=`bilibili-{BV}-cid-{CID}`、multipart=`bilibili-{BV}` 计算，目标为可信 library root 的直属包目录，不接受客户端指定目录名。staging_ref 指向可信注册的**包目录**，源/目标绝对路径只存在可信端口内部，绝不写入报告。

path 使用原始严格 POSIX 相对路径，最多240字符、最多2段；禁止空段、`.`/`..`、反斜杠、冒号/ADS、NUL/控制字符、绝对/UNC/盘符、尾空格/尾点、Windows设备名及大小写折叠重名/前缀冲突。先拒绝再构造值对象，不允许规范化掉恶意输入。路径须匹配上表，不能任意命名。

每文件 size_bytes 为 1..137438953472（128GiB）；nfo/source 最大4MiB，poster/episode_thumb 最大32MiB；总声明字节不超过1099511627776（1TiB）。SHA256 小写64位。正式预检还有更小的服务端读取预算，不能仅凭清单上限放开IO。

## 只读报告与执行边界

清单摘要/形状/跨字段校验先完成，再解析可信 caller/staging_ref/library_id 的作用域，未授权不得列目录或探测文件存在。scope 包含注册版本、许可、到期和受信根；读取前、每文件读取前及最终报告前复核 scope，失效拒绝。预检不能派生生产发布权限。

实际文件集与 manifest 必须完全一致，忽略清单自身不适用：manifest 从参数传入，包内只准 listed files 及必要父目录。额外空目录也拒绝。完整流式 SHA256 与长度验证，读取前后重验大小/修改标记与路径，变化拒绝；验证过程中不得写源或目标。

授权根及祖先、包内目录/文件的 symlink/reparse 一律拒绝；来源与目标注册根不得相同或互相包含。目标包目录任何已存在对象均为 target_exists，无“内容一样就覆盖/重用”。空间以目标卷当前观察，至少满足总字节+64MiB余量；无法获得空间/可用性报 target_unavailable，不能猜足够。

本候选只承诺在任务独占的本地临时沙箱执行。静态链接拒绝+读取前后复核**不等于解决恶意并发目录换链接**；M0-006-G2/生产强路径句柄、真实NAS与断电持久性仍是后续门禁。

默认预检并发1，无等待队列；超出busy。单次真实读取最多32GiB、512文件、120秒，流式缓冲至多1MiB；通过受信构造预算可以下调，不能由 manifest 升高。取消必须关闭自有句柄并传播，不写成功报告。每次重新完整预检，不持久保存或伪造幂等回执。

结果状态仅 `inspected/rejected`，**恒为 grants_file_operation=false**；最多100条固定问题（超出增加单一 truncated 标记），只回包内相对路径/字段位置与固定code，不回传异常原文、绝对路径、文件内容。必含 package_id（只有合法时）、manifest_digest、scope_revision（已授权时）、观察UTC时间、已校验文件数/字节数、错误和 `unverified=[media_decoding,quality_policy,metadata_semantics,publication,indexing,media_server_import,production_path_races]`。

冻结错误词：`invalid_manifest/digest_mismatch/unsupported_version/invalid_identity/invalid_layout/invalid_path/duplicate_path/invalid_file_set/budget_exceeded/unauthorized/scope_changed/source_missing/source_changed/unsafe_path/hash_mismatch/size_mismatch/target_exists/target_unavailable/insufficient_space/busy/timeout/io_failure`。输入不合法不用任意原始异常充当错误，未知异常不能返回 inspected。取消不是 rejection，传播取消。

本轮不解析或信任NFO/source正文的业务值；它们的内容也按完整字节哈希核对，路径/身份来自受限清单，**metadata_semantics 永远列未验证**。这样不在资产预检里复制平台元数据归一器。TS-093补媒体解码与元数据成品一致性，TS-094/095补持久发布/服务器实读。
