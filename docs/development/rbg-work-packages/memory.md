# RBG-04：已实际读取的结构化原文不应误标媒体缺失

本卡为 RBG 结果消费链集中验证中实际失败的定点收口。用户已授权按完整方案交对应窗口修复；复用既有 Memory 聊天和干净独立工作树，GPT-6.1 Terra/max 普通速度，不新建聊天/子代理，不跑测试/构建/烟测，不操作生产或模型。

## 现场与隔离证据

首轮265个必要用例中254通过，夹具同步后只剩 `tests/core/test_episode_semantics.py::test_weather_actual_json_receipt_reaches_extraction_without_media_missing`。当前隔离样本记录位于 C:/YOKI/Codex/tianshu-peiban-bot/.runtime/rbg-execution-20261105/weather-missing-diagnostic.json：input_coverage.complete=true、missing=[]，extraction_state=completed_with_missing，唯一缺项 media_without_confirmed_analysis。模型请求只保留正常 ranked_selection 标记，不是本次缺项来源。

调用链：Weather/Sources capture(application/json)保存实际UTF-8 content及持久media副本；read_view保留正文但不把JSON当图片；materials.py 约616行却对任何original.media且无view.media添加未分析媒体。这个条件基线已存在，不是RBG新引入；现在需要让真实结果完整消费的断言成立，不能伪造analysis_completed或放宽完成断言。

## 精确范围

只改 src/tianshu/memory/material_media.py、src/tianshu/memory/materials.py，tests/memory/test_material_media.py、本卡新增 tests/memory/test_rbg_text_material.py，以及 docs/handoffs/RBG-04.md。不改Sources/Weather/Conversation/Life/Models/全局schema/配置/恢复预算，也不改天气用例的原完成断言。确有其他必需接线先报根。

1. 以 Sources 已实际读取的正文、媒体MIME及模型可见视图的一致性判断：真实文本或结构化原文已经读取时，持久media副本不等于未分析的音视频。复用已有合适公共能力；不要另造平行读取/解码系统，也不要仅针对天气硬编码。
2. 区分 text/*、JSON/XML及已由Sources支持的+json/+xml实际UTF-8原文，未解码content=None、二进制、不支持图片、音视频只带标题/字幕/说明不能免除分析缺项。结构性reply/mention/file包装JSON不冒充原文件已读；空文本与未读取须区分。
3. 保留Sources权限/版本/范围、coverage.missing、next_position与原游标分页；不能因存在一段文字就标整件完成，不能隐藏真实缺媒体/未分析状态。
4. 只修分类与必要用例，保留原语义提交、ID/回执/unknown，不批量重抽取历史episode、不直接清生产缺项标记。

必要用例：文本/JSON和结构化后缀、未解码二进制、音视频附带文本、图片缺失、传输包装、分页cursor/缺项。复用现有read_view与Materials builder定向方式；根以原天气真实Sources→ResultConsumer→Memory用例联合确认，所有原断言保留。

整块实现、静态审阅后提交，给准确SHA、归因及定向用例/未运行项。用户已授权向主协调01a1a72e-d357-7ba2-9e01-6373ebb7741e回执。其余RBG三卡代码已集成，请从根提供的新种子切分支，不在旧RBF基线修改。
