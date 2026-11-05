# RBG-04：实际读取的文本媒体分类

## 范围与提交

- 工作树 `C:/Users/Administrator/.codex/worktrees/rbf-memory/tianshu-peiban-bot`，分支 `codex/rbg-memory-20261105`，基线 `006174f6c32fbb0ea66ecbd48c04188cb5d46e04`；本次提交 SHA 见本交接所在提交。
- 只修改 `src/tianshu/memory/material_media.py`、`materials.py`、`tests/memory/test_material_media.py`，新增 `tests/memory/test_rbg_text_material.py` 和本交接。不修改 Sources、天气、其他领域、迁移、全局配置或恢复预算；没有新增接口装配或数据库迁移。

## 根因与实现

集中验收由协调者报告 265 个必要用例合计 264 通过、天气原链 1 项失败。去敏诊断中 input coverage 完整，唯一 extraction 缺项为 `media_without_confirmed_analysis`。Sources.capture 对文本及受支持的结构化 MIME 保存实际 UTF-8 原文和媒体副本，Sources.read 返回正文、文本 coverage 及原 media；旧 MaterialBuilder 只看 media 存在而模型视图未包含图片，因而误把该副本当作未分析媒体。

Memory 在既有 `read_view` 和 MaterialBuilder 调用处加入分类：仅对 `text/*`、`application/json`、`application/xml`、`application/*+json`、`application/*+xml`，且 Sources 实际返回字符串正文、coverage representation 为 `text`、正文与模型可见视图一致时，不添加该媒体分析缺项。MIME 先去参数、去空白并统一大小写；空字符串是真实已读的空原文，`content=None` 仍表示未解码/未读取，不由字符串或 MIME 单独推断成功。

Sources.record_event 的事件 part 文本可与附件独立，不能因标题、字幕或文本字段推断文件原件已读；`part_kind` 或原事件的 producer/event_id/part 标识均保留媒体缺项。既有 `channel_transport_original` 和 `channel_forward` 是渠道包装，也不据其 JSON 正文声称附件已理解。其他真实 capture 的普通文件 locator 不因名称是 file 被一概排除。音视频、未解码二进制、缺失和不支持的图片沿用原缺项语义；真实 `analysis_completed` 既有分支不变。

分类只消除该项误报：Sources 的权限/版本检查、coverage.missing、已选择的 source range、`next_position` 和 episode cursor 均走原路径，不伪造模型分析回执，不重算历史 episode，不改变 unknown、CAS、来源或回执。

## 回归与集中验证

- `tests/memory/test_rbg_text_material.py`：文本、JSON/XML 及后缀 MIME/参数，空原文与未读取、可见内容不一致、非文本媒体、事件标题/包装和渠道传输包装的分类。
- `tests/memory/test_material_media.py`：MaterialBuilder 级别验证实际文本与媒体副本、保留音视频等缺项、JSON 分页和 source range/cursor、分页文件包装仍有缺项、已有 Sources missing 和权限拒绝。
- 按任务要求本窗口不运行测试、构建、烟测、生产或真实模型。集中最小复验：`python -m pytest -q tests/memory/test_rbg_text_material.py tests/memory/test_material_media.py tests/core/test_episode_semantics.py::test_weather_actual_json_receipt_reaches_extraction_without_media_missing`；原天气完成及无缺项断言保持不变。先整合本提交及现有三卡，再由协调者在隔离环境执行；只有真实 Sources→ResultConsumer→Memory 联合用例通过才可称该本地结果消费链通过，不代表生产业务已恢复。
