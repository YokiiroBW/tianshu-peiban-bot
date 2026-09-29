# QQ 身份功能候选验收

状态：本地开发、独立验收和三产品串行集成完成；未推送、未部署。本文是产品验收记录，不替代安全扫描报告。以下原候选记录保留追溯，最终结论以末节为准。

## 固定候选

- Platform：`32a2bdab2bc2858689cd93637d2d7391fdaf9662`，基线 `c897e8b2351694808a1231f2ce1245425235c8e1`。
- Companion：`879c25c35f21a8eac29e777dc40ef0517df74466`，基线 `644929cd404b106a90abbf0ebe01a049f30a0521`。
- Memory：`da76ee22c1e433ec047201982191650add0092cc`，基线 `0ff79ed315a4d707fd1dd42152e41f28bf7ad2b4`。其父 `a55dd9a` 后新增 caller 令牌值全局去重修复，交付后修改已提交。

## 协调者独立复跑

在 Platform 身份独立工作树使用 `.runtime/venv/Scripts/python.exe`，合成数据和本地监听，无 NAS、真实 QQ 或真实模型调用。

1. 设置 `TS012_CONTRACT_DIR` 为协调仓库 `contracts/text-dialogue/v1`；执行 `-m unittest discover -s tests/backend -p test_bots.py -v`：15/15 通过。覆盖现有机器人行为及新增后台登录/授权/CAS/撤销、内部身份查验、别名待办与凭据分离。
2. 另设 `TS_OBSERVATION_COMPANION`、`TS_OBSERVATION_MEMORY` 指向对应身份工作树；执行 `-m unittest discover -s tests/backend -p test_observation_http_joint.py -v`：1/1 通过。此测试实际验证观察归档链的断线、恢复和策略收紧，**没有验证观察账号注册、称呼同步或档案页面读回**。

## 已确认缺口与返修

观察-only 事件当前仅落观察归档，不登记正式账号/person_id；这不满足实施卡第2项。实现者已确认，协调者已派发独立增量修复：在真实来源校验下幂等登记观察账号，保持不回复，按来源保存昵称/群名片，并补真实三产品 HTTPS 及网页读取链路。新增 exact-field 字段须版本化；不伪造 dialogue 授权，不绕 SourceAuthority。

此前 NoneBot 宿主 HTTPS 测试使用 FakeMemory；浏览器测试使用合成 HTTPS 档案 peer。它们分别证明宿主/平台/核心通信与网页交互，不证明实际 Memory 账号建档完整链路。Memory 独立 HTTP 入口测试亦不能替代端到端联合验收。

管理员授予默认空，功能目前只提供 `identity.explain` 身份说明，不新增导出、删除、设备控制或其他管理能力。跨平台绑定仍只预留，底层档案和数据归属保持独立。

下一步：独立审查其余固定差异，验收观察建档增量；等原协调者给出多角色最终版本后对齐依赖并复核。没有生产授权预置或自动部署。

## 固定候选审查补记

Luna 已完成三个固定候选源码与测试断言复核，唯一确认阻断是上述观察档案闭环；未运行产品测试，不能与协调者复跑混为一谈。报告：`C:/Users/Administrator/.codex/worktrees/66c7/tianshu-peiban-bot/QQ-IDENTITY-20260929-fixed-review.md`。

Platform 固定差异正式扫描 `9c35e337-f84a-46cd-8ff9-e55b600a32ca` 已完成，12 个来源清单文件及额外 9 个测试/合同/交接文件均审阅，无确认漏洞。报告：`C:/Users/Administrator/.codex/state/plugins/codex-security/scans/platform/32a2bdab2bc2858689cd93637d2d7391fdaf9662_20260929T164635Z_pus0n9q3/report.md`。范围不含后续补丁、另两个产品实现及生产环境；不构成整个功能或模型抗注入能力通过。工具汇总 usage 为 4,567,240 tokens（输入 4,547,077，其中缓存输入 4,291,328；输出 20,163，覆盖 3 个线程），这是工具的 rollout 汇总口径，不能当成本轮独立增量耗用。

原协调者已通知多角色最终基线集成并部署：Platform `17af1b43b432f4bd6e250192fcfad35f8b04a8c1`、Companion `9863800f0fcca67f59aa285ae14802d391f77e50`、Memory `870d9269d33b88ed34c6445eb7d95f791b43a914`。身份实现者先提交观察完整增量，再在自己的检出对齐这些固定基线；保留原配置和 role sidecar。本身份任务仍未集成/部署，没有 NAS 写入授权。

## 最终验收与本地集成

已串行快进集成到三个 `projects` 主线，未触及生产：

| 产品 | 最终 HEAD | 多角色基线 |
| --- | --- | --- |
| Platform | `511b631e456297de9185132b9c17f126524766b2` | `17af1b43b432f4bd6e250192fcfad35f8b04a8c1` |
| Companion | `fc4d04caf4b0a376316f9acc2de55ea7030348b1` | `9863800f0fcca67f59aa285ae14802d391f77e50` |
| Memory | `5a635aa5292859a1527730bbf82577af47b1df95` | `870d9269d33b88ed34c6445eb7d95f791b43a914` |

观察建档阻断已关闭。增量 Platform `9a9b1c5` / Companion `2280d35` / Memory `ae47f72` 经 Luna 精准静态复核，未确认新阻断。协调者独立复跑三服务 HTTPS 观察测试 **2/2**，包含真实 Memory 写入和读回、离线重试、同号跨 BOT/群私、同名异号及无回复。登记、别名、幂等标记与归档在同一事务中提交，缺迁移在写入前返回可重试失败。

协调者在对齐后的代码候选 `dd701b0` / `9440d57` / `6405cc0` 独立启动 `run_qq_identity_browser_real_memory_fixture.py`，运行 Playwright **桌面/手机 2/2**：真实 Memory HTTPS profile 读取、后台登录、授权持久化、撤销；截图复核通过。fixture 以 MemoryService 方法合成播种，不冒称这一个浏览器测试覆盖真实 QQ 接入。测试后已通过原 session 关闭自己启动的 fixture。最终三个 HEAD 相较这组三提交仅修改 handoff 文档。

最终实现者影响验证：Platform bot 后端 **16/16**、Ruff、TypeScript 与 Vite；Companion身份/角色/绑定/观察 **20/20**；Memory身份/观察 **5/5**。Companion 首次影响测试有一项因未设置测试合同目录失败，补 `TIANSHU_CONTRACTS` 后通过，非隐藏产品回归。UI 改版的首次旧 locator 超时已按新控件修复并通过复测。

协调者 `range-diff` 核对所有 Platform/Memory 业务补丁重基等价；Companion 仅 `tests/test_role_runtime.py` 两条人格提示断言适配，保留最新角色暂停/断线测试。三个实现树干净、diff 检查通过。集成前逐仓核准预期主线基线与无 tracked 修改；Platform 原有 untracked `build/` 保留。

根 `contracts/qq-identity/v1` 已发布，三产品候选 Git blobs 一致，schema 与 **8 个样例**验证通过。首次工作树字节对比受 CRLF/LF 差异影响，随后按 Git blob 与 JSON schema 验证确认内容一致。暂停沿用既有语义：停止新准入，已接受且 read_enabled/archive_epoch 仍有效的事件可排空归档及建档；显式来源/读取授权撤销或 epoch 失效则拒绝，区别已写入正式合同。

界面位于 `#/settings/7`，默认 QQ 授权空，内部 person_id 不显示；角色名称选择与高级手工范围保持服务端校验。身份投影不含凭据，昵称/正文不授管理员资格，管理员状态只支持 identity.explain。跨平台关联仍未开放。

后续部署需显式 Memory alias 迁移与独立 caller 配置，Companion QQ check reader、Platform 操作权限及 profile/alias 连接配置；保留角色配置和 sidecar，平台主库、QQ sidecar 与 roles sidecar 一并纳入备份。完整 AstrBot 4.27.3 宿主、真实模型抗注入表现、真实 QQ 回复和生产迁移未测；不得据本地成功宣称生产已生效。NAS 当前仍为多角色版本，本任务无 NAS 写入。
