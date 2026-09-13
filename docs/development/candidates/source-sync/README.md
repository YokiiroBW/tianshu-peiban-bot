# TS-002 来源同步主候选 candidate.2

状态：**仅候选，未发布，未实现，完整 L0 仍 blocked**。本包收敛已受理文字来源到 Memory 的最小接线，不建立通用权限框架。只有协调者可审查后发布到根 `contracts/`。

正式方向以[多角色分层方案](multi-actor.md)、[candidate.2 schema](multi-actor.schema.json)和[群私联合例](multi-actor.examples.json)为准：物理消息与角色受理分层，共享会话/世界，不共用私密角色记忆。原candidate.1单actor限制只保留为旧Core迁移保护；其无actor选择器的来源读取形状从未发布，不作为最终接口。

固定读取 Core `812019e`、Memory `69b29f3`、Platform `a5ee59f`、Chat Audit `e6d8e21` 的 Git 对象；不读取 TS-021 可变目录。具体事实、缺口及 blob 哈希见 [证据](evidence.json) 和 [产品分工](ports-and-followups.md)。

协调更新后仅增量核对已集成 Core `17eba4f` 的 `context.py/core.py`：画像及跨作者继承检查已经消费；来源 receipt 基线分析不变。此新基线不再列“Core 尚未消费画像”为待办。

## 决策

- Platform 认证入口、简单主体、路由及当前账号/渠道映射；Core 拥有实际收件 receipt、完整输入、当前修订、撤回、轮次与投递事实。
- Memory 先独立完成 identity resolve/register；随后按来源快照同步自有 ledger，在自有 SQLite 事务里失效完整语义组、候选、投影和版本。身份通路不调用 SourceAuthority。
- 采用有界的 **Core 快照 → Platform 当前授权 → Core 水位复核 → Memory 原子应用**。使用完整指定集合的快照，不首建增量 feed、分页游标或消息总线。读取没有可证明的屏障就失败关闭，零预算也一样。
- 保留Core来源事实、Platform来源授权、Memory后台范围检查三个读取接点；candidate.2补Core批量角色受理操作，返回一个物理receipt及逐actor回执/拒绝结果。Memory同步/确认/候选提交仍用受信本地应用服务，不新增Memory HTTP写端点。
- 保留两个已发布包全部字节及wire形状。新分层和选择器在独立source-sync/candidate.2；common.source仍用Core实际actor receipt，物理receipt放新sidecar，不把actor字段塞进旧source、committed_event或画像响应。
- 撤回、权限撤销、Memory 遗忘分别保存，不覆盖成一个可被远端快照置回 active 的标记。私密变化不推动无活动投影的公开画像 epoch。
- 原始输入记忆只做source级失效，普通回复取消不撤回输入。correct当前只禁用旧值，新值尚不可召回；完整更正另列具体任务。
- 正式候选定义群私多actor接线；[固定Core现状](core-receipts-reproduction.json)仍不能据此宣布已支持，修复前用单actor迁移保护。完整scope coverage最多256个角色依赖，长期累积可能503；容量限制不等于角色架构限制。
- Audit `archive_observed/not_checked` 仅归档观察。当前没有 Core receipt/revision 到 Audit locator 的可信联合绑定，本候选仅接通 `pending/locator=null`，不自造 archived 成功。

## 阅读与运行

[共同语义和迁移保护](semantics.md)、[旧candidate.1 schema](schema.json)、[旧合成文件](examples.json)和[旧反例](relations.json)保留可追溯性；多角色差异与优先级以multi-actor.md为准。`test_source_sync.py`验证共有不变量/迁移保护，`test_multi_actor.py`在群与私两种场景验证新生产者/消费者关系。参考模型不是产品实现、真实HTTPS或授权证明。

使用现有 `contracts/requirements-validation.txt` 的依赖环境：

```powershell
python -B -m unittest discover -s tests/contracts/source-sync -p 'test_*.py' -v
python -B tests/contracts/source-sync/verify_pins.py --workspace C:/YOKI/Codex/tianshu-peiban-bot
```

本任务 Windows 实际解释器和依赖只读位置见 [handoff](../../../handoffs/TS-002.md)。测试所需临时 SQLite 文件只在 `tests/contracts/source-sync/.runtime/`，测试后关闭并清理自己的临时文件。

验收止于schema、合成关系、SQLite故障/双owner回退参考轨迹、固定源码证据，以及隔离固定Core的6项真实收件/存储复现（身份/记忆/模型/渠道依赖仍为其原测试替身）。随后必须分别实现和验证三个产品，再按TS-050的真实联合清单重新验收；不能用本包绿灯解除L0。
