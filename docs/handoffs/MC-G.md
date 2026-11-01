# MC-G：评测工具、集中矩阵与固定身份恢复准备

2026-11-01。分支 `codex/memory-acceptance-20261101`，基线 `1f0146c`。
工作树 `C:/Users/Administrator/.codex/worktrees/tianshu-cr-foundation/tianshu-peiban-bot`。

这是 **written_not_run** 交付。遵守用户“全部代码/前端改完再统一验收”，没有运行 pytest、候选程序、typecheck/build、浏览器、模型或生产/SSH；没有读取私有凭据、访问网络或真实材料。仅阅读授权源码/不可变提交、语料结构和 development 首例/元数据，编写工具/用例并做静态 diff 审阅。held_out 正文没有给实现者用于调提示。没有产品 src/web 改动或部署行为。

## 交付

- `scripts/evaluation/memory_closure/README.md`：一次定向后端命令、F 页面/build 的一次调用、M01–M18 到实际用例/语义故事/生产读回的映射，以及各证据的限制。没有另建全项目 CI 门禁。
- `run.py/dataset.py/runtime.py`：24 故事逐时间流输入真正 product graph，原生 Identity/Conversation/Sources/Episode、共享整理/索引，最终 ContextAssembler 加实际只读 Memory Action/Receipt 工具调用聊天模型。required/forbidden 只在结束后的 review 导出，语义结论始终 pending_human_review，不用合法 JSON 或正则命中判通过。
- `broker.py`：仅评测用单 Broker adapter。先核验隔离合成 Sources，再使用主协调提供的实际授权 model_context、现有 ModelClient、生产唯一 Broker（max_connections=1）；生产请求 source_refs 为空。实际模型路由/物理配额/前后台 service class 不绕开。网络前保存原 attempt 映射，unknown 后只能观察，不重发；结果恢复原合成身份后由原生应用落本地账本。旧镜像新增 receipt 字段的兼容仅在 runner，不修改旧默认算法。
- `metering.py`：按物理 attempt 去重实际回执，input/output/reasoning/cache 分列，未知 null；无费用回执时不制造真实费用。实际文本重复字节不是 token 或人物去重；索引、模型排队/耗时、SQL 实际统计与前台回答耗时各自保存。
- `recovery.py`：固定 manifest 的 plan/apply 接口。复用 C 公开 `repair_completed_imagined_episode` 的原活动/Episode/双 revision 幂等修复；只为同原 Episode 的已知缺来源拒绝且 can_request_review 时调用原 Growth review，unknown 不替换。QQ 只读核对原 event_key/job id/admission/保存的 delivery_allowed，明确当前 mode 与原处置；默认不派发、不造新入站、不更改群策略。
- `tests/core/test_memory_closure_acceptance.py`：评测工具未来材料隔离、实际 usage 不重复相加、adapter 来源/权限分离及持久 unknown 不重发、成本拒绝虚假旧基线、固定恢复双版本/unknown 边界；另补已有管线用例缺少的持续入站最长等待场景。全部仅编写，未运行。没有重写同领域既有测试。

## 对接与执行条件

D 公共端口按其不可变 `9d75b61bfdd3a4d149f6c52a1c151fa0f02b0353` 核对，主协调已集成为 `eb7817c`。源流使用正常的 Conversation 端口；最终读工具使用 Catalog/Actions 的实际 Memory query 和 source.trace。D 已确认真实暂停 synthetic Life activity 可承载 raw trace，Action 保留真实活动 ID、选定的来源、一次额度与统一 deadline；模型不能在参数中扩权。该活动仅用于隔离评测证据生命周期，不激活或执行。

C 修复端口按 `3becfa4`（主协调集成 `7ae598b`）的最终签名准备：
`await life.repair_completed_imagined_episode(context, activity_id, episode_id, expected_revision, expected_activity_revision=...)`。
只处理原已完成 checkpoint，不造新活动或新模型请求。原 occurred_at 保留，补登记 recorded_at 是当前真实时间。上述 D/C 产品依赖无需在 G 分支再重复 cherry-pick；主协调集成 G 后使用全部最终源码执行。

旧成本臂固定为完整实际生产 `dcdf729b5d22f451093e3658d131fd6757a00dde`，镜像 `sha256:679e08db330d32973e40d32f07007ee5b0b3442a1f6e8fc86af550ed39aaa40c`，运行其原单 Episode 默认代码；不是假想按人循环全文。8 development 成本样本 S01/S02/S03/S05/S06/S08/S10/S14。候选 conservative/optimized 同代码、同输入/时间/检查点，optimized 复用 full24 中已完成的同8份结果，不重复付费冒充独立样本。可直接用旧/新镜像挂同一 runner，复用 `--network container:tianshu-core` 的现有内部 Broker，无需新物理 Broker 或公网端口。若需源码，prepare-baseline 仅从固定 SHA git archive 到明确 private-root 内。

所有执行都需要主协调先提供私有配置、明确隔离 PG 管理 DSN 与实际 model_context/Broker URL/token。runner 无默认生产 DSN，禁止 25018；每故事创建 `tianshu_test_UUID`。失败/unknown 保留隔离数据库名及原 step/attempt，不能丢失后自动重跑。产物在显式 private-root 内。开发阶段没有创建/连接 PG，没有 archive 或启动候选。

时间模型明确分开消息 occurred_at 与本次原生 recorded_at，绝不把记录时间回填成故事日期。checkpoint 保存实际 snapshot 时刻。valid_at 按原话时间解释；known_at 没有过去知识记录时保留 unknown，人工 rubric 不按故事预期强行判错。

## 集中验收的实际范围与未覆盖

已有源授权/归档/预算/装配、领域/管线/Growth/模型/能力/渠道/页面定向用例均仅纳入待执行清单；同输入通过后复用结果，只在后续相关修复时重验。真实模型日志、提案/修订/事项/coverage/补查/最终答案用于人工逐人、逐错误类型评审，不能用平均 token 或 health 取代记忆效果。

本工具串行直接驱动原生 Memory handlers，DB 保留真实 enqueue 记录，但没有启动一般队列 worker，所以应用级 Procrastinate 排队耗时不宣称生产测量；实际模型 Broker queue receipt 独立计量。PG 统计可能延迟，没采到 statement latency/真实费用仍为 null。默认不驱动 Growth、Life planner 或外发，其他 C 真实闭环由主协调按其原生场景统一安排。

生产三个具名迁移、受影响域备份、Memory/Growth 固定转换清单和单写切换由 C 私有 cutover 准备与主协调负责。QQ 原 inbox 可能正常排队回复，最终执行前由主协调按实际清单和已有授权决定/一次具体询问；观察或 ignored 项与可能 reply 分列，不以猜测默认允许。GIF/自拍/Fish 配置不在本包，用户暂不绑定音色的决定保持。

当前只有静态 diff/空白检查证据，不能报告工具可执行、测试通过、真实模型语义/成本可接受、迁移成功或业务上线。后续由主协调完整集成后统一启动集中验收。

## 集中返修后的静态兼容准备

按主协调要求，以集成 `5c4d738` 与实际旧生产 `dcdf729` 只读核对，没有执行测试、模型、网络、PG 或生产。首次集中收集发现的 Python 3.12 嵌套 walrus 语法问题已在 `77eb5dd` 改为普通循环，本轮不宣称重新运行通过。

本轮纯工具修复：隔离 Settings 显式 setup_token_ref=null（否则原生 setup 会默认索取真实注册 token）；透传实际 context 配置并纳入共同指纹（否则 tokenizer 预算回退字节）；Windows CLI 的 asyncio.run 使用 Selector；允许原生可选 rerank 文本函数/capability；进度指纹同时检查真实 batch，防止 Episode cursor 不变时过早停止已知截断缩窗，并按 ID 排序避免物理行序造成假进展。相关评测用例仅编写，由主协调纳入受影响集中复验。

静态确认：Dockerfile runtime 只带 installed .venv/web/dist/deploy，缺 `/opt/tianshu/src`。遵照主协调偏好，不增加第二套 installed-source loader；旧/候选均挂各自精确同 SHA 完整 git archive 为 `/code:ro`，同一最终 runner 挂 `/runner/evaluation:ro`，显式覆盖 ENTRYPOINT 为 `/opt/tianshu/.venv/bin/python`。两版 ModelRequest 字段/原生产品入口匹配，旧 receipt 的新字段过滤仅在 adapter；没有改旧 Memory 默认逻辑。当前依赖 manifest/lock/plugin package 在两个阅读提交间没有变化，但实际仍使用各自对应镜像。

主协调已提供 `.230` 的 `/srv/tianshu-core/evaluation-memory-closure-20261101` 私有 config/postgres.env/stories/legacy-code，NAS 隔离 PG `192.168.31.210:25019` 可达。只记录必需字段名和路径，不读取/复述秘密；没有读取 held_out 正文。README 末节给三组准确 Docker 命令、目录层次、ModelCatalog bindings/capabilities、ModelClient wire 及 native初始化/无普通worker的边界。候选 source/image 由 root 在所有产品返修及集中复验、build 后填最终值；不用阅读基线冒充最终候选。

业务写入/文件限定于隔离数据库与私有结果目录；模型请求仍由唯一生产 Broker 正常记录审计/usage/配额，不能称“生产完全无写入”。没有绑定 Fish 音色或驱动外部能力。root 等 C 最后启动修复后统一复验/构建/运行，G 不提前试跑。
