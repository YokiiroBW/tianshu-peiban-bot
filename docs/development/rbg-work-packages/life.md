# RBG-01 Life与Models：旧终态退出当前规划

先读../rbg-repair-2026-11-05.md和AGENTS最新覆盖。用户已批准实施，既有Life聊天及独立工作树，GPT-6.1 Terra/max普通速度；不新建聊天/子代理，不生产操作，不运行测试/构建/烟测。

白名单：src/tianshu/life/{planning_engine,plan_recovery,planning,generation_control,operations_recovery}.py；src/tianshu/models/（module.py/schema.sql除外）；src/tianshu/conversation/generation.py；新增life/rbg_plan_*.py、life/rbg_plan_*.sql、models/rbg_*.sql；tests/life/test_rbg_plan*.py、tests/models/test_rbg_*.py、tests/conversation/test_rbg_admission*.py及原对应定向用例；docs/handoffs/RBG-01.md。不得写life/worker.py、life/recovery.py及共享装配。

完整实现：

1. 统一检查watermark、plan_generations终态、generation_requests、Models准入证据。不能让superseded/cancelled/expired旧规划无限占用当前等待位置；不能将NULL改false冒称未外发。
2. 复用同ID准入/取消账本，证明旧owner/迟到RPC不能再准入，然后按watermark CAS原子切换指针与唤醒。业务退役独立于未知结果/费用；仍有效原请求按原ID续接，过期作废请求不能重新收费执行，新需求遵守真实语义材料和原预算。
3. 覆盖freeze后、claim后、提交后ACK前、换代前后中断；正常新建换代与来源/取消语义保持，不能固定秒数后把unknown推定失败。
4. 接入现有领域恢复入口，提供只读分类清单及有条件执行，单对象重复执行幂等，方便根恢复现场悬挂样本。不能仅helper或平行诊断状态机。
5. 本地planning超时按准备/准入/执行/应用分阶段续接，先查原账本，不扩大超时或全量重算。
6. 新Models公共接口尽早给根准确签名/DTO/错误语义；共享types、module、migrations、worker.plan接线由根负责。

必要用例代码：NULL+superseded+当前指针+缺Models、cancelled recovery、已准入ACK丢失、迟到RPC阻断、并发唤醒仅一个当前计算、新材料推进而旧unknown保留、事务中断不丢唤醒、来源失效/用户取消不可恢复。

静态审阅后仅提交本卡文件，交接SHA/接线/恢复入口/未运行用例。用户已授权向主协调01a1a72e-d357-7ba2-9e01-6373ebb7741e发回执。
