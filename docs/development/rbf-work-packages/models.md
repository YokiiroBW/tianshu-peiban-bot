# RBF-06 Models状态查询与原回执语义

用户已在主聊天批准四项根因修复方案并要求“安排对应窗口修复吧”。本轮为新修复批 RBF，继承 gpt-6.1-terra / max，普通速度 service_tier=default；旧 Fast、旧只读阶段与已完成卡限制不适用于本轮明确范围。只在指定的新工作树写本卡范围；旧聊天原 cwd 和原任务分支保持。所有窗口不运行测试、构建、烟测，不操作生产、真实模型、QQ或其他设备；编写必要回归用例、静态审阅完整 diff、完整实现后提交并交接，由主协调者集中执行必要检查。不能为了省验证而省功能或伪造通过。禁止 reset/clean/stash；共享 product.py、migrations.py、config.py/config.schema.json、runtime/schema.sql、types/、全局依赖/部署配置由主协调者单写，提供明确接线说明。现有配置、新业务数据、原请求/回执/unknown、来源/权限/CAS和纯计算恢复预算保留，本批没有再次清库权限。不要引入平行队列、全局业务状态机或仅按错误字符串盲重放。用户已明确授权主聊天发送任务并接收回执；可向主聊天 01a1a72e-d357-7ba2-9e01-6373ebb7741e 回报接口、阻塞与完成。若消息工具不可用，交接文件和最终回执仍必须完成。

## 唯一工作目录与基线

- 本轮检出：C:/Users/Administrator/.codex/worktrees/rbf-models/tianshu-peiban-bot
- 分支：codex/rbf-models-20261105
- 基线：33e5282f60fe4c803c3b16d498d1d924621ed1b9
- 旧聊天 cwd 不作为本轮写入目录。每条 shell 命令显式 workdir 或先 Set-Location 到本轮检出。

## 范围

- src/tianshu/models/
- tests/models/test_rbf_*.py
- docs/handoffs/RBF-06.md（本轮交接）；需要先给接口可写 docs/handoffs/RBF-06-interface.md。

## 完整任务

1. 修复expected pre-submit lookup absence与真实原请求丢失的观测混同。保持状态查询原语完整，合法预检查无结果不应产生错误事故；必须关联attempt_id/请求阶段，不全局吞404或降级所有404。先向协调者给出HTTP/客户端/事件语义，runtime/api与events由统计告警窗口接线。

2. 核对客户端和主要调用方在已提交/执行中/终态unknown时的状态语义，保证现有纯计算unknown继承原lineage预算、明确取消不可重试、缺失回执不能伪称未发送。

3. 提供重用已成功模型结果的明确边界，未知费用保持unknown；不因业务落库失败重新发模型请求，不更换provider或预算。

4. 保持原native/streaming/submit/result协议，最小必要变更和定向回归用例；如需域外调用方修改提供精确接线而不越界。

## 证据与交接

先读 C:/YOKI/Codex/tianshu-peiban-bot/docs/development/rbf-repair-2026-11-05.md 和当前实际源码；历史入口CURRENT仅历史。证据 C:/YOKI/Codex/tianshu-peiban-bot/.runtime/rb-execution-20261105/ 中root-cause-*.json、monitor-observations.jsonl以及对应detail。可读这些去敏本地证据，不自行访问生产。

交接必须包括基线/提交SHA、完整变更、共享接口/迁移/接线需求、编写的必要用例和建议集中命令、未运行的检查、实际限制、正常领域恢复方式。不以完成代码宣称部署或真实业务恢复。完成后提交本卡文件并回报主聊天，未经新任务不继续扩展。
