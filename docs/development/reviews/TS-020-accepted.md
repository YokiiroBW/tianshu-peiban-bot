# TS-020 陪伴基础审查通过

2026-09-14。初版722c703，普通短期上下文bb155b7，续接修正/合入端点812019e287a5bf37d9b5d810a028ffea828b872e。

普通后续轮次现有持久、有界窗口：默认4轮/8192字节/1800秒，按索引限制候选；完整输入组和confirmed sent回复保留作者与来源，草稿/sending/unknown不当作已见，重启可恢复。同人物/角色/受众/渠道/绑定及范围版本匹配，当前修订/撤回/失效阻断旧内容。

追加复审的observed→permission_revoked→continuation旁路已闭合：collector首次受理固定source_context_revision，不随续句/封存/生成重标；新关联及已关联链都核验它，旧无标记保守排除。保留正常续接与两轮并行对照。无共享wire变化。

协调者复核差异与运行时路径，执行ruff check、ruff format --check及完整pytest：**55 passed，4 subtests passed**；diff --check通过。当前组件测试包括SQLite、ASGI与明确依赖替身，不是实际memory/gateway/QQ/TG的L0/L1。

已快进合入核心协调检出，TS-020按本地C1/C2基础标done。真实来源/matcher/回执查询、网页snapshot/SSE、PG与多进程仍待集成；blocked_scope与unknown都不伪造外部事实。完整群画像和跨人物查询在新契约扩展中处理。
