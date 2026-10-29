# PC-04 状态合同候选（2.2.0）

本候选从线上 `life-runtime/v2` 的 2.1.0 包演进；父 manifest SHA-256 为 `6c032c558d107615a19e49f1bde1c76f7a5994b14c484476f7a71aeab555cadf`。尚未修改生产者或消费者的固定 pin，不是已发布接口。

变化只涉及活动，不改变 concern、订阅、阅读会话等各自的 paused 语义：

- `activity_save_value` 与 `activities_record` 增加 `dormant`。
- paused 活动必须有非空 `next_due_at`。运行层仍需计算未来恢复点，结构合同不能替代时钟验证。
- dormant 必须有非空 `resume_condition`；读投影还必须有服务端生成的非空 `dormant_since`。客户端管理输入不能提交这个时间戳。
- `next_due_at` 是否作为 dormant 的重新评估信号，由目标选择解释；它绝不能使活动步进自动恢复 dormant。
- dormant 不证明完成或进度，不增加 `result_refs`；这是跨版本行为不变量，需要状态机测试证明，结构合同不会声称已验证它。

接入时需由 Companion 的 save/transition、目标应用和读投影共同保证这些字段，再更新 Companion、Platform 与相关消费者的固定 manifest。旧 pin 正确地拒绝本候选的新状态，不能只替换 schemas 文件。

历史 unknown 必须保留原步骤及副作用证据。2026-10-30 的只读盘点发现一条 unknown，既没有顶层模型请求号，也没有嵌套模型回执；不能借状态迁移制造“未执行”“已完成”或新的可重发步骤。处理方案需在后续运行层实现与数据 dry-run 中明确。

例子：`examples/paused-dormant.json`。6 个正例、8 个负例只验证结构边界；未执行运行层联合、生产迁移或自动恢复。
