# CONNECT-M：已有记忆能力的用户接入

用户已授权整批打通已有能力，Memory 缺网页消费接口属于本轮补缺。独占 worktrees/CONNECT-M/tianshu-memory（基线 9a3b2bed），Sol/xhigh 实现；其他产品只读，禁止 NAS 操作、真实数据/凭据或写根 contracts。交接 docs/handoffs/CONNECT-M.md，接口先行 docs/handoffs/CONNECT-M-API.md。

提供独立的受认证用户浏览 HTTP 适配及窄应用查询，服务领域规则复用 MemoryService.operation/SourceAuthority，不让平台读数据库。最少：概览、人物/群安全摘要与记忆条目分页、现有账号关联/修订审批能力的明确接入方案。先与 B 确定只读接口和服务器权威身份映射；已有显式用户写操作如在本任务暴露，必须沿用 LocalUserApplication 的真实确认、作用域、版本和幂等规则，绝不能借服务读凭据自动批准/遗忘或伪造用户确认。不支持的管理操作明确说明，不伪造按钮。

固定专用服务 Bearer 身份，只能查询服务器配置的账号、actor 与共享范围。网页传来的 person/actor 仅能缩小范围；不能读取无权人物、群、草稿、失效/撤销来源。每次请求重新校验权限，来源同步失败不降级旧权限；有界请求、响应、并发与超时，稳定错误码、no-store、不泄漏内部 token/path/origin。用真实已有数据模型和权限实现有意义列表，不能把 profiles/select 上下文检索换名当全量目录。

复用当前 service_https/server_runtime/TLS/诊断与守护规则，不另造绕过验证的服务器。knowledge_cli 已支持受控非 loopback TLS，此处无需另造知识端点。优先不迁移；确需迁移须显式备份、保持 source-guard 和旧 schema 规则并先报告总控。

要求实际应用端口与 HTTP 验证：正确授权、跨actor拒绝、凭据撤销、来源失效、空态、分页一致性/游标、预算、运行入口；夹具隔离且说明不是 NAS 验收。保持已有核心链兼容。新增跨产品 JSON schema/示例先在本产品交接提出，经协调审核后根 contracts 单独发布，禁止修改旧冻结合同。

协调：B 01a0e324-f536-72a2-acaa-6764efd2360e；U 01a0e324-f531-7842-abfc-b55e0472504e；A 01a0e324-fa67-73e2-8f50-133c9e7372bc；总控 01a0ddde-9e0f-7f40-a5c2-073b27a645ee。主动将 API 精确路径发 B/U，完成后发总控与 A 固定提交供验收。
