# RBG-03 Capabilities：真实视频证据与解析恢复

先读../rbg-repair-2026-11-05.md和AGENTS最新覆盖。既有Capabilities聊天、独立工作树、GPT-6.1 Terra/max普通速度；不新建聊天/子代理，不生产操作，不运行测试/构建/烟测。

白名单：src/tianshu/capabilities/perception/、action_checkpoint.py、actions.py、execution.py及现有resume必要恢复模块；新增capabilities/rbg_video_*.sql；tests/capabilities/test_rbg_video*.py及原视频定向用例；docs/handoffs/RBG-03.md。不改其他能力、其他领域、module.py/已有schema.sql/全局迁移或共享types。

完整实现：

1. 明确统一时间精度，处理精度内边界舍入；非有限、反向、空、实质越界仍拒绝。现场[0.0689375,0.1611875]和[0.068937,0.161188]差0.5微秒不应整次失败；规范化仍受真实来源范围约束。
2. 程序为实际frame/audio/native输入生成稳定证据ID及时间/模态清单；模型引用ID描述观察，程序绑定机械字段。字幕、推断、未知分开，原生采样未知保持。acoustic/sound仅在音频存在时映射；video标签不能让静态帧获得连续运动证据。不能只改标签就宣称正文正确。
3. VideoObservation/Actions.resume接通parse恢复，复用原成功Models结果/冻结输入/检查点，禁止重新下载和分析已完成段。旧失败attempt/receipt保留，新解析有版本/原始关联/CAS，来源或检查点变化不能覆盖。
4. 真正语义错误才在原恢复预算有限纠正，使用独立可追溯attempt、仅失败段、原证据约束。需要公共接口尽早报根，不能未接线占位；不可恢复时明确缺失/失败，不以无校验文本假成功。
5. 旧记录恢复支持只读分类与执行条件，旧检查点可读，每次只恢复失败阶段，不重做消费/发送。

现场A action=9758dc58-018f-5cb9-82c3-b9dab9ee1f68，model=292729fd-ef4d-41cf-9e1e-5fc2a197720e；B action=a92eb70a-2ab5-5e32-920d-9faa9045190b，model=23b0df24-5aca-4eae-b598-0eebf7ed5a7b。两次model succeeded、parse failed/effect none。测试使用最小结构等价合成夹具，不复制真实正文。

必要用例代码：半微秒、实质越界/NaN/反向、缺音轨、frame与native、证据ID不存在/越权、字幕/inference/unknown、不合法正文不假成功、旧成功模型零新调用恢复、解析提交中断/重复恢复/来源变更、有限纠正预算及迟到结果。

完整提交并交接SHA/恢复入口/SQL/用例及未执行项。用户已授权向主协调01a1a72e-d357-7ba2-9e01-6373ebb7741e回执。
