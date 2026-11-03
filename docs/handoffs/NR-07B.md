# NR-07B Memory 模型提案与证据归属

基线：`59b9b37a68fae619517cca0862d0848cdf5633e6`。独立受管检出：`memory-protocol/tianshu-peiban-bot`。本卡仅本地实现；未推送、未部署，未调用真实模型或 QQ。

## 实现

- 保留 `consolidation_schema`、`validate_proposal` 与正式 `memory.consolidation.v1` 结果；新增 `MODEL_PROTOCOL = memory.proposal.v2`、`model_schema(frozen)`、`parse_model_proposal(frozen, proposal)`。冻结无 `model_protocol` 仍严格按原格式解释；不修改旧冻结请求。
- v2 输出按 `targets` 分组，每个冻结人物必须显式给 `changed/no_change/unresolved`、操作和未决事项。程序由 frozen 填 schema/batch、CAS、处理覆盖、op/issue/request 编号，并追加明确目标、端点和反例所决定的 record reads；模型声明的额外 reads/context_refs 原样保留。没有自动补 no_change。
- 语义 payload 与正式 schema 复用。四种补查有不同 schema；material 只能用本批 mN。record 补查可请求 stub，读写与端点只允许已给正文的记录。范围按现有 Conversation 端口要求带时区、start < end、至多 24 小时。
- schema 与语义拒绝增加路径、实际项、预期、允许别名或缺失/多余项。`generation.repair_diagnostic(error)` 对模型修正与 manifest 复用有界诊断。预算紧时保留错误位置附近的候选片段及诊断，原冻结证据保持完整；新请求预留相应修正空间。
- `record_context_required` 给 `details.requests`（最多四个完整正式 record 请求），可复用既有一次补查；同时列入可修正错误。补查不可用时模型必须给有据的最终结果或 unresolved。
- 证据类型策略由 `evidence_policy.attributable_statement` 统一归属；真人上传的 image/OCR/reply/mention/技术结构不能单独证明本人明确确认。`RevisionService` 使用 Sources 的权威 locator/media 在事务中复核，来源 human 保持。普通文本、经来源证明的未标注旧文本、合法的 observed/quoted/inferred 使用继续有效；未对旧事实做迁移或回写。
- 仅 Memory 新 Gemini 请求设置 `generationConfig.responseMimeType=application/json`；不改共享路由、其他调用方或现有冻结参数。严格输出校验与有限 repair 保持。

## 集成接口

1. Material 写者给新 frozen 与模型首 JSON 增加 `model_protocol`，材料 `evidence_kind` 取 `text/image/text_image/reference`，保留后台 locator/media 与来源权威。
2. Consolidation 写者将 `_parse` 最后一步改为 `parse_model_proposal`；捕获 `memory_consolidation_record_context_required` 时消费 `details.requests` 并使用原有单次 `_supplement`。补查不成功不能把 stub 当正文应用。
3. Consolidation `_repair` manifest 使用 `generation.repair_diagnostic` 保存同一有限诊断。
4. 根在 `Sources.describe_for_write_on` 的现有 SELECT 最窄补上 `locator,media`。`part_kind` 是 locator 内的键，不新增数据库列。新增核心用例会检查真正来源描述的 locator。

最小模型结果：

```json
{"targets":[{"subject_id":"a","status":"no_change","operations":[],"unresolved":[]},{"subject_id":"b","status":"unresolved","operations":[],"unresolved":[{"material_ids":["m2"],"reason":"缺少本人确认","needed_refs":[]}]}],"needs_context":[]}
```

操作保留 `kind,target,payload,support_groups,context_refs,reads,reason`。省去原 `op_id,subject_id`；旧目标写 `{ "ref": "r1" }`，不输出 expected_revision。新增对象仍用语义关联所需的 `{ "create": "n1" }`。

## 验证与限制

已逐文件审查本卡 diff，并执行 `git diff --check`；未运行测试、构建、数据库迁移或真实模型。所有用例留给根集中执行：

- `tests/memory/test_model_proposal.py`：目标完整性、CAS/覆盖编译、语义依赖保留、新旧协议分离、stub 的目标/read/endpoint/反例补查、非法 raw ID、精确范围诊断及视觉归属。
- `tests/memory/test_evidence_assertion.py`：事务写者对图片/OCR/技术引用的判别，普通/旧文本兼容，以及每个 OR 支持分支都要有本人陈述。
- `tests/memory/test_generation_policy.py`：既有有限修正与预算行为，补上保留错误片段及 Memory 专属 Gemini JSON 参数检查。
- `tests/core/test_memory_revision_domain.py::test_image_upload_origin_stays_human_without_becoming_a_self_confirmation`：Sources 权威描述到统一写者的合成原件链路。

没有据此宣称真实多人语义质量或生产使用通过。是否确实明确确认某个事实仍由语义整理判断；新增程序规则检查其证据类型与可归属性，不用关键词猜事实含义。
