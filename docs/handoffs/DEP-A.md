# DEP-A：四核心候选部署包交接

## 目标、范围与状态

已实现可搬运的发布清单、四核心 Compose 生成、显式配置初始化、离线预检、固定源码导出和 Linux 合成验证入口。
交付状态为 **local_packaging_validated / candidate**，不是已部署、可日用或全系统完成。网页对话默认关闭。
首次实现 `0382e141189376c82ad93532ad6995062076fb0c` 经协调首审提出权限返修，未集成；本文末段记录修正版，待协调复验。

本号分支 `codex/dep-a-unified-deployment`；根基线 `21668e4300d7bedc8ac1ed819bdb7dad4a2bd58b`。
共享 schema 首次提交 `e86d622a813f116b059b62b820cbd1342722042d`；实现提交随本文交付，最终 HEAD 另在任务交付消息中给出，
也可用 `git log -1 --format=%H -- docs/handoffs/DEP-A.md` 唯一定位。协调集成范围为根基线之后本号全部提交。
不重置前进中的根主线、不改根任务板/CURRENT/contracts/projects；合并顺序与集成由协调者决定。

仅写 `deploy/tianshu/`、`tests/deployment/packaging/`、本文。未访问 NAS、真实账号/数据/凭据/模型，未推送、提权、发布或操作其他服务。

## 固定发布输入

采用协调 2026-09-22 已验收并集成的 TS104–106；旧导出保留为历史，本次重新导出固定 Git 提交，未复制活动工作目录。

| 产品 | 固定提交 |
| --- | --- |
| platform | `fa85ee939a2affdda054ee7fa3155ec89ade6196` |
| companion | `cf020fd338b9beefc9d7156a00158e915d41735a` |
| memory | `2f4037620f47991f42a5daa10f471c34c9ba4fd4` |
| gateway | `51121e6c02ed60605be14f31b19b484bc117a746` |

发布清单：`deploy/tianshu/release-manifest.example.json`，release_id `tianshu-wave2-candidate-20260922`。
原字节 SHA256 `6bf459a2cbf8c8b760e0e2b0ce68dfacc50db489748ba3d9aef83120249ebdb9`；
schema SHA256 `cf95607fa7252f806ae4ab4d919298c234d91f1007ce763ec12485091f4d0397`（定义未变）。
四镜像仅有提交标签，digest 全部 null、verification 全部 unverified，evidence 空。

| 合同包 | 原始 manifest.json SHA256 |
| --- | --- |
| text-dialogue/v1 | `0f9880a1bfb151eb7ec48db1c086d779c99c972c51933de0501d77da535605f8` |
| profile-memory/v1 | `20ef6f36b64e39080101e1186672dbc9e5b20529e0c56971e1cc5a2880fe6048` |
| source-sync/v1 | `178d0ce66210bdfad4cfb85d8b5f0905b0b67f834e2a530efe5636ff0373633d` |
| web-conversation/v1 | `e493a1b5d0f4cec8d55995553faf84042f4c33a59365d15423e57f4dc70a6c09` |
| model-protocol/v1 | `52711a71de56dbceebd1d5d96b2baf59a2d9551168029972d59111480f815141` |
| diagnostics/v1 | `5d89f7a21637fd57cea4a236e17f8d8c4917799497ff44f87ee68ea614d4805f` |

每个合同的完整逐文件原字节 hash 同时在清单和 `verification.json`，包括 CRLF，不使用产品加载器的归一化 hash 替代。
本包源文件设置 LF；该设置不修改外部合同。四产品 archive hash、Dockerfile/正式依赖输入 hash 与文件计数见 verification。

## 全部变更文件与行为

`deploy/tianshu/`：

- `.gitattributes`、`.gitignore`：工具文本固定 LF；本机快照、依赖、合成材料及缓存不入库。
- `release-manifest.schema.json`、`release-manifest.example.json`：唯一共享发布定义；固定产品/合同/功能/卷/服务角色及发布阻断。
- `manifest.py`：闭合字段与语义校验，原字节合同、证据绑定，路径/链接/挂载/备份组边界。
- `configuration.py`：实际产品配置、端口/Origin/Host/身份/凭据引用映射，真实 TLS 链与 SAN 检验；无网络、无 issuer。
- `compose.py`：四服务 JSON/YAML Compose；内部 HTTPS、UID/GID10001、只读根、单 owner、显式数据/日志卷、一个平台入口，无 docker.sock。
- `runtime_guard.py`：Linux 状态目录 flock，持有 FD 后 exec 产品；不删除产品 owner 或 guard。
- `bundle.py`：仅新目录初始化，私有输入分离，完整性与容量预检，固定 Git archive 导出；不会初始化产品数据库。
- `release.py`：公开 validate-manifest/init/preflight/export-sources/runtime-check/inspect-acceptance 命令，异常仅输出固定错误码。
- `linux_validate.py`：默认 plan；显式 execute 仅本地 Linux、新合成 QA 项目、空卷、测试 TLS、无模型目标；构建、产品 CLI 建新 Memory 库、活性检查、清理本次新项目。
- `acceptance.py`：只读适配 dep-d/1，核原被测清单与20用例、hash和版本；始终不晋升 release_ready。
- `requirements.txt`：工具直接依赖 jsonschema4.26.0/cryptography50.0.1。
- `templates/deployment-input.example.json`、`templates/platform.json`、`templates/companion.json`、`templates/memory.json`、`templates/gateway.json`：明确配置与环境引用；公开 Origin 故意为不可用占位符，不能直接启动。
- `README.md`、`version-review.md`、`verification.json`：操作说明、官方版本核验与真实本地证据/未执行边界。

`tests/deployment/packaging/`：`.gitignore`、`test_packaging.py`、`test_permissions.py`、`verify_compose.py`、`verify_product_entrypoints.py`、`probe_memory_factory.py`。
另有本交接 `docs/handoffs/DEP-A.md`。没有修改四产品 Dockerfile/源码或其他窗口产物。

## 实际验证与证据

工作目录 `C:/Users/Administrator/.codex/worktrees/1e1e/tianshu-peiban-bot`。
下列 `python` 实际为 `deploy/tianshu/.work/venv/Scripts/python.exe`（Python3.12.14）；
`ruff` 为同目录 `ruff.exe`（0.15.7）。实际原合同根 `C:/YOKI/Codex/tianshu-peiban-bot/contracts`。
未使用生产安装或虚构产品 npm 命令；产品检查运行于本号依赖环境，正式镜像仍消费产品原 Dockerfile。

| 实际命令/动作 | 结果与范围 |
| --- | --- |
| `python -B -m unittest discover -s tests/deployment/packaging -v` | 24 passed，4.291s；合成输入、TLS、路径逃逸、原字节、所有者/卷映射、秘密不外泄、错误版本/证据、重复初始化等 |
| `python -B tests/deployment/packaging/verify_compose.py --compose <本号.work/docker-compose-windows-x86_64.exe>` | 官方2.20.1解析通过；四服务、非root、一个发布端口、无socket、含$凭据字面值保持；未访问daemon |
| `python -B tests/deployment/packaging/verify_product_entrypoints.py --contexts deploy/tianshu/.work/build-contexts-final --contracts-root C:/YOKI/Codex/tianshu-peiban-bot/contracts --report deploy/tianshu/.work/final-product-entrypoints.json` | 最终固定版本7项通过；四CLI、平台只读preflight、Memory真实factory配置截获、新合成库官方schema1→3迁移 |
| `python -B deploy/tianshu/release.py export-sources --manifest deploy/tianshu/release-manifest.example.json --repos deploy/tianshu/.work/repos.json --contracts-root C:/YOKI/Codex/tianshu-peiban-bot/contracts --output <本号绝对路径/deploy/tianshu/.work/build-contexts-final>` | 四产品新固定导出成功；平台加入已核hash的contracts供原Dockerfile使用；没构建镜像 |
| 最终清单+原合同调用公开 `initialize`、`preflight` | 新合成材料包 initialized_candidate/package_valid；无业务库/服务；临时目录已清理 |
| `python -B deploy/tianshu/linux_validate.py --bundle <上述新合成包> --contexts <build-contexts-final>` | 10步骤全部not_run；核真实固定上下文，没有执行容器 |
| `python -B -m unittest discover -s tests/deployment/packaging -p test_packaging.py -k linux_plan -v` | 补齐Linux新库迁移计划后定向1 passed，0.412s；不是Linux执行证明 |
| `ruff check deploy/tianshu tests/deployment/packaging` | All checks passed |
| `python -B deploy/tianshu/release.py runtime-check` | not_available / docker_executable_missing，CLI约定退出3；没有可用Docker CLI/daemon |
| `python -B deploy/tianshu/release.py inspect-acceptance --report deploy/tianshu/.work/dep-d-report.json --subject-manifest deploy/tianshu/.work/subject-manifest.json` | DEP-D固定历史报告格式适配通过；20 not_run、0 pass、release_ready=false；非最终版本联合验收 |

平台 preflight 实际退出1，正确报告 requires_initialization 和 web_static_incomplete（有意不完整的合成静态页）；
config/contract/credentials/TLS均ok，11文件前后字节索引hash完全相同、未建数据库。
Memory factory 在实际 Store 边界截获，证实它读取 `TIANSHU_MEMORY_CONFIG`；本包将其与CLI路径绑定，篡改Compose即使重算hash也拒绝。
另一独立新合成库确实用产品 migrate-profiles/migrate-sources 到schema3并生成source-guard，不计作旧库升级、Linux或恢复演练。

公开证据主文件 `deploy/tianshu/verification.json` 包含以上机器可读结果、最终版本、合同完整hash和未执行列表。
忽略目录 `.work/build-contexts-final/source-inventory.json` 保留完整源清单；archive可按固定提交重新导出。
官方Compose二进制SHA256 `7e6f1f0e5fadd6f834de067f4e321560eb1b6465dff62f59cffe36a23a300497` 与官方release校验一致。

## 未完成、风险与集成下一步

清单只撤掉已完成的 TS104–106 重绑定项，保留9项：linux_images_unverified、joint_acceptance_unverified、
memory_candidate_consumer_missing、memory_disable_boundary_unverified、chat_audit_unconnected、application_log_reclamation_missing、
product_dockerfiles_need_review、model_origin_bootstrap_unverified、real_restore_lifecycle_unverified。

1. 本机无Linux容器环境。真实镜像构建/digest、容器TLS/权限/flock/信号/Memory迁移、认证readiness、浏览器/模型/24h/NAS均未运行。
2. Memory Dockerfile 的Python3.12新venv未显式安装setuptools构建后端却使用no-build-isolation，且uv export --frozen不检查锁新旧；Companion浮动base tag，其他base也未固定digest。已报告协调，需产品负责人修复/实构建。本号只定位风险，不声称实际build失败。
3. Companion七派生索引、schema9不变；旧库启动前完整备份要求与首次后台成功才ready仍需容器验证。预检的日志预算+2倍状态+256MiB只是容量下限，不是磁盘配额或长期吞吐承诺。
4. 自动长期记忆无消费者、Core缺已验收的安全停用边界；Memory空event_scopes不证明不积压。因此不能开放日常聊天或用关闭权限宣称闭环完成。Chat Audit仍未接线。
5. DEP-B保持独立包，按README公开configure命令接入四logs目录，只读UID10001，不复制日志Compose。没有其最终全链路证据，central_logging仍false；应用段回收无确认协议，Loki保留期不能代替。
6. DEP-C合成恢复不等于真实停写/恢复授权或生命周期证明；所有状态目录备份应含全子树DB/WAL/SHM/sidecar/guard/owner/迁移备份。本版Memory guard同物理状态目录，不宣称独立故障域。
7. DEP-D `5c89fc662b923b91e7f33a44c3dc0fc2101c9dce` 历史报告仅用于适配核验，原被测清单SHA256为 `7566375a763440d9408d1ae6a3502aab5521bd996cec3dc93c523ebcd660f922`。
   不能把最终清单hash填回旧报告。需在最终镜像/配置接齐后重跑并保留原始不可变被测清单；static_input_only/适配器自述不是独立执行身份。
8. verified证据正文封装还需协调接齐五类报告及独立执行证明；本包没有promote，所有命令都不宣布release_ready。模型来源assertion取得/续签、版本发布与授权也不能靠模板伪造。

协调下一步：审本号完整差异，串行本地集成；消费此最终清单重绑定DEP-B/C/D；给产品构建缺口补卡；取得合法本地Linux隔离环境后执行公开验证入口；其后再按用户实际授权讨论NAS。开发提交不自动授予这些操作。

## 引用

- 权威源只读任务卡：`docs/development/deployment-wave2-codex-2026-09-22.md`。
- 协调最终接受记录：`docs/development/reviews/direct-fixes-accepted-2026-09-22.md`（权威源）。
- 本号操作细节与官方来源：`deploy/tianshu/README.md`、`deploy/tianshu/version-review.md`。
- 原产品Dockerfile/依赖/CLI与合同均来自上述固定提交和权威合同目录；实际检查记录在verification，不复用旧测试成功声明。

## 权限返修（2026-09-22）

返修基线 `0382e141189376c82ad93532ad6995062076fb0c`。按协调 `docs/development/reviews/DEP-A-review-2026-09-22.md` 精确卡处理：
原预检会接受000配置、漏查合同与guard可读权限，是本包逻辑缺陷；此前24项通过未覆盖该反例。协调自己的24项4.184s记录属于独立首验。

本次仅改五文件：`deploy/tianshu/bundle.py`、`tests/deployment/packaging/test_permissions.py`、`deploy/tianshu/README.md`、
`deploy/tianshu/verification.json`、本交接。没有改产品、共享schema/清单/合同、其他窗口或前进中的根主线。

权限预检现在按容器UID/GID10001的POSIX属主优先规则检查：配置/TLS挂载根与所有子目录可读且可遍历、普通文件可读；
合同全树可读/遍历；直接绑定的guard普通文件可读，不强加执行位或容器不可见的主机源父目录权限；
八个数据/日志根仍要求专属10001:10001及读写遍历、无other访问。配置禁止other访问/组写以及无关组访问，
主机Compose读取的private目录和env保持操作者独占。正常的操作者:10001组读配置、公开非秘密合同/工具都可接受。

初始化只将公开合同目录默认改为0755（合同文件原有0644）；配置目录0750和文件0640、private目录0700和env0600不放宽。
README补全逐挂载权限表及操作者准备顺序。工具不提权、不chown；需用修正版生成新包，随后再做权限预检。

稳定后已复核完整返修差异，实际回归：

- `deploy/tianshu/.work/venv/Scripts/python.exe -B -m unittest discover -s tests/deployment/packaging -v`：**34 passed，4.163s**。
  含既有24项和新增10项权限测试；覆盖四套000配置、无x子目录、异属主不可读合同、不可读guard、属主权限不能退到组/other、
  合法UID/GID读权限、主机绑定父目录不误判、数据/日志根写与遍历、秘密权限和公开输入不可组/other写。
- `deploy/tianshu/.work/venv/Scripts/ruff.exe check deploy/tianshu/bundle.py tests/deployment/packaging/test_permissions.py`：All checks passed。
- `git diff --check`：通过。

新增测试在Windows使用合成POSIX stat元数据与目录节点调用**实际permission_checks**；不是Linux文件访问实验。
Linux权限/容器/NAS仍not_run；此前Compose与7项产品入口等未改变成功检查未重跑，verification显式保留其历史范围。
九项发布blockers、candidate、镜像digest=null、网页对话关闭均保持。返修固定提交后停写，由协调复验和串行集成。
