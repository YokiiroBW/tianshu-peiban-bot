# RBH-06：结构化记忆真实接口接入

修复ContextAssembler.structured传reflection_limit，而MemoryService.projection不接受的确定TypeError。对齐真实服务合同；核对群/个人结构化证据、关系、coverage、时间/当前行为规则的既有消费，不把旧反思重新当当前人格或偏好。若反思仍有实际消费需求，复用当前学习/行为通路；不要添加忽略参数的兼容层或catch TypeError假装成功。不写Memory服务或Growth实现，确需跨界接口先回根。
必须编写一条使用真实MemoryService方法边界的联合用例，避免fake接受任意kwargs使错误漏检；可用隔离DB/既有fixture替代外部模型，但不能替代待验的真实投影方法。覆盖个人、群、无person、来源撤回/覆盖缺项保留及当前学习规则不倒退。此卡不扩大前台UI或整个上下文架构。

允许写入：
- `src/tianshu/conversation/context/assembly.py`
- `src/tianshu/conversation/context/`
- `tests/conversation/test_rbh_*.py`
- `tests/conversation/test_context.py`
- `tests/core/test_rbh_*.py`
- `docs/handoffs/RBH-06.md`


先读 ../rbh-repair-2026-11-05.md 与 ../rbh-interfaces-2026-11-05.md。只读跨域定义，产品写入限本卡；全部 module.py、已有 schema.sql、runtime/product*.py、migrations.py、types、配置/依赖/部署归根。域新SQL交根注册。

整块实现+必要回归用例+静态审阅后提交；本窗口不运行测试/构建/烟测，不操作生产或真实模型/QQ。实际集中验证由根执行。完成写 docs/handoffs/RBH-06.md，回报SHA、接口/迁移/根装配、必要用例和未验证项。用户已授权对应既有窗口派遣和回执，模型GPT-6.1 Terra/max普通速度。
