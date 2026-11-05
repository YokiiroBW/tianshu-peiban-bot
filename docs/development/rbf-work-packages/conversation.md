# RBF-09 前台对话公共恢复合同接线

用户已批准RBF四项根因修复并要求安排对应窗口。此任务是共享Sources/Runtime/Models改动的直接调用方适配，保留现有对话功能，不新增产品范围。

- 唯一写入目录：C:/Users/Administrator/.codex/worktrees/rbf-conversation/tianshu-peiban-bot
- 分支：codex/rbf-conversation-20261105
- 基线：33e5282f60fe4c803c3b16d498d1d924621ed1b9
- 模型：gpt-6.1-terra / max / 普通速度，不开Fast。
- 先读 C:/YOKI/Codex/tianshu-peiban-bot/docs/development/rbf-repair-2026-11-05.md 和 rbf-interfaces-2026-11-05.md。

## 独占范围

src/tianshu/conversation/，但排除persistence.py（RBF-05独占）；tests/conversation/中直接受影响的现有用例与新增test_rbf_*.py；docs/handoffs/RBF-09.md。
不写Channels（RBF-08）、Models（RBF-06）、Sources/Identity（RBF-05）、Runtime（RBF-01/08/主）、Memory/Growth/Life、前端、共享装配/迁移/配置/依赖。

## 完整任务

1. 新Sources锁集变化为RecoveryWait，必须越过完整事务回滚，不被AppError catch误当来源永久失效/最终对话失败。核查本次直接受影响的run/collector/seal/result/model_complete/expression等路径，保留原turn、input、generation、动作和receipt。只对能按原持久状态安全恢复的handler显式resource_recovery=True，不给真实QQ发出路径盲重放；未知外部效果继续查原收据。
2. 原模型状态区分首次尚未确认注册的pre_submit与observe；已提交ACK丢失或status None不证明未发送，原ID/冻结请求与代际保持。成功模型结果遇到本地业务提交失败只重读原结果、来源/权限/CAS后提交，不另起新模型。必要消费ModelPort.status(...phase)、require_receipt、reusable_result；具体接口见已提交RBF-06，源在rbf-models可读但不可改。
3. 配合Sources的新依赖锁顺序，相关写入在无领域锁时一次准备完整refs，派生事件使用prepare_event_writes；明确真实缺料/撤回/取消的终态与资源等待的区别。勿扩展重构全部对话调度。
4. 整块写完，补必要回归（同input/turn资源失败后续接且不重复生成/送达，来源锁集变化不终结，已提交未知不重发），静态审阅并提交。不得运行测试/构建/烟测、真实模型/QQ或生产；主协调者收齐后集中验证。无需为任务小变动再求批准。

原空库重建已结束，本批保留现有配置和新数据。旧聊天cwd、他人worktree保持；所有命令显式workdir到本轮目录，补丁用绝对路径。用户允许主聊天派发并接收回执；向01a1a72e-d357-7ba2-9e01-6373ebb7741e先回接收/边界，再完整实现并回提交SHA、公共接线/迁移、建议集中验证命令与限制。消息工具不可用仍完成handoff/final。
