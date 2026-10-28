# 自主生活第三批 R15 正式发布

2026-10-28。AL06、AL09/10 和 AL12 的已验收改动已合入、正常推送各产品 main，并在 **192.168.31.230** 正式运行。AL13 已从这个版本隔离并行开发，尚未集成或部署；18 项队列没有全部完成。

正式镜像 `sha256:f1d30d60e0b6afe43c0ae6a2cd9a24a01b7bbb47cd06c9fe7a71f6c06bc75288`，容器 `bdb1f876044f8d3536c53c2448437b2aa9e3148c8d2a1c42ca36be8278f7decf`。Companion `16ca5a81490255a892d697d80b5be0fa43644688`、Memory `9453e5e3fd43c80a41f9bd4601f0181e2e7c6db5`；Platform 运行 `177e2eb`（含文档 main `4ef6edb`），Gateway `0f491be`，application `48eee53d`。完整身份及回执 SHA 见[发布 JSON](autonomous-life-wave3-release-2026-10-28.json)。

先与记忆系统和单容器线程核对并取得串行发布写权。四产品 main 均正常快进，根合入远程基础设施历史为 `75a815f`；容量守护冲突保留已含远程修复的统一版本，原88项守护测试85P/3S。原混合协调检出及index保留，无force/reset/clean/stash。

完整离线 Dockerfile 从原五个固定基础镜像重建 venv，未重新解析依赖。66 wheel中只替换两个精确候选，同时更新lock/dependencies/release。实际读取R14、已验收候选和R15三个镜像：C/M安装payload与候选相同，其余64 wheel、56 application runtime、71前端文件、四命令入口和解码器保持原字节；三镜像pip check通过，三个专属核验容器已清理。

1126配置只更新原Gateway同owner/entry/scope租约引用一行，无新增权限；Compose仅换镜像，37mount、25逻辑绑定、凭据引用、日志策略和数据保持。用户提取、分段、摘要、画像、召回算法及旧API不变。

新停写点备份六个PG命名空间、25逻辑源及三个来源guard，导出合计 **16,512,054 bytes**；保全1126配置/14,812,950 bytes及Compose/systemd/原guard控制记录。每份导出恢复到新UUID专属数据库并核前后指纹，scratch数据库及客户端全部清理。没有恢复/迁移生产PG、扫描/复制媒体或NAS Loki chunks。这是本次逻辑恢复点，不是完整物理灾备，原配套物理/辅助恢复资料保留。

两轮各五组双并发、共20次原鉴权ready均200，原九项探针均ok；第二轮距签发312秒。六业务、五观测、漫画插件、25原生只读namespace、独立actor guard、71HTTP静态文件、原生只读链路查询及五源ACK通过。18446网页200，原CA与hostname严格验证的18443匿名401，18765 SDK listener 200；旧13 cold/restart=no，新r8守护arm/active/enabled、心跳正常，无新failure。

构建期间原r7因宿主空闲低于原20GiB下限正常停R14（Exit0，无OOM）。首次冷备在healthy前置拒绝，未执行停写/备份。核本地副本SHA后仅清旧构建tar/wheels/frontend副本，恢复约0.95GB，未降阈值或清原failure；随后沿真实停写点备份并以新r8启动R15。实际中断约29.8分钟，不能称无中断发布。后续三个未被容器引用的旧构建镜像元数据按精确ID/no-prune清理，其余依赖冲突保留，仅释放少量元数据；R14恢复镜像、候选、R15、五base、PG client和在用镜像均保留。

辅助探针两次失败保留：UID10001不能signal root PID1，运行状态检查改为原Docker health实际root身份，数据库/actor/graph/guard仍10001；图查询工具变量替换错误修正后，已通过的10次ready/namespace在首轮汇总复用，未重复计数。没有修改产品健康条件。

业务联动按精确安装字节复用上一批SQLite/PG结果，原全套测试限制保留在[候选交付](autonomous-life-wave3-delivery-2026-10-28.md)。本次不冒称重做业务重放、真实QQ登录/收发、付费模型、外站账号或带登录浏览器业务验收。回退须先停新写者并保全切换后事实/unknown回执；旧R14不识别新observer pending键，不能只换镜像继续跑同库、换键补发或启动旧13并写。

[18项队列](autonomous-life-development-queue-2026-10-23.md)及JSON已同步本批R15部署；AL13的TS390/391/392各独占Memory/Companion/Platform，TS393由协调者冻结合同与集中联合验收，见[执行卡](autonomous-life-al13-execution-2026-10-28.md)。
