# TS-002 来源同步候选 candidate.1

状态：**仅候选，未发布，未实现，完整 L0 仍 blocked**。本包收敛已受理文字来源到 Memory 的最小接线，不建立通用权限框架。只有协调者可审查后发布到根 `contracts/`。

固定读取 Core `812019e`、Memory `69b29f3`、Platform `a5ee59f`、Chat Audit `e6d8e21` 的 Git 对象；不读取 TS-021 可变目录。具体事实、缺口及 blob 哈希见 [证据](evidence.json) 和 [产品分工](ports-and-followups.md)。

协调更新后仅增量核对已集成 Core `17eba4f` 的 `context.py/core.py`：画像及跨作者继承检查已经消费；来源 receipt 基线分析不变。此新基线不再列“Core 尚未消费画像”为待办。

## 决策

- Platform 认证入口、简单主体、路由及当前账号/渠道映射；Core 拥有实际收件 receipt、完整输入、当前修订、撤回、轮次与投递事实。
- Memory 先独立完成 identity resolve/register；随后按来源快照同步自有 ledger，在自有 SQLite 事务里失效完整语义组、候选、投影和版本。身份通路不调用 SourceAuthority。
- 采用有界的 **Core 快照 → Platform 当前授权 → Core 水位复核 → Memory 原子应用**。使用完整指定集合的快照，不首建增量 feed、分页游标或消息总线。读取没有可证明的屏障就失败关闭，零预算也一样。
- 新增三个候选 HTTP 读取：Core 来源事实、Platform 来源授权、Memory 后台范围检查。Memory 同步/确认/候选提交用受信内部应用服务；第一阶段候选工作者和确认适配器与 Memory 同进程，不新增 HTTP 写端点。
- 保留两个已发布包全部字节及 wire 形状。新事实放此独立 `source-sync/candidate.1` 包，只有新 HTTP 请求/响应携带 `candidate_version`；不把附加字段塞进旧 `common.source`、committed_event 或画像响应。
- 撤回、权限撤销、Memory 遗忘分别保存，不覆盖成一个可被远端快照置回 active 的标记。私密变化不推动无活动投影的公开画像 epoch。
- Audit `archive_observed/not_checked` 仅归档观察。当前没有 Core receipt/revision 到 Audit locator 的可信联合绑定，本候选仅接通 `pending/locator=null`，不自造 archived 成功。

## 阅读与运行

[语义和时序](semantics.md) 定义行为；[schema](schema.json) 定义字段；[合成文件](examples.json) 和 [反例变异](relations.json) 与 `tests/contracts/source-sync/test_source_sync.py` 一起验证关系。测试是离线参考模型，**不是产品实现、真实 HTTPS 或授权证明**。

使用现有 `contracts/requirements-validation.txt` 的依赖环境：

```powershell
python -B -m unittest discover -s tests/contracts/source-sync -p 'test_*.py' -v
python -B tests/contracts/source-sync/verify_pins.py --workspace C:/YOKI/Codex/tianshu-peiban-bot
```

本任务 Windows 实际解释器和依赖只读位置见 [handoff](../../../handoffs/TS-002.md)。测试所需临时 SQLite 文件只在 `tests/contracts/source-sync/.runtime/`，测试后关闭并清理自己的临时文件。

验收止于 schema、合成关系、SQLite 故障/恢复参考轨迹和固定源码证据。随后必须分别实现和验证三个产品，再按 TS-050 的真实联合清单重新验收；不能用本包绿灯解除 L0。
