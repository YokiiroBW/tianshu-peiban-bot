# 角色—人物关系与好感本地开发交付（2026-10-01）

状态：**三产品已在独立 worktree 实现并完成隔离本地验收，待串行审查集成、正式合同发布及部署。** 本文覆盖同日早期 preparation_only/blocked 记录。用户后续已明确授权在同一既有任务按 TS-114 → TS-115 → TS-116 实施、修复、验证和本地提交，不新增窗口、不推送或合产品 main。模型配置沿父级已确认的 gpt-6.1-sol / xhigh 记录；BOT UI 归属无本机受支持核验能力，仍不标为已核验，不改内部数据库。

## 固定产品与交付层级

| 任务 | 产品 / 分支 | 固定基线 | 本地交付 |
| --- | --- | --- | --- |
| TS-114 | Memory / work/ts-114 | 9e0c39fecf7d9ebbba6ce8a37077d9b6da14dcd4 | ba03202c44648a3820f7552629edd9eb5797d424 |
| TS-115 | Companion / work/ts-115 | fc4d04caf4b0a376316f9acc2de55ea7030348b1 | ea5c044719bfa76de384b0b69bbfd7ed1439996b |
| TS-116 | Platform / work/ts-116 | dffedb231ae884c97661056e2c5bcb90d9ea950b | 最终 SHA 见同日 delivery JSON |

实际路径分别为 `worktrees/TS-114/tianshu-memory`、`worktrees/TS-115/tianshu-companion`、`worktrees/TS-116/tianshu-platform`。产品协调检出 main 与线上版本均未改变，本批不把本地交付称部署或真实使用验收。线上 95ca75c/da14cfd/9863800 是此前 CURRENT 记录，本轮没有在线复核，不把它推断为当前在线实测。

根原有 7 个已跟踪修改和大量既有未跟踪文件保留，不 reset/clean/stash。根交付提交只选本批文档/候选文件和可分离的本批追加状态，混合文件其余用户修改继续未暂存；精确分离结果见交付清单。不收录运行产物、凭据、临时 receipt、截图、归档或 dist。

## 责任和用户确定的规则

- Memory 是 pair 关系/好感、事件、冻结/衰减、历史和旧增量接管的唯一权威；Platform 不写另一套状态，Companion 不复制计分规则。
- 静态人格只有角色业务内容和表达，不塞身份/鉴权/反注入协议。人物来自可信身份链，关系类型不授予管理员、角色、工具、数据或机器人回复权限，也不自动设伴侣。
- 逐角色—人物冻结自动增减和自然衰减；解冻不追补、不回放冻结事件，短期情绪独立。来源/授权撤销、遗忘和纠正继续有效。
- 群首版不自动计分、不公开私聊关系/称呼/分数。人工调整要真实后台能力、CAS 与原因审计。旧多 scope 或归属不明分数 pending，保留原值、不猜、不合并、不清零。
- 数值与策略采用已收敛默认且可配置；范围 ±1200、最高阶段实际可达。行为负向/修复事件没有可信核实器时拒绝，不由模型定分。

## 最终模块与本批增量

Memory `relationships/` 负责领域与应用、`relationship_migration.py` 负责显式合成迁移，既有 Store/workflow/app/auth/source hooks 只作必要适配。TS-116 接线补齐人工原因持久回读和最近 20 条 history，仍使用原事件 result JSON 和 pair 索引，不新增权威表/自动迁移。

Companion `relationships/` 分离合同、客户端、投影/版本及既有 outbox 交付；Core/app/clients 薄装配。私聊表达与群公开投影分开，生成/发送前版本及权限复核，成功真实互动才生成稳定候选。

Platform `relationships/` 分离配置、合同、TLS、应用与同源边界；前端关系面板懒加载，选择/迟到响应、CAS 冲突、未知结果与取消明确。完整审阅发现正式 serve 构造旧控制台，已让默认应用和正式入口共用一个控制台工厂，并用实际 TLS 内部监听 + HTTP 公共入口测试注册/会话/登录。

## 合同与验收事实

`contracts/role-relationship/candidate-v1` 仍未正式发布。现有 schema/examples 字节不改；三个产品显式使用 LF schema hash `f3b588591411f1ed4b8aa7c9003d201530644d4dfc02294bdd9e9d7f847214a3`。README 已按实际五业务 DTO、内部 read/check/manage/settle/history 及平台 catalog/people/view/manage 收敛；外层信封、managed/check/history 待正式发布纳入，无本批新增功能承诺。

Memory 专项 76 通过，完整回归 1080 passed / 1 已基线复现失败 / 6 缺 MCP skipped。Companion 最终专项与相关联合 41 通过；完整回归记录 704 passed / 5 failed / 1 skipped / 101 subtests passed，随后修正本轮合同路径测试并专项复验，未重新跑完整套件，4 个产品基线问题仍保留。详细历史和边界以各产品 handoff 为准。

Platform 最终完整回归及逐项基线对比、适用静态检查见 `docs/handoffs/TS-116.md` 和 delivery JSON，不宣称全绿。真实隔离 Platform/Memory/Companion HTTPS 五项通过；模型/QQ/渠道为合成替身。桌面及手机 12 项实际浏览器通过、14 图保存在忽略的 runtime；浏览器使用 127.0.0.1 HTTP，内部明确 CA 验证，不安装根证书、不绕过 HTTPS 或改安全设置。两项旧角色浏览器测试内置忽略证书错误，本轮不执行，单列未覆盖。

## 四层状态与下一步

| 层级 | 本批实际状态 |
| --- | --- |
| 用户期望 | 关系独立于权限；可信人物；可选单对冻结；群隐私；明确失败/冲突 |
| 实现及本地验证 | 三产品实现、专项、真实隔离联合和 12 浏览器验收已完成；全量保留已分类历史失败 |
| 集成及上线 | 产品 main 未合入、合同未正式发布、未部署/生产迁移 |
| 真实使用 | 没有真实 QQ/模型或原用户账号验收；橙汐原登录/个人 QQ 绑定阻塞未解决 |

**现在先做：协调者审查固定 SHA 与候选信封/权限/迁移边界，确认正式合同版本，然后按 114 → 115 → 116 串行集成。** 集成前对照各产品 main 是否变化，冲突只在隔离集成检出解决；发生行为变化后重跑对应专项/真实三服务及 UI 门槛。不要直接把包含未部署 QQ 身份的当前 main 整体更新 NAS。

之后才制定明确线上版本选择、alias/角色 sidecar/关系 DB+checkpoint 一致备份恢复、首次关系安装/旧 pending 处理、显式分离凭据和权限、容器/NAS 演练及回滚计划。真实 QQ/模型、长时容量及用户体验另做验收；本批没有授权自动执行这些生产步骤。
