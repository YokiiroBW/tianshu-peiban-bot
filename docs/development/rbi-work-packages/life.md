# RBI-04：稳定观察原 Action、可靠唤醒与条件式规划修复

状态：用户已授权本批修复；本卡是新的 RBI 包，不续写旧完成卡。

窗口：RBW Life日程与后台执行（01a1a772-e80b-7fa0-95d3-d32d50649745）。
工作树：C:/Users/Administrator/.codex/worktrees/rbi-life/tianshu-peiban-bot
分支：codex/rbi-life-20261106
代码起点：b32a606df8238e398c9dcd58e2e145f2ecff9fb3；实际开发从根发布的准备提交开始，精确 SHA 见派发记录。

先读本工作树 AGENTS.md 最新 RBI 条、[公共合同](../rbi-interfaces-2026-11-06.md)、[方案](../domain-recovery-plan-2026-11-06.md)及[队列本卡](../domain-recovery-queue-2026-11-06.md)。本卡允许本域完整实现，不跨写其他工作树。

## 整块交付

- 先补 job 78316 的冲突字段证据，再收敛唯一 ensure_reconcile_on
- 稳定 activity/action/generation 载荷、新物理 key、最早 due 合并
- 观察元数据与 frozen_action/执行截止分离
- 本域短扫描补漏，正常/重启/恢复同路径
- 规划触发原因、语义指纹、请求摘要和合法 review 边界；有证据才改重复计算
- Life 结果回调与 RBI-09 对接；429 下核对合法 future due、原3/3终止及真实材料变化，保留新规划正常推进

## 必须覆盖的行为

- 旧键+重启+合法上下文刷新不冲突且不重复 invoke
- 404/unknown/资源等待/重复通知始终保留合法后续观察
- 观察 due 不延长原 Action deadline，睡眠/未来唤醒保持
- 纯通知且当前决策有效不新增模型；合法 review/跨日/新约束仍可重评
- 真实 ResultConsumer 调用 Life 回调时，业务提交和消费者完成证据原子一致；异常后可合法补缺，不重做 Action
- 相同有效业务输入不因通知revision或新generation_key重开预算；真实语义输入/业务意图变化仍允许新规划且遵守依赖冷却

对相关调用链补必要回归用例代码。使用项目现有夹具；真实跨域联合验证由根负责，不能以任意参数 mock 声称接口已验证。P1 条件项先补证，未证实无效工作时不擅自改变业务策略。

## 允许写路径

- src/tianshu/life/worker.py
- src/tianshu/life/events.py
- src/tianshu/life/contacts.py
- src/tianshu/life/generation_control.py
- src/tianshu/life/diary_retry.py
- src/tianshu/life/writing.py
- src/tianshu/life/rbh_transport_recovery.py
- src/tianshu/life/recovery.py
- src/tianshu/life/service.py
- src/tianshu/life/clock.py
- src/tianshu/life/operations_recovery.py
- src/tianshu/life/planning.py
- src/tianshu/life/planning_engine.py
- src/tianshu/life/plan_recovery.py
- src/tianshu/life/rbg_plan_operations.py
- src/tianshu/life/rbi_*.py
- src/tianshu/life/rbi_*.sql
- tests/life/test_rbi_*.py
- tests/life/test_activity_execution_context.py
- tests/life/test_rbg_result_consumers.py
- tests/life/test_goal_source_transaction.py
- tests/life/test_rb_plan_recovery.py
- tests/life/test_rbf_planning_recovery.py
- tests/life/test_rbh_transport_recovery.py
- docs/handoffs/RBI-04.md

精确白名单以本卡及队列 JSON 为准。全部 module.py、runtime/product、共享 types、已有 schema.sql、迁移注册、配置/依赖、生产和活跃巡检探针由根单写；本域必要新 SQL 交根注册。Runtime 的 process/composition 仅 RBI-01 可写。Life worker/结果回调仅 RBI-04 可写；Conversation 消费账本仅 RBI-09 可写。

写作调用方两文件仅修复已证实的精确 consumer_interrupted 纯计算终态误分类，复用 Runtime 分类；不改变原 Models cancelled 回执、显式取消或预算。安装代际共享锁可以先于 Sources，随后保持 Models 事实锁先于首次 Runtime lineage 写锁。Sources 的 RBI-10 scoped_refs 装配保留 Goal 和原回执同事务、各自权限及可选跳过语义。

## 接口、依赖与执行

基础依赖：RBI-00。集中装配依赖：RBI-01。公共语义已冻结，立即推进可独立实现部分；依赖方的新函数先按合同编写调用，真实装配由根集成。不把接口尚未到齐当作停止整个实现包的理由。接口不可行时尽早回报准确签名与原因，由根单写修订合同。

全包实现、必要回归用例和静态审阅后提交；允许 AST/diff 静态检查，**本窗口不运行测试、构建、烟测、生产或真实模型/QQ命令**。保留所有既有修改，不 reset/clean/stash，不操作旧任务分支。

模型 gpt-6.1-terra/max，普通速度 service_tier=default、fast_mode=false。不新建聊天、子代理或自动化，不推送、不部署、不清数据。

交接 docs/handoffs/RBI-04.md，写精确 SHA、完整实现范围、公共接口/DDL/根装配、必要用例、未执行验证及真实未完成。接口与完成回执发回本批唯一协调聊天 01a1ac6f-8494-7f82-8c4d-ec7d285f44ae；用户已授权本批协作派发与回执。
