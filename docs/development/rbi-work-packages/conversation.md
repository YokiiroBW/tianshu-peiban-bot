# RBI-09：成功结果消费停滞、恢复观察空转与前台本域补漏

状态：用户已授权本批修复；本卡是新的 RBI 包，不续写旧完成卡。

窗口：RBW 对话接入与上下文表达（01a1a772-e842-7d80-864f-3edb1c0443ae）。
工作树：C:/Users/Administrator/.codex/worktrees/rbi-conversation/tianshu-peiban-bot
分支：codex/rbi-result-consumption-20261106
代码起点：b32a606df8238e398c9dcd58e2e145f2ecff9fb3；实际开发从根发布的准备提交开始，精确 SHA 见派发记录。

先读本工作树 AGENTS.md 最新 RBI 条、[公共合同](../rbi-interfaces-2026-11-06.md)、[方案](../domain-recovery-plan-2026-11-06.md)及[队列本卡](../domain-recovery-queue-2026-11-06.md)。本卡允许本域完整实现，不跨写其他工作树。

## 整块交付

- 先关联四个原/恢复消费任务的准确错误和事务结果，不用未关联QueryCanceled替代归因
- 复用result_deliveries/result_consumer_progress，只补pending消费者，保留not_applicable及已提交分支
- 领域统一ensure消费安排和bot-foreground短扫描；稳定receipt/action/revision/generation绑定、新物理key，不把同一opsrec引用再次命中旧failed任务
- 观察时核对原回执/当前head、消费者租约与进展、live job和合法due；无有效工作不无限返回无解释pending
- 保留真实消费者依赖，只让相互独立的合法分支公平推进；不为一分支失败回滚已提交分支
- Life回调由RBI-04负责，真实域组合用例由RBI-07负责；新增SQL仅确需持久字段/索引时交根注册

## 必须覆盖的行为

- 精确原Action已succeeded且原/恢复任务均failed时，不重复执行Action、模型或渠道发送
- Operations停用或只续观察租约时，所属前台进程仍能发现应继续的消费并按原重试预算/版本续接，或给准确终止原因
- 某分支已提交后中断、另一分支失败、重复/乱序通知和旧head到达时，只补缺项且不重复记忆/生活提交
- 未知提交先查幂等回执，来源撤回/权限变化/换代/取消仍阻止旧结果写入
- 两条原receipt的四项pending能按业务事实完成/不适用/失效，不能靠改汇总状态伪造完成

对相关调用链补必要回归用例代码。使用项目现有夹具；真实跨域联合验证由根负责，不能以任意参数 mock 声称接口已验证。P1 条件项先补证，未证实无效工作时不擅自改变业务策略。

## 允许写路径

- src/tianshu/conversation/results.py
- src/tianshu/conversation/rendering.py
- src/tianshu/conversation/service.py
- src/tianshu/conversation/expression.py
- src/tianshu/capabilities/actions.py
- src/tianshu/capabilities/rbi_result_head.py
- src/tianshu/conversation/rbg_result_recovery.py
- src/tianshu/conversation/rbg_result_ledger.py
- src/tianshu/conversation/rbi_*.py
- src/tianshu/conversation/rbi_*.sql
- tests/conversation/test_rbi_*.py
- tests/conversation/test_rbg_result_withdrawal.py
- docs/handoffs/RBI-09.md

精确白名单以本卡及队列 JSON 为准。全部 module.py、runtime/product、共享 types、已有 schema.sql、迁移注册、配置/依赖、生产和活跃巡检探针由根单写；本域必要新 SQL 交根注册。Runtime 的 process/composition 仅 RBI-01 可写。Life worker/结果回调仅 RBI-04 可写；Conversation 消费账本仅 RBI-09 可写。

## 接口、依赖与执行

基础依赖：RBI-00。集中装配依赖：RBI-01、RBI-04。公共语义已冻结，立即推进可独立实现部分；依赖方的新函数先按合同编写调用，真实装配由根集成。不把接口尚未到齐当作停止整个实现包的理由。接口不可行时尽早回报准确签名与原因，由根单写修订合同。

全包实现、必要回归用例和静态审阅后提交；允许 AST/diff 静态检查，**本窗口不运行测试、构建、烟测、生产或真实模型/QQ命令**。保留所有既有修改，不 reset/clean/stash，不操作旧任务分支。

模型 gpt-6.1-terra/max，普通速度 service_tier=default、fast_mode=false。不新建聊天、子代理或自动化，不推送、不部署、不清数据。

交接 docs/handoffs/RBI-09.md，写精确 SHA、完整实现范围、公共接口/DDL/根装配、必要用例、未执行验证及真实未完成。接口与完成回执发回本批唯一协调聊天 01a1ac6f-8494-7f82-8c4d-ec7d285f44ae；用户已授权本批协作派发与回执。
