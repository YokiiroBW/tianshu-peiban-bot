# TS-116 Platform：关系管理API与页面

状态 done / local_verified_not_integrated（2026-10-01最新交付，覆盖原preparation_only门禁）。父级已放行同一既有任务的串行本地实现、验证及提交；工作树 worktrees/TS-116/tianshu-platform，分支 work/ts-116，交付 e3257a2cafab24c473502ecb2c55f302deffb86d。未合产品main、正式发布、部署或真实QQ/模型验收；BOT UI归属仍不冒称已核验，不新增窗口。基线和证据见本批计划、产品同号handoff及固定delivery JSON。

## 责任与白名单

服务端relationships/作为管理应用端口，通过正式服务凭据调用Memory；网页同源Cookie/CSRF/Origin进入平台。平台只持久化必要操作意图/幂等记录，不保存另一套权威分数或跨产品读库。现有管理员认证不等于自动获得新关系操作权限，必须明确配置/检查具体能力。

相对本产品白名单：
- services/platform/relationships/
- services/platform/server.py
- services/platform/__main__.py（收尾审阅发现正式serve需接入共用控制台工厂；只改薄装配，已用真实启动/登录/路由验证）
- services/platform/service.py
- apps/web/src/features/companion/relationships/
- apps/web/src/features/companion/CompanionPage.tsx
- tests/backend/test_relationship*.py
- apps/web/tests/relationships*.spec.ts
- apps/web/playwright.relationships.config.ts
- docs/platform/relationships.md
- docs/handoffs/TS-116.md

server/service仅注册与装配；CompanionPage只增加关系面板入口，由本卡单写。不改全局modules.ts、应用壳、tokens.css、锁文件、既有角色/供应商/身份授权规则，不增加大型前端引擎；沿用现有视觉组件、样式与懒加载/离开清理。

## 用户流程与验收

1. 陪伴页进入关系管理；先选角色，再选获准人物，显示该对关系类型、阶段/分数（仅获准私密视图）、冻结状态和有界事件历史。
2. 显式更改关系类型或冻结开关；文案明确“仅影响此角色与此人物；暂停自动增减和自然衰减；解冻不补扣冻结期间”。短期情绪不冻结。
3. 服务端从真实会话取得操作人；expected_version/CAS，幂等冲突明确提示。失败不显示成功，不把pending当完成。
4. 切换角色/人物立即清空旧结果；取消/版本标记淘汰迟到请求；空、未授权、服务不可用分别呈现适合的状态，不泄露被拒关系是否存在。
5. 不显示服务token、origin_ref、私人原文或协议调试字段；页面不得借关系类型修改QQ回复策略/角色grant/管理员权限。

新test_relationship*.py检验真实Cookie/CSRF/Origin、操作能力、CAS及失败回执；新relationships*.spec.ts在隔离真实后台覆盖角色人物切换、慢响应、冻结/解冻、冲突、桌面手机。先typecheck/build及最窄浏览器；共享后端装配变化按AGENTS补受影响回归。不重复未变WebGL套件、不自动安装浏览器/软件。交接 docs/handoffs/TS-116.md和固定SHA后由协调者做三产品联合；不生产登录写表单、不部署。
