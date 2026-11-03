# NR-10 后台输入预算与重排诊断

用户最初要求所有后台提升到128000，随后明确更新为“全部统一到200000”。按最新要求统一后台生成输入上限200,000 tokens，日记保持200,000，输出及必要的后续纠正预留独立；普通人工对话不扩容。当前集成基线0feed8c094aad07b6e0f3759c869231650dba2b9，预期生产7126564701d6bdaeeed99d1a02fa753b8cef19a9，发布前重新核实。

## 边界与归属

| 子块 | 写者 | 范围 |
| --- | --- | --- |
| NR-10A | memory_materials | 复用models/budgets.py定义统一BACKGROUND_INPUT_TOKENS=200000；Memory默认/配置校验上界/相应样例和相关测试；不改来源/分批/图片语义 |
| NR-10B | memory_recovery | Growth输入与纠正预算；group participation与autonomous chat输入预算，人工chat保持；相关定向测试和正常恢复方案 |
| NR-10C | memory_protocol | Life日程/写作/日记输入预算及相关测试；其他后台模型调用点只读核对，报告遗漏，不改他域共享入口 |
| 根 | 根 | 共享记录、集成、集中必要验证、有效配置、重排请求与供应商诊断、原阻塞分区正常恢复、串行部署 |

三个子块直接从本批准备提交继续新分支，保留旧分支与已有文件；公共预算只有A单写，B/C使用同一导出。没有现有输入token上限的图片/感知/插件调用，不把字节、帧数、输出token或安全内容上限误当输入预算批量替换。专用向量/重排按各自模型与协议边界检查，不设200000输出。原冻结请求、未决结果和成功回执不重写，不因新预算自动重放外部副作用。

## 必要验收

只验证受影响预算装配、输出预留、旧冻结请求/回执和人工聊天不变等实质边界；复用未变NR-09结果，不跑无关全量/前端。检查生产旧配置是否覆盖新默认；配置只更新确需字段，记录哈希、挂载与模型绑定保持。Growth现有14条待处理通过原来源/版本复检和正常入口恢复，不直接改成reviewed。

重排已知证据：同协议有成功回执；3次实际执行接近30秒、排队仅0.07–0.13秒；两次后台批次正常降级提交，前台一次33.66秒返回6条和rerank_unavailable。进一步对照请求字段、路由、实际调用阶段和小规模独立诊断，区分确定证据与仍需供应商日志确认的原因，不把本地字节估计称为实际token，不因超时把原unknown伪改成功。

## 实现与集中验证

集成源码 `84f4d26d640bbc3b792f93f1ee4b1c9e56b91a62`，A/B/C 原提交分别 `401f6f53` / `4aec566f` / `fe04802f`，集成提交 `67247e8e` / `77207dc8` / `84f4d26d`。所有生成后台的既有输入预算统一引用 `models.budgets.BACKGROUND_INPUT_TOKENS`。Memory 默认与配置校验上界同步；Life 原多份输入常量和 Growth 的 13000 别名移除。没有固定输入 token 上限的视觉、图片、表情与插件直连请求不额外增加截断器。

ContextAssembler 的总预算包含输出预留，本批明确按“200000 输入 + 原输出”传入；Growth 初始材料另在输入中保留原纠正空间。Memory 图像、协议及纠正预留仍计入输入预算。普通人工 chat 保持原 16000 总额/2048 输出预留。材料条数、正文字符、输出、超时、授权/来源复查、原请求/回执和 CAS 不变。更高输入上限不会令每次请求自动填满 200000。

15 个精确文件/节点在隔离 PostgreSQL 中集中执行，**136 passed in 27.82s**，无失败、无跳过。覆盖实际 200000 计数边界、Life 使用生产 o200k_base 的约 190000 输入、输出预留、原 unknown 请求复用、图像完整单元及 Growth 14 条原 pending 经正常 CAS 才推进 reviewed。未运行无关全量、前端或负载测试；未实际向供应商提交 200000 tokens 的压力请求。现用生成模型官方窗口允许这一级输入，但实际代理渠道的极限吞吐和延迟不由本次隔离用例证明。

生产有效 `memory.consolidation={}`，无旧较小上限覆盖；因此无需修改 settings，新默认随代码生效。其他部署若显式配置较小值，仍尊重其配置。更改对象是后台生成输入，专用 Qwen 重排不随之扩大到 200000。

## 云端重排诊断

实际路由为 SiliconFlow `https://api.siliconflow.cn/v1/rerank`、模型 `Qwen/Qwen3-Reranker-8B`、协议 `cohere.rerank`。请求是 `model/query/documents/top_n`，documents 是字符串数组，语义 JSON 序列化成每个文档字符串；没有把 chat 的 max_tokens 或消息数组错误地发给 rerank。字段符合[供应商官方接口](https://docs.siliconflow.cn/docs/api/rerank-post)。

同一生产 broker/供应商/路由执行两次独立合成对照，无记忆写入、无 QQ 发送、无配置变化：

| 输入 | 请求字节 | 供应商报告输入 tokens | 端到端耗时 | 结果 |
| --- | ---: | ---: | ---: | --- |
| 3 条中文短文档，top_n=2 | 252 | 267 | 1.391 秒 | 2 个有效索引 |
| 24 条 JSON 语义文档字符串，top_n=6 | 28088 | 7598 | 2.719 秒 | 6 个有效索引 |

对应 attempt 为 `nr10-rerank-short-20261104` 和 `nr10-rerank-typical-20261104`；原请求未重复提交。对照证明当前协议/格式可被服务正确处理，不证明所有历史具体内容都具有相同延迟。

此前三次超时实际执行约 29.88–29.94 秒，排队 0.07–0.13 秒；未见 HTTP 400/413/422 等格式/长度拒绝。现有证据定位到请求执行阶段没有在 30 秒内拿到完整响应，不能进一步唯一归因于云端推理、网关排队或网络传输；需要对应供应商 trace 才能继续细分。两次后台批次已降级提交，前台保留基础检索结果但等待较长。原 timeout_unknown 保持未知，不以这两次成功对照改写。

历史 `estimated_tokens=36021–39215` 是此分支 JSON UTF-8 字节的保守估计，并非供应商实际 token 用量。官方 [Qwen 模型卡](https://huggingface.co/Qwen/Qwen3-Reranker-8B) 标明 32k 上下文，不能拿整个请求字节总数与它直接比较，也不能把本轮生成输入 200000 套用到重排。后续若继续优化，应记录单文档/查询规模和供应商 trace，再针对慢请求做输入精简或更及时的降级；本批不改既定 30 秒执行窗口、不切模型。

## 串行上线与原材料恢复

2026-11-04 02:05（上海时间）上线源码 `84f4d26d640bbc3b792f93f1ee4b1c9e56b91a62`，镜像 `sha256:10530d9c019b316a9c3ecd2c2e636239eb2c829999bdd8acbe28f794f92fd532`，容器 `6c6f50cba69e94c078aa4e391b910fdc87cc098f61fe5edac9b1db8229fa8f74`。构建来自干净 Git archive 和原 8 个固定镜像 digest；部署前即时确认没有正在执行的模型或队列任务。新容器 ready、restarts=0、容量守护 active，配置哈希、全部模型绑定和 11 个挂载一致，数据库无迁移。线上逐项导入和有效配置证明所有目标输入预算均为 200000。两个 QQ available，两个角色各 4 个受影响读取入口通过；Grafana 正常、Prometheus 三个目标 up、无告警。

原 Growth 分区 `d8b0bbd287faba4a37df131d47b5ab05f09704572b8e5f9aff25800d3fb08fa3` 确认为 `active_batch=None`、revision=534、14 条 pending。沿原保存场景通过 Identity 刷新现有授权，检查 scope signature 与场景一致，再调用既有 `GrowthService.request_review`（与 HTTP 入口同一应用命令），幂等键 `nr10-budget-review-d8b0bbd287faba4a-r534`，CAS 推进为 535。没有直接修改业务状态、生成请求或旧回执。

正常 worker 使用一次新反思请求 `52ce1399-18a4-54fc-ac20-34bc53d6653e`，DeepSeek V4.1 Flash 成功，实际执行约 34.88 秒；应用输入估计 29265，输出仍 8192、执行窗口仍 180 秒。02:06:33 正常提交后原状态 revision=536，last_error=null、active_batch/blocked_material 均空，游标 e:5761→e:5817。逐项对比恢复前后 14 个 Episode ID/revision，全部原版本 reviewed_revision=revision=1，没有换 ID 或仅清错误。

02:06:47 读回：全局 Growth pending=0、主阻塞=0、重试 active/waiting/blocked=0，Memory pending=0；无发布后队列失败、新 Memory failed/quarantined/stale 或活跃模型。两角色 next_due 均为本日 07:00、reason=actor_resting、last_error=null；未强行改作息，后续日间自主行为仍待自然触发，未发送 QQ 测试消息。本次恢复成功不代表云端间歇重排延迟或所有外部行为已验收。

临时测试库 `tianshu-nr10-background-test-pg` 及其精确目录已按既有隔离清理流程退役。未动生产数据库、其他服务或旧运行资料。本机完整测试及操作回执在 `.runtime/nr10-background-20261104/`，真实业务材料与凭据不入库。各块具体装配和恢复边界见 `docs/handoffs/NR-10A.md`、`NR-10B.md`、`NR-10C.md`。
