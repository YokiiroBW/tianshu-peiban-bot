# TS-050 接线矩阵与最小接点提案

2026-09-14。此文件是实际集成边界记录，不是完成声明。只消费 text-dialogue/v1 1.0.0；profile-memory 不在本批通过范围。

| 段 | 实际产品/公开应用端口 | 传输与替身 | 当前边界 |
| --- | --- | --- | --- |
| 本地演练来源 | Platform `Origins.issue/resolve` | 真实认证、SQLite、resolve HTTP；合成部署身份 | local_rehearsal，不是 QQ/TG SDK 登录 |
| Core 来源客户端 | `Origins` → `JsonService` | 实际 HTTPS，显式 AsyncHTTPTransport + 临时 CA | 库构造端口支持；环境配置 build_runtime 没有 CA 接点 |
| Memory 来源客户端 | `Authenticator.resolve` | 固定 HTTPS、trust_env=False、默认 CA | 无 transport/verify/SSLContext 接点，不能信任隔离临时 CA |
| 首账号登记 | Memory `create_app/MemoryService.resolve/register` | 实际 Memory HTTP/SQLite；已获批方案 B 使用真实平台受信 resolve 应用端口 | 回填已实证；不代表产品 Authenticator HTTPS 正向通过 |
| 人物/会话回填 | Platform `prepare_mapping/confirm_mapping` | 已文档化的受信 Python 端口，无共享 HTTP wire | 只接真实响应，不能提交夹具 response 冒充产品回执 |
| 收件、防抖、双轮、恢复 | Companion `create_app/Core` | 真实 HTTP、Core 单所有者持久状态 | Memory 来源阻断时，只能验证入站/封存/失败关闭 |
| 最小记忆与零预算 | Memory `create_app` select gate | 真实 HTTP；source_authority=None | select/consume/revise 必须 503；无正向召回资格 |
| 后台当前来源 | Platform `verify_current_sources` | 受信应用端口，验证入口、tuple、修订/墓碑 | archive_verified=false、scope_version_verified=false 原样保留 |
| Memory 来源/候选/确认 | `LocalWorkflow` 仅接受 LocalFixtureSources | 本批不加载该替身 | 缺真实 source observation/current invalidation/confirmation/candidate 应用接点 |
| 模型配置 | Platform `Models.publish/snapshot` → Gateway `HttpConfigSource` | 真实发布、SQLite、实际 loopback HTTP | 精确版本、来源用途、配置撤销可联合验证 |
| 模型转发 | Gateway `create_app` → 录制模型 | 实际 HTTP，目标/IP 显式登记；模型是唯一允许替身之一 | 记录字节、次数、回执、unknown，无外部模型 |
| 有序发送 | Companion `Sender` → 录制渠道 | 接收器可录制 published send wire | 只有 Core 真正生成后才可证明发送；不能手填回复/turn 表 |
| Chat Audit / profile / L1 | 未纳入指定提交组合 | 未接入 | 不声称通过 |

## 已发现的阻断

1. **TLS-MEMORY**：Memory `auth.py:Authenticator.resolve` 内部直接创建 `httpx.Client(timeout=5, follow_redirects=False, trust_env=False)`，只准 HTTPS。临时可信 CA 无法配置；不设置 verify=False、不改系统信任、不改 certifi 文件、不 monkeypatch 库。最小产品接点：可注入受控同步 transport 或 SSLContext，环境工厂支持明确 CA 路径；保留 HTTPS/目标/重定向检查。
2. **SOURCE-AUTHORITY**：非 local_fixture 工厂设置 source_authority=None；HTTP select/consume/revise 返回 503。现有 SourceAuthority.verify/current 仍依赖 Memory 内部 sources/lineage，写入只在 `LocalWorkflow`，且其构造器明确要求 LocalFixtureSources。仅对接平台 partial flags 无法完成来源核验或维护当前账本。
3. **OWNER-FACTS**：平台不证明 Core turn/input_revision/delivery、Memory scope_version 或归档回执。需要各权威自己的受信应用接点组合，来源更新/撤销与 Memory 失效须确定事务边界；不能查询其他产品内部表来拼接。
4. **CANDIDATE/CONFIRMATION**：当前候选提交/确认签发同在 fixture-only LocalWorkflow。即便完成来源适配，也缺可用于本组合的真实来源候选与用户确认入口。

## 协调提案

协调者已在本任务中明确批准 B：当前使用真实 Platform resolve/prepare/confirm 应用端口的显式同进程组合适配，验证真实 Memory identity 与 Core 回填后的 source-unconfigured 503。B 不等于产品 Authenticator HTTPS 正向接通或完整 L0 通过；产品原 Authenticator 另有真实 HTTPS 失败复现。A 中 Memory 后续修复由协调者在 TS-031 集成后另行分派。

不增加共享 wire、不修改产品、不读取产品内部表作为跨产品权威。实际测试结果与完整场景状态另见 TS-050-report.md 和机器轨迹。
