# 当前执行截面（2026-10-31 R67）

已授权的剩余实现已集成部署；18卡中12卡实机验收、3卡已部署，PC-02/06/18继续。W1长期条件未满足，用户明确要求先完成剩余实现并串行发布，已覆盖下方历史W3前置限制；原真实验收条件不变。当前版本与未验证项以[发布记录](proactive-companion-repair-r67-2026-10-31.md)、CURRENT.md及机读队列为准。

以下是原始开工交接，线上版本、指标及阶段限制为当时历史截面。

# PC 修复队列 · 一页纸交接

给执行者：先读完这一页，再打开完整队列。

## 这是什么

天枢"主动日程 / 陪伴系统"的修复与优化队列。18 张卡（`PC-01`–`PC-18`），分五波次。

- **完整队列**：`docs/development/proactive-companion-repair-queue-2026-10-30.md`
- **机读版**（依赖、路径、完成条件、禁止项）：`docs/development/proactive-companion-repair-queue-2026-10-30.json`
- **问题依据**（每个结论的代码行号与线上数据）：`docs/research/proactive-companion-deep-dive-2026-10-30.md`

## 开工前必须知道的三件事

**1. 线上代码 ≠ 本地工作树，本地是旧的。**

线上 `tianshu_companion-0.1.0`（构建于 2026-10-29 17:24）。这三个模块与本地不一致：

| 模块 | 线上 MD5 | 行数对比 |
|---|---|---|
| `life_goals.py` | `8573313031862543cb2a3921c52a1eb8` | 线上 574 / 本地 492 |
| `life_daily.py` | `779b72fe8da648f9a07f71a771390fba` | 线上 800 / 本地 769 |
| `core.py` | `997e1e93730534f57fde7f90f6861955` | 装配差异 |

其余七个模块（`life_activities` / `life_affect` / `proactive` / `participation` / `actor_memory` / `runtime_execution` / `life`）与本地逐字节相同。

**第一件事：用这些 MD5 在 Companion 仓库定位与线上 0.1.0 对应的 commit，从它起分支。** 不要在本地工作树上直接改。

**2. 存储是 PostgreSQL，不是 SQLite。**

schema：`ts_tianshu_companion_main_0c6bf7a49daf`、`ts_tianshu_companion_observations_af0a741151ec`，位于 `xinghai` 库内。

盘上的 `companion.db`（7.9MB）是迁移前遗留，mtime 停在 10-13，**不是数据源**。容器内没有 `psql`，查询走宿主机直连。DSN 在 `config/modules/companion/settings.json` 的 `storage_backends`，**读它时要脱敏**，不要打印到输出或写进文档。

**3. 这套系统线上基本没跑起来。**

| 指标 | 实测 |
|---|---|
| 自主决策成功率 | **21.7%**（15 / 69） |
| `actor:household` 停摆 | **25 天**（2026-10-05 起，`cursor=null`） |
| paused 中 `next_due_at` 为 NULL | 6 条中 **5 条** |
| 日记 | 62 条，**近 11 天零成功** |
| `proactive_*` 八张表 | **全部 0 行，从未运行** |
| 三个 actor 的 `life_interests` | **全为 `[]`** |
| Platform 鉴权拒绝 | **193 次/分钟**，连续两小时无间断 |

所以不要按"优化一个在跑的系统"的心态开工。当前是"让它先能跑"。

## 执行顺序

```
W0  PC-01                    补观测、固定基线
W1  PC-02 → PC-06 / PC-07    止血（403 / 活动黑洞 / 日记 / 主动闭环）
     PC-03、PC-04 → PC-05    可与上面并行
W2  PC-08 ~ PC-11            结构性修复与小 bug
W3  PC-12 ~ PC-17            算法升级
W4  PC-18                    联合验收与连续观察
```

**硬门禁：W1 未完成，不得进入 W3。**

理由：W3 的显著性打分需要一条真实运行的主动链路来验证，而 `proactive_*` 八表全空。给从未运行过的东西加打分，无法证明任何效果。

**如果只能先做三件：PC-02、PC-04、PC-07。** 403 是日记、兴趣、决策三个故障的共同根因；活动黑洞已经造成 25 天可见损失；主动闭环是用户最核心的期待而它从未发生。

## 不许做的事

完整清单见队列 §3 与每张卡的"禁止"项。最容易踩的几条：

1. **不要用放宽授权来消除 403。** 若结论是"原本的拒绝是错的"，先说明原因并请用户确认。不要加白名单绕过。
2. **不要让 dormant 自动重开，也不要让 dormant 增加 `result_refs`。** 休眠是"她没继续做"，不是"她做完了"。
3. **不要用模型生成搜索结果冒充真实搜索。** 没有适配器就移除技能声明，不要返回空成功。
4. **不要在未跑 dry-run 的情况下对生产数据执行变更**（PC-05）。
5. **不要为演示顺畅就宣布"有了完整生活"**，用户明确要求过这一点。
6. **不要新建平行的队列、调度器、记忆服务或搜索服务。** 沿用现有 `life_activities` / `life_concerns` / `proactive_*` / `actor_memory`。
7. **不要把本地当线上**（见上文第一条）。

## 代码质量要点

完整版见队列 §4。密度最高的四条：

- **状态迁移走显式迁移表**，且要检查 `save()` 直调是否绕过（`life_goals.py:467` 就有这样一处）。
- **NULL 不可比较。** `WHERE deadline <= ?` 在 `deadline` 可为 NULL 时永不命中——这就是 PC-04 的根因。
- **模型调用在锁外，提交时校验版本。** 照 `life_goals.work` 的现有结构做。
- **只跑受影响的定向测试**（`AGENTS.md` 2026-10-28 的明确要求），不为小改动跑全量。

注释风格：这个库的注释解释"为什么"不解释"做了什么"，新代码保持同样习惯。

## 收口

每张卡必须同时具备：代码提交（精确 SHA） + `docs/handoffs/PC-xx.md`（含**未验证项**，不许留空）+ 结构化结果 JSON。

状态词严格分开，**禁止单用 `done`**：

`planned` → `in_progress` → `local_verified`（本地提交+定向验证） → `integrated`（已合产品主线） → `deployed`（镜像在跑） → `verified_production`（实机验收通过）

`local_verified` **不等于**已完成。这是本项目反复出过问题的地方。

## 需要用户决定的一件事

**PC-02 归"协调"而非 Companion。** 因为它可能只是配置或凭据同步问题，跨三份 `settings.json`，不一定是 Companion 的代码 bug。若定位后确认纯属 Companion 代码问题，视野收回 Companion 单写者。

## 已知的信息缺口（不要在未取证前下结论）

- 403 的具体被拒路由、peer 名、principal：日志 `attributes` / `metadata` 均为 `{}`，`last_failure.peer` 记为 `"unknown"`。PC-01 就是为了补这个。
- 容器 10-29 13:53 之前的日志已轮转丢失。
- 10-27 凭据轮换与 10-29 的 403 风暴时间接近，但**没有直接证据**证明因果，本次未作结论。
