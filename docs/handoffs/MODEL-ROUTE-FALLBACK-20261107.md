# 主路由不满足请求时自动尝试已配置备用

用户授权：“应该是只要主模型不符合，就切换备用尝试，做一下这个修复吧。”
基线 a2129bc4（产品2821fa3a），独立分支 codex/model-route-fallback-20261107。
本次只处理原模型尝试准入前的选路；已入账、已提交和unknown继续原结果观察。

## 实现

- Models Catalog复用一个route_requirements检查主/备用：可用性、明确请求能力、
  原生协议和无损适配、实际配置输出上限、已知上下文容量。不合格按配置顺序继续，
  返回第一个合格候选。额度/路由冷却仍走原有独立配额规则。
- 容量未登记保持unknown，不写造窗口证据，不把unknown当已知超限；允许正常有界调用。
  已知容量不足、缺工具等必需能力、不兼容的私有/native历史、错误输出配置仍跳过。
  不额外要求旧后台生成登记JSON能力，只使用既有required_capabilities及工具/媒体推导。
- 原显式固定路由、probe目标、embedding profile空间及已经建账的路由/revision保持。
  没有候选时返回model_no_compatible_route和有序安全rejections，不创建伪物理失败回执。
- Conversation保留waiting_external和原冻结请求，error_code显示model_no_compatible_route，
  停止submit/observe互相自唤醒。配置阻塞的恢复归属conversation/model_admission.py，
  沿现有前台maintenance每30秒最多32条，游标公平扫描，单轮3秒、单条1秒本地时限。
  先观察原Models；有原结果只恢复消费，未确认且未进入recovery_unconfirmed的请求才读profile。
  可恢复时同事务清阻塞+enqueue原ID，入队失败回滚阻塞；不改原Models或重放外部动作。
- Broker/Client的只读generation_profile统一使用waiting_budget，避免旧legacy deadline
  阻止尚未准入的普通聊天。原budget值不改；Broker/Client仍各有10秒元数据时限。

## 根需单写装配

1. conversation/module.py现有bot-foreground maintenance内加入
   `await service.resume_model_admissions(limit=32)`，与现有result_consumer.reconcile_due共存。
2. 共享web/src/design/humanize.ts增加model_no_compatible_route可读文案：
   “当前没有符合请求要求的模型，等待配置更新”。单项文案直接检查即可。

无新依赖、表、迁移、配置、绑定、能力或窗口修改。未知物理结果不换号/延时重发。
本地只做语法/import、相关新文件Ruff、diff检查；没有运行测试/构建/生产命令。
旧大文件原有导入Lint未扩大清理。

## 根集中必要验证建议

- tests/models/test_route_fallback.py
- tests/models/test_profile_waiting_budget.py
- tests/models/test_rb_profile_quota.py（两条旧“窗口未知必拒绝”用例按用户新语义改为已知超限必拒绝）
- tests/models/test_rb_routing_timing.py中
  test_fallback_uses_independent_configured_quota_and_verified_context、
  test_same_dimension_different_embedding_fingerprint_is_not_a_fallback、
  test_fallback_translates_only_proven_equivalent_options
- tests/models/test_output_budget.py::test_profile_is_read_only_and_frozen_binding_cannot_drift_or_select_another_route
- tests/conversation/test_model_admission_waits.py（包含根maintenance装配合同，需先补装配）
- tests/conversation/test_rb_model_handoff.py

沿用同一隔离PG及既有验证框架，适用旧结果复用，不重复传输、UI或无关全套。
最后由既定唯一生产写者串行发布，读回原turn377c076d.../attempt fc59d93f...的自然推进，
不得把本地通过或备用选择当作实际回复送达。主旧probe timeout_unknown继续保留。
