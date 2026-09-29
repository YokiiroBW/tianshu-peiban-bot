# QQ-IDENTITY-20260929 · observation v3 精准增量复审

日期：2026-09-30
方式：只读以下固定 Git 对象及其父提交；未查看活动工作树、未运行测试、未启动正式扫描。

## 固定 SHA 与父提交

| 产品 | 增量提交 | 父提交 |
|---|---|---|
| Platform | `9a9b1c58d700f2371ee3fc46e9c5876cc45dc35f` | `32a2bdab2bc2858689cd93637d2d7391fdaf9662` |
| Companion | `2280d359ed73c21e4b5dca3d6528cc334506554f` | `879c25c35f21a8eac29e777dc40ef0517df74466` |
| Memory | `ae47f7248255a1b2fe3e7570877fb4a334803e9a` | `da76ee22c1e433ec047201982191650add0092cc` |

三仓的候选 `README.md`、`schema.json` 和 `examples.json` Git blob SHA 完全一致。

后续角色对齐后的产品代码提交为 Platform `dd701b0f78504770e9e1e48854143622b69cf88b`、Companion `9440d57bba0e767686b36e2a39ff521aa69ebb36`、Memory `6405cc0fc33a096798db6cf0567282cf14620c78`。协调者已核对 Platform 观察补丁 range-diff 等价；本次独立 range-diff 亦显示 Companion v3 增量 `2280d35` 与 `9440d57` 等价，Memory v3 增量 `ae47f72` 与 `6405cc0` 等价。新对齐范围另带入此前已存在的身份/凭据功能提交，不属于本次 v3 改动。

## 复审结果

在本次指定增量中，未确认新的来源伪造、跨账号归属、重复入账、事务半提交、隐式回复或重试丢失问题。

- **来源绑定：** Companion 和 Memory 均精确接受 observation v2/v3 事件形状；v3 的 nickname/group_card 包含在完整事件摘要内。Platform 持久化摘要，并按 `source_ref`、摘要及 scope version 回查已接收来源；Memory 以独立 HTTPS Platform verifier 凭据核对摘要、source_ref、机器人、作者、会话、事件和 scope version 后才登记。
- **注册与事务：** Memory `register_observed()` 只接受 Platform verifier 返回且与完整事件摘要相同的证明；同一账号已有 person 映射时复用，否则在当前 SQLite 事务内创建 person/account。别名、幂等事件标记和 observation archive 行在同一个 `Store.transaction()` 中写入；任何后续失败会回滚全部写入。缺少显式 QQ alias migration 时，事务在建档前以可重试的 503 退出。
- **幂等与回滚：** `source_ref` 与 `source_digest` 共同约束重放；相同摘要作为 duplicate，摘要冲突拒绝。别名按 account/kind/bot/group 更新，事件标记与 archive 一起提交。Memory 测试覆盖迁移缺失时不建账号、迁移后重复事件、伪造摘要拒绝，以及无 jobs/turn_inputs；实现共用 SQLite 事务保证异常回滚。
- **旧版兼容：** v2 仍被 Platform、Companion、Memory 接受并只归档；Memory 仅对 v3 调用身份登记。v3 明确携带可空 nickname/group_card；NoneBot 与 AstrBot 的适配器将 SDK 名称清理后放入 v3。三份 vendored RPC 的修改保持一致。
- **无隐式回复：** Companion observation outbox 仍只调用 archive intake，适配器观察回调不认领回复；Memory 新路径没有建立 dialogue origin、role 或 turn。测试断言无 jobs、turn_inputs，三 HTTPS 联测断言 `self.sent == []`。
- **重试：** Companion inbox 对可恢复错误保留 pending 并退避重试；Memory 不可用或 alias migration 尚未就绪不会先提交 person/archive。Platform alias todo 只保存身份和显示字段，持久游标允许越过失败页，后续回绕重试；`finish_alias()` 仅在 Memory 确认后删除。
- **测试证据：** Platform `test_observation_http_joint.py` 新用例运行 Platform、Companion、Memory 三个本地 HTTPS 服务，覆盖 Memory 离线后恢复、同号跨 BOT/群私、同名异号、档案读回和零回复。新增浏览器 fixture 实际启动 Memory HTTPS app 并由 Platform 档案代理读取；fixture 直接在 Memory 内种合成账号/别名，UI 测的是真实 Memory 读接口，不是 observation 写入链。写入链由三 HTTPS 联测覆盖。协调者另行负责执行测试。

## 已决的 scope 语义

Platform 的来源 verifier 允许已在旧 observation revision 下持久接受的来源，在当前 `read_enabled` 与 `archive_epoch` 仍有效时完成归档；现有 `test_offline_pause_recover_via_authenticated_https` 正是验证这个 drain 行为。v3 将 person/alias 登记绑定到同一次归档事务，因此暂停 group/private observe 后，先前已接受但尚未完成的 v3 事件仍可能随后创建 profile/alias。

协调者已确认该行为符合产品语义：暂停停止新消息准入；已接受且当前 `read_enabled` / `archive_epoch` 仍有效的来源允许排空归档并登记该来源的 QQ 档案/称呼，这不产生回复或管理权限。显式来源撤销、关闭 `read_enabled` 或使 `archive_epoch` 失效时必须拒绝。暂停不等同来源授权撤销，因此不要求新鲜 revision 覆盖既有已接受队列。

## 结论

指定增量已修复 observation-only 档案缺口；来源授权、同事务登记/归档、幂等、回滚、v2 兼容、无回复权限和重试路径未见新的确认阻断。暂停后的迟到 v3 建档语义已由协调者确认。协调者报告真实 Memory 浏览器和三服务观察 HTTPS 联测通过；本报告未自行重跑测试。
