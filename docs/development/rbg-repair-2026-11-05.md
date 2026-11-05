# RBG：规划终态、结果消费与视频解析恢复

用户已批准完整方案并要求交给对应窗口修复。本批承接 RBF，不重做空库安装。产品基线 d9016ca7cccb02534498fd512a00a1a8d20e72e6，包含应用 79c52228 与 Sources 函数 54855325；三卡从随本计划派发的种子提交开始。

## 确认的问题

- 旧规划是本轮数据代际中的请求，04:37 UTC 创建、04:39 superseded，recovery_state=cancelled，但 watermark 仍指向它且 generation_consumed=false。submission_confirmed/admission_started 均 NULL，Models attempts/submissions 无原 ID。08:03 UTC 仍每30秒 observation_unavailable，角色 last_planned_at 停在03:36。can_replace 缺少历史终态退出当前指针的安全出口。缺回执不能推断未收费或盲重发。
- Life plan 作业18182五次超时是另一条记录，恢复须分阶段查原账本，不扩大全局执行超时。
- 视频 A 范围[0.0689375,0.1611875]与输出[0.068937,0.161188]仅差0.5微秒，且输出 acoustic；B实际 audio/image，部分标签 video。两次模型 succeeded，本地校验失败。结构证据不证明全部正文正确，不能把静态帧当连续动作。当前 resume_preflight 排除了 parse。
- 活动 step/reconcile 和前台消费有 revision_conflict。lock_material_row 在来源锁与领域锁间发现快照变化后抛错；锁顺序/CAS需保留，后台须按最新业务状态重评。ResultConsumer 多消费者执行完才写总消费标记，中途失败存在漏消费；一原回执08:03 UTC仍未消费。
- 13:29至15:27中国时间，18个可观测池无新增获取超时；不以本批扩展 Sources 性能重构。

现场去敏证据：C:/YOKI/Codex/tianshu-peiban-bot/.runtime/rbf-execution-20261105/monitor-plan-20261105T0627Z.json、monitor-followup-20261105T0527Z.json、monitor-receipts-20261105T0727Z.json、monitor-attempt-receipts-20261105T0727Z.json。本批结构样本在 .runtime/rbg-execution-20261105/problem-evidence.json。禁止复制真实正文/凭据到测试或提交。

## 边界和公共合同

- RBG-01 Life窗口：规划终态/Models准入取消与定向恢复。独占 Life规划相关精确文件、Models、conversation/generation.py，见life卡。不写life/worker.py。
- RBG-02 Conversation窗口：并发与逐消费者恢复。独占Conversation除generation.py/module.py/schema.sql外的相关文件、life/worker.py及本卡结果辅助模块。不写Life规划、Models、Capabilities。
- RBG-03 Capabilities窗口：视频协议/规范化/parse恢复与Actions接线。不改其他领域。
- 根单写product.py、migrations.py、config、types、全局依赖/部署、各module.py、已有schema.sql、life/recovery.py、共享记录与巡检。域迁移用新SQL交根注册。跨界需要准确接口/最小接线回报根，不停掉可并行部分。
- 保留原ID/body/completion身份、来源/权限、代际、CAS、回执历史和原恢复预算；未知副作用不能因本地错误重做。
- 模型准入证据由Models权威提供。Supersession与用户取消分开；解除当前指针前阻断旧owner/迟到RPC再准入。未知原结果继续对账，不无限占用已作废的业务位置。新需求须是真实当前材料，不用新ID绕过原未知重试条件。
- RBG-02可在conversation/persistence.py定义内部快照变化异常类型，对外revision_conflict语义保持。只在确认回滚的后台边界有限恢复；不能吞所有409、去掉CAS或放宽锁顺序。
- 整体result_consumption仍表示所有适用消费者完成；扩展逐消费者持久进度。业务写入与对应完成标记同事务，或用既有领域幂等回执证明完成；不能done后再执行，不持跨域大事务等待模型/渠道。旧结果不能回退当前状态。
- 视频只规范化协议精度内舍入；非有限、空/反向、实质越界拒绝。证据ID来自真实输入，模态别名只能映射真实存在的输入；缺音轨不可变音频，frame不可冒充连续video。
- parse恢复优先复用原成功模型结果，机械恢复不新增模型调用。语义错误才用原预算有限纠正、独立可追溯attempt；不把不合法输出改成无校验成功。旧检查点、失败回执和解析版本保留。

## 验证与恢复

三个窗口整块实现、补实质用例、静态审阅后提交，不运行测试/构建/烟测。根收齐后集中必要验证，未变结果不重复。覆盖NULL终态、提交/ACK丢失、换代中断/迟到RPC；重复/乱序回执、消费者中途崩溃、CAS、来源撤回/显式取消；舍入/标签/真实越界、零调用parse恢复、有限纠正与迟到结果。

部署后根通过正常领域入口恢复准确对象，先分类列出条件和不能恢复原因，不清表/改成功/批量重放。保留旧失败审计并关联业务恢复证据；已完成的旧Memory batch不再恢复。普通发布只核对版本、启动、受影响入口及原记录结果，不另加全套烟测。

巡检看规划指针合法性与实际应用推进、各消费者未完成年龄、视频解析恢复及模型调用增量；进程健康或作业succeeded不能替代业务完成。无变化保持安静。

交接docs/handoffs/RBG-01.md、RBG-02.md、RBG-03.md：SHA、接口/迁移、定向用例、未执行项与正常恢复入口。主协调者为唯一生产写者。
