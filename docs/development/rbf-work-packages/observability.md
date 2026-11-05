# RBF-08 统计覆盖、事件归因与巡检摘要

用户已在主聊天批准四项根因修复方案并要求“安排对应窗口修复吧”。本轮为新修复批 RBF，继承 gpt-6.1-terra / max，普通速度 service_tier=default；旧 Fast、旧只读阶段与已完成卡限制不适用于本轮明确范围。只在指定的新工作树写本卡范围；旧聊天原 cwd 和原任务分支保持。所有窗口不运行测试、构建、烟测，不操作生产、真实模型、QQ或其他设备；编写必要回归用例、静态审阅完整 diff、完整实现后提交并交接，由主协调者集中执行必要检查。不能为了省验证而省功能或伪造通过。禁止 reset/clean/stash；共享 product.py、migrations.py、config.py/config.schema.json、runtime/schema.sql、types/、全局依赖/部署配置由主协调者单写，提供明确接线说明。现有配置、新业务数据、原请求/回执/unknown、来源/权限/CAS和纯计算恢复预算保留，本批没有再次清库权限。不要引入平行队列、全局业务状态机或仅按错误字符串盲重放。用户已明确授权主聊天发送任务并接收回执；可向主聊天 01a1a72e-d357-7ba2-9e01-6373ebb7741e 回报接口、阻塞与完成。若消息工具不可用，交接文件和最终回执仍必须完成。

## 唯一工作目录与基线

- 本轮检出：C:/Users/Administrator/.codex/worktrees/rbf-observability/tianshu-peiban-bot
- 分支：codex/rbf-observability-20261105
- 基线：33e5282f60fe4c803c3b16d498d1d924621ed1b9
- 旧聊天 cwd 不作为本轮写入目录。每条 shell 命令显式 workdir 或先 Set-Location 到本轮检出。

## 范围

- src/tianshu/operations/
- src/tianshu/runtime/api.py
- src/tianshu/runtime/events.py
- src/tianshu/runtime/logging.py
- src/tianshu/channels/observability.py
- tests/operations/test_rbf_*.py
- tests/runtime/test_rbf_events*.py
- tests/channels/test_rbf_statistics*.py
- scripts/ops/rbf_monitor_summary.py
- docs/handoffs/RBF-08.md（本轮交接）；需要先给接口可写 docs/handoffs/RBF-08-interface.md。

## 完整任务

1. 统计required channels与实际channels.media覆盖不匹配：按真实数据域补生产者或明确分别覆盖，不能仅加别名宣称全渠道完整。life.planning live_producer complete=false应区分持续生产与积压/回填未完整，不要求停服才表示已追平。

2. 与Models窗口定义expected absence事件语义；将attempt_id、发生阶段、错误依赖/角色/池身份可靠关联，合法预检不升为事故，已知原请求丢失仍有告警。不能全局隐藏404或将数据不足当成功。

3. 业务恢复、原回执结果、计费已知与否独立汇总；新状态以领域账本为准，不把健康/队列空等同业务完成。内部诊断模型未配置如实显示，不擅自绑定模型或通知收件人。

4. 提供读取既有monitor-observations.jsonl和monitor-issues.jsonl的确定性增量摘要脚本：最新问题视图、累计/滚动窗口/新增明确区分、恢复证据、去重，并保留原事件日志。不修改活跃巡检的文件/定时配置；协调者最后接线，减少每轮重读历史。

## 证据与交接

先读 C:/YOKI/Codex/tianshu-peiban-bot/docs/development/rbf-repair-2026-11-05.md 和当前实际源码；历史入口CURRENT仅历史。证据 C:/YOKI/Codex/tianshu-peiban-bot/.runtime/rb-execution-20261105/ 中root-cause-*.json、monitor-observations.jsonl以及对应detail。可读这些去敏本地证据，不自行访问生产。

交接必须包括基线/提交SHA、完整变更、共享接口/迁移/接线需求、编写的必要用例和建议集中命令、未运行的检查、实际限制、正常领域恢复方式。不以完成代码宣称部署或真实业务恢复。完成后提交本卡文件并回报主聊天，未经新任务不继续扩展。

## 本轮渠道接线范围补充

允许必要修改 channels/service.py、ingress.py、delivery.py 的统计调用点，当前本批无其他Channels写者。按已有业务账本补实际渠道覆盖，避免与conversation.messages/delivery重复计数，不能仅把旧missing换成新missing即宣称修好。未有真实送达样本仍如实标明。
