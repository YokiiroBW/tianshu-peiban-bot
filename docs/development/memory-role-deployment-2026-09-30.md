# 记忆角色选择部署与验收 · 2026-09-30

记忆页面已更新到 NAS，入口 http://192.168.31.210:18446/#/memory/0 。顶部“查看哪位角色的记忆”选择当前账号有权读取的角色；创建并完成核验、开启记忆读取的角色会出现在列表中。选择会在当前账号的标签页刷新及记忆子页之间保留，登出清除。原页面实际固定默认角色，没有全角色混查；本次补齐可选角色和后台精确读取授权。

## 固定代码与集成

- NAS Platform `95ca75c3a441847c015e391f0fbaf3a179d98539`，镜像 `127.0.0.1:19550/tianshu/platform@sha256:cd1ddccd05b595fc858160dff4dc31e1c092060453af7c7c154ac59db53944e7`。
- NAS Memory `da14cfd91675b2a0d400227b916e3f3a299904b6`，镜像 `127.0.0.1:19550/tianshu/memory@sha256:0628ac682cfc6ccdcb237b886655947739438f430bf89dbd72fbcd2038b8513d`。交接最终提交 `087f9c956848887b3a86d50cadbabe8ba14e2625` 只增加文档，运行代码与部署一致。
- 产品 main Platform `dffedb231ae884c97661056e2c5bcb90d9ea950b`、Memory `9e0c39fecf7d9ebbba6ce8a37077d9b6da14dcd4`；保留另线 QQ 身份集成。NAS 从旧部署基线发布独立固定 SHA，本次不包含需要单独迁移与凭据的 QQ 身份功能。
- Companion 保持线上 `9863800f0fcca67f59aa285ae14802d391f77e50`，没有更换其镜像或容器。Gateway 保持原镜像，为刷新精确来源而重建。
- Luna 最终报告 `docs/development/reviews/memory-role-browser-review-2026-09-30.md`，提交 `d7c1fb6636544444e156a403df5c6cb7e046dbe1`；返修缓存可见性 P2 已关闭。

## 行为与授权

所有概览、人物投影、记录及分页游标绑定所选 actor。网页只提交目录里的角色和版本；Platform 从原账号与完整读取模板派生来源，Memory 以当前精确 grant 再次授权。未知角色、停用、记忆读取禁用、版本变化与跨角色游标拒绝，不自动回退。角色切换清空旧数据并取消旧请求。后台恢复到前台先隐藏旧缓存、核验后重读当前子页；可见页面每 15 秒复核，失败清空。

保留原 account/person/conversation/audience 模板。当前 self_private 授权不扩大到 group_only，也不把观察归档或共享身份直接变成所有角色共同记忆。NAS 本轮更新前只有默认角色、0 个管理角色和0个语义组，没有为验收在生产创建合成角色。

## 有效验证

- 总控独立真实 Chromium 网页→Platform→HTTPS Memory，1 项/10.037秒通过，桌面1440×1000与手机390×844；没有拦截 API。覆盖真实登录、同人物会话双角色独立概览/记录/投影、跨角色游标、伪造角色、grant撤销、重启与刷新保留。缓存恢复 pending/撤权 UI 状态用例另用接口替身，桌面/手机2项/6秒通过；证据明确分开。
- 合入产品 main 后总控真实 HTTPS 后端联合1项/4.318秒通过。schema6个有效/5个无效样例通过；4张合成截图及哈希位于 `docs/development/reviews/memory-role-browser-2026-09-30/`。
- 实现者 TypeScript、Vite build及针对性测试通过；Memory全量1001通过/6跳过。Platform全量915项存在14失败/39错误/62跳过：主要为未改的媒体regex worker、开发依赖闭包/build pin及未提供独立测试上下文，详见产品handoff；没有把全量结果记为通过。NAS固定锁依赖的两镜像构建通过。
- NAS一致冷备份 `241756160` 字节，SHA256 `01e8ab64498b5ccd7150a938b64cc6d5548b62c803059dab3a781f021becd4f1`。原账号文件及4类原人格表摘要一致；7个其他容器ID保留，NoneBot镜像和健康状态未变。
- 十服务容量guard ready、五核心health/ready通过；生产默认actor `actor:household` 通过已审计的临时origin进行只读Memory overview，语义组计数 `0`。无模型/BOT消息、无生产角色/人格写入。
- LAN返回资源与镜像SHA256一致，含memory角色选择器；匿名state/overview/subjects/records均拒绝。既有观察仍ready、群聊私聊observe_only；归档查询及Loki完整性读取通过，invalid_source_lines=0。

## 部署与恢复记录

更新目录 `/volume2/tianshu-v2-resident-updates/memory-role-20260930`；冷备份 `deployment-before.tar`，同时保存原Compose、capacity、systemd unit与capacity state。新的状态目录为其 `capacity-state`。运行Compose仍使用 `bots-20260928` 的稳定路径，Dockge已同步。配置仅增加Platform `web_memory.runtime_roles` 和Memory platform caller/reader的 `allow_runtime_roles`，保留原凭据、账号与scope。

实机检查是服务/已授权后台读取/资源/匿名门禁/日志；未掌握用户网页密码，未执行生产登录会话下的表单操作。隔离真实网页入口和当前NAS局域网均为用户指定HTTP；未验证浏览器HTTPS/Secure Cookie。本次没有迁移QQ身份数据、主动发送真实聊天、测试模型回复或修改其他独立应用。

机器证据：`memory-role-deployment-2026-09-30.json`。本记录覆盖旧的“记忆页面仅默认角色/未部署”状态。
