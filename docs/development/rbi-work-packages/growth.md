# RBI-05：按真实失败分类恢复原反思批与本域补漏

状态：用户已授权本批修复；本卡是新的 RBI 包，不续写旧完成卡。

窗口：RBW Companion学习与表达样例（01a1a772-e96f-7600-a841-94ca8dd26ab6）。
工作树：C:/Users/Administrator/.codex/worktrees/rbi-growth/tianshu-peiban-bot
分支：codex/rbi-growth-20261106
代码起点：b32a606df8238e398c9dcd58e2e145f2ecff9fb3；实际开发从根发布的准备提交开始，精确 SHA 见派发记录。

先读本工作树 AGENTS.md 最新 RBI 条、[公共合同](../rbi-interfaces-2026-11-06.md)、[方案](../domain-recovery-plan-2026-11-06.md)及[队列本卡](../domain-recovery-queue-2026-11-06.md)。本卡允许本域完整实现，不跨写其他工作树。

## 整块交付

- 先复现 waiting 但 Runtime due=NULL 的衔接风险
- 消费 Models 真实证据，区分本地重解析/有限输出纠正/程序修复后续接/明确停止
- 真实版本改变复用 update_version_on；同版本修复走合法 repair_ref 领取
- 复用 request_review/retry 和本域有界补漏
- 复用本轮五个429后已恢复批次、十个reviewed版本证据，仅核对兼容性不重做

## 必须覆盖的行为

- 不只改领域 waiting 留下无人可领取的批次
- 同一 repair_ref 幂等、纠正次数和原预算不清零
- unknown/来源失效/取消/换代不被修复入口绕过
- 原材料 revision 2 正常消费或留明确退出原因，已恢复 15 批不重做

对相关调用链补必要回归用例代码。使用项目现有夹具；真实跨域联合验证由根负责，不能以任意参数 mock 声称接口已验证。P1 条件项先补证，未证实无效工作时不擅自改变业务策略。

## 允许写路径

- src/tianshu/companion/growth.py
- src/tianshu/companion/growth_retry.py
- src/tianshu/companion/growth_policy.py
- src/tianshu/companion/growth_operations.py
- src/tianshu/companion/growth_output_repair.py
- src/tianshu/companion/reflection_output.py
- src/tianshu/companion/growth_recovery.py
- src/tianshu/companion/growth_statistics.py
- src/tianshu/companion/rbi_*.py
- src/tianshu/companion/rbi_*.sql
- tests/companion/test_rbi_*.py
- tests/companion/test_growth_retry.py
- tests/companion/test_growth_output.py
- tests/companion/test_rb_growth_pure_retry.py
- tests/companion/test_rbh_growth_receipt_sources.py
- docs/handoffs/RBI-05.md

精确白名单以本卡及队列 JSON 为准。全部 module.py、runtime/product、共享 types、已有 schema.sql、迁移注册、配置/依赖、生产和活跃巡检探针由根单写；本域必要新 SQL 交根注册。Runtime 的 process/composition 仅 RBI-01 可写。Life worker/结果回调仅 RBI-04 可写；Conversation 消费账本仅 RBI-09 可写。

## 接口、依赖与执行

基础依赖：RBI-00、RBI-01、RBI-02。集中装配依赖：见公共合同。公共语义已冻结，立即推进可独立实现部分；依赖方的新函数先按合同编写调用，真实装配由根集成。不把接口尚未到齐当作停止整个实现包的理由。接口不可行时尽早回报准确签名与原因，由根单写修订合同。

全包实现、必要回归用例和静态审阅后提交；允许 AST/diff 静态检查，**本窗口不运行测试、构建、烟测、生产或真实模型/QQ命令**。保留所有既有修改，不 reset/clean/stash，不操作旧任务分支。

模型 gpt-6.1-terra/max，普通速度 service_tier=default、fast_mode=false。不新建聊天、子代理或自动化，不推送、不部署、不清数据。

交接 docs/handoffs/RBI-05.md，写精确 SHA、完整实现范围、公共接口/DDL/根装配、必要用例、未执行验证及真实未完成。接口与完成回执发回本批唯一协调聊天 01a1ac6f-8494-7f82-8c4d-ec7d285f44ae；用户已授权本批协作派发与回执。
