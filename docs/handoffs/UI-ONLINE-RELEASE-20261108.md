# UI 在线发布及功能模型绑定交付

用户于 2026-11-08 授权核对 UI 分支，并合并修复聊天
`01a1b8f2-29f7-7f91-8a1c-1db2489c45ee` 的模型绑定问题后推送部署。
本批协调及串行发布归 `01a1b700-8dc7-7283-91e8-a53b7070b835`。

## 交付边界

复用 `codex/ui-online-release-20261108` 的 `48c8cc03`，在独立工作树
`codex/ui-model-release-20261108` 集成。原协调工作区的 Wiki/README 修改、
所有其他工作树及原 UI 工作树未提交内容保持；需要的 UI 补修已读取并纳入本批快照。

核对发现原报告的真实浏览器集成用例尚未提交，最终交接文件缺失，核心镜像也未生成
具有固定版本 base 的内置 UI。本批补齐真实两版本用例，消除测试机硬编码和旧源码缓存复用，
并在镜像构建中生成 `ui-core-<source_sha>` 及完整发布清单，保证旧根路径资源可独立保留。

UI 使用既有静态服务、/data 挂载、通知和命令监控。首次接入需要常规核心升级；其后兼容的
UI 包通过版本目录、文件锁和原子指针安装/切换/回退，不重启核心。页面不会自动刷新；
这不代表刷新后可恢复所有草稿或组件状态。操作说明见
`docs/development/ui-online-release-operations.md`。

## 模型绑定

模型选择按功能与角色统一，任务类别只参与调度。界面移除执行类别，绑定保存、删除、CAS、
继承和 Broker 路由一并调整，原配额、优先级及 Models 尝试账本保持。

新增 `models_function_bindings` 增量迁移，先将全部原绑定及原修订完整保存在
`models.binding_class_history`，再按功能/角色合并为一个选择：优先原页面默认编辑的
interactive，其次 elastic，再次 due；角色覆盖不变。修订提升到原组最大修订 + 1。
当前存储保留三个同步别名，专用于旧页面及前镜像回退，不能独立配置；所有新写入在同一事务
同步，所有新路由不以请求 service_class 选择模型，删除同时移除该功能的三个别名。
该兼容是首次在线升级和前镜像回退的实际需要，不引入第二套业务控制器。

首次现场只读核对发现运行源码实际为 `b6095012`，251 个 Models unknown、6 个 Action unknown。
Memory/reflect 在读取时三类均已选 OC，其他功能和角色仍有独立绑定。本批按各功能已有选择
统一，不把全部功能改为 OC，不重放既有请求或改写原 unknown，不调整模型供应商或预算。

## 验证与发布结果

产品提交 `34dfba6314d869c3a15c18b5e63db4d151ff83e3` 已推送到
`github/codex/ui-model-release-20261108` 并串行部署。正式镜像为
`sha256:26f33e09d2e6757d5163ffbff52759b44867f4db8c51f2bb23641b173436573f`。
北京时间 2026-11-08 10:47:39 核验：六角色 ready、HTTP 200、0 重启、无 OOM，
容量守护 healthy，容量守护与巡检服务 active。

- 55 个不同受影响 Python/隔离 PG 用例通过；54 项首轮有效结果复用，
  1 项旧“跨执行类别应无绑定”的断言按统一功能绑定语义更新后定向通过。
  Windows npm mock 的修订包含在该轮结果中，未重复计数。
- TypeScript 与正式前端构建通过；桌面/手机共 10 项 Playwright 通过，
  包括两个真实构建包、真实 aiohttp 静态服务、旧页面懒加载与图片、输入与 unknown
  提示保留、API/后台模拟工作连续及 A→B→A。
- 同正式 Linux Dockerfile 的镜像构建通过。11 个受影响安装文件与不可变源码归档
  SHA256 一致，包含模型 SQL、静态服务和配置资源。
- 正式环境以 UID/GID 10001:10001 使用同一工具安装内置基线 A 和独立包 B，
  实际完成 A→B→A→B；每次切换与回退均取得 HTTP 实际版本核验。
  容器 ID/启动时间/重启计数、六个职责进程 instance_id 和 data_generation 全程不变。
  两个版本共 10 个实际 HTTP 资源 SHA256 匹配，3 项原根路径资源仍与旧版一致。

当前独立 UI 为 `ui-20261108-34dfba6314d8`，切换序号 4、compatible=true、
degraded=false；前一版本为 `ui-core-34dfba6314d869c3a15c18b5e63db4d151ff83e3`，
已安装保留，可用同一 rollback 工具恢复。

原 48 条绑定全部与 `models.binding_class_history` 逐字段核对一致；统一后共
20 个功能/角色选择、60 条兼容同步别名，主路线、备用顺序和修订与合并规则一致。
只读调用实际安装的 ModelCatalog，确认 memory.extract、reflect、life.plan 对各调度类别
选择一致；前两项仍为用户选定的 OC DeepSeek，Life 规划保留其现有 DeepSeek 绑定。
其他功能和角色仍按各自配置选择，不能据此宣称所有 Gemini 请求均应消失。

配置文件摘要、11 个挂载、其他 2 个容器、媒体配置、频道配置及业务代际保持。
新增迁移仅 `models_function_bindings`；没有业务清理。
原 251 条 Models unknown、6 条 Action unknown 和 0 条频道 unknown 的身份与关键事实
摘要逐项保持，本次未新增 unknown。原配置另有发布前 JSON 快照，原镜像与工作树保留。
隔离验证数据库、容器及临时凭据已按 owner 回收。

没有发送真实模型或 QQ 测试请求，没有重放未知外部效果；健康和部署核验不代表全部历史业务
已经收敛。完整构建、部署、UI 切换及核验材料保存在原协调目录的
`.runtime/ui-model-release-20261108/`，最终摘要为 `final-receipt.json`。
