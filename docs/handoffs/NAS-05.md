# NAS-05 对话探针 HTTP Host 修正

基线 f37568a。只修改部署验收探针，不更改产品来源/CSRF/认证策略。探针继续通过 platform.internal 的受验证 HTTPS 连接服务，网页请求 Host 使用配置的公开控制台 authority；健康探针保持原行为。

真实 HTTPS 回归测试：不匹配 Host 返回 403；正确 Host 返回 200，Secure/HttpOnly 会话 Cookie 跨请求保持。证书和主机校验开启。完整 packaging 88 项通过、0 skip，Ruff 通过。不是浏览器渲染证明。

NAS g 原失败保留，禁止重放初始化。后续使用新 scope、镜像标签及未占用网段，将本提交固定导出到 NAS 再执行。此交付时 NAS 对话尚未通过，日志/恢复/常驻部署仍待。
