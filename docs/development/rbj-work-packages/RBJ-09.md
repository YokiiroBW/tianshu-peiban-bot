# RBJ-09 native-inflight与not_started机械观察

冻结合同2.1第18-19节为准。仅本卡白名单，其他文件归根或另一实现者。独立新worktree/branch，不续旧完成卡。

- src/tianshu/runtime/jobs.py
- src/tianshu/runtime/rbi_execution_budget.py
- src/tianshu/runtime/rbj_execution_repair.py
- src/tianshu/runtime/rbj_native_admission.sql
- tests/runtime/test_rbj_native_admission.py
- tests/runtime/test_rbj_execution_repair.py
- tests/runtime/test_rbi_execution_budget.py
- docs/handoffs/RBJ-09.md

完整实现和必要用例代码，静态diff审阅后提交并回根；不运行测试、构建、烟测、生产、真实模型/QQ。根集中必要验证、串行发布及原对象对账。禁止改已部署旧SQL迁移内容，新命名迁移归本卡交付、全局注册归根。
