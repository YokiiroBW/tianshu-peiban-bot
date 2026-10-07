# 2026-10-08 延迟图片及发送授权修复

用户报告：明确要求睡衣照片，角色已答应，图片未发出。已获用户授权修复并检查关联发送链路。

## 根因与实现

原图片任务生成完成，比该轮 origin 的短租期晚19秒；发送前校验使用过期 origin，异常被后台忽略，notice 永久 waiting。第一阶段恢复授权续接后，又验证到关系投影的120秒缓存年龄阻断；在隔离数据库副本上仅刷新检查时间后，真实权限与原版本核验通过。未修改生产任务、关系、授权或图片数据。

- Platform bot-delivery/v2.1 的 context 续接既有 response/direct origin：保留同一个ref，以当前注册、路由、连接、撤销位、scope/channel、注册绝对期限校验。既有v2合同不改。
- Companion 普通/流式及直接发送使用续接；主动消息原已有实时context。未知投递结果保持查询既有expression，避免重复发送。
- 图片后台保存错误码、尝试时间，30秒后重试，不再静默忽略；已完成原图不重新生成。
- 关系投影发送前由Memory实时check原版本和当前权限；去除实时check之前仅凭缓存年龄拒绝的路径。初次read仍要求新鲜投影；撤权、版本变化和未来时间仍拦截。
- legacy非Platform通道保持原协议，不声称新增续接能力覆盖全部旧适配器。

## 固定版本

- Platform最终 `cbd592b739450542b5667d9980cef0c2aaa7291d`，包含授权续接 `5c3dd0628b2a8e4265756a7438bba5fdf9747c1d`
- Companion最终 `537ea036298a239b8dae29baf7dba1f7ea4e39c5`，包含前置 `2a2afda084568ea9f8634bf087b19fb5299b11a4`
- 合同 `f3c478962f3ae9a0219f2530dcff573ddb943ee7`
- Platform最终镜像 `sha256:f5fd643d7e6d46f7d33d4bc81921f891b2c8fc6f04e4f1d2cf3076659b8f1d73`
- Companion最终镜像 `sha256:c5a692d07aa197bfdcc0f5f3294ae38f7fa645a9f44de1ff8d32cef7af9b3498`

上述产品与合同均推送各GitHub main。仅精确隔离检出变更纳入提交，根及其他工作树未提交修改保留。

## 验证

Platform相关53项通过，额外实际Companion客户端到Platform HTTP联测1项通过；合同正例5/反例4通过。末次图片与投递专项8项通过，覆盖65秒生图、context短暂不可用后的恢复和单次发送。补充关系31项通过，覆盖600秒延迟后实时check、版本变化/撤权拒绝。格式/静态检查通过。

Companion全量为761 passed /26 skipped /8 failed /101 subtests；8项缺少新检出的runtime路径配置，补齐后重跑7项通过，剩余test_source_https旧夹具缺少QQ管理员读取配置，在未改动build_runtime处失败。没有宣称全量全绿；未新增模型/GPU或QQ测试请求。

## 部署过程与证据

首轮发布曾因合同目录位于发布暂存区、超出守护允许的部署根路径，在guard arm阶段失败；十个管理容器按流程停止并保留数据库，没有盲目恢复旧库。将逐文件校验一致的固定合同放入 `/volume2/tianshu-v2-resident/contracts-delivery-f3c478962f3a`，修正两个目标服务挂载和guard配置后恢复，guard代码未改。恢复脚本首次因guard模块导入位置错误退出；已修正导入并保留原回执后重新执行；没有业务数据回滚。

首轮恢复已验证十容器、五核心TLS就绪、guard ready、57网页资源一致、Dockge同步、管理员授权/角色/天气/设置保留，33SQLite冷备通过。第二轮仅补充关系缓存校验修复，最终运行与图片回执见下方收口结果。

本地证据：`.runtime/delivery-origin-20261008/`、`.runtime/delivery-origin-final-20261008/`。NAS首轮发布目录 `/volume2/tianshu-v2-resident-updates/delivery-origin-20261008-release`；最终发布目录 `/volume2/tianshu-v2-resident-updates/delivery-origin-final-20261008-release`。内部凭据、聊天正文及图片二进制均不入库。

## 通道全链路补修

第二轮授权通过后，原任务进入Platform但被错误记为SDK unknown。继续查明：BotAdapters._call对所有请求套用64KiB限制，原请求实际2675228字节，在网络发送前被拒绝；错误处理又将无远端回执写为unknown。真实插件messages/status返回found=false。此为明确的本地发送前拒绝证据，不由“无回执”单独推断未发送。

普通机器人和observation回复两条发送路由现都与既有插件端一致支持45MiB有界请求，控制请求及响应仍保持64KiB；15项adapter回归、1按条件跳过、3子测试通过，3MiB实际HTTP覆盖两个发送入口。978fb6c仅构建过候选、未部署，最终cbd592b覆盖两条路由。

第三轮在33库冷备后，仅针对精确原图reply/attempt、请求SHA256和长度、unknown/sdk且无消息ID的记录，将其恢复pending；保留同一attempt、内容和expression。原始备份和修复回执保留；未批量重试其他unknown、未重新生成图片。最终发布目录 `/volume2/tianshu-v2-resident-updates/delivery-media-20261008-release`，本地证据 `.runtime/delivery-media-20261008/`。

## 最终收口

**已提交、推送、部署并验证原图真实发送成功。** 上海时间2026-10-08 00:39:41左右，原notice与Platform、插件端回执均为sent，QQ返回一个消息ID，同一job/attempt/图片内容，未再次调用模型或GPU。并非仅以服务健康推断送达。

最终十容器运行、五核心TLS就绪200、guard ready，33SQLite冷备通过；57网页资源与固定镜像一致、Dockge同步。管理员授权、角色、用户配置、天气和服务settings核验保留。精确版本、镜像和运行证据摘要见[机器可读记录](delivery-origin-deployment-2026-10-08.json)。
