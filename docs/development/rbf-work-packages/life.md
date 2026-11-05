# RBF-02 Life语义触发与原子规划续接

用户已在主聊天批准四项根因修复方案并要求“安排对应窗口修复吧”。本轮为新修复批 RBF，继承 gpt-6.1-terra / max，普通速度 service_tier=default；旧 Fast、旧只读阶段与已完成卡限制不适用于本轮明确范围。只在指定的新工作树写本卡范围；旧聊天原 cwd 和原任务分支保持。所有窗口不运行测试、构建、烟测，不操作生产、真实模型、QQ或其他设备；编写必要回归用例、静态审阅完整 diff、完整实现后提交并交接，由主协调者集中执行必要检查。不能为了省验证而省功能或伪造通过。禁止 reset/clean/stash；共享 product.py、migrations.py、config.py/config.schema.json、runtime/schema.sql、types/、全局依赖/部署配置由主协调者单写，提供明确接线说明。现有配置、新业务数据、原请求/回执/unknown、来源/权限/CAS和纯计算恢复预算保留，本批没有再次清库权限。不要引入平行队列、全局业务状态机或仅按错误字符串盲重放。用户已明确授权主聊天发送任务并接收回执；可向主聊天 01a1a72e-d357-7ba2-9e01-6373ebb7741e 回报接口、阻塞与完成。若消息工具不可用，交接文件和最终回执仍必须完成。

## 唯一工作目录与基线

- 本轮检出：C:/Users/Administrator/.codex/worktrees/rbf-life/tianshu-peiban-bot
- 分支：codex/rbf-life-20261105
- 基线：33e5282f60fe4c803c3b16d498d1d924621ed1b9
- 旧聊天 cwd 不作为本轮写入目录。每条 shell 命令显式 workdir 或先 Set-Location 到本轮检出。

## 范围

- src/tianshu/life/
- tests/life/test_rbf_*.py
- docs/handoffs/RBF-02.md（本轮交接）；需要先给接口可写 docs/handoffs/RBF-02-interface.md。

## 完整任务

1. 明确区分通知版本与规划材料语义版本。capability-observation 的随机watermark/缓存刷新不能进入决策材料；保留真正能力语义变化、用户明确重做/取消、来源失效等。结合 Catalog 接口变更，单角色一个有效在途计算+最新待刷新版本；普通通知合并，同材料复用原请求/回执，不把TTL失效暂时unknown当作新事实反复生成。

2. 修复 supersede先提交而当前指针稍后切换的中断窗口；用既有plan_watermarks作为当前工作权威、plan_generations保留历史。当前指针、旧状态处置、目标版本、下一次唤醒及入队需原子或有可恢复的明确准备态；不可长期出现planner_queued指向superseded、无活跃任务也无有效续跑。

3. 接入Runtime等待合同，临时DB/依赖等待保存due、允许安全续接，不重新调用已成功模型。检查本次影响的规划/活动/日记/写作相关消费者；不扩展无关Life功能。

4. 复用现有来源/CAS/候选组应用/外部动作回执。不要因通知revision不同废弃语义仍适用的结果，真实业务变化须重核；外部未知不盲重放。和Sources窗口对齐Life应用锁顺序；写入前准备完整依赖集。

## 证据与交接

先读 C:/YOKI/Codex/tianshu-peiban-bot/docs/development/rbf-repair-2026-11-05.md 和当前实际源码；历史入口CURRENT仅历史。证据 C:/YOKI/Codex/tianshu-peiban-bot/.runtime/rb-execution-20261105/ 中root-cause-*.json、monitor-observations.jsonl以及对应detail。可读这些去敏本地证据，不自行访问生产。

交接必须包括基线/提交SHA、完整变更、共享接口/迁移/接线需求、编写的必要用例和建议集中命令、未运行的检查、实际限制、正常领域恢复方式。不以完成代码宣称部署或真实业务恢复。完成后提交本卡文件并回报主聊天，未经新任务不继续扩展。
