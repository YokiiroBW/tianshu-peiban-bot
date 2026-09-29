# 多角色运行时最终门禁：静态角色 cancel 增量复核

日期：2026-09-30

## 结论

上轮报告 `644682bc0acb4eb566ff4ec6649ddaccc3529a80` 中的 P1 已关闭：静态角色在 Core 尚无 runtime 记录时，即使 pending 修正选择了非空 profile，取消也会以 expected version 0 写入显式 Core disabled fact；待选 profile 不会应用。Platform 在 Companion 与 Memory 均确认拒绝后才返回 disabled。

对 `17af1b43b432f4bd6e250192fcfad35f8b04a8c1` 相对 `4ce64212693c5bb075846b43b26f7a75ce9e4c39` 的窄增量复核未发现新的阻断项。旧五项及已覆盖的原 P1 不在本轮重新审查范围内。

## 固定范围与状态

| 产品 | 本轮审查提交 | 检出核对 |
| --- | --- | --- |
| Platform | `17af1b43b432f4bd6e250192fcfad35f8b04a8c1`（相对 `4ce64212693c5bb075846b43b26f7a75ce9e4c39`） | clean；HEAD 与固定 SHA 一致 |
| Companion | `9863800f0fcca67f59aa285ae14802d391f77e50`（未变） | clean；HEAD 一致 |
| Memory | `870d9269d33b88ed34c6445eb7d95f791b43a914`（未变） | clean；HEAD 一致 |

Platform 增量仅调整 `_resume_cancel` 对静态角色的检查，并新增一项真实 HTTPS 联合用例及对应 handoff/合同说明。未修改产品、NAS、部署配置或真实数据。

## P1 关闭依据

Platform `services/platform/role_runtime.py` 在 `current is None` 且 `row["legacy"]` 时，现在只要求 Companion catalog 仍将 actor 列在 `legacy_roles` 中。随后以 `version=0` 合成 Core 写入输入，并明确使用 `profile_id=null/profile_version=null`。Companion 会从静态角色当前已发布 Persona 写入 disabled runtime fact，不会应用 pending 修正选择的 profile。Core 写入成功后，Platform 才执行 Memory deny；任一 owner 不可确认时，状态保持 pending。

新增 HTTPS 用例 `test_cancel_static_adoption_with_unapplied_selected_profile` 覆盖了 profile 修正尚未提交时取消，并核对原 Persona 修订未变、无 profile approval/backlink、Core disabled、Memory deny。新增固定 Platform 单测还覆盖静态 cancel 的两个首次 pause 回执分支，以及未创建动态角色取消时不生成 Core 角色。此前新增的静态重启/绑定测试仍覆盖 Core 重启后继续拒绝及原 binding 保留。

### 本复核的独立最小复现

`.runtime/role-runtime-repair-review-repro-2026-09-30.py` 已改为从 Platform `17af1b43…` 与 Companion `9863800f…` 的 Git 对象提取源码。它调用真实 Platform `RoleRuntime` 与 Companion `Core`，Memory 与故障注入走合成 transport，不替换业务方法。触发顺序是：静态接管先因 provider preflight 失败留下 Platform failed 行；重配选择一个合法 profile；Core 首次 pause 在提交前断连；随后取消 pending 意图。

复现输出确认：Platform `disabled/complete`；Companion runtime 记录 `enabled=false, profile_id=null`；`_role_allows(actor, "dialogue") == false`；原静态 Persona 仍为 `Static household role`；新 profile approval 数为 0、`last_applied_target` 为空；Memory grant 为 `enabled=false`。

同一脚本另以“不选择 profile”的直接静态 cancel 路径复核 version 0→1 disabled 写入；固定 SHA 的 Platform 定向测试函数 `test_cancel_first_static_adoption_writes_core_denial_before_memory` 和 `test_cancel_uncreated_dynamic_role_does_not_write_core_role` 共 **2 项通过**。未运行完整测试套件或联合 HTTPS 链。

## 覆盖限度

- Sol 报告本轮 Platform 定向 10 项通过及新增真实 HTTPS profile-cancel 用例 1 项通过；总控报告四项 HTTPS 联合用例通过。本复核未重复这些联合成功链。
- 新增 profile-cancel HTTPS 用例验证未提交 profile 不被发布、原 Persona 修订不变及两个 owner 均拒绝；重启后拒绝和静态 binding 保留由上一项静态角色联合用例覆盖。生产 QQ、NAS 与部署数据不在本次验证范围。
- 新建 Platform 行为空时，`_begin` 仍要求静态角色首次接管保留原 Persona（profile id 为空）；非空 profile 的 pending 行可在先前 pre-apply 失败后重新配置到达。该既有入口约束未由本次增量修改，本报告不将其扩展为新 finding。
- 更大失败矩阵中标记为部分/未覆盖的 queued/preparing、外部发送、授权组合及 Gateway 容量场景仍是既有覆盖限制；本门禁结论仅针对静态角色首次 cancel 增量。

## 门禁结论

对本轮唯一 P1，结论为 **已关闭**；本增量 **未引入新的阻断项**。本报告只记录固定版本窄复核，不表示合同已冻结、产品已集成或允许部署。
