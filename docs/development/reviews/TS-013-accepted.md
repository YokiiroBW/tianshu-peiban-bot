# TS-013 来源授权验收

2026-09-14，实现90a4ed1、交接b2cd85e、测试修正/合入a94d34534ba0b6002bdcdab9db1d5dd899a06a16。

协调审查TLS、迁移与调用链，独立审查input/current身份、历史来源、双route及默认冻结。额外三项真实组件反例（prepare后撤actor、duplicate串actor origin、prepare后撤input ref）均403且无admission写入，未确认阻断。

协调首轮backend45项中44通过，1到期测试失败；原因确认是原浮点now+5小于序列化的配置到期时刻，产品allowed正确。修正仅测试：配置epoch的紧邻前/精确/紧邻后及水位，15项来源套件协调重跑全通过；其余30项及生产源码输入不变，无需重复。ruff/完整diff通过。最终去重45项全部得到通过证据。

快进合入。实际新TLS仍使用Core合成HTTP owner、Memory合成调用方；旧Platform+Gateway真实子链保留，不等于三产品L0。legacy缺可证明历史current503、真实QQ/网页登录/生产恢复/archived未接。仅隔离SQLite迁移验证，无生产迁移。
