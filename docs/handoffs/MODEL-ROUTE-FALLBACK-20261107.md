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


## 根集中验证、串行发布与真实业务结果

2026-11-07 18:19，北京时间。源dbbe09d3在根串行集成为27f5e7aa；共享maintenance/humanize两行装配a06d28b2；测试断言补修df5ac062集成为b3c76eee。实际部署产品b3c76eee2c31d4bb825398c6880f5f49a93cf392，镜像sha256:bc06e3b3123c6236214dcbd623717b4e35579198f4b938bc2dca8e24d30b7528，容器6983b0223716d8010f898d729025f14cb357dbfd3fd4368b3ac0e3506e591486。

- 41个不同定向用例有效通过，0未决失败/错误/跳过。初次40通过/1失败为测试误期望remaining()返回负数；产品profile已经成功。只修断言、单项重跑通过，40项直接复用，产品无新变更。临时PG及凭据已按owner/容器ID回收。
- 不可变Git归档、8个固定基础镜像构建并推送github；单生产写者串行切换。18:19:05六角色ready、HTTP200、0重启/无OOM，两个守护active，7个实际安装Python模块SHA256与Git逐一匹配。中文提示仅静态检查及正常前端构建，未做浏览器视觉验收。
- 原turn377c076d-1912-56a1-9c57-e332c073633e/原attempt fc59d93f-f360-4821-a21b-d7ebf8a47a23在18:17:54自动准入第一合格备用Gemini3.8（provider-ed51ac379e2746ecb2fa5b1d6e1435fe，revision2），18:18:01模型succeeded，18:18:07上下文done，18:18:08.901 send_private_msg confirmed，18:18:13原turn completed。expression delivered，execution=succeeded、effect=confirmed，回执baeb90d0-4c11-4d99-94bd-3cc72a5877d4；这是自然业务路径，不是主动QQ测试或手动重放。
- 配置/11挂载/另2容器/代际/模型绑定、供应商、路由能力与窗口均保持。原231条Models unknown、3条Action unknown和1500退役记录保持，本次新增unknown=0。执行事实计数912→916随自然业务变化，不宣称该表摘要相同；repair grants原5份摘要保持。
- 主旧probe timeout_unknown未重发，未知容量仍unknown，已入账/提交/unknown不另起备用；本轮只覆盖准入选路及无候选恢复，不声明Memory本地错误、日记对账或全部成长支路已修复。

完整本机证据：C:/YOKI/Codex/tianshu-peiban-bot/.runtime/model-route-fallback-release-20261107/。包括validation-final.json、test-pg-cleanup.json、build-result.json、release-status.json、final-health.json、preservation.json、conversation-before.json、conversation-after.json、execution-final.json。
