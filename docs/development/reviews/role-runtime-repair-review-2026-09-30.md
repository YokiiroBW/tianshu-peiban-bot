# 多角色运行时返修固定 SHA 窄复核

日期：2026-09-30

## 结论

旧报告的五项 finding 在本次固定提交中均有对应修复及定向测试。总控消息报告，新固定版真实 HTTPS 联合 `test_role_joint.py` 为 **3 passed / 38.67 秒**；本复核没有重复联合链。

当前版本仍不通过验收：独立最小复现发现 **P1：取消首次接管静态角色的 pending 意图，可能没有撤销 Companion 的静态角色权限，却把 Platform 状态记为 disabled**。修复后应针对该路径补回归并重新提供固定 SHA。

## 固定范围与检出状态

| 产品 | 基线 | 本次固定提交 | 检出核对 |
| --- | --- | --- | --- |
| Platform | `c897e8b2351694808a1231f2ce1245425235c8e1` | `4026e8f698cfa57bbc18ee0a5f74469cf1aa95f7` | clean；HEAD 与固定 SHA 一致 |
| Companion | `644929cd404b106a90abbf0ebe01a049f30a0521` | `9863800f0fcca67f59aa285ae14802d391f77e50` | clean；HEAD 与固定 SHA 一致 |
| Memory | `0ff79ed315a4d707fd1dd42152e41f28bf7ad2b4` | `870d9269d33b88ed34c6445eb7d95f791b43a914` | clean；HEAD 与固定 SHA 一致；仅合同文档变化 |

核对了旧审查的五项 finding、三个固定提交的变更、各自新增 handoff、Platform 失败矩阵及候选合同。产品代码只从上述 Git 对象读取；未修改产品检出、NAS、部署配置或真实数据。三个提交均未合并、未推送、未部署。

## 旧 finding 复核

1. **P1，停用受当前 profile 版本阻断：已关闭。** Platform 对 active 角色走 `cancel_reconcile`，使用 Companion 当前角色快照及 owner 当前版本，不使用页面提交的 profile/provider 修订；只有 Companion 与 Memory 都确认拒绝后才落为 disabled。Companion 停用同一 profile 配置时复用已固定的 `profile_revision/persona_revision`，不会重读升版后的 profile。新 HTTPS 测试覆盖 profile 升版、provider 失效后的 active 停用和双方拒绝确认。
2. **P1，Core pause 后 Memory 故障与 profile 升版使 pending 意图无法恢复：已关闭。** Platform 将一个稳定 `application_id` 和配置请求用于 pause/enable；Companion 以 `application_id`、`operator`、配置签名和 owner 版本 CAS 验证 enable。pause 时已固定的 Persona 修订由后续 enable 重用。定向联合测试在 Memory 成功但回执丢失后编辑源 profile，恢复仍发布并启用原批准修订。
3. **P2，应用 profile 丢弃既有 Persona 扩展字段：已关闭。** Companion 改为复用 `Personas.apply_profile`：保留目标 Persona 的非编辑扩展，档案未提供的可选文本按约定清除。固定测试覆盖 `custom="keep-me"` 保留及可选文本清除。
4. **P2，Persona 发布归因/回链不完整：已关闭。** 新动态角色先注册为 unpublished，再在同一操作事务中经 `apply_profile` 完成批准、发布、目标修订及 profile 回链；既有角色也复用该流程。记录 Platform 操作主体，并由 runtime 记录 `application_id`。测试核对 approval、publication、profile backlink 和 operator。`role.manage` 明确授权仍是应用权限；draft 的显式应用不是越权，也不要求另加审批页面。
5. **P2，UI retry 成功后复用旧 pending id：已关闭。** retry 结束后，UI 只在仍 pending 时保留当前 client id；到达 active/disabled 终态则清掉。固定 Chromium 流程覆盖失败、retry 成功、编辑同一角色后再次提交并得到新意图。

另外，Companion 对旧 enable 收据重放会同步当前角色状态，不会用历史回执复活 disabled 角色。provider 撤销本身仍不是停用阻断项：固定实现的 disable 路径不查 provider。没有将其列为 finding。

## 新 finding

### P1 — 首次接管静态角色的 pending cancel 可跳过 Companion 拒绝

Platform `services/platform/role_runtime.py` 的 `_resume_cancel`（固定 SHA 第 302–313 行）只从 `catalog["roles"]` 查当前角色。如果查不到且 `cancel_source_stage == "start"`，就直接进入 Memory revoke。相同 Companion catalog 的 `legacy_roles` 没有参与判断。

Companion `core.py` 的 `manage_role("platform", {"operation":"list"})`（固定 SHA 第 1139–1148 行）会把仍由部署静态配置提供、尚无 runtime 记录的角色单独放在 `legacy_roles`，而不是 `roles`。此时 `RoleRuntime.allowed()` 对该角色仍允许静态路径（固定 SHA `src/tianshu_companion/role_runtime.py:42`：没有 runtime 记录时返回允许）。因此，未创建显式 disabled runtime 记录不能算作 Companion 已确认停用。

**最小复现：** `.runtime/role-runtime-repair-review-repro-2026-09-30.py` 从上述两个固定提交 `git archive` 提取源码，调用真实 Platform `RoleRuntime` 和真实 Companion `Core.manage_role`/`_role_allows`；只将 Memory owner 和 Core 首次请求的瞬时断连作为合成 transport。步骤为：

1. 将 `actor:household` 配为 Companion 部署静态角色。初始 catalog 为 `roles=[]`、`legacy_roles=[actor:household]`；runtime 记录为空，`_role_allows(actor:household, dialogue)` 为 true。
2. 开始 Platform 的静态角色接管，令首次 Core pause 在发送前返回 `dependency_unavailable`。Platform 保留 `state=pending, stage=start`。
3. 取消此 pending 意图。恢复 Core 后，Platform 仅查 `roles`，跳过 Core 写入；Memory grant 被拒绝，Platform 返回 `disabled/complete`。

复现输出：

```json
{
  "cancel_result": {"state": "disabled", "stage": "complete", "legacy": true},
  "companion_runtime_roles": [],
  "companion_legacy_roles": ["actor:household"],
  "companion_runtime_record": null,
  "companion_allows_dialogue": true,
  "memory_grant": {"actor_id": "actor:household", "version": 1, "enabled": false}
}
```

这会让 Platform 显示已停用、Memory 拒绝新 grant，但 Companion 仍按旧静态角色放行；此前的静态 binding 仍可能调用该角色。问题只在已有静态角色尚未完成首个 Core pause、随后取消该 pending 操作时出现，但状态结果与实际 Core 权限不一致，故列为 P1。

**修复建议：** `legacy=true` 且 cancel 来源为 `start` 时，不能走“Core 无角色即可略过”的新动态角色分支。应对 `legacy_roles` 中的静态角色执行一次带 owner 当前版本 CAS 的显式 disabled 写入，持久化 Companion runtime 拒绝；随后再确认 Memory deny。Platform 仅在两个 owner 都确认后落为 disabled。新增回归应覆盖“静态接管首个 pause 前断连 → cancel → Core 恢复”，并断言 Companion runtime 状态、对话授权以及重启/旧意图重放后仍 disabled。

## 验证与边界

- 独立复现只执行了上述最小失败场景；没有重复成功联合链或运行完整测试套件。脚本来自固定 Git 对象，不覆盖或替代产品业务函数。
- 据总控本轮消息，新固定版三项真实 HTTPS 联合测试为 **3 passed / 38.67 秒**，结果记录位置为协调工作区 `.runtime/role-runtime-repair-acceptance-20260930/results` 与 `docs/development/reviews/role-runtime-repair-acceptance-2026-09-30.json`。这份 JSON 不在当前独立协调检出中，因此这里把联合结果标为总控报告，未声称本复核独立验证其文件内容。
- Platform 失败矩阵仍标有部分或未覆盖场景，包括若干 queued/preparing/外部发送交界、授权组合和 Gateway 容量组合；它们是验收覆盖限制，不是本次新 finding。候选合同尚未发布到根 `contracts/`。没有真实 QQ、付费模型、NAS 或生产数据验收结论。

## 交接

总控可按新 finding 修复后再次提供固定 SHA。本次复核报告提交只记录审查结论，不改任何产品文件。
