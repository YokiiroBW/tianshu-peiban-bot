# Memory closure：集中验收准备

本目录是 MC-G 的评测工具，不是另一套 Memory、Broker 或部署系统。2026-11-01 开发期间仅编写和静态审阅，**全部 written_not_run**。主协调整批代码及前端集成完成后，才执行下列必要检查。工具本身、替身合同、真实模型语义、生产切换及真实 QQ 投递分别记录结果。

## 一次定向验证

复用 `tests/conftest.py` 的 `database` 夹具：只有显式 `TIANSHU_TEST_POSTGRES_ADMIN_DSN` 才创建 `tianshu_test_UUID`，没有生产 DSN 默认值。旧实例退役后，主协调本轮已另备 NAS `192.168.31.210:25019` 的隔离 PG17+pgvector；不使用生产 25018。工具作者没有连接或核验该实例。下列是**待执行清单**，不是已通过记录。缺少 DSN 导致 PG 用例 skip 时，不得报告对应行为通过。

全部后端适用用例放在同一次 pytest 调用，避免不同窗口重复跑同一输入。PowerShell 中 `$python` 指主协调选定的候选虚拟环境解释器；在最终集成仓库根执行：

```powershell
$targets = @(
  'tests/core/test_memory_revision_domain.py',
  'tests/core/test_consolidation_pipeline.py',
  'tests/core/test_incremental_consumers.py',
  'tests/core/test_memory_evidence_retrieval.py',
  'tests/context/test_memory_evidence_units.py',
  'tests/memory/test_consolidation_contract.py',
  'tests/memory/test_extraction_contract.py',
  'tests/core/test_source_memory_stories.py',
  'tests/core/test_archive_sources.py',
  'tests/context/test_budget_units.py',
  'tests/core/test_memory_closure_acceptance.py',
  'tests/companion/test_growth_output.py',
  'tests/companion/test_growth_budget.py',
  'tests/companion/test_growth_legacy_recovery.py',
  'tests/life/test_planner_execution.py',
  'tests/life/test_imagined_repair.py',
  'tests/models/test_completion.py',
  'tests/models/test_native_settlement.py',
  'tests/capabilities/test_runtime_observations.py',
  'tests/capabilities/test_available_materials.py',
  'tests/channels/test_onebot_protocol.py',
  'tests/runtime/test_product_assembly.py'
)
& $python -m pytest -q @targets
```

来源授权变动涉及当前、历史、别名/归档、公共投影与跨模块装配，所以清单包含 `test_source_memory_stories.py`、`test_archive_sources.py` 和实际 `test_product_assembly.py`，不扫描整个工作区。C 的模型结算/Growth/渠道/能力用例只跑本批受影响文件。MC-G 新文件仅补评测工具的真实边界（未来信息隔离、配对输入、实际 usage、原 attempt、固定恢复清单）及已有管线用例遗漏的持续输入最长等待场景。

前端在 `web/` 运行一次 `npm run build`（已包含 typecheck，不再单独重复），随后 `npm run test:e2e -- memory-closure.spec.ts --workers=1`。这是 F 的模拟 HTTP 页面用例，不代表生产恢复/真实模型/QQ 已通过；其他 F 实际改动若其交接列出必要定向文件，主协调合并进同一调用。仅失败修复影响的部分随后重验。

## M01–M18 与实际证据

以下文件简称 `domain=test_memory_revision_domain.py`、`pipeline=test_consolidation_pipeline.py`、`retrieval=test_memory_evidence_retrieval.py`（均在 `tests/core/`）、`contract=tests/memory/test_consolidation_contract.py`。描述对应已编写用例名，不宣称已运行。`H*` 保留只供最终验收，不能回传实现者调整提示。

| 蓝图 | 定向合同/PG 用例 | 真实模型/消费与人工复核 |
| --- | --- | --- |
| M01 群作者/对象/引用 | pipeline `shared_prefix_uses_one_model_request_for_several_new_episodes`、`episode_speaker_comes_from_original_sources_and_never_tool_prerequisites`、`quote_original_remains_observation_with_reported_author_metadata` | S01/S12/S13/S15 + held_out；查看每共享窗口真实请求、逐人结果与最终回答 |
| M02 no_change/漏人 | contract `every_target_has_explicit_result_even_when_words_match`、`wrong_persons_material_and_unknown_revision_cannot_be_applied`；pipeline `pending_receipt_reports_only_frozen_targets_without_inventing_results`、invalid/truncated | 24 故事逐 target 检查 changed/no_change/unresolved/处理材料；不要求每人新增事实 |
| M03 同文不同人 | contract `every_target_has_explicit_result_even_when_words_match`；pipeline 的来源作者验证 | S09 + held_out；以人物 ID/原件区分，检查少发言者，不能按正文合并 |
| M04 无关目录/256截断 | pipeline `sparse_identity_resolution_does_not_truncate_a_301_member_group`；现有 source/auth stories | 实际请求 subjects 与原作者；301 的既有测试覆盖 >256 边界，额外 500 人场景不声称另跑过 |
| M05 尾部/持续输入 | `test_incremental_consumers.py::test_long_original_and_more_than_128_parts_reach_the_tail`；MC-G `continuous_intake_cannot_push_wake_past_first_pending_maximum` | 每个 checkpoint Episode cursor/coverage；原生入站不等待模型 |
| M06 跨段/中间关键依据 | contract `nearby_prefix_prefers_a_boundary_keeping_a_reply_with_its_parent`、`overlapping_context_is_sent_once_by_exact_source_range`；长原件尾部用例 | S14 + held_out；模型只能看已到达前缀，查看补查步骤及最后回答 |
| M07 多值/条件 | domain `different_values_and_conditions_do_not_invent_contradiction` | S04/S05；当前证据与回答保留条件和可并存值 |
| M08 纠正/变化 | domain `correction_and_transition_have_different_history`；retrieval 时间查询用例 | S02/S03 + held_out；Claim 修订、不同命题/有效区间与当前回答分别审阅 |
| M09 事项身份 | domain `about_matter_keeps_event_identity_and_authorized_derived_projection`；retrieval `matter_valid_at_keeps_the_report_before_rescheduling` | S06/S07/S12 + held_out；稳定 Matter ID、历史及取消/改期不能误合并 |
| M10 报告完成/执行 | domain `matter_history_completion_basis_and_missing_receipt`；Life `test_action_receipt_original_keeps_actual_consumed_source_and_execution` | S08 + held_out；basis 为报告/实际 receipt 分开，无额外外部动作 |
| M11 生成中纠正 | domain `full_read_set_and_expected_revision_reject_stale_proposal_atomically`、`changed_supporting_proposition_retires_current_hypothesis` | 人工看冻结 read_set/版本；事务替身证据不冒称真实并发/生产压测 |
| M12 撤回/授权/历史 | domain `source_forgetting_removes_historical_versions_and_relationships`、`private_correction_does_not_replace_public_head`；retrieval `withdrawn_counterexample_retires_interpretation_despite_independent_new_or_support`、`rerank_rechecks_sources_and_drops_the_whole_unit`；archive/source stories | 来源闭包与最终整单元；缺来源不经历史或 raw trace 重新暴露 |
| M13 unknown/重启/丢响应 | pipeline `unknown_recovery_keeps_original_attempt_and_local_material_map`；Growth 原 unknown/原清单；MC-G adapter unknown+重建映射 | 实际 adapter attempt-map、Broker receipt、ModelSteps；遇 unknown 只观察原 attempt，保留隔离 DB |
| M14 截断/非法引用 | pipeline `invalid_or_truncated_output_does_not_partially_commit_or_advance`；contract 引用/目标；C 模型 completion/settlement 与 Growth 截断 | 保存原失败输出、全目标 coverage 与未推进 cursor；不只评 JSON 合法 |
| M15 反例/同源/冻结 | domain hypothesis/changed-support 用例；retrieval counterexample 闭包；Growth 固定 Episode 版本/预算/旧清单用例 | S10 + held_out；认识支持、反例、条件及实际答案；人格/关系冻结不由评分改写 |
| M16 三种时间查询 | domain `current_valid_at_known_at_choose_version_before_validity`；retrieval `valid_truth_and_knowledge_time_choose_different_versions_and_unknown_stays_unknown`；pipeline 反思时间 | S11/S16 + held_out；真实 memory 工具 time plan 与 unknown coverage 原样保留 |
| M17 真正答案消费 | retrieval `all_five_types_are_real_candidates_and_hypotheses_keep_their_status`、`model_catalog_query_uses_time_plan_and_propagates_the_full_source_closure`、`original_followup_is_range_bound_once_per_foreground_and_keeps_actual_action_identity`；`tests/context/test_memory_evidence_units.py` 全 3 项 | 全24：真实 ContextPack→chat；原生 Memory Action/Receipt、一次原文 trace、实际回答与 required/forbidden 人工审阅 |
| M18 旧数据/任务切换 | pipeline `legacy_application_error_is_converted_once_and_original_attempt_is_only_observed`、`legacy_conversion_does_not_replay_completed_or_unproven_history`、`legacy_without_originals_gets_one_terminal_receipt_and_no_model_request`；Growth legacy；Life imagined repair；product assembly | 固定转换清单、三个具名迁移实读、ID/head/cursor/unknown 保留；生产状态由主协调切换后读回，工具准备不等于迁移验收 |

## 24 个中文流式故事

语料由主协调已有的私有 `semantic-stories.json` 提供（16 development + 8 held_out，12 个额外合同场景）。不把 held_out 正文写进仓库，也不发给实现者调提示。`run.py` 不创建模型评分器：`review.json` 的 required/forbidden 仅在运行后供人读，任何非空回答/合法 JSON 都不会自动变成语义通过。

每个故事使用独立、全新 `tianshu_test_UUID` 数据库。通过真正 `build_product` 组装；Identity 原生登记静态人物目录，只有身份、不包含未来话语。消息依序经过 `admit_event → Conversation.ingest(reply_required=False, autonomous_eligible=False)`，实际来源和 Episode 保存在隔离应用。首条后整理一次，随后每最多三条/故事结束形成检查点；可用私有 `stream.stories.<id>.flush_after` 与 `occurred_at` 固定覆盖，三个成本组必须相同。一次共享窗口可以分多次有界 native 批次，不能把“每故事总共仅一次调用”作为错误要求。

消息 `occurred_at` 是故事锚，`recorded_at` 始终是本次真实原生写入时刻，评测不回填成过去。每检查点另存 actual_snapshot_at，可用它构造本次实际知识边界的 known_at；valid_at 解释原话的有效时间。若问题问过去系统“当时知道什么”，但本次才首次入库且没有旧记录，应保留 unknown。人工 rubric 需要结合该时间含义，不按故事预期硬判错误或修改写入时间。

只串行驱动本次 Memory 原生 handler 和实际索引；不启动一般 worker、Life clock、contact 或任何投递。DB 中保留真实 enqueue 记录，但应用任务调度时延不伪称生产队列实测。真实模型全部复用现有 `ModelClient` 和生产唯一 Broker（`max_connections=1`），原 service class/route/配额继续由它管理，不配置新的 provider/quota group。

私有配置由主协调执行前提供，内容类似：

```json
{
  "synthetic_material_only": true,
  "isolated_postgres": true,
  "postgres_admin_env": "TIANSHU_TEST_POSTGRES_ADMIN_DSN",
  "broker_url": "<已有内部Broker URL>",
  "broker_token": "<仅私有文件保存>",
  "model_context": {"<生产实际授权TrustedContext>": "由主协调提供完整字段"},
  "keep_database": false,
  "stream": {"anchor": "2026-11-01T09:00:00+08:00", "message_interval_seconds": 60},
  "context": {"tokenizer": "o200k_base", "structured_seconds": 1.0, "retrieval_seconds": 2.0},
  "retrieval": {},
  "answer_seconds": 120,
  "max_answer_rounds": 4
}
```

`model_context` 与合成应用上下文严格分开。adapter 先在隔离 Sources 复核请求依赖，再将合成文本请求映射到实际授权 `model_context`，发送生产 Broker 的 `source_refs=()`；生产 DB 没有合成来源，不能伪造其授权。收到结果将 receipt 恢复为原合成 step/attempt 身份，仍由本地原生 pipeline 按原合成引用提交。映射在网络前保存；unknown 后没有换身份重发路径。无来源/非法上下文在请求前拒绝。只支持合成文本，不上传真实私人材料或媒体。它是显式评测适配，不能放进产品。

在对应候选进程/容器内挂载同一 runner；`--code-root` 指只读挂载的精确同 SHA 源码归档根（必须含 `src/tianshu/runtime/product.py`），`--source-sha`/`--image-digest` 填主协调已核实值。**实际 Dockerfile 最终阶段只有已安装的 `.venv`，没有 `/opt/tianshu/src`，不能把 `/opt/tianshu` 填成源码根。** 旧镜像可复用 `.230` 保留的 production image 作为其依赖环境，配合实际旧 SHA 完整归档；候选也挂它自己的最终同 SHA 归档。执行镜像时覆盖 ENTRYPOINT 为 `/opt/tianshu/.venv/bin/python`，不沿用默认 `python -m tianshu serve`。`--network container:tianshu-core` 访问已有内部 Broker，不新增端口。下列 `$runner/$private/$codeRoot/$sha/$imageDigest` 均由主协调按实际容器路径赋值，后文给本批具体命令：

```powershell
& $python $runner semantic --config "$private/config.json" --corpus "$private/semantic-stories.json" --private-root $private --output "$private/optimized-24" --variant optimized --stories all --code-root $codeRoot --source-sha $sha --image-digest $imageDigest
```

每故事输出 arrival、checkpoint-state/dispatch、final-state、context-pack、model-calls.jsonl、attempt-map、answer-transcript、review、report。包括真实提案、Claim/Matter/history/link、各人覆盖、需要补查及实际工具回执。向量正文不重复导出；metadata 仍在。失败/unknown 自动保留隔离数据库名与原请求映射，不删除未结算应用状态、不自动重跑；之后由主协调沿原 attempt 观察和恢复。恢复观察不能创建新一轮“独立样本”。输出只允许在显式 `--private-root` 内。

## 同输入三组成本对照

固定8个 development：S01/S02/S03/S05/S06/S08/S10/S14，分别覆盖多人引用、纠正、变化、条件、事项改期、报告完成、认识反例和中间关键依据。

- 旧组必须使用完整实际生产 `dcdf729b5d22f451093e3658d131fd6757a00dde`，镜像 `sha256:679e08db330d32973e40d32f07007ee5b0b3442a1f6e8fc86af550ed39aaa40c`；运行其默认 `MemoryWorkers.extract` 单 Episode 算法。绝不编造“按每人重复全文”的旧基线。兼容新 Broker 多出的 receipt 字段仅发生在 runner adapter，不修改旧源码/算法。
- conservative 使用相同候选代码，max_episodes=2、max_materials=8、max_old_records=64、context_messages=12、32k 输入，保留完整原件和人物，正常继续处理剩余前缀。
- optimized 使用候选默认策略，仅把真实静默等待替换为同一输入检查点。直接复用上述 full24 中同8份结果，不再付费运行8份冒充独立样本。

旧组/保守组各在其对应独立进程或镜像执行同一命令，将 `--variant legacy|conservative --stories cost` 和输出目录替换；optimized 不重跑。旧代码若需源码，可在私有准备目录用 `prepare-baseline --repo <协调仓库> --private-root <私有根> --output <私有根/old-production>` 从固定 SHA `git archive`，只读准备不执行程序。

```powershell
& $python $runner compare --legacy "$private/legacy-8" --conservative "$private/conservative-8" --optimized "$private/optimized-24" --private-root $private --output "$private/cost-comparison.json"
```

compare 校验相同 corpus、原始输入/时间/检查点、模型授权与共同执行配置指纹，旧生产 SHA 和新两组同源码，保留每样本链接身份/失败与未决，不自动合并成通过分数。实际 provider/model/route revision 也按回执分列，便于核对期间路由变化。各项 usage 从真实 receipt 分列 input/output/reasoning/cached input/cache write；reasoning 不再加到 output，cache 不再加到 input，未知保持 null。按物理 Broker attempt 去重观察结果；有映射但无回执的身份不冒充已确认物理派发。估计输入独立列明，不当作实际 usage。无 provider 费用回执时实际费用 null，不按 token 推算后称真实费用。

还保留实际输入文本字节/精确文本段重复字节（不是 tokenizer 或人物去重）、索引调用/耗时、失败、补查、实际模型排队/执行时延、回答总时延及隔离 PG 实际统计（统计可能延迟，SQL statement latency 未采到则 null）。按人物检查漏记、错误归因、条件、认识反例与答案，再判断成本可接受；不能靠少处理人/少记事实拿平均 token 收益。

## 固定 ID 恢复

`recovery.py plan` 是默认准备用途，无网络/DB/模型；`apply` 仅显式执行 imagined Episode 的公开修复端口，并只读核对 QQ 原 inbox。不调用模型，不修改活动，不重建输入，不偷偷改群策略。

固定清单 schema `memory.closure.recovery.v1`，包含 manifest_id 和 items。imagined 项必须有原 `context/activity_id/episode_id/expected_episode_revision/expected_activity_revision`；CLI 调用 C 的 `LifeService.repair_completed_imagined_episode`，使用保存的 completed checkpoint 登记 ACTOR 原件、同事务修订原 Episode。原 ID/event/occurred_at 保留，recorded_at 为实际补登记时间。C 的 CommandStore 负责重复清单幂等与漂移拒绝。没有历史足够依据的活动不回填。

可选 `resume_known_source_rejection=true` 和 `expected_growth_error` 仅适用于已知缺来源拒绝，且原 blocked_material/batch 实际包含该 Episode。修复后先读原生 Growth view；observer 已续接则不用新审。仍为同一已知拒绝、`can_request_review=true` 才使用固定 client_request_id 调原生 request_review。任何 unknown 原 attempt 不替换。

QQ 项必须提供原 `event_key/connection_id/producer/event_id/event_revision/expected_state='accepted'`。apply 仅读回原 events/job-key/admission/turn 身份及保存的 delivery_allowed；原 observe/ignored 与可能正常 reply 分开，缺值 unknown，当前 link mode 明确标为 current。原 reply 路径可能排队正常回复：由主协调对实际清单审阅、必要时一次具体向用户确认后，恢复**原 job id/event_key**，不生成新消息或重新提交 raw。原功能和权限保留。

三个具名迁移由现有 runtime 单一注册者执行：`0021_memory_consolidation`、`0034_companion_growth_batches`、`capabilities-0002-runtime-observations`。本脚本不隐式 migrate、不启动一般 worker。主协调遵循 C 独立 cutover 准备脚本，备份只涉及实际受影响域及恢复引用，实读 Memory/Growth 固定转换清单；保留未知模型原身份。若必须回滚，镜像与对应数据状态成对恢复，保留切换后的新增原件；不让旧不等值冲突 writer 写入新修订结构。

```powershell
& $python $recovery plan --manifest "$private/recovery-fixed.json" --private-root $private --output "$private/recovery-plan.json"
& $python $recovery apply --manifest "$private/recovery-fixed.json" --config "$private/recovery-config.json" --code-root $codeRoot --private-root $private --output "$private/recovery-result.json"
```

恢复配置由主协调提供 `recovery_postgres_env`（必须显式，无默认 DSN）与实际 `app_settings`。plan 不需要配置。apply 也不打印/导出 DSN 或秘密配置。恢复结果/配置/真实材料全部留在私有目录，不能入库。

## 本批真实运行命令与兼容核对（静态准备）

阅读基线为集成 `5c4d738` 和真实旧生产 `dcdf729b5d22f451093e3658d131fd6757a00dde`。这里没有执行命令。候选迁移/集中返修完成后，主协调使用**最终已核实的源码 SHA 和镜像**，不把本次阅读基线当作最终候选。

主协调已提供远端 `/srv/tianshu-core/evaluation-memory-closure-20261101`：`config.private.json`、`postgres.env`、`semantic-stories.json`、精确旧归档 `legacy-code/src`。配置的必要字段为 synthetic_material_only、isolated_postgres、postgres_admin_env、broker_url、broker_token、model_context；context/retrieval 使用主协调已从实际设置复制的值。model_context 需要实际 principal_id、actor_id、person_id、conversation_id、scope_ids、audience、access_epoch、origin_kind、permissions、conversation_kind、delivery_allowed，不接受合成人物或猜造 epoch。秘密值不出现在命令行/报告。postgres.env 只需实际隔离 TIANSHU_TEST_POSTGRES_ADMIN_DSN，不复用整份生产 .env。该 NAS 地址可由 `.230` 访问；container namespace 中的 localhost 不是宿主 loopback。

`build_product(RuntimeServices(..., role='interactive'))` 复用原生图。隔离身份显式 setup_token_ref=null，避免读取生产注册 token；没有复制账号/供应商/QQ 配置，CredentialStore 不需要生产 master key。caps.prepare 只构造原生域/注册任务，不调用 module.start、caps.initialize、channels.start、queue worker 或 supervisor。只读 Memory Action 会启动其本地 execution-owner 心跳并在 close 停止；业务写入和状态文件都在隔离 DB/输出目录。真实 Broker 会正常记录本次授权请求/usage/配额，这是预期模型结算，不是“生产完全无写入”。

| 原生请求 | 保留的选择方式/要求 |
| --- | --- |
| memory.extract / elastic | 两版均 route_id/route_revision/protocol=None，由实际 actor 的 function 或 `*` binding 选择，要求 text；候选 visible+reasoning 输出预算来自原生 policy |
| chat / interactive | 实际 chat binding；带原生只读 Memory ToolSpec，要求 text+tools，不把它改绑为独立付费模型 |
| embed / elastic、interactive | 同一真实 embedding_profile 的 route，1024 维、none/l2 normalization；索引与查询保留原 service class |
| rerank / 原调用 class | 仅实际 retrieval.rerank=true 才走原生 rerank binding/capability，adapter 已支持；默认 false 时不额外调用 |

两版 ModelRequest 字段一致；旧结果没有 completion/hold_until，adapter 只过滤旧 ModelReceipt 不认识的新 wire 字段，保留完整实际 wire 回执用于计量。旧图/旧迁移/单 Episode worker/旧检索/旧工具均从旧完整归档导入。旧工具目录提供 memory.search/claims/matters，候选增加 memory.source.trace；schema/name/availability 均取真实 Catalog。改写类 Memory 工具不提供给评测回答，不提供外部能力工具。driver 的 nativehandler 进度同时观察 Episode 与 batch（包括已知截断缩窗），不把 batch 状态不变的 unknown 反复当新请求。Windows CLI 在 asyncio.run 前启用 Selector policy。

下面是 `.230` 上的 Bash 命令；只准备文本，由主协调在完整集中复验和候选 build 后执行。主协调另把**与候选镜像相同的最终 SHA 完整归档**展开到 candidate-code；runner 跟随该归档，保持 evaluation/memory_closure 目录结构，所有臂共用同一 runner。

```bash
EVAL_ROOT=/srv/tianshu-core/evaluation-memory-closure-20261101
OLD_SHA=dcdf729b5d22f451093e3658d131fd6757a00dde
OLD_IMAGE=sha256:679e08db330d32973e40d32f07007ee5b0b3442a1f6e8fc86af550ed39aaa40c
CANDIDATE_SHA='<最终已核实的40位SHA>'
CANDIDATE_IMAGE='<对应候选image的完整sha256 ID>'
CANDIDATE_CODE="$EVAL_ROOT/candidate-code"
RUNNER_HOST="$CANDIDATE_CODE/scripts/evaluation"
mkdir -p "$EVAL_ROOT/results"

run_arm() {
  local arm="$1" code="$2" image="$3" source_sha="$4" stories="$5"
  docker run --rm --name "tianshu-memory-eval-$arm" \
    --network container:tianshu-core --no-healthcheck --read-only --user 0:10001 \
    --tmpfs /tmp:rw,noexec,nosuid,size=128m \
    --entrypoint /opt/tianshu/.venv/bin/python \
    --env-file "$EVAL_ROOT/postgres.env" \
    --mount "type=bind,src=$code,dst=/code,readonly" \
    --mount "type=bind,src=$RUNNER_HOST,dst=/runner/evaluation,readonly" \
    --mount "type=bind,src=$EVAL_ROOT/config.private.json,dst=/evaluation-input/config.private.json,readonly" \
    --mount "type=bind,src=$EVAL_ROOT/semantic-stories.json,dst=/evaluation-input/semantic-stories.json,readonly" \
    --mount "type=bind,src=$EVAL_ROOT/results,dst=/evaluation-output" \
    "$image" /runner/evaluation/memory_closure/run.py semantic \
    --config /evaluation-input/config.private.json --corpus /evaluation-input/semantic-stories.json \
    --private-root /evaluation-output --output "/evaluation-output/$arm" \
    --variant "$arm" --stories "$stories" --code-root /code \
    --source-sha "$source_sha" --image-digest "$image"
}

run_arm optimized "$CANDIDATE_CODE" "$CANDIDATE_IMAGE" "$CANDIDATE_SHA" all
run_arm conservative "$CANDIDATE_CODE" "$CANDIDATE_IMAGE" "$CANDIDATE_SHA" cost
run_arm legacy "$EVAL_ROOT/legacy-code" "$OLD_IMAGE" "$OLD_SHA" cost

docker run --rm --network none --read-only --entrypoint /opt/tianshu/.venv/bin/python \
  --mount "type=bind,src=$RUNNER_HOST,dst=/runner/evaluation,readonly" \
  --mount "type=bind,src=$EVAL_ROOT/results,dst=/evaluation-output" \
  "$CANDIDATE_IMAGE" /runner/evaluation/memory_closure/run.py compare \
  --legacy /evaluation-output/legacy --conservative /evaluation-output/conservative \
  --optimized /evaluation-output/optimized --private-root /evaluation-output \
  --output /evaluation-output/cost-comparison.json
```

run_arm 的 output 必须全新；失败已有目录不删除/重跑，不把同一 unknown 换请求身份。root 根据 report/attempt-map/database 保留原账本恢复。cost compare 直接取 optimized 中相同8份结果；它不联网、不调模型。命令没有挂生产 config/data/socket，没有调用 tianshu serve；不启动普通 worker、QQ 网关或发送流程。Source archive 与镜像/迁移的真实可用性仍由主协调本轮集中结果证明，不能由本文静态核对冒称运行成功。
