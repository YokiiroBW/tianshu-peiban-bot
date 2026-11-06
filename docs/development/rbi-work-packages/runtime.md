# RBI-01：恢复领取机械边界与 role 内维护回调

状态：用户已授权本批修复；本卡是新的 RBI 包，不续写旧完成卡。

窗口：RBW 运行时与故障隔离（01a1a772-e95a-7922-a72f-1beb061dd287）。
工作树：C:/Users/Administrator/.codex/worktrees/rbi-runtime/tianshu-peiban-bot
分支：codex/rbi-runtime-20261106
代码起点：b32a606df8238e398c9dcd58e2e145f2ecff9fb3；实际开发从根发布的准备提交开始，精确 SHA 见派发记录。

先读本工作树 AGENTS.md 最新 RBI 条、[公共合同](../rbi-interfaces-2026-11-06.md)、[方案](../domain-recovery-plan-2026-11-06.md)及[队列本卡](../domain-recovery-queue-2026-11-06.md)。本卡允许本域完整实现，不跨写其他工作树。

## 整块交付

- 优先复用现有端口；确有缺口才补 repair_ref 幂等修复领取
- 保留普通永久任务键语义，验证原生锁/合并/重试边界
- 复用现有维护周期，按 role 调用领域短回调并隔离故障
- 验证既有 Retry-After、依赖冷却和单探针在跨领域/版本变更后仍成立；按证据修缺口，不笼统扩大重试

## 必须覆盖的行为

- 真实 failed 且 due=NULL 可合法领取或返回明确拒绝原因
- 重复 repair_ref 不重置预算、不重复领取；unknown/有效原尝试不走该端口
- doing+一个 todo、提前合并和原生 retry 均不丢未来唤醒
- 单域回调失败和 Operations 不可用不阻塞其他领域
- 429 不冒充 transport_not_sent；新通知或新 key 不更新相同业务的原预算；跨领域共享依赖冷却保持

对相关调用链补必要回归用例代码。使用项目现有夹具；真实跨域联合验证由根负责，不能以任意参数 mock 声称接口已验证。P1 条件项先补证，未证实无效工作时不擅自改变业务策略。

## 允许写路径

- src/tianshu/runtime/recovery_policy.py
- src/tianshu/runtime/jobs.py
- src/tianshu/runtime/process.py
- src/tianshu/runtime/composition.py
- src/tianshu/runtime/rbi_*.py
- src/tianshu/runtime/rbi_*.sql
- tests/runtime/test_rbi_*.py
- tests/runtime/test_rb_recovery_policy.py
- tests/runtime/test_rbf_probe_leases.py
- tests/runtime/test_rbf_database_jobs.py
- docs/handoffs/RBI-01.md

精确白名单以本卡及队列 JSON 为准。全部 module.py、runtime/product、共享 types、已有 schema.sql、迁移注册、配置/依赖、生产和活跃巡检探针由根单写；本域必要新 SQL 交根注册。Runtime 的 process/composition 仅 RBI-01 可写。Life worker/结果回调仅 RBI-04 可写；Conversation 消费账本仅 RBI-09 可写。

## 接口、依赖与执行

基础依赖：RBI-00。集中装配依赖：见公共合同。公共语义已冻结，立即推进可独立实现部分；依赖方的新函数先按合同编写调用，真实装配由根集成。不把接口尚未到齐当作停止整个实现包的理由。接口不可行时尽早回报准确签名与原因，由根单写修订合同。

全包实现、必要回归用例和静态审阅后提交；允许 AST/diff 静态检查，**本窗口不运行测试、构建、烟测、生产或真实模型/QQ命令**。保留所有既有修改，不 reset/clean/stash，不操作旧任务分支。

模型 gpt-6.1-terra/max，普通速度 service_tier=default、fast_mode=false。不新建聊天、子代理或自动化，不推送、不部署、不清数据。

交接 docs/handoffs/RBI-01.md，写精确 SHA、完整实现范围、公共接口/DDL/根装配、必要用例、未执行验证及真实未完成。接口与完成回执发回本批唯一协调聊天 01a1ac6f-8494-7f82-8c4d-ec7d285f44ae；用户已授权本批协作派发与回执。
