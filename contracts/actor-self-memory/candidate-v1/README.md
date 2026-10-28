# Actor self memory candidate v1

Status: isolated-development candidate, not a production release. TS-309 / AL-01.
This package does not modify the user memory contract or its person-bound scope.

The Memory actor domain accepts append_episode, get_episode, check_episode,
get_receipt, withdraw_episode, and apply_source_revision at a future dedicated
POST /internal/v1/actor-self-memory/operate endpoint. Producer authority endpoints
use actor_check, source_facts, and source_check. These endpoints are contracts to
implement, not claims that current production exposes them.

Authentication is service identity from the transport, never a field nominated by
the model. actor_id and role_version must match the current registered actor.
The existing Platform-admin-only role_grants API must not be borrowed with an
administrator token. Wire the new narrow actor_check producer or fail unavailable.
All authority addresses and producer-to-client mappings are operator configuration,
never caller URLs. No fixture or allow-all authority is a production default.

append_episode carries only an exact source reference and perspective. Summary,
source_constraints, timestamps, coverage, known relation and outcome come from the real
source owner. A successful reading step can cover only part of a video. completed
is the operation outcome, while each content_reads[].coverage[].complete describes
content coverage. Every content_reads entry names the exact content_ref, including
its producer, object ID, revision and hash. Ranges from different contents must not
be pooled or assigned by array position. A non-reading event can have no content_reads.
An observed message can be retained even when no reply was sent. observed,
reported, fictional, planned, unknown and failed never imply an executed action.

Source restrictions and request authorization are separate:
- source_constraints is durable owner-reported information. It identifies actor_only,
  public, an exact group conversation, or an exact private person/conversation. It
  never contains assertion_ref or another temporary credential. public is valid only
  when the real owner establishes it; it is not a fallback for missing restrictions.
- context is specific to a read/check request. Its private/group audience includes
  a fresh assertion_ref for trusted resolution. An old source restriction neither
  supplies this proof nor grants current access. actor_only permits internal use
  only; it does not make material publishable. All dependencies remain restricted.

Operation responses are defined as follows:
| Request | Success response |
| --- | --- |
| append_episode / withdraw_episode / apply_source_revision | command_receipt |
| get_receipt | Original command_receipt; not_found is an error, not proof of no effect |
| get_episode | get_episode_result with the requested active episode, current role version, context and domain_version |
| check_episode | check_episode_result with one result for every requested episode_id/expected_revision pair, context, domain_version and checked_at |

get_episode must return exactly the requested episode_id and expected_revision,
or fail without returning a stale/withdrawn body. check_episode takes versioned
episodes, not bare IDs. current means current_revision equals expected_revision and
the exact version's sources were checked; changed means they differ. A non-current
result cannot authorize reading or disclosure of the version the caller holds.
Only current plus read_allowed and disclosure_allowed permits use in the echoed
context. The actor_only context never permits external publication, even when its
check succeeds. A check is a current observation, not a future authorization lease.

Runtime invariants beyond JSON Schema:
- Bind responses to request_id, actor, exact source refs and hashes; every requested
  ref must occur exactly once. Missing, duplicate or mismatched entries are failure.
  For get/check also compare role_version and the whole context to the request.
  Check result items must match requested episode IDs and expected revisions exactly;
  reject repeated episode IDs, including repeats with different expected revisions.
- Current actor, known relation and permission are checked before authority work
  and again before mutation/return. Source generation/sequence/access versions must
  stay coherent across facts/current. Persist the versions actually checked.
  The requested perspective must match the source owner's verified relation.
- Coverage end must be at least start. Source producers retain the real gaps; a
  requester cannot promote partial content, pending receipt or inferred time. Check
  the referenced content versions as well as the reading event; a source whose
  authority adapter is absent is unsupported/unavailable, never implicitly verified.
- Private/group context assertion_ref must be resolved by a trusted authority.
  Derivatives inherit source_constraints; changing owner does not expand disclosure.
- Idempotency binds authenticated caller, actor, operation and semantic digest.
  Episode identity additionally binds source event/revision and perspective. Same
  key/different body is conflict; retries and multiple projections are not extra evidence.
- Network/model work never runs under a DB transaction. Revalidate versions before
  commit. Missing authority means dependency_unavailable, not successful verification.
- Source invalidation is directional. Local forgetting never deletes the user's
  source. Late work and restored backups must not revive revoked sources.
- Unknown write outcome is reconciled by the original identity. Never infer a safe
  resend from arbitrary exception text. An unknown error code or unknown execution
  requires retryable=false. A not-found receipt does not prove no effect.

Timestamps use a timezone-qualified RFC3339 profile with seconds and optional
fractional seconds. The schema pattern checks syntax; consumers must also check
real calendar dates. This profile excludes leap seconds and year zero, matching
the standard-library datetime validator in the contract tests. Do not rely on
jsonschema's optional RFC3339 dependency being installed to validate dates.

The domain has its own StorageRuntime, schema, migration/guard, queue, indexes,
bounded request/connection capacity, and lifecycle. It does not use user records,
automatic_facts, jobs, source_revision, person identities, or user Store transactions.
No default route is added to the existing Memory service by this contract alone.

Requests: maximum 256 KiB; source batches at most 64 references. Responses: maximum
1 MiB. Reject oversize complete records rather than truncating evidence. Explicit
timeouts are configured on trusted clients. Error responses contain closed codes,
never arbitrary exception text. SQL/version checks, fresh authorization and receipt
semantics are application acceptance requirements, not schema-only guarantees.

## 2026-10-26 当前权威上下文接点

Platform 固定 `POST /internal/v1/actor-self-memory/authority` 处理 `actor_check` 与 `context_check`，实际 service 身份必须有 `actor.memory.authorize`。角色和版本来自当前 RoleRuntime；allowed_operations 由该角色当前 memory.read/write 能力映射，不能用 operator/admin 或关系好感替代。

`context_check` 的 actor_only 只表示角色内部使用。group/private 必须重新验证实际 origin 的有效性、当前路由 companion→memory/dialogue、actor、audience、conversation；private 还核 person。请求不能自报已授权、服务身份、账号或路由。access_version 绑定本次当前权限事实，仅是比较值，不能缓存成永久授权。producer 的 source_check 仍须核自己的当前来源和披露约束；Platform 不证明内容阅读或行动完成。

command_receipt 持久保存第一次成功提交的 request_id。get_receipt 返回该原始回执；调用者按自己持久原请求核对。直接重复写入允许响应携带本次 request_id，但 episode/revision/domain_version/result 不变。duplicate 仅表示另一个语义键已经生成同一 episode，不能用它掩盖新执行。
## AL04 有界派生与查询

- Existing six episode operations unchanged. New derived content is explicitly a model/producer proposal, never an owner fact.
- Memory resolves exact current same-actor episode references, every content_ref, source constraints and all source versions in DB; caller does not provide authority/constraints.
- Derived inputs reference episodes only in v1, no recursive generic graph. Replaces only explicitly supplied current derived refs for this actor.
- Current-role and source/context proofs before use and before commit. All refs AND; missing/unavailable fails closed. actor_only never authorizes publication.
- Query restricts actor/audience/state/expiry before local ranking or any external document rerank. No response augmentation after proof. Limits/budget count complete evidence units.
- Checks are current observations, not future grants. Frozen refs carried by Companion into before-model/before-send checks.
- No new scheduler/model step. Existing producer outbox owns delivery; Memory derived state belongs only to this domain.

replaces 仅同角色当前 derived 引用，不替换原始 episode。非 current check 一律 read_allowed/disclosure_allowed=false。importance/retention 是提议，Memory 使用本域既有周期配置并执行到期，不代表永久事实。query/check 的正态只在 checked_at 当前成立，发送前仍需按原 refs 复核。

AL13 candidate.3 adds episodes-only query mode=timeline over a fixed UTC occurrence window, stable scoped version-bound cursors and explicit incomplete/omitted results. The default keyword query is unchanged. Public audience is restricted to diary/management; all basis and current owner disclosure checks still apply. Null occurrence times never become learned times.

## Core completion candidate.4 (2026-10-28)

All operations reuse /internal/v1/actor-self-memory/operate, caller operation and
actor allowlists, current Platform actor_check and source/context double checks.
Platform's existing role_runtime.memory service credentials and scoped role
management mediation are sufficient; no Companion credentials are borrowed.
Management reads/writes accept only actor_only/management context. interests
accepts the existing scoped context and discloses only complete authorized basis.

interests returns source-proven current preferences, grouped by explicit topic.
Legacy untagged preferences remain individual entries with topic=null and
stance=unspecified, never inferred sentiment. support_count counts independent
original non-fictional digital-action or observation evidence, not repetition or derived count.
history contains version references, not an extra copy of historic text; it is
bounded and history_omitted reports incompleteness. Expired/forgotten records do
not become current. Projection rebuilds from authoritative derived records.

manage_list pages all requested states/kinds by recorded_at/id with a signed,
actor/filter/domain-version bound cursor. next_cursor=null marks exhaustion;
omitted preserves inability to prove complete readable results. Tombstones have
kind/text/importance/retention/interest=null, empty source_refs, forgotten=true.
Active episodes have occurred_at from the original fact. Derived revision is
editable; factual episode summary is not edited into a fabricated source.
get_episode remains the exact source/coverage detail port.

memory_impact returns dependent refs and exact domain_version. Oversized impact
is reported omitted=true; the write still computes and erases the full closure.
revise_memory creates a new derived record replacing the exact old revision,
preserving its original episode basis and validating it again. Omitted interest
preserves an existing tag. expected_domain_version and ref revision protect
concurrent changes. Its normal memory_command_receipt points to the replacement.

forget_memory overwrites current actor fact/payload copies and removes derived
search terms, including dependent derived records. It preserves body-free IDs,
fingerprints, source version suppression and original command receipts. It does
not delete original owner content, user conversation records or historical
backups. The result is memory_command_receipt state=withdrawn and new revision.
Forget of a derived record suppresses same-kind late proposals whose entire
basis is a subset of the forgotten evidence set. New independent real evidence
is required; changing wording, partitioning basis or model self-narration cannot
resurrect it. Existing check APIs continue withdrawn/read_allowed=false.

maintain is called by the existing bounded Companion background cycle. It expires
and clears up to limit derived bodies and compresses genuinely redundant same
basis/text items without inventing a summary or creating evidence. Existing
append_derived(kind=autobiography,replaces=...) accepts semantic compression
from the existing producer model, under unchanged full basis validation.
Relevance fades with retention age; explicit episode timeline remains available.
No extra model, scheduler, user memory logic or permissions are introduced.

Storage schema2->3 explicitly backs up the independent actor domain before
installing a rebuildable actor-only inverted term index. The seven authoritative
tables and guard stay authoritative; startup never silently migrates schema2.
