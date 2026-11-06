# RBJ-08 推送、保留数据部署与原结果恢复

用户已明确授权“推送部署吧”；唯一协调/生产写者为01a1ac6f-8494-7f82-8c4d-ec7d285f44ae。

## 发布输入

- 分支codex/rbj-integration-20261106；已验证产品d8021aeb017776a53ec2e83e7b18d3e164c8f650、公共合同1.9，279个不同受影响PG用例通过。后续仅文档差异，构建输入逐项核对。
- 发布前线上44590b88b4d5db4c8ea0288751bc35cb40a70348、镜像fb3b0de046614e9af9a3619c1b375c0081e16bcebf0328935e06e8acc444513d，11挂载及其他两个容器已读回。
- 精确本地/远端证据保留于根.runtime/rbj-execution-20261106，不提交配置、环境、数据库或业务正文。

## 执行与回退

从已提交源码git archive及既有固定摘要基础镜像构建。推送独立集成分支，不强推或覆盖main。停写后仅备份受影响Sources/Runtime/Conversation/Life四域，校验dump目录和摘要；保留原代际、已有表记录数和原对象身份。增量迁移五项：rbj_wake、rbj_execution_repair、rbj_result_repair_evidence、rbj_lock_graph、rbj_source_proof。保持配置、11挂载、容量守护、宿主巡检和其他容器，统一切换六角色。

若切换失败，停止新容器，恢复已保存compose/installation指向的旧镜像，重新绑定容量守护并启动；保留新增schema/SQL函数与全部数据，不倒灌旧数据库。新函数保持原公开签名，旧代码可读新增字段，但回退会恢复旧旁路与重试缺陷。修复资格仅在新版本健康及新鲜原目标检查后签发；如恢复后需回退，先停止签发、核对已领取和提交事实，未知先对账，doing不强杀，未开始载荷仅经Runtime正常端口退役。

## 业务核对

部署前按当前代际有界扫描同缺陷未完成终态结果；部署后先查原head、来源、owner、CAS和既有proof，再经ResultRecovery可信登记及原事件端口恢复。保留四次普通失败，仅为已确认修复缺陷的原consumer签发一次受限repair，不invoke/resume原Action。分别记录五条原回执与新增同类目标的Life/Goal/Memory、consumer proof和aggregate；自然新增结果独立记录。403/429和外部unknown保持，不做模型/QQ测试。

当前状态：发布准备中，尚未在本卡声称已推送或部署。后续将写入实际源码、镜像、备份、迁移、健康、原对象与自然样本结果及仍有影响的限制。
