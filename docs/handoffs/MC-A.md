# MC-A Memory domain implementation

Status: domain implementation complete, awaiting the one combined integration verification.
No tests, typecheck, build, model or live acceptance run during development.

## Frozen MC-A / MC-B interface

`ClaimService.revisions.apply_operations_on(conn, context, *, batch_id, frozen, operations)`
uses the caller's existing transaction and returns a JSON-compatible mapping with
`local_refs`, `changed`, `affected_subject_ids` and `semantic_link_ids`.

`frozen` is a mapping with `subjects`, `materials`, `records`, `read_set`.
Subjects are a server-verified ID map. Material IDs map to full `source_refs`,
`speaker_id`, `origin_kind`, `occurred_at` and role. Record IDs map to
`{kind,id,revision,subject_id,scope_ids,...}`. `reads` are frozen record aliases;
support groups and context refs are frozen material aliases. Link endpoints may
be frozen record aliases or proposal-local creates. IDs are never interpreted
using refreshed array order. The full read_set is checked, including objects
the model did not name in its operations.

MC-B locks and rechecks all frozen source dependencies, then partition/batch,
and calls this domain service. It locks existing domain objects in stable
kind/ID order and checks expected revisions. It does not commit or advance
cursors. Every created ID is deterministic from actor/batch/local identity.
MC-B commits coverage, cursor, commit refs and batch applied in that transaction.

`ClaimService.revisions.source_changed_on(conn, sources, kind)` cleans domain
history, relations and their projections; MC-B calls it from its source callback.

Named migration: `0021_memory_consolidation`; four tables and all fields follow
blueprint sections 5.2–5.5. Batch rows also include created_at/updated_at.
Legacy recorded_at remains NULL. Old contradiction arrays remain audit data
and are not migrated to semantic links.

## Delivered behavior

- `revisions.py` owns the common Claim and Matter revision writers. Manual
  correct/save, explicit callers and frozen batch operations all use them.
  Claims support create, reinforce, correction, true transition, retract and
  hypothesis revisions. Transition creates a stable successor, preserves the
  older proposition and records a sourced supersedes relation. Unknown change
  time remains unknown; the old proposition becomes history_only.
- Full frozen read_set rows are locked in stable kind/ID order and their
  revisions checked, including records the model did not repeat in reads.
  Proposal-local identities derive from actor/batch/alias. No transaction is
  opened or committed by the domain apply method. MC-B owns source-first,
  batch/partition locking, coverage, cursor, idempotent replay and applied state.
- Source alternatives remain OR, necessary fragments and derivation context
  remain AND. Reinforcement preserves independent alternatives and evidence
  event identities; it reuses an unchanged content embedding. A different
  scope/principal cannot overwrite a shared head: correction saves a separate
  scope-local object and sourced replacement relation.
- `semantic_links.py` owns about_matter/same_event/supersedes/contradicts/
  supports/counterexample relations. Endpoints retain exact revisions. Changes
  in meaning end current proposition relations; exact fingerprints allow pure
  support changes to retain meaning. Stable event associations survive title
  or due-time changes. Cyclic replacement is rejected; a bounded cycle-search
  overflow remains an explicit unresolved failure without cursor advancement.
  Same-domain hypotheses depending on changed propositions are retired from
  the current projection, including bounded downstream support chains. Frozen
  current-status checks and all rendered relation endpoints participate in CAS.
  Private relations cannot retire a public head. Apply results additionally
  return `derived_invalidations` and their affected subject IDs for MC-B's
  targeted follow-up. This does not turn a withdrawn inference into a new fact.
- matter_claims and Claim.matter_id are rebuildable indexes. Business reads
  derive associations from currently authorized semantic links. Legacy unequal
  values never create a contradiction. Legacy explicit associations supported
  by real sources become about_matter relations before the old index is rebuilt.
- Matter revisions store full business snapshots and completion basis. Human
  reports remain reports; action_receipt requires server-frozen actual execution,
  action and receipt identities. Model-provided IDs and imagined activities do
  not establish execution success. This writes no Life/Action command.
- `source_changed_on` evaluates all active Claim history, Matter history and
  current projection, semantic links and cached associations. It scrubs
  unavailable content and vectors and never revives an older head. MC-B retains
  responsibility for its Episode/Reflection/batch/model-step cleanup.
- `history` and `matter_history` return paginated complete evidence units with
  current source/access checks. `list/get/matters` support current, valid_at and
  known_at. Knowledge revision selection precedes validity filtering. Legacy
  unknown recording times are excluded from precise known_at and reported as
  missing. Matter effective time inferred only from a report is explicitly
  marked incomplete.
- `evidence.build_evidence_unit` handles Claim/Hypothesis, Matter, Episode and
  Reflection. It preserves author/assertion, conditions, times, states,
  completion basis, counterexamples and exact sources. Authorized relation
  endpoints include bounded, nonrecursive complete snapshots so conflicts and
  changes retain both sides in one budget unit. Their sources participate in
  whole-unit revalidation. Episode.consolidation is a server-injected projection
  for real coverage and recovery actions, populated by MC-B.

## Integration details

- MC-A base: `8bca244`; initial interface/migration commit: `4a70af8`.
  MC-B protocol dependency `32625c3` was cherry-picked here as `6273eba`.
- New index tasks use only `memory.index_object` with `{kind,id,revision}`.
  MC-B registers it; old memory.index remains a finite stored-task adapter.
- Exact subject lookup calls MC-B's resolve_memory_subjects_on for supplied
  subject IDs. The old directory route remains only for callers asking for the
  directory itself. domain_utils owns time/lexical/predicate rules; service can
  re-export parse_time by importing it.
- Migration 0021 includes consolidation_partition_id on Episode and a pending
  partition index requested by MC-B. Original identity/cursors are unchanged.
- Runtime additionally registers `0034_companion_growth_batches`, depending on
  MC-C SQL commit `3fe73bc` at companion/growth_batches.sql. MC-C owns that SQL;
  it must be integrated before collecting all product migrations.
- Runtime registers `capabilities-0002-runtime-observations` immediately after
  capabilities-0001, using MC-C-owned capabilities/runtime_observations.sql.
- Manual correct/save call MC-B's domain_changed_on in the command transaction,
  carrying actual changed IDs/revisions, command sources and affected subjects.
  MC-B records the internal memory_revision input once and schedules the related
  partition. Automatic apply does not call the hook for every successful batch.
- Real CAS drift: `revision_conflict` (409). Frozen-structure errors
  memory_read_set_incomplete/ambiguous and semantic cycle/constraint errors are
  terminal apply failures, not permission to generate a new model attempt.

## Written verification material, deliberately not executed

`tests/core/test_memory_revision_domain.py` contains 14 synthetic stories:
coexisting values/conditions, correction versus transition, full-read-set CAS
atomicity, OR/AND dependencies, private correction versus public head, Matter
completion/history and false receipt rejection, bitemporal selection and
unknown recording time, source forgetting across history, manual correction
replay, complete evidence, stable about_matter identity, cycle rollback and
hypothesis counterexample withdrawal.
The final story retires a hypothesis after its supporting proposition changes
and rejects a frozen proposal against the formerly current derived projection.

All new SQL and code require the final combined isolated-PG/domain run. No
migration, runtime health, real model semantics, QQ or production outcome is
claimed here. Multi-type retrieval and consumer/context assembly remain the
separate MC-D integration scope and will consume these types/builders.

## 集中验收后的迁移返修（2026-11-01）

- 基于集成 `5c4d738b5c354d15938c630c9dc5076ef27df846` 在原 worktree 新建
  `codex/memory-migration-fixes-20261101`；原 A/D 分支及提交完整保留。
- 首轮集中 PG 验收日志 `backend-pg-01.log` 的 setup/root product migration
  报 `GroupingError: aggregate functions are not allowed in FROM clause of their own query level`，
  指向 0021 原第 187 行。`jsonb_agg(to_jsonb(r.scope_ids||m.scope_ids))` 的参数只有
  外层变量，PostgreSQL 因而将 aggregate 归到外层 INSERT SELECT 查询；该层的
  FROM/LATERAL 里不能包含属于自己的聚合。不是业务数据不足，也不是改 GROUP BY
  或跳过旧关联就能正确解决的问题。
- scope JSON 先作为支持组合子查询 `g.scopes` 的本地列，再由本层
  `jsonb_agg(g.scopes)` 聚合。完整迁移其余两个聚合分别引用本地 `g.refs` 和
  `raw_ref.value`；后者显式命名列以明确归属。没有跳过迁移或删除旧关联保存逻辑。
- 每一端都必须有非空证据组，且组合经真实 Sources 谓词核验才转为 about_matter；
  没来源/已撤回的旧关联不升级成确认语义，旧不等值 contradictions 继续只保留审计数据。
- 已集成的 `budget_failure`、speaker/partition、索引及未知记录时间字段保留。
- 新增 `tests/memory/test_consolidation_migration.py` 两个真实 PG 行为用例：
  空安装/幂等重入及集成字段；带旧 cache/显式 matter_id、OR 支持、已撤回和无证据
  端点的 0020→0021 升级。检查合法关联重建、未知 recorded_at 和审计数组保持。
- 本次只静态阅读错误日志、完整 SQL 和 diff，编写用例并提交；**没有运行测试、
  SQL/语言解析器、构建、模型或生产操作**。修复效果等待所有返修完成后的集中复测。

## 第二轮集中验收后的私人纠正返修（2026-11-01）

- 从已集成 `753eaa2a76c59d1091d755b9df32a702ce319bb0` 建立
  `codex/memory-domain-repairs2-20261101`，原 A/D 及首轮迁移返修提交完整保留。
- 协调者第二轮运行报告迁移已通过，领域节点
  `test_private_correction_does_not_replace_public_head` 失败。全场在另一个预算故障处
  被终止，尚无该节点完整 exception trace；本次没有重跑或从失败节点猜测错误。
- 静态调用链可确定的根因：ordinary_users 的 Alice/Bob 分属独立私聊，Bob 可经公开
  Sources 读取 Alice 的 shared Claim，但 Identity 的当前候选目录不包含 Alice。
  原 writer 在读取真实目标、判断 scope fork 之前先调用 `_claim_values_on`，后者只接受
  目录人物，因此在 `claim_subject_forbidden` 处结束，无法保存应有的私人解释。
- writer 先以实际 ID/expected_revision 读取并锁定当前真实目标。同一目标 subject
  不可替换；目录没有此主体时，仅当该真实旧目标仍 active 且 Sources 当前完整支持组
  可读，才承认这个对象自身的不可变 subject。没有信任 frozen.subjects、放宽身份目录、
  读取私人原件或为新对象任意 subject 赋权；actor 和原 CAS 检查继续适用。
- 保留原 principal/scope fork 判断、稳定新 ID、expected=0 CAS 和 supersedes 关系。
  fork 使用已核验的值在同一 writer 内保存，避免递归新建时失去旧目标的窄授权依据；
  不修改公共 head、公共历史或公共来源。关系继续使用私人支持范围，公共读取看不到
  私人解释/依据，原授权来源撤回后私人分支及历史也失效。
- 原失败用例的两条断言保留，追加 Bob 目录缺项、实际公开读、私人作者/subject、
  当前列表的范围内替代、公共 head/revision/来源不变、私人关系及历史隔离断言。
  新增 `test_manual_private_correction_keeps_public_head_and_rechecks_source_on_replay`
  覆盖手动命令共用 writer、幂等、公开来源撤回后的当前/历史/重放；新增
  `test_public_target_subject_authority_cannot_be_reassigned_or_forged` 覆盖不可换 subject、
  伪造冻结主体不能新增其他人事实及原公共 head 保持。均为 **written_not_run**。
- 仅修改 revisions.py、对应领域测试和本交接；没有触碰 B 预算/分段、前端、模型或
  生产。没有执行测试、语言解析、typecheck、build 或模型调用，等待整批集中复验。
