# 文字对话合同 v1 · TS-001 候选

版本：`1.0.0-candidate.1`；wire `schema_version: 1`。状态：**可供生产者/消费者审查；未发布；无产品联合验收**。协调者审查后才能绑定到实现任务。JSON Schema 的 `.invalid` ID 仅用于本地注册解析，不进行网络取 schema。

这是文字链路的边界字段和验证资料，不提供业务服务、调度器、数据库或通用 RPC 框架。媒体只保留引用占位；不实现 I07–I13、I16 或完整 I17。现有 AssetLibrary、Chat Audit 内部协议不在此重新定义。

## 入口

- [操作与语义](semantics.md)：身份来源、消息键、双轮、下行、记忆、模型、恢复。
- [版本与责任矩阵](compatibility.md)：生产者、消费者、验收及未确认范围。
- `schemas/`：common、conversation、identity-memory、web、model 五份 Draft 2020-12 schema；每个 `$defs` 是可单独引用的 wire 类型。
- `examples/`：具名正向文档、边界响应、负向变体、跨边界联合样例与状态轨迹；全部为合成数据。
- `manifest.json`：候选版本、本版本目录内文件摘要与生产者/消费者状态。摘要按 UTF-8 文本规范为 LF 后计算 SHA-256，避免 Windows/Git 换行转换使同一合同误报变更。其他版本/能力、根入口及共用验证工具不纳入本版本摘要，其来源由 Git 提交固定。
- [TS-001 决定](../../../docs/development/decisions/TS-001-text-dialogue.md)。

## 执行验证

在任务 worktree 根目录，Python 3.12+：

```powershell
uv pip install --python <python-executable> --target contracts/.deps -r contracts/requirements-validation.txt
& <python-executable> contracts/validate.py
```

有正常 Python 环境也可用 `python -m pip install -r contracts/requirements-validation.txt` 后 `python contracts/validate.py`。`.deps` 是可选的本地隔离依赖，不入库。验证器拒绝外网引用、schema/实例不匹配、漏覆盖的 wire 类型、文件哈希偏移、跨消息/身份/范围/顺序约束违例；负例必须按指定层及指定原因失败，不能用任意异常充当通过。

此命令只验证合同与合成轨迹，**不代表 L0 产品进程互通**，也不证明真实渠道、身份凭据、模型上游、持久化事务或隐私执行正确。下游需将同一实例接到各自实际端点/模块验证，登记结果后再运行 L0/L1。
