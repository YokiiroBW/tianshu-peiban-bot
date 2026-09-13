# 跨产品契约入口

已有 [TS-001 文字对话合同 1.0.0](text-dialogue/v1/README.md)：JSON Schema、正反实例、离线可执行检查与兼容决定。**已发布为实现基线，生产者/消费者设计确认通过，L0/L1尚未运行**。[I01–I18](../docs/architecture/v2/interface-catalog.json) 仍是能力目录，合同路径不代表已运行端点。

优先范围：标准消息与消息组、人物解析、按范围记忆查询、模型原生请求接入、渠道/网页回复下行、轮次提交后的记忆写入、版本化模型配置读取。缺口与验收见 [并行契约审查](../docs/development/workstreams/contracts-review.md)。

发布单元放在 `contracts/<能力>/<主版本>/`，至少包含语义说明、schema、正常实例、负面实例、生产者/消费者和兼容决定。项目只引用已发布版本；若需要本地生成代码，生成物记录契约哈希，不能各自改一份 schema。

提议 → 生产者与消费者检查 → 协调者合并 → 任务更新绑定版本 → 各仓库实现 → 联合验证。兼容性破坏需要新主版本或明确迁移，不以静默兼容代码掩盖分歧。根合同不重复收编 AssetLibrary 的内部 AssetLink 等现役契约。

当前发布状态：**文字首切片 1.0.0 已作为实现基线发布**，L0/L1 未运行。网页回复、模型配置下发和初次记忆写入不能因为目录未单独编号而被遗漏。

当前实现入口：[文字合同 1.0.0](text-dialogue/v1/README.md)。按 manifest 的版本与哈希绑定，不再引用旧候选 worktree。

画像扩展：[profile-memory/v1 1.0.0](profile-memory/v1/README.md)，请求人/目标分离，版本领域独立；`python contracts/profile-memory/v1/validate.py` 验证该包与固定文字依赖。仅实现基线发布，产品与L0/L1验收另记。

来源同步：[source-sync/v1 1.0.0](source-sync/v1/README.md)，物理来源与角色受理分层、平台逐角色授权、Memory同步失效屏障及后台检查。已发布为三产品实现基线，状态见[TS-002发布记录](../docs/development/reviews/TS-002-release.md)。在既有合同验证依赖环境运行 `python -B contracts/source-sync/v1/validate.py`；不把离线验证当实际端点或完整L0通过。
