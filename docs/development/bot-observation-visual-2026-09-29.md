# 机器人观察页视觉统一

用户反馈 NAS 观察页原生账号下拉框、fieldset 方框与其他设置页不一致。确认现组件的账号 select 未置于已样式化表单内，`.bot-form fieldset` 不能命中 `fieldset.bot-form` 本身。按既有设计令牌和供应商设置页修复，不重新设计全站。

可见专项任务：机器人观察页视觉统一与响应式修复，Sol/xhigh；创建回执 `client-new-thread:fe4faa93-5657-43ab-a98f-20068be81ddc`（排队回执，不等于已开始执行）。产品工作树 `C:/YOKI/Codex/bot-observation-ui-worktree`，分支 `codex/bot-observation-visual-20260929`，基线 `04cbe8a6c0652c4d1ddec572afce1b895416f145`。

独占 BotObservationPanel.tsx、bots.css、必要 BotAdapterPanel.tsx、对应浏览器用例及交接。账号选择套用统一表单，群私策略采用桌面并排/移动单列卡片，状态中文徽章、统计分组、统一字号和间距；历史撤销保留明确含义与次要位置。保留 fieldset/legend 无障碍语义、禁用和焦点可见状态。不得修改后端、认证、策略行为、数据与保存逻辑，不新增依赖或全局主题，不动小屋。

视觉验收必须提供已连接、群私策略、发现/档案、错误/锁定状态的真实浏览器截图，并与已有设置页对照；同时核对桌面和手机无横向溢出、字段可操作。类型、构建及受影响用例通过后提交固定 SHA。协调者负责审查集成与现有 NAS 平台更新，不重跑数据库迁移或机器人登记。

本文件是任务说明，尚非交付或部署证据。
