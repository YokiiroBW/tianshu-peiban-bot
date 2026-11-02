# NR-06 日记闭日 scope 对账收口

- 基线：`dbf8b268d5b7a152468faaf43061865ead410846`。
- 分支：`codex/diary-scope-closeout`。
- 状态：隔离本地实现已完成，测试交根集中执行；未操作生产、真实模型或 QQ。

## 问题与实现

旧手动绑定及 `reconcile_saved` 使用作者全部 scope 的哈希寻找闭日。实机作者上下文为 `[A,B,C]`，原闭日为 `[A,C]`，成功日记材料仅属 `[C]`，因此原成功正文不能清除自动日程的旧错误。作者与日程的 principal、actor、person/conversation/delivery 相同，origin 分别 human/timer，audience 跟随各自 scope，属于正常授权子集差异。

复用 `DiaryAutomation` 增加同一闭日匹配归属：只查同角色、同日、同 principal/目标场景，要求原 scope 与 audience 在作者授权内，精确 scope 优先；否则只能接受一个较窄候选。查询最多锁两行，多个较窄候选明确报 `diary_day_scope_ambiguous`，不任取、不新建日子、不占其他 generation。

新手动生成绑定后使用闭日原上下文，经既有 `_reader` 刷新权限及持久化校验，再用原时区采集。这避免把作者额外范围的经历写进原自动日程。自动请求、未闭日手动请求与原 active CAS 语义保持。

已保存稿继续要求原成功 model receipt、相同 generation/保存版本、有效来源及材料版本。匹配时还要求已保存正文范围在闭日原范围内，配方日期与时区一致；全部材料通过原闭日 reader 的 `episodes_by_refs` 复核，且仍是当天真实经历、覆盖 day 的 material revisions 后，才复用原 bind/settle 修复派生自动状态。正文、原 recipe、请求、生成配方审计、模型回执均不改写，不运行模型重生成；材料改变仍仅排入既有日记 review。

## 修改与验证

只修改 `src/tianshu/life/diaries.py`、`diary_automation.py`、`tests/life/test_diary_automation.py` 和本交接；无 DDL、无新依赖、无 GenerationControl 变更。

新增用例复现作者三 scope / 闭日两 scope / 正文一 scope、human/timer 与 audience 子集，并核对 epoch 刷新后旧正文/配方/请求/审计/模型结果逐项不变；覆盖宽作者新手动生成保持原采集范围、保留他人 active、候选歧义及精确候选优先。13 个拒绝参数覆盖 principal、目标场景、scope、audience、actor、日期、时区、他人 active、材料改版/未覆盖/跨日、来源失效和其他调用者。原“宽作者永不绑定”断言已改为“唯一既有闭日才可绑定，未闭日不创造日程”。测试 Memory 端口沿用真实接口的 actor/scope 过滤，避免假端口掩盖范围错误。

已静态审查完整 diff 与 bind 调用方，`git diff --check` 通过。遵照本轮授权，代理未运行测试、build、typecheck 或真实模型。根只需对集成后的 `tests/life/test_diary_automation.py` 执行已有定向验证，再按唯一生产写者流程发布，以新 client_request_id 调用原 reconcile 入口；旧已成功返回的无操作 command receipt 保留。

## 待根确认

集中验证通过后，实机应在原已保存正文和成功回执不变的情况下清除唯一匹配闭日的 `last_error`，将 `last_reviewed_at` 对齐原保存时间；不重生成日记，不扩大到其他日、角色或来源。

## 根验收与发布结果（2026-11-03）

已集成为 `48fa2657dbc6b9ef5030a511e168ef3c81d337e6`，根在独立 PostgreSQL 对上述日记自动化文件验证：29 passed in 12.54s。最终串行发布并通过原 reconcile 入口实机对账，旧错误已清除，last_reviewed_at 为原保存时刻；日记整行、正文版本、原生成请求和模型回执哈希保持，未重生成。服务/两 QQ/容量守护正常，配置和 11 个挂载保持，无 DDL，测试库已退役。版本、第一次实机失败及最终证据集中在 [NR-06](../development/nr06-diary-and-computation-retry-2026-11-03.md#接管与日记状态收口2026-11-03)。
