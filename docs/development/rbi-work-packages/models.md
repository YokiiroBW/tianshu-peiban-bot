# RBI-02：invalid_upstream_response 准确归因与原输出复用证据

状态：用户已授权本批修复；本卡是新的 RBI 包，不续写旧完成卡。

窗口：RBW Models异步执行与计时（01a1a772-e93b-73d0-9b3f-33cd579098da）。
工作树：C:/Users/Administrator/.codex/worktrees/rbi-models/tianshu-peiban-bot
分支：codex/rbi-models-20261106
代码起点：b32a606df8238e398c9dcd58e2e145f2ecff9fb3；实际开发从根发布的准备提交开始，精确 SHA 见派发记录。

先读本工作树 AGENTS.md 最新 RBI 条、[公共合同](../rbi-interfaces-2026-11-06.md)、[方案](../domain-recovery-plan-2026-11-06.md)及[队列本卡](../domain-recovery-queue-2026-11-06.md)。本卡允许本域完整实现，不跨写其他工作树。

## 整块交付

- 原 Growth attempt 的去敏阶段/异常类型证据
- 既有 diagnostic 内区分协议解析、领域内容、程序异常与传输事实
- 复用已留存原输出的合法本地解析路径，缺证据保持 unknown
- 429/502/503 的发送、结果、用量/hold_until 语义与去敏原身份关联；缺失证据不推断

## 必须覆盖的行为

- 不把 read_complete 后所有本地异常当作模型坏输出
- 不扩大正文日志或伪造完整输出
- 原请求/失败回执/未知费用保留，不由 Models 做 Growth 业务转换
- 既有六次429/五Growth批恢复证据复用；429本身不证明未发送或零费用，502/503已发出时保留unknown

对相关调用链补必要回归用例代码。使用项目现有夹具；真实跨域联合验证由根负责，不能以任意参数 mock 声称接口已验证。P1 条件项先补证，未证实无效工作时不擅自改变业务策略。

## 允许写路径

- src/tianshu/models/broker.py
- src/tianshu/models/transport_diagnostic.py
- src/tianshu/models/codec.py
- src/tianshu/models/rbi_*.py
- tests/models/test_rbi_*.py
- tests/models/test_rbh_transport_evidence.py
- tests/models/test_result_recovery.py
- docs/handoffs/RBI-02.md

精确白名单以本卡及队列 JSON 为准。全部 module.py、runtime/product、共享 types、已有 schema.sql、迁移注册、配置/依赖、生产和活跃巡检探针由根单写；本域必要新 SQL 交根注册。Runtime 的 process/composition 仅 RBI-01 可写。Life worker/结果回调仅 RBI-04 可写；Conversation 消费账本仅 RBI-09 可写。

## 接口、依赖与执行

基础依赖：RBI-00。集中装配依赖：见公共合同。公共语义已冻结，立即推进可独立实现部分；依赖方的新函数先按合同编写调用，真实装配由根集成。不把接口尚未到齐当作停止整个实现包的理由。接口不可行时尽早回报准确签名与原因，由根单写修订合同。

全包实现、必要回归用例和静态审阅后提交；允许 AST/diff 静态检查，**本窗口不运行测试、构建、烟测、生产或真实模型/QQ命令**。保留所有既有修改，不 reset/clean/stash，不操作旧任务分支。

模型 gpt-6.1-terra/max，普通速度 service_tier=default、fast_mode=false。不新建聊天、子代理或自动化，不推送、不部署、不清数据。

交接 docs/handoffs/RBI-02.md，写精确 SHA、完整实现范围、公共接口/DDL/根装配、必要用例、未执行验证及真实未完成。接口与完成回执发回本批唯一协调聊天 01a1ac6f-8494-7f82-8c4d-ec7d285f44ae；用户已授权本批协作派发与回执。
