# RBW-frontend 前端交接

- 工作分支：`codex/rbw-frontend-20261105`
- 基线：`e2529c95a92857f368111d9ae5b23ee66800b636`
- 前端代码和测试提交：`a372f953a3d0fb032cd51a884fcafd509d62f0a2`
- 范围：`web/`；交接文件另行提交。主聊天修改的 `.codex/config.toml` 保留在工作树中，未包含在提交内。

## 已接入

- 独立日志、统计、运维事件与设置页进入现有导航。日志使用固定范围和不透明续页游标，按原事件身份去重，虚拟列表与页面内记录均有界；详情关联原任务、模型尝试和运维事件。只导出服务端提供的脱敏 `entries`，不导出兼容流原始行；持久化、生成、采集和迟到补传覆盖分别说明。
- 统计页读取指定时间范围、时区、模块、角色和路由，分别呈现区间/累计、实际调用/业务结果、实际/估算/未知用量、恢复验证、时延阶段和来源水位。投影不可用与零样本分开；趋势中的缺失值不连线。
- 运维事件按原事件与版本请求恢复，展示诊断、恢复观察和通知回执；诊断主/备用路由与 QQ/SMTP 配置复用领域接口，SMTP 密码只在明确替换时提交。模型用途绑定增加有序 `fallback_routes`，路由可录入已核验上下文容量，容量组分开显示 `cloud_concurrency`。
- 生活页展示角色状态、后台进度及日程候选的逐项应用结果；薄活动列表按需读取 checkpoint，编辑前比对版本。日记可记录可选反馈，不增加发布前置步骤。
- 成长页复用现有当前规则、来源和撤回入口，增加修订、历史、样例及样例撤回；记忆页反思改读 Companion 权威接口，按范围与时间分页。记忆详情显示提交状态、必要上下文缺项和已返回的索引状态。
- 对话和群会话按需展示实际取材、预算、供应商用量和 `DialogueResult.flow` 中逐轮阶段；历史未记录、空选择、业务应用及投递分别呈现。
- 独立项目、资料、共享经验和家庭设备使用已授权的独立范围；插件支持选择授权工作区绑定，保留角色绑定。NAS 采集缺项按命令、权限等原因展示。
- `SessionView.data_generation` 建立当前代际；后续 JS API 使用 `X-Tianshu-Data-Generation`，原生媒体/下载使用同源 API URL 的 `data_generation` 参数并保留 Range。检测代际变化时清除旧业务缓存、对象标识和未发意图，重新读取，不自动重发，保留本地外观设置。原生命中旧代际时依赖服务端拒绝旧请求。

## 接口与集成约定

- 本包无 DDL、无后端模块注册。须由主协调者按各域交接完成迁移、路由、后台生产者与跨域装配。
- 会话、登录、首次设置返回 `data_generation`；所有 API 响应返回 `X-Tianshu-Data-Generation`，后续携带旧代际返回 `409 data_generation_changed`。原生资源 GET 的查询参数由共享 middleware 按同一代际校验。
- `system/logs` 返回 `entries`、`next_cursor`、`coverage` 和 `freshness`；游标绑定固定范围/过滤条件。`system/tasks` 可选 `job_id` 为正整数或十进制字符串，仍返回 `{jobs, counts, limit, complete}`；该查询不以最近列表代替原任务。
- `system/statistics` 依照冻结合同返回 `range`、`overview`、`cumulative`、`series`、`breakdowns`、`resources`、`coverage`、`freshness`、`recovery_rate` 和 `as_of`；统计的真实分母、生产者覆盖和物理调用口径由后端维护。
- Operations 的 `settings/save`、`recover` 必须返回完整标准 `CommandResult`，包括无回执时的 `receipt: null`；恢复受理不等于业务已恢复。设置使用 `smtp_password`/`clear_smtp_password` 与 `sealed:operations:smtp`，读取不回显密码。
- Models 的 `bindings` 接受有序 `fallback_routes`，目录返回绑定与容量组实际值；备用候选还依赖服务端能力、容量、协议和配额判定。`conversation/turn` 返回 `context_selection`、`token_usage`、`provider_usage`、`flow`，没有历史记录应保留 `null` 或 `not_recorded`。
- `memory/extraction/status` 使用 `commit_state`、`required_context_missing`、`indexing`；`companion/growth/reflections` 是反思列表权威；Life 的 `day-plan` 应用结果、活动 checkpoint 与可选日记反馈、Growth 的 `revise/history/examples` 按各域已确认 DTO 装配。

## 尚待集中完成和验证

- Memory 当前合同只保证索引 `unknown` 或 `not_required`；已应用但仍待索引以及索引完成的实时事实须等 Retrieval/Memory 的公开观察口接通，不能从应用成功推断索引成功。
- “当前规则已遵守但持久修订未完成”的独立业务事实，以及日程查询的业务阶段计时，仍需后端提供实际可读字段后在对应页面接入；本包没有把未记录状态伪装成完成。
- 22 类既有功能、窄屏、旧书签、真实媒体 Range、日志大于单页且同时间戳、跨域动作与权限，尚未完成集成环境验证。日志/统计页面可用不代表采集、业务收敛或真实用户体验已验收。
- 新增/修改了日志、统计、运维、代际、模型备用、对话取材、成长、生活、独立范围和记忆相关回归用例；本窗口按授权未运行测试、构建、类型检查、浏览器烟测、真实模型、QQ 或生产操作。只做静态 diff 审阅和提交前 `git diff --cached --check`，通过；统一验证由主协调者执行。
