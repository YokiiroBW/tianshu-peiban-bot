# RBF-01 运行时等待协议与数据库资源

用户已在主聊天批准四项根因修复方案并要求“安排对应窗口修复吧”。本轮为新修复批 RBF，继承 gpt-6.1-terra / max，普通速度 service_tier=default；旧 Fast、旧只读阶段与已完成卡限制不适用于本轮明确范围。只在指定的新工作树写本卡范围；旧聊天原 cwd 和原任务分支保持。所有窗口不运行测试、构建、烟测，不操作生产、真实模型、QQ或其他设备；编写必要回归用例、静态审阅完整 diff、完整实现后提交并交接，由主协调者集中执行必要检查。不能为了省验证而省功能或伪造通过。禁止 reset/clean/stash；共享 product.py、migrations.py、config.py/config.schema.json、runtime/schema.sql、types/、全局依赖/部署配置由主协调者单写，提供明确接线说明。现有配置、新业务数据、原请求/回执/unknown、来源/权限/CAS和纯计算恢复预算保留，本批没有再次清库权限。不要引入平行队列、全局业务状态机或仅按错误字符串盲重放。用户已明确授权主聊天发送任务并接收回执；可向主聊天 01a1a72e-d357-7ba2-9e01-6373ebb7741e 回报接口、阻塞与完成。若消息工具不可用，交接文件和最终回执仍必须完成。

## 唯一工作目录与基线

- 本轮检出：C:/Users/Administrator/.codex/worktrees/rbf-runtime/tianshu-peiban-bot
- 分支：codex/rbf-runtime-20261105
- 基线：33e5282f60fe4c803c3b16d498d1d924621ed1b9
- 旧聊天 cwd 不作为本轮写入目录。每条 shell 命令显式 workdir 或先 Set-Location 到本轮检出。

## 范围

- src/tianshu/runtime/database.py
- src/tianshu/runtime/jobs.py
- src/tianshu/runtime/execution.py
- src/tianshu/runtime/process.py
- src/tianshu/runtime/recovery_policy.py
- src/tianshu/runtime/rbf_*.py
- src/tianshu/runtime/rbf_*.sql
- tests/runtime/test_rbf_*.py
- docs/handoffs/RBF-01.md（本轮交接）；需要先给接口可写 docs/handoffs/RBF-01-interface.md。

## 完整任务

1. 在现有 RecoveryPolicy 上提供明确、可复用的依赖等待判定/处置合同（原因、下次唤醒等）；冷却/探测占用/claim占用不得被误当模型最终失败，未提交新物理请求的等待不得虚增模型失败或尝试预算。尽早给主协调者精确 Python 接口和领域接入样例，Memory/Life/Growth随后消费。

2. 根据当前出错调用链补齐数据库池身份/角色、等待阶段和必要占用统计；采用异常类型/SQLSTATE区分本地DB争用与远端模型故障。现有5秒取连接不是所有工作统一失败的业务期限，资源不足应能由领域持久延后；不可给所有有副作用任务打开通用自动重试。

3. 核对单事务连接复用、资源准入、取消释放、控制连接保留；避免持有连接再等待同池第二条。实现证据支持的根因修复，不盲增 max_connections，不全局降低并行度。必要 config装配由根处理。

4. 支持当前业务状态原子入队与安全重唤醒，复用原 Jobs/代次/去重键；只对已确认回滚的本地事务恢复，提交结果不明先查原命令收据。

## 证据与交接

先读 C:/YOKI/Codex/tianshu-peiban-bot/docs/development/rbf-repair-2026-11-05.md 和当前实际源码；历史入口CURRENT仅历史。证据 C:/YOKI/Codex/tianshu-peiban-bot/.runtime/rb-execution-20261105/ 中root-cause-*.json、monitor-observations.jsonl以及对应detail。可读这些去敏本地证据，不自行访问生产。

交接必须包括基线/提交SHA、完整变更、共享接口/迁移/接线需求、编写的必要用例和建议集中命令、未运行的检查、实际限制、正常领域恢复方式。不以完成代码宣称部署或真实业务恢复。完成后提交本卡文件并回报主聊天，未经新任务不继续扩展。
