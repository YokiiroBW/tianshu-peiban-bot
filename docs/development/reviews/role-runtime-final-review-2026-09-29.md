# 多角色运行时固定 SHA 最终审查

日期：2026-09-30

审查性质：对固定提交做只读审查；没有修改产品仓库代码。

## 固定范围

| 产品 | 基线 | 审查提交 |
| --- | --- | --- |
| Platform | `9bed67e2de8a101f55f9a95e9a8b1288719ce8c2` | `c897e8b2351694808a1231f2ce1245425235c8e1` |
| Companion | `c24230216c41818c425d0b0a61f2f23ba0dfe578` | `644929cd404b106a90abbf0ebe01a049f30a0521` |
| Memory | `10214b2f7dbfb6990e986f38f92c8103e07d6b9e` | `0ff79ed315a4d707fd1dd42152e41f28bf7ad2b4` |
| Model Gateway | — | `e4f112f02d28a1ded134126feed33f15e003ec20`（未变） |

固定版本差异为 Platform 25 个文件、Companion 9 个文件、Memory 9 个文件。产品检出开始变化后，后续代码核对和复现只从上述 Git 对象读取；没有跟读 Sol 的未提交改动。

验证只运行了隔离的合成复现，脚本及输出位于本机忽略目录：

- `.runtime/role-runtime-review-repro-2026-09-29.py`
- `.runtime/role-runtime-review-repro-2026-09-29.out.txt`

脚本用 `git archive` 提取 Platform/Companion 固定 SHA，操作临时 SQLite 和合成档案/记忆授权。没有读取真实数据、连接生产服务或重复真实 HTTPS/Chromium 联测。协调者在任务消息中报告真实 HTTPS 四服务 + Chromium 联测 **1 passed / 28.34 秒**；本审查未独立复跑。

## 发现

### P1 — 角色停用仍依赖 profile 当前版本，失败后 Companion 和 Memory 仍保持启用

Companion 的 `apply` 对有 `profile_id` 的请求总是读取 profile 并比较当前 `profile_version`，即使目标是停用。profile 在角色当前配置后升版，而停用请求仍携带旧版本时，Companion 会在 pause 前拒绝操作。Platform 随后把本地意图记为 `failed`，但没有远端变更或补偿。代码位置：`src/tianshu_companion/role_runtime.py:117`、`services/platform/role_runtime.py:249,309`。

固定 SHA 的复现中，profile 从 v1 改到 v2、旧页面仍提交 v1 时，停用结果为 `failed/start/version_conflict`；Companion 角色仍 `enabled=true`，Memory grant 仍 `enabled=true`，而 Platform `active=false`。因此停用按钮失败后，控制面本地状态和两个授权 owner 已经分叉；不能将结果描述为角色已停用。

停用应使用 owner 当前版本和已应用的不可变人格修订，不重新读取可变 profile。Companion pause 与 Memory revoke 都确认前应保持可重试的“未停用”状态；需要按 owner 精确版本处理回执丢失和部分成功。

### P1 — 中途失败后 profile 升版会把意图永久留在 pending，无法修正或取消

Platform pause 和 enable 都从同一意图发送 `profile_id/profile_version`；Core pause 成功后，Memory 阶段失败，再重试会继续该阶段，随后仍以相同 `profile_version` 调用 enable。Companion 每次都拿当前 profile 重新比较版本，所以档案在等待期间升版后，enable 恒定返回 `version_conflict`。Platform `_record_error` 只会把 `stage=start` 的错误转成 `failed`；`pending` 角色又不能通过 `_begin` 发起新意图，retry 只能重放旧的固定版本。代码位置：`services/platform/role_runtime.py:211,260,309,396`、`src/tianshu_companion/role_runtime.py:117`。

隔离复现顺序为：Memory 首次失败 → Companion 已 pause → profile v1 升到 v2 → retry。结果为 `pending/provider_checked/version_conflict`；新 client id 携 v2 修正被 `role_configuring` 拒绝；原 retry 仍然失败。此时 Companion 已暂停、Memory grant 已打开、Platform 不认为 active。旧意图没有用户可用的恢复入口。

同一操作应在 pause 时固定并持久化精确 `profile_revision/persona_revision`；后续 enable/disable 重用固定修订，不重新解析 profile。对无法继续的阶段还需提供具备幂等回执和补偿语义的取消/停用入口，并使它与后台 resume、重启恢复互斥。

### P2 — 既有角色换档案会丢弃 persona 的非编辑扩展字段

Companion `RoleRuntime.apply` 对既有角色直接把 profile 的 `revision["content"]` 传给 `_write_revision`，替换目标角色的整份 persona 内容。档案可包含其自身的扩展字段，但它不一定包含目标角色的扩展字段。现有 `Personas.apply_profile` 则明确先复制目标角色的非编辑扩展字段，再以 profile 的四个编辑字段覆盖，并清除档案中为空的可选字段。代码位置：`src/tianshu_companion/role_runtime.py:126`、`src/tianshu_companion/personas.py:1418`、`tests/test_persona_authoring.py:199`。

合成复现导入了 `custom="keep-me"` 的既有角色，再通过角色管理应用一个不含该字段的 profile；新发布的 `runtime.pin()` 得到 `custom=null`，而旧修订仍保存在历史中。既有 Persona 用例则断言新发布修订必须保留该字段。这是已应用 persona 的可观察字段丢失，影响接管的旧角色以及后续携有扩展字段的角色；新角色没有旧扩展字段，不触发这一损失。

应复用 `Personas.apply_profile` 的原子业务语义，或抽出共享领域函数：编辑字段按档案替换、空可选字段按约定清除、非编辑扩展保留，同时绑定角色运行时版本和回执。

### P2 — Role 管理已显式授权，但 Persona 发布历史没有复用现有操作审计

权限门确实检查已认证 Web session 的配置 principal 是否具有 `role.manage`；因此选择 draft profile 并点击应用是显式授权动作，本审查不把“档案只有 draft”认定为越权，也不要求增加独立审批页面。代码位置：`services/platform/role_runtime.py:31`、`services/platform/auth.py:10`。

问题在于发布来源记录不一致。新角色走 Personas `_seed`，发布记录显示 `kind=seed`、`operator=deployment`、修订来源 `initial_config`，尽管触发动作是控制台从 profile 创建角色。既有角色换 profile 会留下 `kind=profile_apply` 的 publication，但 `operator=platform`，没有对应的 Persona approval/operation 记录；profile 的 `last_applied_target/profile_revision/target_revision` 也没有更新。角色运行时自己的操作表有 request id、摘要和结果，但 Persona 作者历史不能据此显示哪位控制台操作者应用了哪个 profile 修订。`Personas.apply_profile` 的既有语义则在一个事务内提交角色修订、批准、发布指针、操作账本及 profile 的应用回链。代码位置：`src/tianshu_companion/role_runtime.py:126`、`src/tianshu_companion/personas.py:1395,468`。

这不是缺少用户确认，而是显式 `role.manage` 应用没有留下与既有 Personas 应用流程等价、可追溯的审计事实。应在同一事务中记录真实应用主体、源 profile 修订、目标修订和操作 id；新角色初次创建也不应把控制台 profile 应用伪记为 deployment seed。

### P2 — retry 成功后 UI 没清除旧 pending id，后续修改得到 idempotency_conflict

`save()` 把 `pendingId` 作为下一次 `client_id`；普通 apply 成功时会清除它。但 `retry()` 成功后只 refresh 和显示 notice，没有清除 `pendingId`。用户接着编辑同一角色时会重复使用旧 `client_id`，同时请求摘要已因新 role version/新字段改变；Platform 的 intent 表按同一 client id 拒绝不同摘要，返回 `idempotency_conflict`。重新选择角色会清空 pending id，但也重置表单。代码位置：`apps/web/src/features/companion/RoleManager.tsx:174,216`、`services/platform/role_runtime.py:389`。

retry 到达终态后应清除或替换 pending id；成功重试之后再提交同角色修改应产生新请求 id。

## 已闭合与审查边界

- 早期审查发现的旧 enable 收据重放可复活已停用角色，固定 Companion 代码已改为按当前角色状态 `_sync_live(current)`，并有停用后重放及重启回归用例；该问题在本次固定 SHA 上关闭。
- provider 撤销不阻断停用：固定 Platform 的 `_provider` 在 `row["enabled"] == false` 时直接返回（`services/platform/role_runtime.py:189-191`）。用固定提交的真实 `ProviderCatalog` 将线路禁用后，停用结果为 `disabled/complete`，Companion 与 Memory 均变为 disabled；脚本输出记录了该非问题结果。本报告不把 provider 不可用作为停用缺陷。
- Companion 对动态角色在对话、Memory 读写和 outbox 提交处检查实时角色状态及 turn 能力快照；Memory 通过持久化精确 actor grant 授权，禁用 grant 会覆盖旧静态 allowlist。Platform 对显式 provider 校验 provider id/revision 和可用状态，失效时失败，不静默回退。新增 Web 路由受现有 Origin、CSRF、登录会话校验及 `role.manage` principal gate 约束。
- 12 项失败矩阵保留了“部分/未覆盖”边界。尤其是全部 origin 撤销组合、Persona 网页 allowlist 反向组合、动态角色 queued/preparing/external-send 精确交界、disable 竞态及 Gateway 每 provider 容量没有全部联合注入。这里把它们作为覆盖范围限制，不当成已验证漏洞，也不声称 12 项全部通过。

## 结论

这些固定提交还不能作为无条件通过的最终版本。停用仍依赖 profile 的当前版本、跨阶段 profile 版本竞态无恢复入口、以及既有角色换档案丢扩展字段都需要修复；Persona 发布记录与 retry UI 状态也需要补齐。provider 撤销后的停用复现成功，不列为缺陷。收到修复提交后应按精确新 SHA 窄复核这些不变量、旧回执重放、Persona 扩展保留和 UI 重试后再提交验收结论。
