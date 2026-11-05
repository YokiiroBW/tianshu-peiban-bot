# RBH-02：模型连接证据与安全诊断

实现公共合同 diagnostic.transport；在真实HTTP执行的适当边界收集连接异常类、errno/TLS原因、host/port、阶段和发送证据。闭合时错误不回写成建连失败；headers/body/response/output出现后不能声明未发送。保留http_timing、原终态、原attempt和request/completion身份，沿用codec/client/receipt正常查询。
为Runtime的单一分类校验提供强证据，领域不另读Models数据库。不要改变模型绑定、供应商、API凭据、超时或执行真实探测/模型。依赖恢复预算由Runtime独占，不在Models另造一套domain预算。
必要用例：DNS/连接/TLS失败、HTTP前后不同失败、输出后断流/关闭异常、敏感异常message/URL凭据不泄漏、诊断序列化RPC往返、历史receipt仍能读取。对Runtime合同疑点尽早回根。

允许写入：
- `src/tianshu/models/`
- `tests/models/`
- `docs/handoffs/RBH-02.md`


先读 ../rbh-repair-2026-11-05.md 与 ../rbh-interfaces-2026-11-05.md。只读跨域定义，产品写入限本卡；全部 module.py、已有 schema.sql、runtime/product*.py、migrations.py、types、配置/依赖/部署归根。域新SQL交根注册。

整块实现+必要回归用例+静态审阅后提交；本窗口不运行测试/构建/烟测，不操作生产或真实模型/QQ。实际集中验证由根执行。完成写 docs/handoffs/RBH-02.md，回报SHA、接口/迁移/根装配、必要用例和未验证项。用户已授权对应既有窗口派遣和回执，模型GPT-6.1 Terra/max普通速度。
