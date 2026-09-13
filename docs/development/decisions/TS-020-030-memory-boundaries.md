# 陪伴与记忆的实现衔接

2026-09-14。解释文字合同 1.0.0 的事件与查询边界，不改变已发布 schema/摘要。

- memory 对每次 select 请求执行其预算；companion 管理整轮累计装配预算，去重、扣除已用后再传剩余额度。估算计数不能称为实际模型用量。
- turn_committed 为异步 outbox 事件，使用已认证且获准发布的 companion 服务身份；不额外增加必填 X-Origin-Assertion-Ref，不要求晚到事件重用过期的用户会话来源引用。
- 消费者仍核验事件 owner、turn/input revision、subject/scope、当前来源/修订与墓碑，不因服务已认证就放弃数据约束。原文 pending 不冒充 archived，候选工作受理不等于确认记忆写入。
- 当前来源核验先作为可测试内部端口，实际核验后端未配置须明确不可用；测试替身不代表生产连接完成。确需新增跨服务来源读取/撤销传播协议时，先提交最小提案再由协调者发布，不猜 URL。
- 含 origin 的注册、查询、更正命令继续执行其现有验证规则。
- SQLite/WAL/FTS5 允许作为 TS-030 隔离首切片，生产 PostgreSQL、真实嵌入、真实 L0/L1 另行验证。

补充：发送前可用零预算 select + known_scope_version 核验当前授权/版本，零预算不跳过校验，不重新装配内容，也不宣称核验与外部发送原子。渠道回执查询暂为接收者内部端口，不猜共享 HTTP 路径；不可查时按期限 closed_unknown，保留不确定事实。缺权威 scope_version 的 blocked_scope 仅为核心本地待修复记录，不扩展公开 turn.phase，不伪造事件；失败轮次仍封账释放槽，恢复后检查当前修订/取消/遗忘/范围才可形成幂等事件。
