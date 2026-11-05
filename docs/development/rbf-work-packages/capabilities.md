# RBF-04 能力观测语义与媒体阶段恢复

用户已在主聊天批准四项根因修复方案并要求“安排对应窗口修复吧”。本轮为新修复批 RBF，继承 gpt-6.1-terra / max，普通速度 service_tier=default；旧 Fast、旧只读阶段与已完成卡限制不适用于本轮明确范围。只在指定的新工作树写本卡范围；旧聊天原 cwd 和原任务分支保持。所有窗口不运行测试、构建、烟测，不操作生产、真实模型、QQ或其他设备；编写必要回归用例、静态审阅完整 diff、完整实现后提交并交接，由主协调者集中执行必要检查。不能为了省验证而省功能或伪造通过。禁止 reset/clean/stash；共享 product.py、migrations.py、config.py/config.schema.json、runtime/schema.sql、types/、全局依赖/部署配置由主协调者单写，提供明确接线说明。现有配置、新业务数据、原请求/回执/unknown、来源/权限/CAS和纯计算恢复预算保留，本批没有再次清库权限。不要引入平行队列、全局业务状态机或仅按错误字符串盲重放。用户已明确授权主聊天发送任务并接收回执；可向主聊天 01a1a72e-d357-7ba2-9e01-6373ebb7741e 回报接口、阻塞与完成。若消息工具不可用，交接文件和最终回执仍必须完成。

## 唯一工作目录与基线

- 本轮检出：C:/Users/Administrator/.codex/worktrees/rbf-capabilities/tianshu-peiban-bot
- 分支：codex/rbf-capabilities-20261105
- 基线：33e5282f60fe4c803c3b16d498d1d924621ed1b9
- 旧聊天 cwd 不作为本轮写入目录。每条 shell 命令显式 workdir 或先 Set-Location 到本轮检出。

## 范围

- src/tianshu/capabilities/
- tests/capabilities/test_rbf_*.py
- docs/handoffs/RBF-04.md（本轮交接）；需要先给接口可写 docs/handoffs/RBF-04-interface.md。

## 完整任务

1. CatalogObservations目前expiry即使结果相同也返回changed；规划读取又引发刷新回调。拆开缓存刷新/新鲜度和能力语义变化，用稳定语义投影比较，避免verified_at/TTL等机械字段触发规划。保留最后有效观测的来源/新鲜度，未知不冒充可执行；执行阶段仍真实复核。必要资料首次缺失进入明确等待，无关慢探针不能阻塞已知候选。尽早回报Catalog/Life回调合同；product.py由主协调者接线。

2. VideoObservation._video_result成功后understanding.interval失败，通用actions将无effect的Fault映为unknown。明确获取/模型/解析/消费提交阶段，保存模型回执和原输入；确定性范围非法为解析失败或有界纠正，不伪造覆盖、不盲clamp非法区间、不重下载/重放整个动作。

3. 核对本批content.read browser_source_unavailable/capability_timeout/capability_failed的失败分期和恢复；只恢复失败材料读取，外部效果未知保持原观察。不要全局将所有Fault改成effect=none。

4. 用现有动作账本/消费检查点避免重复学习、重复消费；给Life提供可明确结束/等待/继续的结果。不要顺手改独立媒体引擎或添加依赖。

## 证据与交接

先读 C:/YOKI/Codex/tianshu-peiban-bot/docs/development/rbf-repair-2026-11-05.md 和当前实际源码；历史入口CURRENT仅历史。证据 C:/YOKI/Codex/tianshu-peiban-bot/.runtime/rb-execution-20261105/ 中root-cause-*.json、monitor-observations.jsonl以及对应detail。可读这些去敏本地证据，不自行访问生产。

交接必须包括基线/提交SHA、完整变更、共享接口/迁移/接线需求、编写的必要用例和建议集中命令、未运行的检查、实际限制、正常领域恢复方式。不以完成代码宣称部署或真实业务恢复。完成后提交本卡文件并回报主聊天，未经新任务不继续扩展。
