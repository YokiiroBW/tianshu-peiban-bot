# RBI-10 Sources：多上下文事件批预锁交接

## 交付与边界

- 工作树：`C:/Users/Administrator/.codex/worktrees/rbi-sources/tianshu-peiban-bot`；分支：`codex/rbi-sources-20261106`。
- 基线：`af226eeb9ebabad7ecbc1f86fc9b3f5fa6bd1171`。本交接与代码同一提交，完整提交 SHA 以协调窗口收到的完成回执为准。
- 只改 Sources 公共服务、端口及 RBI-10 用例；没有 DDL、迁移、配置或共享入口装配改动。Life、Action 和生产均未操作。

## 接口与锁序

```python
async def prepare_event_writes(
    connection, context, events, *, additional_refs=(), scoped_refs=()
) -> tuple[str | None, ...]: ...
# scoped_refs: ((original_context, (source_ref, ...)), ...)
```

旧调用和按事件顺序返回的 aliases 不变；`events=()`、空来源组和默认 `scoped_refs=()` 均可使用。每个 scoped 组保留独立的真实授权上下文：先对全部上下文按稳定的完整身份字段排序，逐一调用已有 `Identity.lock_context_on`；之后获取事件锁并调用现有 Sources 闭包锁一次，合并事件写集合、主来源依赖、旧根/别名和所有 scoped 来源。原 `additional_refs` 与事件 `support_refs` 仍由主上下文强制校验，失败仍为 `source_support_unavailable`。

闭包锁定后，各 scoped 组使用自己的上下文调用既有 `check_many_on` 预检。合法上下文的不可见结果不会阻断主事件；异常、身份失效、格式错误、证明预算不足与 `RecoveryWait` 不转换成布尔结果。scoped 预检结果不合并、不缓存到其他上下文，也不改变返回值。

Life 必须在领域锁之前提供固定候选的完整来源读集，并在同一事务后续重新用各自原上下文调用 `check_many_on` / `validate_persistence`。持锁不是授权；尤其要核对新 receipt 对 Goal 上下文的可见性，再按既有逻辑跳过或更新 Goal。Action fence、领域行锁和快照 CAS，以及 Goal 与原 receipt 的原子提交仍由 Life 负责。若后续出现预锁集合之外的新来源，不能在 Action 之后追加来源锁。

## 用例与验证状态

新增 `tests/sources/test_rbi_scoped_preparation.py`，编写了隔离 PG 权限上下文与公共/私有来源、不可见 Goal 的模拟跳过及主 receipt 提交、主依赖强制授权、空 scoped/events 兼容、身份/格式/typed wait 和证明错误、反向输入一致锁序、来源版本修改与 alias 撤回并发的用例。用例中的临时 Goal 仅验证 Sources 合同，不替代 Life 实际业务行、Action fence 与跨域同事务集成验证。

按本批集中验收约定，本窗口只做 Python AST 静态解析和完整 diff/空白检查；没有运行测试、构建、烟测、真实模型、QQ 或生产检查。请协调者在全部域收齐后运行新增 Sources 用例、受影响既有 Sources 用例，以及 Life 的真实 Goal/receipt、Action 头和跨上下文并发用例；最终状态以集中验收为准。
