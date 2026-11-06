# 用户失败任务清理：领域退役端口交接

用户已选择只清理失败任务和旧告警，保留经历原文，并授权由既定生产写者
`01a1ac6f-8494-7f82-8c4d-ec7d285f44ae` 串行执行。本文是代码和清单交接，尚非生产执行回执。

## 实现

基线 `4552ba8d`（产品 `c277d7ea`），分支 `codex/user-failed-retirement-20261107`。
复用各域的原对象和维护入口，仅追加当前 data_generation 下的管理退役记录。
原生任务状态、事件、job_keys、RBI execution facts 和所有领域原状态、请求、回执、attempt、预算均不改写。
Memory 经历正文、整理覆盖、Sources 和 Growth 输入不删除，也不伪标为已完成。

- Runtime 只允许原失败任务，校验原 attempts 和 args digest；有 RBI execution facts 的任务保留。
- Operations 关联原任务的 Action，未知外部效果保留；各领域自行校验退役资格。
- Memory 校验原 manifest digest / updated_at、代际和 Models 原账本；未知、缺失、活动批次和未解决远端等待保留。
- Growth 校验原 revision、代际、完整 Models 尝试历史和活动位；仅退役已知终态的 blocked 批次。
- Operations 按原 incident episode / revision / occurrences / last_seen 归档，活动恢复、诊断和未知发送保留。
  归档不等于 resolved。重复事件和归档前延迟日志不重开；新事件开启下一 episode，重新获得正常诊断机会。
- 默认任务、Growth 重试和告警列表及维护扫描排除已退役对象；精确历史查询保留真实失败状态和退役记录。
  Memory 展示整理工作已退役，原 extraction_state 和原失败事实仍在。

新增命名增量迁移 `user_work_retirement`，已注册 product / execution 两条迁移路径。
不改变原表或已有数据。维护 CLI `tianshu.operations.failed_work_cleanup` 从 stdin 读取显式清单，默认只预览，
`--apply` 才逐对象经领域端口提交；整个清单的结构先校验，每项在事务中按原 CAS 复核。
CLI 不构造 Models 或 Channels 客户端，不重试业务。

## 精确候选清单

仅在协调者本地：
`C:/YOKI/Codex/tianshu-peiban-bot/.runtime/failed-cleanup-20261107/cleanup-plan.json`。

- source inventory SHA256 `9a7ab3609f19207f1fa7843d0460d03672bd7565a86e6e2a0d0ba9da6de42231`
- plan SHA256 `c6422250717459216090ee29563aff2cd3ea60940bc1a16500fb8e65d8168e49`
- data_generation `0833be12-8fba-4db7-9e22-2120e8ea13ea`
- 原候选 Runtime 1182、Memory 452、Growth 17、Operations 79。
  当前仍存在的 quota_group_not_configured 告警已在清单外保留，未知和活动对象继续由端口逐条跳过。
  数量是候选数，实际执行必须以 preview / apply 回执为准，不能把跳过项当清理完成。

执行前核对不可变代码、清单 digest 与实际代际。由唯一生产写者按现有保留配置、数据和挂载的发布流程上线；
把该清单以原 UTF-8 字节交给已部署 CLI：

```text
python -m tianshu.operations.failed_work_cleanup --settings <实际配置路径> < cleanup-plan.json
python -m tianshu.operations.failed_work_cleanup --settings <实际配置路径> --apply < cleanup-plan.json
```

示例重定向语法供容器 Linux shell 使用。每步保存完整结构化回执。CAS 改变、未知、正在运行或 proof 保护对象不强制清理；
若需要刷新已变化的旧候选，保留原清单，针对同一原 ID 获取新的只读 CAS 清单，再经同一领域端口处理。
不得趁此扩大到新数据、重置代际、重新提交旧材料或替换失败账本。

## 验证

隔离 pgvector PostgreSQL 上共 **60 个不同的有效受影响用例**：

- 先运行新增 11 例 + 既有 Operations 及 Growth/Memory 恢复相关 40 例，51 passed。
- 扩充本批保护场景后，新文件 20 passed；既有 40 个结果复用，故不同用例合计 60。
- 新增保护涉及预览无写入、幂等、原请求/失败次数/经历正文保留、RBI proof、未知/缺失 Models 回执、
  Memory 活动位与本地未知标记、Growth 活动/等待/原批预算、归档后列表/通知/诊断、旧日志与新事件、
  代际隔离、整个清单预校验。changed Python AST 和 git diff --check 通过。

测试使用 Windows SelectorEventLoop 包装；首次运行的 Windows Proactor 错误及夹具字段修正不属于产品成功用例。
产品未调用真实模型或 QQ。本窗口没有生产读写、构建、部署、推送或迁移。
临时测试实例和凭据由其 owner 生产协调者回收，本地 XML 记录位于上述 .runtime 目录。

生产完成后请回收实际退役/归档/跳过数量和原因，并核对原快照中 Episodes、Models unknown 和 Action unknown 的摘要。
自然增长的数据单独核对，不能要求总量静止。本批不修复聊天准入、日记材料续接、模型测试或商汤协议问题。
