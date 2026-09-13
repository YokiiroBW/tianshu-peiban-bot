# 画像查询实现合同发布

2026-09-14。发布独立profile-memory/v1 1.0.0，不修改文字合同1.0.0。

已审查TS-031候选语义、subject/共享范围/epoch、机读schema与样例。发布版本增加unit.category用于显式核验interest/style/topic与request.selection，禁止public_preference承载style或group主题；与现有服务内分类相符。补3个结构负例，样例预算按加入category后的实际canonical UTF-8装配块重算。

验证器不依赖可变的Memory产品代码，仅加载本包和已钉住的文字合同；验证包/依赖摘要、15个结构样例及7个跨字段正反检查，全部通过。关系检查覆盖关联、subject、selection、整组覆盖、预算上限与真实字节计数。它不是运行授权或L0/L1证据，实际服务/迁移/HTTP验证由TS-031继续完成。

消费者按version_domain隔离新旧scope_version；群外目标拒绝，未知/仅私密内容保持空结果且不暴露私人存在性。共享可依据明确的持久策略，不要求逐条弹窗；模型不能自己授予共享。来源只给Memory投影，不输出原文血缘。
