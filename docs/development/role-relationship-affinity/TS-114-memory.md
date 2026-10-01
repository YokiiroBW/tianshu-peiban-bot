# TS-114 Memory：关系与好感权威

状态 done / local_verified_not_integrated（2026-10-01最新交付，覆盖原preparation_only门禁）。父级已放行同一既有任务的串行本地实现、验证及提交；工作树 worktrees/TS-114/tianshu-memory，分支 work/ts-114，交付 ba03202c44648a3820f7552629edd9eb5797d424。未合产品main、正式发布、部署或真实QQ/模型验收；BOT UI归属仍不冒称已核验，不新增窗口。基线和证据见本批计划、产品同号handoff及固定delivery JSON。

## 责任与边界

Memory唯一拥有(actor_id, person_id)关系绑定、策略、好感结算账本、阶段和冻结时间。relationships/为领域模块；Store只注册表/迁移挂钩，workflow旧增量通过同一结算入口。来源授权必须复用MemoryService.operation、SourceAuthority及当前角色授权，不能以服务令牌或模型字段代替用户确认。

白名单（均相对本产品）：
- src/tianshu_memory/relationships/
- src/tianshu_memory/relationship_migration.py
- src/tianshu_memory/store.py
- src/tianshu_memory/workflow.py
- src/tianshu_memory/service.py
- src/tianshu_memory/app.py
- src/tianshu_memory/auth.py
- src/tianshu_memory/source_migration.py
- src/tianshu_memory/source_recovery.py
- tests/test_relationship*.py
- docs/relationships.md
- docs/handoffs/TS-114.md

共享文件仅允许所需路由/工厂/迁移/来源guard注册及旧增量适配；不重构 unrelated 身份、浏览、Knowledge等模块，不修改已发布合同/依赖锁/其他产品，不导入插件账户表。本包独占Memory迁移主线，新增权威表必须纳入source-guard、备份、恢复和失效检查。

## 具体增量与验收

1. 绑定类型与真实权限分离；显式选关系，阈值不自动设恋人。
2. 来源可追溯事件、稳定幂等键与不同负载冲突、确定性规则/预算、阶段投影；管理调整独立审计。
3. 单对冻结原子结算：最多结算冻结时点前的合法衰减，冻结区间所有自动增减和衰减为0；解冻更新衰减游标，不追补、不回放已拒事件。短期情绪无Memory冻结字段。
4. 检查来源/授权/版本变化；冻结不阻止撤销/遗忘的隐私失效。
5. relationship_entries已有有效增量需显式映射/一次性接管、幂等迁移；同一逻辑分数不能双算，不能静默把无法映射项归给默认角色。
6. 新空合成库、旧合成库备份失败/迁移中断/重复执行/完整恢复；不开真实库。

最窄测试新 tests/test_relationship*.py；来源/迁移/事务共同变化后按AGENTS跑现有相关套件及完整产品。使用已有环境，缺依赖明确blocked，不自行安装。覆盖冻结时刻并发事件、解冻重复/重启、自然时间边界、时钟回退、最高阶段可达、负分是否衰减、来源失效与旧增量防双算。所有验证给实际命令、结果与skip。

先交付正式合同实现反馈和固定SHA；G1通过后交Companion/Platform联合。交接 docs/handoffs/TS-114.md。不得部署、生产迁移、改QQ策略、读取凭据或发送消息。

## 最新放行

父级明确授权当前已有任务内执行此包，模型正式设置已确认，UI归属待答复且不新窗口。此段记录早期放行时点的in_progress；最新状态为顶部done / local_verified_not_integrated，固定本地交付ba03202c44648a3820f7552629edd9eb5797d424。候选接口未正式生产发布。
