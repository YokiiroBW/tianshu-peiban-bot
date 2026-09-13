# TS-002 candidate.1 暂缓发布

2026-09-14，候选417da9da1fd7790ae0c6733985c835ed3ab3773d。协调复核23项离线测试通过，17固定源码blob/2发布manifest通过。尚未合入或发布。

发布前需收敛：

1. 取消回复不撤回已受理输入。语义要求turn取消/输入撤回失效旧候选，但覆盖和参考模型只处理source集合。若存在独立turn输入失效，已提交组的零预算读取须覆盖owner turn并失效；否则明确只按真实来源失效，移除将普通回复取消泛化为撤回的规则。
2. correct永久抑制旧source，当前仅受理replacement并使旧组invalidated/pending，无可信新更正来源和新值可读路径。必须如实定义受理边界及具体后续能力，不能称完整更正已可召回，也不能直接清除旧来源抑制。
3. 待工作者据实际Core核对多target actor输入与单scope source_fact、稳定key是否歧义；尚非已确认缺陷，不凭猜测更改旧source wire。
4. 全scope/actor活动血缘覆盖最多256来源为首切片容量限制，超限失败会影响长期使用，文档必须明确；不在缺少证据时扩大分页/消息总线设计。

TS-002继续原候选工作树修订，完整L0保持blocked。绿灯离线测试不替代产品来源授权或边界完整性。

## e74d4d9复核

31项离线测试通过；固定Core17eba4f六项局部复现由协调者重跑通过。取消与source失效、更正受理边界、双owner水位已补齐说明/参考轨迹。复现确认actor未进入inbox/collector区分：空target跨actor重投复用首receipt、不同actor新消息可混collector、更高edit可换actor；当前已集成Core仍有此限制，不可宣称多角色接入完成。

单actor渠道准入可作为临时保护，不能冻结为用户要求的多角色最终架构。继续候选：物理来源与actor admission分层、精确selector/receipt授权、编辑撤回传播及Memory各actor来源/抑制边界。若旧wire不足，提最小兼容新版本而非静默修改；共享会话的两轮与顺序保留，共享世界不授予角色私密记忆互通。产品修复待正式合同，不在当前候选中越界写业务代码。
