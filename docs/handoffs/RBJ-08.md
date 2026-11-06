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

实际已推送并部署e317734fcf0f1d9551a3f7838c95e0efbcfd464d，镜像sha256:3ef6809f59f2a9aee29e86ac1ac6a2a887ce5574b16e65551b691833a1e838e5。六角色就绪、0重启，首页和四项静态资源200，容量守护与宿主watcher active。五迁移完成，29张旧表记录数、五个原Action摘要与67条模型unknown在停写/迁移前后相同；配置、11挂载、原代际0833be12-8fba-4db7-9e22-2120e8ea13ea和两个其他容器保持。

四域备份/srv/tianshu-rbj-releases-20261106/e317734fcf0f/affected-domains.dump，56275228字节，sha256:8d2617ef2d770a3f5f54378770f6e2bdc2e7c8769153fac71af012b9e30b986f。目录和TOC已验证；未倒灌数据库。

业务验收尚未通过：首目标5bca45d3...经正常端口授权grant1、派发repair145703。Operations先观察其ordinal0 native started557219并持久化unknown，worker预检被该fact挡住，持久日志error_code=execution_repair_already_started。ordinal1随后写准确unknown stop并native succeeded，但Life仍未apply新rev5、无consumer proof/aggregate，不能称完成。普通失败4、repair失败0、原grant与unknown均保持；其余四项资格暂停。发布后新增两个delivery是旧external unknown/已有aggregate缺完整consumer proof的观察投影，不能当新外部执行或强补成功。

冻结合同2.0及新卡RBJ-09/10交既有Runtime/Conversation窗口处理native-inflight和可信not_started对账；根集中必要验证后串行补发。自然新增业务样本尚未证明，不关闭原问题。


## 准入补修集中验证与补发准备

Runtime 1e16b553→908dba0d、Conversation a6a2ae0a→e3a945ac，合同2.1及新命名rbj_native_admission迁移已装配在产品和执行两条公共路径。新普通物理结果只在可信发布时持久标记，旧job/adopt/coalesce不补标；原生inflight与业务begin分开，真unknown与其它doing仍阻断；历史分页沿同native保留精确未应用出口，不刷新预算或重建外部Action。

11个受影响用例文件集中PG最终139个不同用例通过，0失败/错误/跳过；首运行135通过、4旧夹具失败均保留。根7cc1f85c只让旧历史/租约用例经公开Queue创建未标旧任务，再按真实事件发布/采用，未删除marker或放宽unknown、lease、receipt及budget断言，4个定向重跑通过；未变135不重复运行。原279例中未变有效结果继续复用，不重跑完整472旧套件。完整真实Sources/Life/Goal/Memory及新普通/repair观察交错、同native分页、not_started精确机械对账和真实unknown保护已覆盖；并非仅stub结果。

补发使用新不可变源码镜像，仅Runtime有新增增量迁移，因此该次停写仅再备份Runtime，原四域备份/源镜像/事实与原生历史保留。实际原145703:0仍待新版本上线后，由根受限脚本逐项核验不可变e317代码、同序号持久日志、原事件与来源/Action/Life CAS/租约，再公开resolve追加not_started。其原unknown/evidence/recorded_at保留，不以稍后ordinal1的退出或native succeeded推导；随后只复用grant1和原结果正常恢复。其余四条同样先核对新鲜权威与来源再签发原consumer受限资格。

独立巡检仍报告Memory新批连续403，45个关联回执均同主路由、未见可关联备用路由尝试，未调整路由/配置或制造模型测试。同期部分池commit_unknowns增加但未建立具体对象关联，不能据计数改写或重放未知。此处仅保留问题和证据，不称本批全部业务已收敛。


## 最终补发与原对象业务结果

最终应用运行8dbe3eeb386c8a982a12d078322fcc18d38d904c，镜像sha256:08a0e5215c839c0955d0ba7b5d01fd3a326bfca18c272b5a004dce2bec29e5be，容器f52e2eb251d3935075699e668923b6da62bc508d6b769b1e565041be39d5c530于2026-11-06T15:18:01Z启动。六角色就绪、0重启、HTTP/首页与四资源200；6个已安装关键Python模块SHA逐一匹配Git。原配置/11挂载/代际/其他2容器保持，迁移前后35张既有表记录数、原Action及72条Models unknown完全一致。后续原结果对账亦逐条核验72条unknown的状态、dispatch、请求与结果摘要未变。

本次Runtime备份/srv/tianshu-rbj-native-releases-20261106/8dbe3eeb386c/affected-domains.dump，18240088字节，sha256:a4d4d0a84da605a115e5808eb2efc93c3b4284fb73acd9aa54c61a562688bf54；唯一新增rbj_native_admission，使迁移总数25。原四域备份和两代回退镜像保留；无数据库倒灌、配置覆盖或原外部Action重放。

受限脚本逐项核验不可变e317代码的begin/seed互斥、原生started557219及原序号持久错误日志链、完整native事件、owner revision8、原来源/Action头、无消费租约和未apply后，公开resolve为145703:0追加not_started，原outcome=unknown/evidence_ref/recorded_at均保留，观察引用rbj-prebegin:sha256:785862683274ba2ae6b5a5fb0af62bad2a59dd7b23423f7d92c1078c09111f31。不是从ordinal1的stop、native succeeded或当前Life状态推测原来未执行；原grant1未刷新。

| 原receipt | 本次原结果job | 资格 | 逐对象验收 |
|---|---:|---:|---|
| 5bca45d3-6f88-4195-93cc-5d74fb6efaaa | 149133 | 1 | Life done，Memory revision5，六消费者aggregate |
| 58375a65-0bff-401d-ad44-2d74486b81ba | 149210 | 3 | Life done，Memory revision2，六消费者aggregate |
| 3efab48c-967e-44fd-807c-467f7aab083a | 149231 | 4 | Life done，Memory revision2，六消费者aggregate |
| 91b545df-9529-4d50-a586-54bed5ab46a4 | 149263 | 5 | Life done，Memory revision2，六消费者aggregate |
| 8e8c7b7f-0a44-48fc-85fa-d21f2547d8dc | 149285 | 6 | Life done，Memory revision2，六消费者aggregate |

五条均保持原owner/Action/receipt/revision proof及原Action/attempt/receipt摘要；其他5消费者各为not_applicable。五grant均真实completed关闭，每目标原normal失败4/repair失败0；20条旧native失败的status/attempt/due/event摘要原样。随后通过正常ResultRecovery重复证明对账全部verified，绑定领域观察时首次close时间和机械引用保留，没有再次apply。

自然新普通结果10a7f9b5-d18f-4908-b686-b1641ea23dbc（Action dff1b198-e95f-5453-94bf-c2416a3811d4，job149316）没有repair参数，可信normal_begin_required与原生normal completed事实均已核验；Life、Memory episode revision2、6消费者aggregate在15:22:13UTC完成。它是正常领域续接产生的真实新结果，不是测试模型/QQ。Goal53234e3e-bb7f-5027-b15c-e784912eb373依据两条有效完成记录达到2/2、progress1.0；部分活动仍按领域继续或等待，视频片段continue不等于完整视频已看完。

独立巡检15:28:57–15:31:20UTC已重新读取现场并复核上述原五条、新普通结果、Goal和原unknown保存。关键限制：这6条Memory episode写入成功，但后续整理已逐条关联到6个403失败批，extraction_state=consolidation_failed，不能称Memory整理/索引已收敛。更早45批关联回执也全为同一路由submitted/failed/403，没有可关联的备用路由尝试证据；未修改主备路由。两个旧external unknown且缺完整consumer proof的delivery投影继续未知；新video b0accb48执行unknown/403具体业务后果仍未知。旧生命周期内提交unknown计数增长也未据此重放或改写对象。真实QQ端到端交付不由以上Life结果证明。

证据：.runtime/rbj-native-execution-20261106/business-final.json、health-final.json、release-status.json、validation-summary.json，及.runtime/rbj-execution-20261106/monitor-post-release-episode-trace-20261106T1528Z.json。既有半小时心跳已更新最终源码/资格/逐对象状态，仍只读、未变安静。两次临时PG及两组本批源码导出已按owner/精确ID/目录核验清理，源提交、工作树、日志、备份和镜像保留。当前收口仅指本批部署与受影响结果链，未声称全部系统业务无问题。
