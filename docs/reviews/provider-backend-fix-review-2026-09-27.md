# 模型供应商自助配置后端固定修复复核

审查日期：2026-09-27

## 固定基线与方法

| 范围 | 固定提交 |
| --- | --- |
| 根合同与联合测试 | `578908b202e79fcf4332ff28e873241d9a9946a4` |
| 平台 P1 | `5ead3759a44744a15f50f095870c4e34f40eb7b5` |
| 网关 G1 | `f93b08a6ce2d31774d2f1b27dc00fd4d5f3ee26d` |
| 陪伴 C1 | `74dfdaefbd8c38a77d52ebbfb69a942d5c2b2963` |
| 前端 U1（有限 diff 审查） | `3072ec610a3b5af7e1eeeb19a56504018f757c23`，相对 `557084c` |

先核对固定提交和源代码，再把提交导出到独立 scratch 目录中复跑测试；没有改作者工作树。测试仅使用本地隔离夹具、录制 TLS 上游和独立临时数据库。本轮未访问 NAS、真实供应商 API 或生产数据；未操作生产配置。

## 原发现的修复状态

1. **C1 admission 时固定模型版本：已修复。** `source_sync.py` 在接收事务前调用 selector，并将 reservation 写入 collection；turn seal 使用已固定的 `turn_id/config_version/model_selection`。新测试覆盖消息入队后再切换默认的情形，已入队 turn 仍使用 admission 时的版本。
2. **P1 管理请求排队后 authority/lease 复核：已修复。** provider worker 的读写路径重新执行 `_gate()`；`WebConsole` 在 provider route 成功或失败返回前复查 session/解锁状态。新增联合测试覆盖读取排队期间锁定和 principal 撤销。
3. **P1 paid test 结果与幂等回执的原子性：已修复。** provider 测试结果和 settled attempt receipt 在同一事务提交。故障注入测试令 receipt 更新失败，确认 verdict 与回执一起回滚；移除触发器后重试成功，重启 replay 返回同一回执。合同明确提交前崩溃可产生 `result_unknown`，不重发可能已付费的请求。
4. **G1 NAT64 目标分类：已修复，部署配置仍有条件。** 默认 well-known `64:ff9b::/96` 和显式配置的 RFC 6052 前缀会提取嵌入的 IPv4 地址，并按原 IPv4 policy 再检查。支持的前缀长度有校验。没有核验目标部署实际使用的企业 NAT64 前缀；自定义前缀需配置，或由出站网络策略拦截。

## 新发现：C1 动态 source-sync 去重与 collection 追加会被 selector 故障阻断

**结论：后端集成合并阻断项。**

位置：C1 `src/tianshu_companion/source_sync.py:583-599`，固定提交 `74dfdaefbd8c38a77d52ebbfb69a942d5c2b2963`。

新逻辑把 `prior is None` 当作需要创建新 selection reservation 的条件。`prior` 来自 command/idempotency 历史；它不能说明 actor 是否已有已接收的物理消息或正在收集的 collection。物理消息 actor 去重及 collecting collection 复用只在后续 accepting 事务中由 `accept_actor()` 检查，见 `source_sync.py:325-361`。因此，已有消息使用新的幂等键重试，或同一个 collection 接收第二条消息时，都会先再次请求 selector。若 selector 此时不可用，本可从本地历史应答或复用 pinned selection 的操作会返回 `dependency_unavailable`。

独立复现使用现有 `SourceHarness` 与动态 `Selector`，将 `silence_ms` 设为 60000；首条消息成功后令 selector 失败，再用同一物理消息的新幂等键重试，并向同一 collection 追加另一条物理消息。结果：

```text
first: accepted selector_calls: 1
duplicate_error: dependency_unavailable
append_error: dependency_unavailable
collection_messages: 1 selector_calls: 3
```

预期是 duplicate 返回已有 duplicate receipt，第二条物理消息追加到现有 collection 并沿用其 selection；两者都不应要求新的模型选择。真正的新 turn 仍应在 collection seal 后选择当前默认版本。并发检查还应覆盖同物理消息/同 collection 的竞争：慢 selector 不应持有数据库事务；一方提交后另一方应重新校验并收敛为 duplicate 或 append，共享同一个 pinned selection，不产生重复 inbox、collection 或 turn。Selector 已在途时多一次后被丢弃的选择调用本身可以接受；测试需锁定持久化结果与故障响应边界。

现有 `tests/test_model_selection.py` 覆盖 admission pin、selector 失败/取消/revocation、retraction 和重启，但没有动态 selector 下的 source-sync 去重或 collection 追加用例。原 source-sync actor dedup 测试使用静态 selector 路径。最小复现和测试断言已直接发送给 C1 作者线程，请其在修复中加入串行回归及受控并发覆盖。

## 独立复跑结果

所有命令均在固定提交的独立导出和 scratch 运行环境中执行；下面是本次独立复跑结果。

| 范围 | 结果 |
| --- | --- |
| 根联合测试 `python -m unittest discover -s tests/provider_self_service -v` | 6 passed |
| P1 provider catalog | 20 passed，包含事务故障回滚 |
| P1 `test_web_models.py` | 26 passed |
| G1 provider adapter | 18 passed，包含 NAT64 |
| G1 `test_gateway_http.py` | 28 passed |
| C1 `test_model_selection.py` | 11 passed |
| C1 完整 unittest discovery | 271 passed，7 skipped，55.443 秒 |

C1 交接记录提到 `600 passed / 8 skipped / 101 subtests`；本次独立复跑使用 `unittest discover -s tests -v`，结果为 271/7。两组数字可能来自不同 runner 或 subtest 计数口径，本报告只记录可复现的本次命令结果，不据此判定缺陷。

## 验收边界

- 根联合测试实际启动平台 aiohttp 路由、网关路由和本地录制 TLS 上游，并通过 `Harness` 调用 Companion Core。Companion 侧使用测试 `LocalServiceClient` 和手工构造的 selector，没有覆盖生产 `build_runtime` 中 `JsonService` 的 HTTPS 与 CA 装配；没有部署环境验收结论。
- P1/G1 固定后端变更及根联合样例通过上述隔离复跑。C1 新发现仍阻断后端集成，既有通过结果不覆盖动态 source-sync 去重/追加场景。
- U1 `3072ec...` 相对 `557084c` 的有限 diff 审查未发现 blocker：`unknown` 状态明确显示结果未知和人工核对提示；provider 管理 authority 丢失或 reload 失败时清除过期 URL 状态。交接报告的浏览器 7/7 是执行者提供的结果，本次未复跑前端测试。`result_unknown` 浏览器夹具仅证明前端映射，不证明后端进程在原子提交边界崩溃后的完整行为；也没有完整 Companion 浏览器回复链证据。
- provider 上游 URL 按 HTTPS 执行。合同允许内部平台与网关在隔离网络或 TLS 下使用 HTTP；浏览器访问 `http://NAS:18446` 不代表支持 HTTP 上游。内部 service credential/key 的明文传输仍取决于部署隔离或 TLS 配置，本次未将其认定为固定代码缺陷。

## 合并结论

P1 两项、C1 admission pin 和 G1 已修复的代码问题在固定提交上通过了本次定向复核与隔离测试；U1 有限 diff 通过，但前端测试仅采信交接记录。当前后端不建议合并：C1 selector 预选使动态 source-sync 的已存在 duplicate 和同 collection append 依赖外部 selector 可用性，作者修复并补上串行及受控并发回归后，应只针对这些场景和真正新 turn 重新复核。企业 NAT64 前缀配置、内部服务 TLS/网络隔离及生产运行时装配是部署验收条件，和当前代码 blocker 分开跟踪。
