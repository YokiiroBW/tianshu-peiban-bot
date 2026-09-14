# TS-022 多角色核心验收

2026-09-14，实现2229c9d、续接修复fbd79c0、交接/集成a759f1755c3e9b2afa6b6e5b3fd5e2b6b8072d79。

协调与独立有限审查P/A、facts、迁移、后台check、回执/collector与既有双域上下文。首轮确认跨角色插话使方案续接绑定其他角色。已改精确actor/person/audience/conversation索引下前序LIMIT1，不预滤状态/版本、不放宽_check_dependency；最近无效或未发送候选不会回退旧方案。

协调审查修复diff及新群私/插话/多人物/失效/在途/画像回归，显式启用TLS及固定Memory69b29f3联合后完整复跑：100 passed、47 subtests passed，17.59秒，无skip。初始ruff/diff通过，修复diff通过；产品报告修复格式/静态也通过。

快进合入。P/A来源、共享会话两槽/顺序、新facts与ingest-actors、Memory后台check均可进入三方联调。组件TLS的Platform/Memory仍合成owner；legacy缺来源历史、生产恢复/确认/归档等限制保留。不以100项组件测试标L0通过。
