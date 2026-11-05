# RBH-05：规划提前唤醒重排与连接恢复

修复已证明的未来retry被合并进更早observe任务后丢唤醒：原61730在20:01:51执行，repair_due20:02，run早返回没有重排。所有提前/合并/无变化返回路径必须确保未完成最早repair/review仍有持久队列唤醒，原子CAS，保持actor最新指针。不要取消队列合并或用新模型调用规避等待。覆盖重复/并发/通知插入，真实睡眠/暂停/取消/已终态不无故唤醒。
同时接入Runtime有效预算与Models确证未发送分类；保留旧plan3次实际计算额度、原unknown观察和RBG退役准入封存。历史原154条中含Life，不能只修未来wake却保留领域旧总attempt次数判断。
必要用例用生产同构时序：未来retry+更早observe合并、提前运行后仍有正确future wake，且到期只执行一次；generation/capability通知并发、无更早通知也自动到期；原ID/迟到原完成/撤回/换代、已发送未知不重放。正常恢复入口复用既有plan operations；如需新增有界reconcile端口交根接线。

允许写入：
- `src/tianshu/life/planning.py`
- `src/tianshu/life/planning_engine.py`
- `src/tianshu/life/plan_recovery.py`
- `src/tianshu/life/computation_retry.py`
- `src/tianshu/life/rbg_plan_operations.py`
- `src/tianshu/life/rbh_*.py`
- `src/tianshu/life/rbh_*.sql`
- `tests/life/test_rbh_*.py`
- `tests/life/test_planning.py`
- `tests/life/test_rbg_plan_retirement.py`
- `docs/handoffs/RBH-05.md`


先读 ../rbh-repair-2026-11-05.md 与 ../rbh-interfaces-2026-11-05.md。只读跨域定义，产品写入限本卡；全部 module.py、已有 schema.sql、runtime/product*.py、migrations.py、types、配置/依赖/部署归根。域新SQL交根注册。

整块实现+必要回归用例+静态审阅后提交；本窗口不运行测试/构建/烟测，不操作生产或真实模型/QQ。实际集中验证由根执行。完成写 docs/handoffs/RBH-05.md，回报SHA、接口/迁移/根装配、必要用例和未验证项。用户已授权对应既有窗口派遣和回执，模型GPT-6.1 Terra/max普通速度。

协调补充：允许修改 generation_control.py 及相关既有恢复用例，完成规划、日记、写作共用恢复预算的真实消费；沿用现有生成语义，不扩大产品功能。

追加允许写入：
- `src/tianshu/life/generation_control.py`
- `tests/life/test_computation_retry.py`
- `tests/life/test_rbf_generation_recovery.py`
