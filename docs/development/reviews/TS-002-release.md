# TS-002 source-sync/v1 1.0.0 实现基线发布

2026-09-14。候选83c4fd252e459cc03e0c6bf890d9661851750ced经协调与独立只读收尾审查。已补事件分类/aggregate版本、固定issuer、逐actor映射覆盖及同事务inline admission证明。此前确认的Core跨actor收件/collector问题仍需产品任务修复，契约不等于修复完成。

发布目录contracts/source-sync/v1，manifest LF SHA256：178d0ce66210bdfad4cfb85d8b5f0905b0b67f834e2a530efe5636ff0373633d。原text/profile发布内容不变。只复制独立release-ready包，无产品/历史候选/测试模型依赖；旧提案仅留历史证据。

协调复核55项任务测试通过。独立发布包验证在准备目录及最终合同布局均通过：62正向文件、7结构反例、84关系案例，本包与两个依赖hash、全部本地schema引用通过。SQL模型仅离线参考，来源授权、产品迁移、真实多角色及完整L0均未验收。

作为实现基线发布，允许Core、Platform、Memory绑定同一哈希并行实现。Core拥有物理来源P和actor受理A，保持共享会话两槽/有序发送；Platform拥有真实输入与逐actor权限；Memory拥有A级ledger/抑制、物理否定传播、失效屏障与版本域。跨产品仅接口、不共用数据库。成功组件测试必须与真实联合验证分开。

本版限制：pending归档、correct仅禁旧值且新值尚不可读、完整coverage256上限、单消息mixed不可提炼、恢复完整性不明拒绝服务。真实模型/渠道、生产迁移/设备/原文擦除不在此次发布授权内。三个产品通过后重启TS-050联合验收，完整L0仍blocked。
