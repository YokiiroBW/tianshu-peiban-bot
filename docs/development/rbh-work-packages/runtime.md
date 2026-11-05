# RBH-01：共享恢复预算、探针与分类指标

实现公共合同中的 note_not_sent_on/computation_budget_on 及唯一证据校验。保留总尝试序号，新增审计分类，计算预算和连接预算分离，inspect/claim/reserve/fail 所有入口一致；单探针、共享退避、取消/撤回/换代/unknown 不弱化。证据不能仅信 error_code；历史只接受精确回执与无发送/无输出时序。历史已失败登记幂等，不给已有后继回拨 due。分类 SQL 交根注册。给出该两接口及字段的确认回执，若发现合同实质问题先回报根，不等待其他领域开发。
数据库指标保留原累计数，新增等待、事务冲突、SQL故障、提交不确定的有效分类；不把所有 BaseException 都称数据库错误。小而准确地复用既有异常分类，不新建恢复服务。
必要用例：4次已证实未发送后计算额度仍保留、16次连接额度有界、重复证据只记一次、真实发送/unknown/缺证据不扣除、错ID/代际/owner/版本拒绝、多lineage单探针、过期probe迟到结果隔离、所有claim入口预算一致、普通事务计数兼容。

允许写入：
- `src/tianshu/runtime/recovery_policy.py`
- `src/tianshu/runtime/database.py`
- `src/tianshu/runtime/rbh_*.py`
- `src/tianshu/runtime/rbh_*.sql`
- `tests/runtime/test_rbh_*.py`
- `tests/runtime/test_recovery_policy.py`
- `tests/runtime/test_database.py`
- `docs/handoffs/RBH-01.md`


先读 ../rbh-repair-2026-11-05.md 与 ../rbh-interfaces-2026-11-05.md。只读跨域定义，产品写入限本卡；全部 module.py、已有 schema.sql、runtime/product*.py、migrations.py、types、配置/依赖/部署归根。域新SQL交根注册。

整块实现+必要回归用例+静态审阅后提交；本窗口不运行测试/构建/烟测，不操作生产或真实模型/QQ。实际集中验证由根执行。完成写 docs/handoffs/RBH-01.md，回报SHA、接口/迁移/根装配、必要用例和未验证项。用户已授权对应既有窗口派遣和回执，模型GPT-6.1 Terra/max普通速度。
