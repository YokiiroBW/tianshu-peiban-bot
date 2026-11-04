# RBW-retrieval：检索与索引投影

先读AGENTS.md最新RB授权与[公共合同](../rb-contracts-2026-11-05.md)。完整需求以第六版队列为准，最新配置保留/全部天枢业务清空覆盖旧数据保留条款。

## 文件边界

- `src/tianshu/memory/retrieval.py`
- `src/tianshu/memory/retrieval_budget.py`
- `src/tianshu/memory/query_plan.py`
- `src/tianshu/memory/candidates.py`
- `src/tianshu/memory/candidate_sources.py`
- `src/tianshu/memory/embedding.py`
- `src/tianshu/memory/indexing.py`
- `src/tianshu/memory/index_jobs.py`
- `src/tianshu/memory/evidence.py`
- `src/tianshu/memory/evidence_policy.py`
- `src/tianshu/memory/display.py`
- `src/tianshu/memory/source_trace.py`
- `src/tianshu/memory/tools.py`
- `src/tianshu/memory/rb_retrieval.sql`
- `tests/memory/test_rb_retrieval*.py`
- `tests/core/test_rb_retrieval*.py`

Memory检索子包，不改Memory写入域文件/schema.sql/module.py/types.py。新投影DDL在rb_retrieval.sql，所需types变更回报协调者。真实原生memory工具也必须携带学习引用并尊重用途，不直查Companion私表。2秒fallback；部署示例配置由协调者同步。成功数学复用有界，来源最终复核，中文检索及多支路降级。

公共product/migrations/config/schema/types和部署根目录由协调者写；确需改变时回报精确接口/代码片段，不改他人文件。你可新增职责明确的本域文件；shared path白名单优先，不创建平行系统。

## RB-09 候选检索、重排与计算复用

### 交付要求

- 结构化主体/场景/时间/当前状态约束优先，词法与向量返回小型 ID/分数集合，再批量证明并按需要补候选
- 有条件使用融合与多样性选择；只在候选接近且用途需要时重排，不能仅为缩时取消语义质量
- 向量复用键包含规范化输入、模型/维度/预处理版本；重排复用还需 query 和候选版本
- 同请求只准备一次图片、token 估计和所需材料；撤回/权限变化使缓存失效
- 将本地向量候选查询与来源校验窗口的代码fallback、新装配置种子、部署示例及配置文档统一为2.0秒；不得只依赖生产配置覆盖。既有显式用户设置继续生效，embedding/rerank各30秒独立。
- 承接ML-09/SD-09：中文整词/短语/姓名优先并保留合法单字回退；独立词法/向量支路在原总预算和连接预算内并行，成功支路保留。复用既有RRF/MMR、批量来源校验和有界补位，不新造检索系统。
- 精确对象/list走结构化查询，完整列表不等于top-k；诊断分开候选未召回、资格/预算丢弃和最终装入。跨请求成功数学结果缓存仅在代表负载证明净收益后采用小容量/短TTL，绝不缓存权限决定、失败或unknown。

### 移除或替换

- 删除全授权域大正文扫描与逐候选独立来源往返；不按每个无语义变化重建向量

### 最终集中验收要覆盖的行为

- 省略配置项和使用部署示例两种新装入口均实际加载2.0秒本地候选/来源窗口；显式覆盖仍可用，embedding/rerank各30秒及合法词法降级语义保持。
- 短预算仍有合法词法结果；大 AND/OR 支持组不超参数限额；source-filter 后有界补候选
- 记录同规模质量与端到端时间/调用数；ANN 命中不能证明完整清单
- 同维不同嵌入空间的备选不能查询旧索引；索引切换尚未完成时沿用可用profile或词法降级，并如实显示覆盖。
- 中文姓名/合法单字、否定/时间、多主体、前批失权后的补位与支路失败均保留质量；缓存冷/热/低命中分别有开销和省掉的实际调用证据，未证明收益时只保留请求内及相同索引文本复用。

## RB-07 Memory 当前事实、修订与搜索投影

本卡仅实施上方边界属于本工作包的部分；其余部分由同号卡其他包完成。

### 交付要求

- 事实/事项/关系维持明确类型，以 head 指向语义 revision，进度与访问统计分离
- 搜索文档为可重建投影；学习规则索引只引用 Companion 记录和版本，当前状态须由所属域解析
- 当前与历史查询使用显式用途；撤回原件立即使派生材料不能被消费，索引可异步清理
- 原文正文、媒体对象和向量各归一处；保存具体时间、主体、否定、条件，不只留摘要
- 索引profile按嵌入空间区分，profile重建期间当前词法/结构化读取继续；不得将不同模型同维向量混存为同一有效profile。
- 承接ML-11：Episode仅在语义内容变化时追加历史，提取游标/索引状态不复制正文；current/valid_at/known_at分开。学习历史由Companion单一权威承接，不另建可编辑Reflection副本。
- 搜索投影按对象语义版本/profile写回并复核当前head；旧版本索引晚到不得覆盖新版本或让撤回内容复活。索引未就绪使用合法词法/结构化原域读并报告freshness。
- 关系/好感账本仍属Memory，角色—人物冻结不授予权限；短期情绪仍归Companion。作者明确冻结策略作为配置保留，新自动数值不从旧BOT业务回灌。

### 移除或替换

- 删除多域复制学习正文/active 状态；删除业务 JSON 内重复嵌入的大证据正文和向量

### 最终集中验收要覆盖的行为

- 更改/撤回事实后当前查询不回旧结论，历史查询仍能解释演变
- 暂未建向量不丢关键词/结构化召回；不同条件记录不会因为文字相似被合并
- 角色想象和第三方观点不写成用户客观事实；关系冻结与权限独立保持
- 经历语义编辑后各时间模式能读到真实已保存版本；历史/缓存/派生索引也服从遗忘。旧embedding晚于新版本返回不覆盖当前head，元数据变化不产生新语义历史。

## 完成回执

提交本工作包全部实现和必要测试代码，写`docs/handoffs/RBW-retrieval.md`：基线/提交、已完成条目、公开接口与DTO、DDL及注册顺序、共享装配需求、未完成和未执行验证。不要跑测试/构建/烟测。不能把假实现、未连接的helper或只写文档视作完成。以完整功能块收口后再交协调者，不对每个小改动发验收请求。
