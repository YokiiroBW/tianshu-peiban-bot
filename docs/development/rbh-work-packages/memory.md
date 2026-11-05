# RBH-04：记忆恢复分类与汇总等待

接入Runtime统一未发送证据和有效预算。依赖冷却/其他探针执行中的批次保留原阶段和材料，在既有分区调度中汇总最早持久due，避免所有隔离批次各自短周期轮询。复用原恢复组件，不新增并行等待引擎；独立新材料仍能推进，明确原attempt未知的批次继续原观察。
提供 reconcile_transport_waits(limit=32) 有界端口供根接既有Operations/tick；按Models正常查询获得receipt，不跨库直读Models。不能恢复已完成的5480批、清理旧失败或重算所有历史。实际模型与纠错预算保持，仅确证未发送走新连接额度。不要修改MemoryService.projection或material_media.py，本卡不做接口消费或分类重构。
必要用例：多批同依赖只有一个实际探针、等待不制造O(批数×短周期)任务增长、源批/版本/请求和原回执保持、不同材料可继续、暂停/撤回/未知保留、有限恢复和幂等、原成功回执只应用一次。

允许写入：
- `src/tianshu/memory/consolidation.py`
- `src/tianshu/memory/consolidation_retry.py`
- `src/tianshu/memory/consolidation_wait.py`
- `src/tianshu/memory/recovery_wait.py`
- `src/tianshu/memory/recovery_policy.py`
- `src/tianshu/memory/consolidation_operations.py`
- `src/tianshu/memory/rbh_*.py`
- `src/tianshu/memory/rbh_*.sql`
- `tests/memory/test_rbh_*.py`
- `tests/memory/test_consolidation_retry.py`
- `docs/handoffs/RBH-04.md`


先读 ../rbh-repair-2026-11-05.md 与 ../rbh-interfaces-2026-11-05.md。只读跨域定义，产品写入限本卡；全部 module.py、已有 schema.sql、runtime/product*.py、migrations.py、types、配置/依赖/部署归根。域新SQL交根注册。

整块实现+必要回归用例+静态审阅后提交；本窗口不运行测试/构建/烟测，不操作生产或真实模型/QQ。实际集中验证由根执行。完成写 docs/handoffs/RBH-04.md，回报SHA、接口/迁移/根装配、必要用例和未验证项。用户已授权对应既有窗口派遣和回执，模型GPT-6.1 Terra/max普通速度。
