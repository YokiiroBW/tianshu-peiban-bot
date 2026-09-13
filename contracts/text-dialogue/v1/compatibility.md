# 责任与兼容矩阵

候选 `1.0.0-candidate.2`；尚无已发布当前版或前一版。双方“待审查”不是确认；主协调者的设计方向认可也不代表生产者/消费者真实联调。

| 边界 / schema | 能力映射 | 生产者 → 消费者 | 验收子集 | 状态 |
|---|---|---|---|---|
| common / 来源解析与错误 | 公共边界 | platform/NoneBot issuer → companion/memory；各责任方 → 调用方 | A01/A02 来源与版本 | 候选；来源解析服务待实现 |
| conversation / ingest、bundle、turn | I01 | NoneBot/platform → companion；companion → 自身边界快照 | A01/A03/A04 | 候选；无调度实测 |
| conversation / send、cancel | I02/I18 | companion → NoneBot；platform/桥接 → companion | A03/A04 | 候选；无真实发送核对 |
| identity-memory / resolve、register、link | I03 | companion/platform → memory | A01 | 候选；账号证明获取流程待实现 |
| identity-memory / select、revise | I04/I05 | companion/platform → memory | A02/A05 | 候选；权限/索引运行检查待实现 |
| conversation / committed_event、consume_receipt | I05 首写补充 | companion → memory | A05 | 候选；候选提炼与首写未运行 |
| web / snapshot、projection | I02 下行补充 / I17 文字子集 | companion → platform → 授权浏览器 | A15 文字/恢复子集 | 候选；无 SSE 服务联调 |
| model / config、route | I14/I15 | platform → model_gateway；companion → model_gateway | A14 文字协议子集 | 候选；TS-040/TS-041 审查后绑定 |
| source.archive_state | I06 定位与降级 | Chat Audit/适配器 → companion/memory | A02/A05 来源子集 | 不虚造归档写端点；待核实现有接入 |

## 版本决定

1. wire 主版本 1；目录及 `$id` 绑定主版本。发布 manifest 的文件摘要钉住本候选完整内容；生成代码需记录 manifest 版本与具体 schema SHA-256，不能各自改 schema。
2. 删除字段、改字段类型/语义、可选改必填、扩大数据可见范围均为破坏性变更，发新主版本。修改候选也须更新 candidate 序号和摘要，由协调者审查，不能把原哈希重新解释。
3. 此版生产者校验为封闭字段，防止误传 authority/secret 等。**向旧服务发送新增可选请求字段也必须先协商版本/能力**；不能仅称“可选”就向严格旧端发送。旧客户端的原请求在新服务仍须可用。响应可选扩展由消费者只投影已知字段并校验，安全边界字段一律不通过宽松透传扩大权限；当前 fixture 检查仍严格匹配候选生产者形状。
4. 正式下一版的兼容门禁需同时喂当前发布版、明确支持的前一版请求/响应样例；本版没有前一已发布版，不捏造跨版本通过。`unsupported_version` 负例拒绝未知主版本；闭包类型和字段关系不得因容忍扩展而失效。
5. 发布顺序：主协调者审查候选 → 责任方及直接调用方检查实例/记录意见 → 发布不可变摘要与任务版本绑定 → 各实现运行相同 fixture → 集成 L0 → 测试账号 L1。TS-001 可审查提交不自动将主任务板改 done。

## TS-041 / L0 前置缺口

- TS-040 完整原生协议保真样例及客户端政策字段最终对齐；本包不宣称 Responses/Anthropic/embedding 完整接口。
- 网关实际凭据解析后端、TTL/撤销传播、请求诊断持久化、流中断与取消核对；无真实提供商验证。
- companion 持久收件/outbox、collector 排队预约、双轮与未知发送核对实现；NoneBot 能力盘点及真实回执语义。
- memory/platform 来源引用与账号关联证明的实际签发、解析、有效期和撤销测试；更正确认、私密来源过滤和墓碑索引检查。
- platform 快照/SSE 原子边界、分页授权、过期缓存处理；Chat Audit 真实归档/读取接口盘点。

本地 schema/关系/轨迹检查、产品替身 L0、真实渠道 L1、生产验收四栏必须分别记录。这里仅交付第一栏。

## candidate.2 协调审查修正

candidate.1 未发布，已被本候选替代，不承诺两候选可同时互通。candidate.2 拆分 recall_source 的 raw_message/shareable_projection，来源上下文补 verified_channel 并允许尚无逻辑会话；区分模型入站与上游形状，新增合法缺省及失败样例；记忆修订结果补 semantic_state；澄清本人精确 scope、延迟封存与 turn_changed 只读刷新。正式发布及双方实现确认仍待协调者审查。
