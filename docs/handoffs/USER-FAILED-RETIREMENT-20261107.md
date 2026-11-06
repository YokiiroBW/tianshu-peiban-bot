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

## 协调者最终发布与执行回执

最终产品f9a98a8a45792f782ba85badac1259ebb1f0c777，镜像sha256:310a8b825c44a4b143fce629a75038d6ee9991e8434e98b761266642f7d72523，容器abd5ce916e14e3439cacb7874ebff4c7ec773465c1552d3233ab9c531af74544。北京时间2026-11-07 03:11:51六角色ready、HTTP200、0重启/无OOM，容量与巡检active，配置SHA/11挂载/原代际/其他2容器保持。初版0c4f9abf新增唯一user_work_retirement，停写迁移前后35张既有表数量、原Action和75条unknown相同；Runtime受影响域备份保留。

原清单预览保存后，逐对象领域端口实际退役Runtime968/1182、Memory450/452、Growth14/17，归档Operations68/79，共1500，保护跳过230。Runtime194关联unknown Action、20有RBI执行证明；Memory2原模型未决；Growth2原模型未决和1非已知终态失败；Operations6未知问题、5CAS变化。清单外当前quota_group_not_configured与新告警保留，未扩清单。

6861经历原ID/revision/正文/来源/结果摘要、全部1182原生任务和452Memory原行保持；14退役Growth原行摘要保持。原74条及执行时75条Models unknown、3条Action unknown、604个execution facts/1份repair/5份grant/6份repair job完整摘要不变。两条未退役waiting Growth行由正常维护自然更新。原失败/原预算/unknown不改，不重放或发QQ/模型测试。

上线后真实RuntimeDiagnostics.tasks初版计数因逐行work_is_retired在约15.7万任务上超时，已由根f9a98a8a改同语义集合anti-join（现场0.091秒、可见failed214），最终实际默认列表、退役job精确失败历史和proof job可见性均通过；IncidentStore默认18/含归档86且默认无归档项，Memory原failed452中450已退役、Growth原未完成17中14已退役。原2323条经历整理失败仍如实保留，不能当业务恢复。24acfc60仅补Memory退休状态可读文案，同一最终发布完成。

原60个不同受影响PG用例有效结果复用，根补修AST/diff、不可变镜像构建及实际只读查询/端口通过；没有重跑全套，另一分支679bbc45的新断言未运行、未合入。临时PG在0测试库/0其他连接后按精确owner与容器ID销毁，专属目录及本地临时凭据已删除。

精确结构化回执及保护校验位于协调者.runtime/failed-cleanup-20261107/execution-final.json、execution-receipt.md、preservation-verification.json；两次发布分别在.runtime/user-failed-retirement-release-20261107和.runtime/user-failed-retirement-final-20261107。本批只处理用户历史失败工作，不含聊天准入、日记对账、模型测试及DSF修复。
