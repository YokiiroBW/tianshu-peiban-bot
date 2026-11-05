# RBF-03 Memory等待恢复与材料重复归因

用户已在主聊天批准四项根因修复方案并要求“安排对应窗口修复吧”。本轮为新修复批 RBF，继承 gpt-6.1-terra / max，普通速度 service_tier=default；旧 Fast、旧只读阶段与已完成卡限制不适用于本轮明确范围。只在指定的新工作树写本卡范围；旧聊天原 cwd 和原任务分支保持。所有窗口不运行测试、构建、烟测，不操作生产、真实模型、QQ或其他设备；编写必要回归用例、静态审阅完整 diff、完整实现后提交并交接，由主协调者集中执行必要检查。不能为了省验证而省功能或伪造通过。禁止 reset/clean/stash；共享 product.py、migrations.py、config.py/config.schema.json、runtime/schema.sql、types/、全局依赖/部署配置由主协调者单写，提供明确接线说明。现有配置、新业务数据、原请求/回执/unknown、来源/权限/CAS和纯计算恢复预算保留，本批没有再次清库权限。不要引入平行队列、全局业务状态机或仅按错误字符串盲重放。用户已明确授权主聊天发送任务并接收回执；可向主聊天 01a1a72e-d357-7ba2-9e01-6373ebb7741e 回报接口、阻塞与完成。若消息工具不可用，交接文件和最终回执仍必须完成。

## 唯一工作目录与基线

- 本轮检出：C:/Users/Administrator/.codex/worktrees/rbf-memory/tianshu-peiban-bot
- 分支：codex/rbf-memory-20261105
- 基线：33e5282f60fe4c803c3b16d498d1d924621ed1b9
- 旧聊天 cwd 不作为本轮写入目录。每条 shell 命令显式 workdir 或先 Set-Location 到本轮检出。

## 范围

- src/tianshu/memory/
- tests/memory/test_rbf_*.py
- docs/handoffs/RBF-03.md（本轮交接）；需要先给接口可写 docs/handoffs/RBF-03-interface.md。

## 完整任务

1. 修复consolidation._failure把有batch_id的recovery_dependency_probe_in_progress视为terminal：批次准备、建立、纠正、续上下文与重试阶段都按统一等待合同收敛，保存原批次/材料/来源/claim/due，不遗留失败材料无唤醒。

2. 沿现有RecoveryPolicy/ConsolidationRetries接线，原unknown语义及8次预算不重置；成功模型结果遇到提交失败仅恢复提交；到期claim和批次指针原子或可恢复。

3. 围绕现有extraction/resume提供受影响原批次的正常恢复路径与前置CAS，交接明确原两条材料如何恢复；不直接UPDATE生产表、不重建伪新episode。

4. 按现有batch manifest/episode revision/游标/材料摘要区分新增处理、补上下文、纠正和重复计算。证据支持的重复唤醒/准备尽量合并复用；没有证明的重复不要牺牲真实新材料、图文或闭包完整性。提供无正文归因查询或只读工具，保持已有用户侧记忆算法。

## 证据与交接

先读 C:/YOKI/Codex/tianshu-peiban-bot/docs/development/rbf-repair-2026-11-05.md 和当前实际源码；历史入口CURRENT仅历史。证据 C:/YOKI/Codex/tianshu-peiban-bot/.runtime/rb-execution-20261105/ 中root-cause-*.json、monitor-observations.jsonl以及对应detail。可读这些去敏本地证据，不自行访问生产。

交接必须包括基线/提交SHA、完整变更、共享接口/迁移/接线需求、编写的必要用例和建议集中命令、未运行的检查、实际限制、正常领域恢复方式。不以完成代码宣称部署或真实业务恢复。完成后提交本卡文件并回报主聊天，未经新任务不继续扩展。
