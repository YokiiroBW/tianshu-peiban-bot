# 官方版本与配置核对（2026-09-22）

以下注册表表格保留DEP-A当时的只读观测，不能用作DEP-E当前应用镜像证明。
DEP-E固定组合现在是Platform `0a3cf65b8da1`、Companion `e94b60909936`、Memory `9a3b2bed6aeb`、Gateway `ec20f95e3849`。
TS107/108构建输入修复已由协调验收；本任务只消费提交，未运行Linux构建。平台/网关续期提交不等于Dockerfile修复。

这里只核对上游资料和现有产品构建输入，不把 tag 存在当作镜像构建通过。NAS 记录中的 Docker24.0.2 / Compose2.20.1
是历史只读采样，本任务未复查 NAS；实际本地只有单独下载的 Compose 解析器，没有 Docker CLI/daemon。

| 输入 | 官方核对结果 |
| --- | --- |
| Compose v2.20.1 Windows x86_64 | 官方 release 二进制与同 release `.sha256` 校验一致；SHA256 `7e6f1f0e5fadd6f834de067f4e321560eb1b6465dff62f59cffe36a23a300497`；只执行 version/config |
| node:24.19.0-bookworm-slim | Docker Hub library/node tag API 返回 linux/amd64 digest `sha256:e5a8dee7bc1e6a215d224a7ef8206f7e77271bc3cabd5febf2beafac0674f174` |
| python:3.12.11-slim-bookworm | library/python tag API 返回 linux/amd64 digest `sha256:c00fc7b44d844b6da22861ec24af43968a5200eac4ec607b4725d585165d6b49` |
| python:3.12.12-slim-bookworm | library/python tag API 返回 linux/amd64 digest `sha256:2986c55feb36e6cae00fa1fefb454283e4b33f35e75ff8bdd123b134130be301` |
| Companion python:3.12-slim | 产品原文件浮动标签，未选定其当前 digest，不在发布包中虚构确定值 |

上面 digest 是具体架构 manifest 的注册表观测，不是多架构 index，不是四个应用镜像 digest，也未实际拉取。
产品原文件仍只写 tag；本号不改 Dockerfile，源导出/Compose不冒充可复现构建已完成。后续由产品负责人固定并真实构建复验。

DEP-A此前绑定 Platform `fa85ee939a2a`、Companion `cf020fd338b9`、Memory `2f4037620f47`、Gateway `51121e6c02ed`。
与上一集成基线相比，三产品 Dockerfile/正式依赖声明未变，上述构建风险仍在；本次已重新导出原字节固定快照，记录于 `verification.json`。

已审产品依赖：Platform `pyproject.toml` 精确 aiohttp3.14.1/jsonschema4.26.0/referencing0.37.0，前端 package-lock；
Companion Dockerfile 四项直接依赖与 pyproject 一致；Memory 与 Gateway 有 uv.lock。正式镜像仍用各产品原构建命令，
不从开发 venv、系统安装或测试依赖拷贝环境。

Memory旧基线曾存在下述风险（TS107已修，保留诊断来由）：`python -m venv /opt/tianshu/venv` 后仅安装 uv 导出的正式依赖，再以
`pip install --no-deps --no-build-isolation .` 安装要求 `setuptools>=75` 的产品。Python3.12 venv 不再自带 setuptools，
故缺少构建后端的路径需要产品负责人处理并真实构建确认。另 `uv export --frozen` 跳过锁新旧核对，原注释“不同步即失败”不成立；
应由产品任务评估 `--locked`。本号未修、不声称已观察 Linux build 失败。

Docker 的 Dockerfile 同目录 `Dockerfile.dockerignore` 有内置支持；Memory 原文例子里的 `docker build --ignorefile` 不在 Docker buildx 参数中。
本包使用正常 `--file` 选择，不复制、更改产品 ignore 文件。

资料：

- [Docker Compose 2.20.1 官方发布](https://github.com/docker/compose/releases/tag/v2.20.1)
- [Compose 服务字段](https://docs.docker.com/reference/compose-file/services/)：bind/create_host_path、user、只读/能力/健康检查/资源。
- [Compose 多文件路径规则](https://docs.docker.com/compose/how-tos/multiple-compose-files/merge/)：本包用独立根，DEP-B保持独立。
- [Docker build context 与 Dockerfile 专属 ignore](https://docs.docker.com/build/concepts/context/)
- [Docker 网络概览](https://docs.docker.com/engine/network/)：前端同时接可外部访问网络与内部后端网络，只有显式发布端口作为网页入口。
- [Docker buildx build 参数](https://docs.docker.com/reference/cli/docker/buildx/build/)
- [Python3.12 venv](https://docs.python.org/3.12/library/venv.html)：3.12不再将setuptools作为核心依赖。
- [uv 锁与同步](https://docs.astral.sh/uv/concepts/projects/sync/)：frozen 与 locked 区别。
- [Node 官方 tag 元数据](https://hub.docker.com/v2/repositories/library/node/tags/24.19.0-bookworm-slim)
- [Python3.12.11 官方 tag 元数据](https://hub.docker.com/v2/repositories/library/python/tags/3.12.11-slim-bookworm)
- [Python3.12.12 官方 tag 元数据](https://hub.docker.com/v2/repositories/library/python/tags/3.12.12-slim-bookworm)

本号工具直接依赖固定 jsonschema4.26.0 / cryptography50.0.1，实际本地安装成功；工具不是产品运行依赖。
# DEP-G 第四批追加绑定

协调确认 TS111/112 本地集成后，当前 example 重绑 platform
`b98c8a2a09279125df08bd48f3fab3422f3de165` 与 gateway
`601974194042641c5a85cc3c061cbd1880d7daf1`；companion e94b6090、memory 9a3b2bed
及续期合同 c5017724 保持。两应用 image.digest 仍 null，verification 仍 unverified，
全部默认 feature 仍关闭。下方历史评审及 verification.json 保留原证据，不能当新版 Linux 通过。
运行入口及本轮证据范围见 LINUX-VALIDATION.md 和 docs/handoffs/DEP-G.md。

## DEP-G R1 日志来源窄重绑

协调接受 DEP-I R1 并集成到根 4f3f7231 后，observability.source.commit 更新为
`65b88a6d1c2b5047ca6bfb2f7f7484749eb14154`。四产品与合同固定版本不变；
实际固定 Git 导出、合成 TLS 配置及五卷检查通过，Linux 日志容器和真实 Loki 未执行。
新增证据见 tests/deployment/packaging/evidence/DEP-G-R1-combination.json；不改历史证据。
