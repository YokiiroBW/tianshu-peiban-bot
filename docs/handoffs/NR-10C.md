# NR-10C Life 后台输入统一 200,000

基线 `f1a783b0`，分支 `codex/nr10-life-budgets`，保留旧 NR-09 分支。依赖 NR-10A 导出的 `tianshu.models.budgets.BACKGROUND_INPUT_TOKENS = 200000`；A 已确认该接口，本树不修改共享预算文件。

## 实际装配

| 功能 | 新输入上限 | 原输出与协议预留 |
| --- | --- | --- |
| Life 日程 | 直接导入共享常量为 `INPUT_BODY_TOKENS`，完整最终 system/messages 仍须不超过 200,000 | `TASK` / `ROLLING_SCHEDULE` 的实测协议成本先预留；现有实际 profile 的输出（16,384 或 32,768 等）另加，不从 200,000 扣除 |
| 写作 | `max_input_tokens = BACKGROUND_INPUT_TOKENS + 6000`，净输入从旧 26,000 提升至 200,000 | 原 6,000 输出预留与 `max_output_tokens=6000` 保持 |
| 日记 | 原 200,000 保持，调用点改为直接引用共享常量 | 继续 `input + actual output` 装配，保留整段语义材料选择和原 source closure |

移除 `life/model_budget.py` 中无其他生产消费者的两个旧输入常量，不保留平行定义；该模块的输出、执行时间、路由/profile 参数和业务截止逻辑均未改。旧冻结 TASK 原文、请求观察、GenerationRequests、重试/原回执、来源/版本/CAS、QQ 策略保持原实现。新输入上限不会改写已冻结请求或令旧外部动作自动重放。Life 本域未发现输入上限的配置覆盖入口。

## 必要验证交接

本代理未运行测试、编译、构建、真实模型或生产操作。只执行完整差异静态审阅与 `git diff --check`；不能称用例通过。

- 新 `tests/life/test_background_input_budgets.py` 共 5 个参数化场景：生产 `o200k_base` 计数，约 190,000 tokens 日程分别配原 16,384 / 32,768 输出；超过 200,000 的整段人格拒绝；真实写作请求/冻结/保存链路保留三章完整输入，检查 `200000+6000`；超上限写作在模型前失败，原 recipe 不裁切、不产生完成证据。
- 更新 `test_diary_materials.py`：日记调用实参和最终 pack 上限均为共享输入加原输出，原整段省略、未知/迟到回执、跨日独立场景保留。
- 根建议集中验证上述文件及 `test_planner_contract.py`（旧冻结 TASK 和预算裁剪）、`test_model_budget.py`（原输出/执行时间/profile）与 `test_computation_retry.py::test_chapter_retry_uses_the_existing_writing_queue_and_saves_only_the_selected_successor`（冻结请求/回执不重写）。复用未变 NR-09 日程执行与恢复结果，无需重跑其他 Memory 或前端。

## 其他后台调用只读核对

已按 `ModelRequest` 及输入 token 装配入口核对当前 `src/tianshu`。B 负责的 Growth、participation、autonomous chat 不在本树修改。`conversation.context.assembly` 的 16,000 默认值和 `companion.ports` 的同值声明仍服务普通聊天/接口默认，后台由实际调用显式传入上限。

`capabilities/images/service.py` 的 image.plan、`images/consistency.py`、`perception/service.py` 的 vision、`perception/video.py` 的 video.observe、`reactions/service.py` 的 reaction.search、`capabilities/plugins/service.py` 的模型桥未发现另一个固定输入 token ceiling；它们使用直接 ModelRequest，没有借用 Life/ContextAssembler 的旧小输入预算。所见 1,800 / 2,500 / 8,192 等是输出限制，图片数、帧数、媒体字节与业务文本字符边界不是输入 token 上限，均未改。embed/rerank 为专用协议；Models HTTP/codec 是转发/反序列化，management 为配置探针，不另设后台生成输入预算。

此核对未发现需要根额外派发的固定输入 token ceiling 遗漏。200,000 是应用输入上限，不代表已验证实际供应商支持该输入加输出的窗口；根负责实际配置/窗口与串行发布核对，本代理无真实模型调用。
