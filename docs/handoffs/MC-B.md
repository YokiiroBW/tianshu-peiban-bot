# MC-B：共享材料整理、固定执行与恢复

本分支基线 `8bca244`，实现者 gpt-6.1-sol / max。用户要求全批代码完成后统一验收。本交接只说明代码与静态阅读结果，未运行测试、typecheck、build、模型或实机调用，未操作生产、账号或远程。

## 提交与依赖

- 协议小提交 `32625c3` 已由协调者和 MC-A 取用。
- 领域依赖：`4a70af8647f5f8d52915a1cfb1140188183bb4b8`、`f668254be07e0457e54503ca85aaf5ea152eb0e4`、`bad09f4eeb313bfcd5cfdc12e9d29977875782e7`。本分支已取用；协调者已有对应集成，勿重复回收。
- Broker completion 依赖 `16dccd4`，本分支已取用。
- Growth 实际版本队列依赖 MC-C `ce723b8`：与 Memory 的 `episode_observer` 和 `episodes_by_refs` 成对装配；本分支不复制 C 代码。
- 检索依赖 MC-D 的 `candidates`、`query`、`indexing.index_text` 及 Episode 唯一可证 speaker 列。D 尚在本批实现，协调者应在统一验证前集成。

## 结果与责任

新默认仅 `memory.consolidate` 写整理结果。分区由角色、执行身份、会话、可读权限域和原件写域确定，群发言者不进入分区。静默、最大等待与材料预算共同触发；无材料不调模型，无新材料及无可恢复原请求不循环整理。

`materials.py` 选择有界完整前缀，超长原件以源序号和字符位置继续，保留真实来源、作者、引用元数据、邻近连续前文及固定对话截止位置。新增消息正文不按人物复制；稀疏主体与当前旧事实、假设、事项、词法/向量候选并集统一进入一次多人请求。群目录不使用前 256 人截断。旧证据关系两端实际版本也进入冻结 read_set。

`consolidation.py` 拥有冻结 batch/step、来源先锁、分区/批锁、领域调用、逐人覆盖与游标同事务提交。局部别名不重排，最多一次固定补查；第一次结果不提前落库。`ModelSteps` 持久化派发权，跨进程未知只观察原 attempt。供应商物理 succeeded 与业务完整输出分开；length / max_tokens 明确为 truncated，整批不提交、游标不动，并有界缩窗，达到最小窗后要求预算配置。

`recovery.py` 只将未完成旧 Episode 登记到固定转换清单。已提交或未知旧请求只观察原 attempt；存量原结果可证明映射时才转换，不能证明则留可解释废弃回执，再按原权限把尚未处理的新材料交给唯一默认流程。已完成历史不重新播放。`memory.extract` / `memory.index` 只保留真实已排队键的有限适配；新任务使用 `memory.consolidate` / `memory.index_object`。

Memory 统一在 `observe_episode` 事务中通知 Growth，传实际 Episode 身份与版本。Conversation 输入、能力结果和公开表达不再各自发无版本的 Growth 通知。`episodes_by_refs` 精确读取实际版本，缺失或改版返回 coverage 缺项，不用 max sequence 当已消费证明。`record_reflection` 可复用调用方事务并 CAS 冻结 Episode，锁全原件闭包；ACTOR imagined 完成原件只能支持主观反思，不能成为人类自述。

手动纠正和真实派生失效通过 `domain_changed_on` 引入有来源的修订材料；自动批次只对实际派生失效唤醒，避免自激。撤回清除 Episode/Reflection 正文、索引、batch/step 敏感输入，领域历史/关系清理由 MC-A 的唯一来源回调执行。索引先写规范词法正文，再请求可选 embedding，旧空索引按稳定任务键有界补齐，不伪造 recorded_at。

合法 quote 允许 256 个结构片段，原件仍为 observation；引用原作者只从已核验原事件解析，转发显示字段不会升级为本人确认。Memory 的邻近读取端口固定截止位置、权限域和原件复核。

## 实际 HTTP 合同（供页面装配）

以下均为 POST，请求有 actor_id 及当前合法 person_id / conversation_id；写操作还需 client_request_id。领域返回 `Page={items,next_cursor,coverage}`，完整证据单元字段由 `memory/types.py` 定义。

| 路径 | 请求主要字段 | 实际结果 |
|---|---|---|
| `/api/v1/memory/claims` | subject_id?, cursor?, limit?, time_mode?, valid_at?, known_at? | ClaimRevision Page，含 head_revision / relations / memory_kind / 双时间与缺项 |
| `/api/v1/memory/claims/get` | claim_id, time_mode?, valid_at?, known_at? | 单个已授权固定时间命题 |
| `/api/v1/memory/claims/history` | claim_id, cursor?, limit? | 完整 MemoryEvidenceUnit 历史 Page |
| `/api/v1/memory/matters` | subject_id?, cursor?, limit?, time_mode?, valid_at?, known_at? | MatterView Page，含完成依据、条件、due_at、关系与历史语义 |
| `/api/v1/memory/matters/history` | matter_id, cursor?, limit? | 完整 MemoryEvidenceUnit 事项历史 Page |
| `/api/v1/memory/query` | plan:{query?,subject_ids?,matter_refs?,time_mode?,valid_at?,known_at?,evidence_kinds?,coverage_mode?,budget?}, cursor?, limit? | EvidenceUnit Page；服务器构造 deadline，list 是稳定分页清单，ranked 是有界候选且不冒称完整 |
| `/api/v1/memory/episodes` | cursor?, limit? | EpisodePage（items / next_cursor / watermark / coverage）；每项 consolidation 为实际批状态和恢复动作 |
| `/api/v1/memory/extraction/status` | episode_id | episode_id / revision / batch_id / status / error_code / action / fragment_cursor / target_results / unresolved；来源可读时还有 step_ids / schema_version / context_unfulfilled |
| `/api/v1/memory/extraction/resume` | episode_id, expected_revision, client_request_id | CommandResult.data:{episode_id,queued,action,batch_id}；重放不重发任务，复核后重建当前 consolidation，replayed=true |
| `/api/v1/memory/projection` | person_id? 或 group_id?, time_mode?, valid_at?, known_at? | claims、hypotheses、matters、reflections、evidence_units、time、pages、coverage；假设与事实独立 |
| `/api/v1/memory/index/rebuild` | client_request_id, kinds?（默认五类）或单 kind?, cursor? | CommandResult.data:{queued,kinds,next_cursor,coverage,replayed}；游标 `i1:` 绑定同一类型集合、kind 与实际稳定 ID，所有任务走唯一 index_object |

群 projection 另有 `group_consolidation={view:'current_processing',items,subjects,coverage}`：按最近 16 条当前可读 Episode 的真实 batch 去重，返回被授权逐人 target_results 和 unresolved；窗口截断、来源与主体缺项均在 coverage 中。它是当前处理状态，不伪装成历史事实。需要完整处理记录时继续使用 episodes 分页。反思页也支持 time_mode / valid_at / known_at；首次知道时间未知或反思有效时间未知会明确降低 coverage。

Episode.speaker_id 只取来源中可证明的唯一人类作者，多个作者、只有引用或工具前置的人类材料都留 NULL。它与 person_id（所讨论/所服务人物）不同。新重建入口默认覆盖 claim / hypothesis / matter / episode / reflection；重放只复核当前可读分页、不重新派任务，queued=0、replayed=true。

`consolidation.status` 使用真实 batch 状态 `prepared/waiting_model/ready/applied/stale/failed/revoked`；尚无批时为 Episode extraction_state。`target_results` 含 subject_id、status=`changed/no_change/unresolved`、processed_material_ids、operation_ids；这些字段来自已原子提交的回执，未提交结果不展示为成功。

页面只使用以下 action，不从失败字符串推断：

- `observe_original_attempt`：观察原 batch / step / attempt，不新建模型请求。
- `refresh_material`：已知旧应用不可用，用原权限和未推进游标重新准备当前材料。
- `wait_for_new_evidence`：当前材料已处理，缺项需要新证据，不空转重模型。
- `configure_budget`：输入或最小窗输出预算不足；先调整配置，再恢复。
- `legacy_application_abandoned`：旧映射无法证明，原执行回执保留，不伪称旧结果已应用。

## 编写但未运行的用例

- `tests/memory/test_consolidation_contract.py`：逐人精确覆盖、错误人物/版本、群分区、固定授权序列化、短 manifest、来源区间去重、连续前缀、推理占用预算、证据关系端点 read_set 与五类索引游标。
- `tests/core/test_consolidation_pipeline.py`：多 Episode 单请求、未知观察原 attempt、非法/截断全批不提交、301 人群稀疏身份、quote 原作者不冒认、按实际 Episode 版本读取、唯一可证作者、反思版本 CAS 与历史时间缺项。
- `tests/core/test_incremental_consumers.py`：原增量场景切换到共享整理器、长原件尾部完整覆盖、真实视频分析与未消费模态缺项。

## 集成后必须核对的限制

本批尚无执行证据。领域、Broker、Growth、检索和前端全部集成后再由协调者一次运行必要用例与装配验证。D 的规范索引 helper / speaker 字段和 C 的 Sources kwargs / Growth SQL 为成对依赖，不可只发布 B。模型语义、真实成本、跨进程故障和生产迁移均未在开发阶段验收。
