# RBH-03：成长原批恢复与有效预算

接入Runtime合同，不能继续用单调attempt_number>=4作为所有失败的计算预算。实际模型/纠错仍4次；只有Models原回执严格证明未发送的连接尝试才归连接预算。每次失败先正常归档原attempt，再幂等note、依据统一预算和依赖due安排已有队列；保留冻结请求/材料、retry_key、history和CAS。
提供 reconcile_transport_waits(limit=32) 的已有恢复组件端口，有限发现旧blocked transport_not_sent批次，逐原历史attempt取得Models权威回执，当前来源/授权/版本/代际有效后按正常domain路径复评并安排工作。网络恢复不要求用户手工逐批点重试。不得清零原attempts、改原失败成功、盲恢复invalid_upstream_response或unknown，也不得把新材料混入旧冻结批。
必要用例：原4/4全未发送批可按新预算继续而序号递增；真实生成4次不绕过；混合unknown/缺证据、取消/撤回、版本变化；并发扫描只安排一次；有限扫描与依赖仍冷却不热轮询；成功后原输入版本reviewed。

允许写入：
- `src/tianshu/companion/growth.py`
- `src/tianshu/companion/growth_retry.py`
- `src/tianshu/companion/growth_policy.py`
- `src/tianshu/companion/growth_operations.py`
- `src/tianshu/companion/rbh_*.py`
- `src/tianshu/companion/rbh_*.sql`
- `tests/companion/test_rbh_*.py`
- `tests/companion/test_growth_retry.py`
- `docs/handoffs/RBH-03.md`


先读 ../rbh-repair-2026-11-05.md 与 ../rbh-interfaces-2026-11-05.md。只读跨域定义，产品写入限本卡；全部 module.py、已有 schema.sql、runtime/product*.py、migrations.py、types、配置/依赖/部署归根。域新SQL交根注册。

整块实现+必要回归用例+静态审阅后提交；本窗口不运行测试/构建/烟测，不操作生产或真实模型/QQ。实际集中验证由根执行。完成写 docs/handoffs/RBH-03.md，回报SHA、接口/迁移/根装配、必要用例和未验证项。用户已授权对应既有窗口派遣和回执，模型GPT-6.1 Terra/max普通速度。

协调补充：允许修改 `tests/companion/test_rb_growth_async.py` 中受影响的 RecoveryPolicy 测试替身，补齐新公共方法；不改变原断言语义。

同调用链必要用例补充：`tests/companion/test_*growth*.py` 仅对受本次合同变更影响的替身及断言适配，真实来源/CAS/unknown 断言不放宽。
