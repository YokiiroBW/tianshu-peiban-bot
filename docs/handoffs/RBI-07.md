# RBI-07：八域修复本地集成与集中验收

状态：**local_integrated_verified_not_deployed**。日期：2026-11-06。八个既有窗口全部完成，协调者已收齐、串行集成并完成必要验证；本批没有推送、生产发布、生产数据迁移或真实模型/QQ测试。RBI-08 仍未执行。

## 代码、归属与行为

代码起点 b32a606df8238e398c9dcd58e2e145f2ecff9fb3；已验证产品代码提交 **8034743c455191a426567e89227bcf30f72554fa**，分支 codex/rbi-integration-20261106，权威检出 C:/Users/Administrator/.codex/worktrees/rb-integration/tianshu-peiban-bot。后续收口提交只改协调文档。原始窗口提交、必要返修和准确集成映射以 [派发记录](../development/rbi-dispatch-2026-11-06.json) 为准。

保留六角色和各领域状态机。Runtime 只拥有机械预算、领取、原生任务事实和维护周期；Memory/Growth/Life 各自于 bot-background 补漏，ResultConsumer 于 bot-foreground 补缺。正常推进不依赖 Operations，不新增中心业务推进器。

- Models 提供原 attempt/submission 的权威事务事实和完整取消回执；终态请求、结果、费用与 charged_units 不被取消或清理覆盖。failure_stage 与原输出可复用性有独立证据，缺失保持 unknown。
- Runtime 校验新鲜原回执、原身份/代际/摘要和显式取消；修复领取幂等、保留原冷却、原预算和总序号。纯计算 consumer_interrupted 保留 raw cancelled receipt，机械账本按真实计算失败计数，不伪造未发送或退款。
- Memory 连续 unknown 与本地 queued 按真实当前 head 观察续接；Growth 只有已知失败、真实修复和原预算合格才取得资格。Memory/Growth 的 DB due 判断与 DB 时钟一致，避免客户端偏差。没有用 sleep 或时间宽限跳过 future due。
- Life 统一原 Action 观察绑定及唤醒，404 不重做原 invoke。Goal 与原 receipt 保持同事务；Sources scoped_refs 在完整闭包预锁后分别按原 context 授权，Goal 不可见时按原逻辑跳过，不扩大权限。派生完成唤醒保存固定目标/版本并有界推进。
- Life 来源失效仍归档已由锁定 Models 事实确认的原物理失败，但不授予未发送信用或新重试。未确认事实和未知效果继续观察。
- Conversation 各消费者独立领取、记真实 proof；Life/Memory 效果和完成证明同事务。确认提交后丢 ACK 按原 proof 收敛；不确定外部效果不重放。直接渲染、工具、表达及渠道发送后的投影均使用 Action head 围栏和 CAS；无 Action 的渠道权威与本地 unknown 保持原含义。
- 结果任务跨新 job 固定最多四次确定失败；typed wait 不冒充失败。原生历史按每次最多 16 job × 128 event 页、固定上界及持久游标推进。登记 ID 的完整性核对和数组读写成本仍随本 receipt 的 job 数增长，不能称整个事务为常数上界。

## 共享装配与迁移

四个模块接入各自维护回调；Life 注册 life.activity_completed_wake。ExpressionService 注入 caps.actions，Operations ResultRecovery 注入现有 actions。两个 Runtime SQL 同时注册 product/execution 入口；后者排在 Procrastinate 基础表之后。

新增迁移名：rbi_recovery_repairs、rbi_execution_budget、rbi_memory_maintenance、rbi_growth_maintenance、rbi_activity_reconciliation、rbi_result_reconcile。life decision_trace 对旧行保留 NULL，不伪造历史复评边界。Runtime budget 迁移包含 public.procrastinate_events(job_id,id) 索引；发布时按真实数据量安排该索引与受影响域备份。

## 实际验证

**45 个直接受影响测试文件，当前收集的 472 个不同用例均有通过结果，最终失败/错误/跳过为 0。** 复用输入未变的已通过项；返修只复验失败、新增行为和受影响公共调用方，没有运行全产品、双后端、整套浏览器或性能负载矩阵。

覆盖真实隔离 PostgreSQL 的两套迁移入口、重复启动/升级保留、五个应用角色实际装配、Source/Identity 权限与锁、Models 原事实/取消、Runtime 原生事件序号与预算、Memory/Growth 恢复、真实 ResultConsumer/Life 原子回调、表达 Action 头竞态。用例中的模型/渠道为明确合成端点和替身；不把它们称作真实模型调用或 QQ 送达。

首轮旧 RBG 并发夹具在未到达事件前已出错而持续等待；该批已停止，按同代码收集顺序和可核对的终端进度保留完成项，未把中断当整批通过。失败另行取得完整 XML；夹具补齐迁移/公开端口并加入等待退出后，剩余项实际执行。后续逐阶段日志记录每个用例。所有现行用例均与最终收集清单逐项对齐。

证据保存在 C:/YOKI/Codex/tianshu-peiban-bot/.runtime/rbi-execution-20261106/：validation-final.json、final-nodes.json、各轮 XML/JSON 与进度日志、probe-adapter-validation.json、validation-cleanup.json。临时 PG 的完整容器 ID/owner/挂载已核对后清理；镜像、其他容器、配置、原数据、业务文件及工作树保留。根与实现窗口都没有发送 QQ 测试。

## 巡检接线与尚未验证事项

既有探针的 RBI 候选为 C:/YOKI/Codex/tianshu-peiban-bot/.runtime/rbi-execution-20261106/chain_probe.py。复用原只读执行器和四秒 SQL timeout，移除 Memory 全历史 JSON 过滤，接入 current/current_holds/history/target/target_attempts 五个参数化范围；按 data_generation 保存独立游标，失败保留该范围游标，动态 live 页 overall_complete 保持 null。

该候选已通过 AST 及在自有空产品库上对实际生成代码的参数化查询适配验证；后端 SQL 的样本/错误覆盖由已通过的探针 PG 用例提供。这不证明生产查询性能或真实业务恢复。**活跃巡检未切换**：新 helper 随 RBI 代码发布后才存在，应由 RBI-08 与发布一起接入既有巡检，不提前让旧生产导入它，不新建监控或改写旧 JSONL/SQLite。

既有追加模板按真实查询可选添加 business_observations；target 必须含 domain/kind/id/version/data_generation。业务证明、原 unknown/费用、有效 job/合法 due、消费者进度与观察租约分别记。仅 observer 续租不报 recovering；缺项保持 unknown。coverage 的 producer/real_samples/queries/target_sets 不互相替代；只有完整受影响目标集合及逐对象证明才整体关闭问题，局部空页或单对象恢复不够。

旧 Growth invalid_upstream_response 的失败阶段未知且原输出未保存，不能凭新代码重建证据；按新分类入口决定合法续接或准确停止。Life 两次历史规划是否非法重复缺复评边界证据，未取消合法重评。Memory 同 revision 不同片段的合法游标进展保留，不按 episode ID 一刀切去重。Runtime 尾事件锚点存在不能证明更早任意中间事件没有被删除；缺失历史不初始化为零。

## RBI-08 后续

保留数据串行发布、活跃探针切换、准确原对象正常恢复及自然巡检仍未执行。只通过正常领域入口接续，不直接更新业务表、不重放未知外部动作。两条成功原回执的旧 native job 关联须通过既有 Operations IncidentStore.get 公开事实核对，再传 recover_result(known_job_ids=...)；已知组为 99471/99627 与 99817/100073，ID 本身不证明完成。

实际发布后分别核对版本/健康、原材料业务结果和外部未知，再看自然巡检；不要用本地通过、模型回执或健康容器提前宣称原生产问题已恢复。
