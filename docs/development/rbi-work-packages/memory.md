# RBI-03：新鲜回执驱动续接、原批恢复与本域补漏

状态：用户已授权本批修复；本卡是新的 RBI 包，不续写旧完成卡。

窗口：RBW Memory写入历史与共享整理（01a1a772-e983-7ad0-b572-61409937e393）。
工作树：C:/Users/Administrator/.codex/worktrees/rbi-memory/tianshu-peiban-bot
分支：codex/rbi-memory-20261106
代码起点：b32a606df8238e398c9dcd58e2e145f2ecff9fb3；实际开发从根发布的准备提交开始，精确 SHA 见派发记录。

先读本工作树 AGENTS.md 最新 RBI 条、[公共合同](../rbi-interfaces-2026-11-06.md)、[方案](../domain-recovery-plan-2026-11-06.md)及[队列本卡](../domain-recovery-queue-2026-11-06.md)。本卡允许本域完整实现，不跨写其他工作树。

## 整块交付

- 同次权威观测原子同步领域投影和 Runtime 记账，直接传递新鲜 outcome
- head/版本变化后重读，不以旧 queued 投影判断物理结果
- 特定本地程序失败的正常原批恢复入口
- 从现有 recover 抽取有限到期元数据检查并复用同一安排函数
- P1 核查280批高用量的真实片段身份、输入摘要、游标范围和应用进度；有重复证据才修复

## 必须覆盖的行为

- 连续两次 unknown 且第二次本地 queued 不再触发错误永久失败
- head 改变/租约过期/迟到成功不能覆盖当前选择
- 漏通知/漏入队在 Operations 停用时仍可由本域发现
- 原批材料覆盖可追查；原 unknown 和原预算不变
- 同 revision 不同片段可正常推进；同片段已应用/仅已知缺失且无新可用输入时不再触发无效模型计算；不把 completed_with_missing 说成完整成功

对相关调用链补必要回归用例代码。使用项目现有夹具；真实跨域联合验证由根负责，不能以任意参数 mock 声称接口已验证。P1 条件项先补证，未证实无效工作时不擅自改变业务策略。

## 允许写路径

- src/tianshu/memory/consolidation.py
- src/tianshu/memory/consolidation_retry.py
- src/tianshu/memory/model_outcome.py
- src/tianshu/memory/rbh_transport.py
- src/tianshu/memory/workers.py
- src/tianshu/memory/recovery.py
- src/tianshu/memory/operations_recovery.py
- src/tianshu/memory/rbi_*.py
- src/tianshu/memory/rbi_*.sql
- tests/memory/test_rbi_*.py
- tests/memory/test_rbh_memory_transport_recovery.py
- tests/memory/test_rbf_wait_recovery.py
- docs/handoffs/RBI-03.md

精确白名单以本卡及队列 JSON 为准。全部 module.py、runtime/product、共享 types、已有 schema.sql、迁移注册、配置/依赖、生产和活跃巡检探针由根单写；本域必要新 SQL 交根注册。Runtime 的 process/composition 仅 RBI-01 可写。Life worker/结果回调仅 RBI-04 可写；Conversation 消费账本仅 RBI-09 可写。

## 接口、依赖与执行

基础依赖：RBI-00。集中装配依赖：RBI-01。公共语义已冻结，立即推进可独立实现部分；依赖方的新函数先按合同编写调用，真实装配由根集成。不把接口尚未到齐当作停止整个实现包的理由。接口不可行时尽早回报准确签名与原因，由根单写修订合同。

全包实现、必要回归用例和静态审阅后提交；允许 AST/diff 静态检查，**本窗口不运行测试、构建、烟测、生产或真实模型/QQ命令**。保留所有既有修改，不 reset/clean/stash，不操作旧任务分支。

模型 gpt-6.1-terra/max，普通速度 service_tier=default、fast_mode=false。不新建聊天、子代理或自动化，不推送、不部署、不清数据。

交接 docs/handoffs/RBI-03.md，写精确 SHA、完整实现范围、公共接口/DDL/根装配、必要用例、未执行验证及真实未完成。接口与完成回执发回本批唯一协调聊天 01a1ac6f-8494-7f82-8c4d-ec7d285f44ae；用户已授权本批协作派发与回执。
