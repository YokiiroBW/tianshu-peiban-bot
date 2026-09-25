# NAS-A1 r2k 与协调主线只读合入兼容审查

## 范围与结论

只读比较协调主线 `826b89853da59603073785eeeb27300769ec793d` 与 A1 最终提交 `16e698d822081f5fc4f6a98a27fb11c57715fe40`，共同基线为 `2e04eff5d0bf1283a321d9042d9b62f94a54dabb`。根 `main` 工作树有其他未提交资料，A4 未写入、合并或清理。审阅协调者独立集成检出 `C:/Users/Administrator/.codex/worktrees/nas-a1-integration-20260926` 中的暂存合并结果。

**结论：存在一处已处理的文本冲突；公共代码自动合并后的两项关键边界均保留。** r2k NAS 通过证据绑定 A1 冻结代码树，不自动证明合并后的代码树；协调者在独立检出中的定向联合验证是合入门槛。

## 重叠与解决

两侧相对共同基线分别修改 65 与 55 个文件，交集只有三项：

| 文件 | 共同修改 | 暂存合并检查 |
| --- | --- | --- |
| `deploy/tianshu/bundle.py` | 主线允许 A3 隔离验收项目使用资源配置；A1 将固定回环 API 端口写入部署元数据 | 两处位于不同函数段；暂存结果保留两者 |
| `deploy/tianshu/configuration.py` | 主线增加 `tianshu-accept-a3-` 资源配置命名空间；A1 增加仅限 `tianshu-qa-a1-`、回环地址、固定端口和 QA profile 的 A1 输入校验 | 暂存结果保留 A3 命名空间、A1 更严格专用约束、输入字段形状排除及端口调用 |
| `tests/deployment/packaging/test_resource_profile.py` | 双方都在 `ProfilePackagingTests` 类开头插入测试 | 唯一文本冲突由协调者在独立集成检出中处理；AST 统计暂存结果 22 个 `test_*` 方法，等于主线 16 与 A1 19 的并集，无遗漏或多出方法 |

对最终 A1 提交执行旧式只读 `git merge-tree`，仅资源配置测试出现冲突标记；在协调者暂存结果中无未解决路径或冲突标记，`git diff --cached --check` 无空白错误。`deploy/tianshu/compose.py` 的 A1 端口映射随同配置验证保留。主线另改 `synthetic_init.py`、`runtime_identity.py`、`linux_runtime.py` 等 A1 运行路径，但主要是 A3 验收命名范围扩大或新入口；需要在合并树上联合验证，不能从文本无冲突推定运行兼容。

## 最低必要协调验证

协调者已在独立集成检出执行定向检查。至少覆盖资源配置与打包测试（同时触及 A3 命名和 A1 固定端口），并覆盖 A1 准备、来源流程、单次驱动及其直接使用的恢复路径一次。稳定后核对完整暂存 diff、A1 公开证据 JSON、A4 独立报告是否一并进入协调历史。输入未变化的 r2k NAS 成功检查无需重跑；新的真实 NAS 尝试不在本次合入验证范围。

A1 `NAS-A1.md` 引用了 A4 `NAS-A1-R2K-RUNTIME-INDEPENDENT-REVIEW.md`；该文件及其静态前置报告由 A4 提交 `ef9acc9`、`d4eab0d` 提供，与 A1 分支无文件重叠。协调合入时需纳入这两份报告，避免引用悬空。

## 交接

A4 仅做只读分支比较、旧式 `merge-tree`、集成暂存树内容审阅及测试方法集合检查；未修改协调 `main`、独立集成检出或 A1 分支。合入操作和联合测试结果由协调者记录。
