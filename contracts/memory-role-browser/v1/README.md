# 记忆页面角色选择 v1

本增量定义登录网页到 Platform 的角色选择；不修改 Platform–Memory 已发布来源合同。请求形状见 `request.schema.json` 中各路由定义，合成正反例见 `examples.json`。HTTP 路径为 `POST /api/web/memory/{state,overview,subjects,records}`，会话与 CSRF 门禁仍由 WebConsole 执行。

读取请求可成对增加 `role_id` 与 `role_version`，必须对应当前登录操作者的服务器角色目录。完全省略两字段保留原默认角色语义；未知、null、版本过期、停用或未开启记忆读取时拒绝，不回退到其他角色。前端不能提交 account、scope、origin、服务凭据或上游 URL。目录返回友好名称与不可读原因。

Platform 以原 `web_memory.entry_id` 的 account/person/conversation/audience/route 模板派生每次请求的独立来源，只替换经过服务器授权的 actor。响应返回前复核会话、角色版本、scope 与来源。Memory 需要同时显式开启 caller 和 browser reader 的 `allow_runtime_roles`，并核准当前精确 actor grant；原注册账号和完整 scope 模板继续生效。

各角色概览、人物投影、记录和游标沿用完整 actor scope 隔离。跨角色游标拒绝；self_private 模板不能借角色选择扩大到 group_only。共享人物身份不表示共享私有记忆。

网页切换角色立即清空旧结果并取消旧请求；选择按登录账号保存在当前标签页，退出清除。恢复前台先隐藏缓存并核验，成功后重新读取当前角色当前子页；可见页面每 15 秒检查权限，失败清空。此轮没有服务器撤权推送；浏览器截图与代码已下载的内容不是可远程撤回的数据。

实现与联合验收见 `docs/development/memory-role-view-plan-2026-09-30.md` 及部署记录。本文件由总控发布；运行实现不加载此附加 schema。
