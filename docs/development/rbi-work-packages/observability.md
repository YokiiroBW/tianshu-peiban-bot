# RBI-06：业务推进、物理未知与逐项采样覆盖

状态：用户已授权本批修复；本卡是新的 RBI 包，不续写旧完成卡。

窗口：RBW 日志后端与持久链（01a1a772-e81f-7a10-8a21-5221bed680f8）。
工作树：C:/Users/Administrator/.codex/worktrees/rbi-observability/tianshu-peiban-bot
分支：codex/rbi-observability-20261106
代码起点：b32a606df8238e398c9dcd58e2e145f2ecff9fb3；实际开发从根发布的准备提交开始，精确 SHA 见派发记录。

先读本工作树 AGENTS.md 最新 RBI 条、[公共合同](../rbi-interfaces-2026-11-06.md)、[方案](../domain-recovery-plan-2026-11-06.md)及[队列本卡](../domain-recovery-queue-2026-11-06.md)。本卡允许本域完整实现，不跨写其他工作树。

## 整块交付

- 当前业务阻塞/恢复中/已恢复但原结果未知/待样本/明确终态分开
- last_evaluated 与 last_recurred 分开
- 按准确对象和版本关闭缺口，分别报告账本/生产者/真实样本覆盖
- 为既有 chain_probe 提供有界SQL修订：当前候选、历史未知页、指定原对象分开；根单写活跃探针，不新建采样器
- 报告 live job/合法due/消费者进度与观察租约的差别；保留 unscoped/uncorrelated 事件，不能猜因果

## 必须覆盖的行为

- 新非重叠批完成不能关闭原批问题
- 业务恢复不抹去物理 unknown，旧 unknown 不无限误报同一业务卡住
- 历史 append-only 账本不改写；汇总不执行模型或领域写入
- QueryCanceled 后该项为 unknown；单对象查到/有限页为空/账本输入完整均不能代替整体业务或生产者覆盖

对相关调用链补必要回归用例代码。使用项目现有夹具；真实跨域联合验证由根负责，不能以任意参数 mock 声称接口已验证。P1 条件项先补证，未证实无效工作时不擅自改变业务策略。

## 允许写路径

- scripts/ops/rbf_monitor_summary.py
- src/tianshu/operations/statistics.py
- src/tianshu/operations/rbi_*.py
- tests/operations/test_rbf_monitor_summary.py
- tests/operations/test_rbi_*.py
- docs/handoffs/RBI-06.md

精确白名单以本卡及队列 JSON 为准。全部 module.py、runtime/product、共享 types、已有 schema.sql、迁移注册、配置/依赖、生产和活跃巡检探针由根单写；本域必要新 SQL 交根注册。Runtime 的 process/composition 仅 RBI-01 可写。Life worker/结果回调仅 RBI-04 可写；Conversation 消费账本仅 RBI-09 可写。

## 接口、依赖与执行

基础依赖：RBI-00。集中装配依赖：RBI-02、RBI-03、RBI-04、RBI-05、RBI-09。公共语义已冻结，立即推进可独立实现部分；依赖方的新函数先按合同编写调用，真实装配由根集成。不把接口尚未到齐当作停止整个实现包的理由。接口不可行时尽早回报准确签名与原因，由根单写修订合同。

全包实现、必要回归用例和静态审阅后提交；允许 AST/diff 静态检查，**本窗口不运行测试、构建、烟测、生产或真实模型/QQ命令**。保留所有既有修改，不 reset/clean/stash，不操作旧任务分支。

模型 gpt-6.1-terra/max，普通速度 service_tier=default、fast_mode=false。不新建聊天、子代理或自动化，不推送、不部署、不清数据。

交接 docs/handoffs/RBI-06.md，写精确 SHA、完整实现范围、公共接口/DDL/根装配、必要用例、未执行验证及真实未完成。接口与完成回执发回本批唯一协调聊天 01a1ac6f-8494-7f82-8c4d-ec7d285f44ae；用户已授权本批协作派发与回执。
