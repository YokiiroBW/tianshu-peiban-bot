# TS-175 容量守护诊断与采样竞争

执行模型 `gpt-6.1-sol / max`。主工作区 `C:/YOKI/Codex/tianshu-peiban-bot`；本任务只写 `worktrees/TS-175/workspace`。先读主工作区 CURRENT、基础设施计划、执行安排和 `docs/development/resident-guard-docker-failure-2026-09-28.md`，再读现有 guard 与测试。

只允许 `ops/resident_capacity/`、`tests/resident_capacity/`、`docs/handoffs/TS-175.md`。不改任务板、根 AGENTS、其他产品、现有保护阈值和 NAS。

完整实现可解释的 Docker 失败诊断：固定阶段、退出码／超时、耗时、批次数，保存至原私有状态体系；不保存原始 stdout／stderr、凭据或原始命令。通过真实子进程假 Docker CLI 复现 `ps` 后容器消失与命令非零。若能证明完整重采样可解决竞争，在原总期限内最多有限重采样；权限、目标身份、挂载冲突和容量不足仍准确失败，不把未知当健康。不需要为了消除已知未知而放宽保护。

审查与未来容器控制的交互，在交接明确固定 ID 维护／重建应如何接入既有维护流程；不要未经本卡要求增加在线维护开关或新权限。复用现有异常、原子文件与停止回执结构，沿用 unittest，覆盖影响路径，不跑无关全仓套件。

完成后提交自己的代码和交接，返回精确 SHA、文件清单、实际命令／结果、仍需实机核对的事项。主协调者会审查、独立验收；不自行声称线上修复，不推送／部署。需要返修由原任务继续。
