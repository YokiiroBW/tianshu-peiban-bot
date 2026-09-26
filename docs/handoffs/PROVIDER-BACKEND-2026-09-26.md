# 供应商自助配置后端贯通交接（2026-09-26）

## 范围与基线

本次只在 `PROVIDER-P1/tianshu-platform`、`PROVIDER-G1/tianshu-model-gateway`、`PROVIDER-C1/tianshu-companion` 三个独立产品工作树和当前根协调检出工作；没有改网页、`projects/` 协调检出、NAS、真实设备或生产数据。产品基线分别为 `3ae3a66d`、`0e208a9`、`d794882`；根浏览器 HTTP 契约先提交为 `c644b7bd`。本交接是本地代码与证据，尚不是部署验收。

## 已接通的功能

1. 平台接入私有 AES-GCM 供应商目录、管理员同源 Cookie/Origin/CSRF 与模型管理解锁。`/api/web/providers/{view,save,clear-key,delete,models,test,default}` 按 `contracts/provider-self-service/v1/README.md` 工作。保存不触发付费调用；读取模型、短回复测试和设默认分开。测试在网络调用前持久占用 `client_id`，取消/不明结果不自动重发。
2. 平台以专用服务身份接受陪伴端新回合 `select`，给出固定版本和一小时有界租期；网关 `runtime` 必须带精确版本、turn ID、服务和工作负载。默认切换只影响新回合，编辑/删除/停用/清密钥使旧修订不可再执行。版本续期由新回合按需生成，不要求用户每日发布。平台自己的数据库与私有目录不对其他产品开放。
3. 网关用独立平台凭据接收实际 `models/test`，公共 HTTPS 目标经过 DNS 地址检查与固定、TLS 校验、无重定向/环境代理/重试、有界响应。动态对话复用原有 scheduler、持久开始标记、诊断、SSE、回执和去敏路径；排队后和建立连接后再次向平台核验同一个版本与修订。没有版本通配授权。
4. 陪伴端可选 `provider_self_service` 装配专用 `provider_selector` 服务客户端。新回合在 Memory/生成前取得并固定版本，现有静态版本模式仍可用。
5. 平台新增显式 `providers-init` 命令；正常启动和 preflight 只验已有库、密钥、密文及必需表，不会自动新建或重置。生产 `cryptography==50.0.1` 及 hash-locked runtime 依赖已纳入产品构建。

## 部署接线清单（尚未执行）

- 平台配置 `provider_self_service: {directory, gateway_url, gateway_token_env}`；目录在服务/管理员独占的非公开路径，先在凭据环境下运行一次 `python -m services.platform --settings <settings.json> providers-init`，后续只运行普通 preflight/serve。`providers.sqlite` 与 `providers.key` 必须停写后一同备份/恢复；Windows ACL 由部署层设置。不要把目录放进静态资源、诊断包或 Git。
- 平台 `companion` principal 增加 `config.select`，`gateway` principal 增加 `provider.runtime`。网关配置 `provider_management_credential_ref`（独立于对话/平台凭据）、`provider_self_service: true`，在 `secret_references` 中登记该引用。平台 `gateway_token_env` 的值须对应网关管理引用。平台/网关现有服务凭据与身份不能复用为管理凭据。
- 陪伴配置 `provider_self_service: true`，在 `services.provider_selector` 配置平台内部 URL、独立 companion token 环境名和 CA 文件（内部 JsonService 要求 HTTPS）。浏览器当前 HTTP 局域网入口可按既有方式继续；内部 HTTP 若传输服务凭据和上游密钥，必须由部署网络隔离或另配 TLS。
- 上述设置、ACL、服务网络、网关运行容量、前端新操作流和真实供应商调用均需后续部署/验收任务另行确认；本次没有改真实部署 JSON 或迁移现有默认模型。

## 已运行验证

- 根联合套件：4 项通过。真实 aiohttp 浏览器 Cookie/CSRF 管理请求、平台↔网关 HTTP、网关↔隔离录制上游 TLS、陪伴 Core 新回合贯通；覆盖空目录、保存/枚举/测试/设默认、真实转发、重启、默认切换与旧回合、超过一小时续期、清密钥撤销、错误密钥/地址、枚举不支持、超时、取消不重发及 SQLite 无明文密钥。首次初始化命令一次成功、重试拒绝、损坏备份 preflight 拒绝。仅测试目标策略在夹具中允许 loopback，生产策略未放宽。
- 平台专项 `test_provider_catalog` 19 项、现有 `test_web_models` 26 项通过。网关 `test_provider_adapter` 17 项、`test_gateway_http` 28 项通过。陪伴 `test_model_selection` 10 项、`test_bootstrap` 5 项、`test_gateway` 1 项通过。三个产品改动文件的 Ruff check 与 format check 通过，`git diff --check` 待提交前复核。
- 平台 `scripts/build/check_install.py --execute` 在已提交代码上，用 Windows Python 3.12 独立 build/runtime venv 构建非 editable wheel，hash-locked 依赖安装、`pip check`、已安装 CLI 均通过；证据在产品工作树忽略目录 `.runtime/provider-build-committed-20260926/evidence.json`。Linux 镜像没有执行。

## 仍需验收

网页添加/编辑/选择/状态与“开始对话”流程由网页任务按已冻结 HTTP 契约完成；本次没有改页面。生产/ NAS 未启用此配置，没有真实供应商、付费模型或真实聊天数据测试。联合测试的陪伴服务客户端使用受控本地 HTTP 测试传输，生产 `JsonService` 的 HTTPS 配置与证书联验仍待部署。根合同 `v1` 是本地集成候选，合入协调仓库前需按单负责人顺序审查各提交。
