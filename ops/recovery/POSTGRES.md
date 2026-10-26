# PostgreSQL 与文件库的常驻冷备

TS-236 在现有十服务 `resident_snapshot` 上增加可选 PG 原生备份。默认 SQLite 的停止、文件复制、完整性校验和单份最新冷备政策继续使用原入口；不能把旧 SQLite 副本当成已启用 PG 后的当前数据。

## 当前 SQL、文件和控制状态一起保存

冷备仍要求原十个业务/日志 owner 全部停写，保留配置、私有凭据、独立 guard、`.storage/` 后端绑定目录、文件根和既有日志账本。媒体成品及已登记原始日志外置挂载沿用既有排除规则。

Memory / Knowledge 的 `knowledge_files_directory`（省略时为 `<database_path>.files`）属于必备原件。位于部署根内时随原完整目录复制；外置时按该服务实际 bind 映射到主机路径，自动作为独立受保护控制目录复制并核对。外置 bind 仍须按现有容量配置登记；不能把它只列为“排除的数据挂载”而遗漏原件。自定义文件根使用绝对容器路径，必须有对应 bind。现有媒体库、硬链接和成品路径不移动。

工具从文件清单发现 `.tianshu-storage-<service>.json`，检查 PG `active` 和每个逻辑 schema 的安装归属。`pending` 或丢失目标绑定不能产出“备份成功”。每服务的全部逻辑 schema 放进一份 `pg_dump --format=custom`，保留事务内一致性；不同业务服务使用既有停止全部 owner 的流程取得跨服务静止窗口。不能同时运行本部署以外的写者。

每份 dump 在新生成的 `ts_restore_<uuid>` 临时数据库中用 `pg_restore` 实际恢复，核对所有产品普通表的完整行摘要、行数与序列水位，再核对源库未变化。Memory / Knowledge 另核对当前 PG metadata 与独立 source guard；旧 SQLite 仍做自身完整性检查，不拿它的落后 revision 冒充当前 PG 检查点。临时库在成功或失败结束时删除，仅限本次创建的 UUID 名称。报告保存 schema、安装 ID、摘要、大小和原生恢复结果，不保存连接 URL。

最新冷备保留 `postgresql/*.dump`、原件文件及所有必要控制目录；验证临时副本可按原保留策略清除。只有下一份完整冷备原生恢复成功、文件 hash 与来源绑定均通过后，才允许清除旧冷备的数据目录；原清单和收据继续保留。

## 客户端与私有连接

备份执行机需要 `psql`、`pg_dump`、`pg_restore`。优先使用与服务端同主版本的官方客户端；将可执行文件放在 `PATH`，或在私有 expectation 的 `postgresql_tools` 显式给三个可执行路径。Python 恢复代码仍只依赖标准库。容器内有 PG 服务不代表主机已安装这些客户端。

默认从当前部署 `config/<service>/settings.json` 的 `storage_backends.postgresql_url` 及 `private/<service>.env` 中非空 `TIANSHU_POSTGRES_URL` 取得该服务当前连接。只在内存读取备份所需字段；URL/密码通过子进程环境传递，不进入命令行、公有发布清单或错误正文。连接支持标准 `postgresql://` / `postgres://` URI、TLS 及常用 libpq 参数；不支持的参数明确拒绝。

可在私有 expectation 中按服务指定命名环境变量：

```json
{
  "postgresql": {
    "memory": {
      "url_env": "TIANSHU_MEMORY_POSTGRES_URL",
      "maintenance_url_env": "TIANSHU_MEMORY_BACKUP_POSTGRES_URL"
    }
  }
}
```

`url_env` 账户须可读该产品的所有 schema、表及序列。隔离恢复账户另需同一 PG 实例的 `CREATEDB` 及恢复自身临时库的权限。自动发现模式优先读 `TIANSHU_<SERVICE>_BACKUP_POSTGRES_URL`，再读 `TIANSHU_BACKUP_POSTGRES_URL`，均未给时使用当前服务连接。维护账户仅供冷备过程，不要求业务运行账户长期拥有 `CREATEDB`。认证、客户端不匹配、权限不足或恢复失败均不能生成已验收收据。

## 使用现有 PostgreSQL 容器的匹配客户端

宿主没有匹配客户端时，可使用 `postgres_container.py` 标准库桥接现有 PG 容器的三个客户端，不安装另一版本、不复制数据库目录。私有 `ROOT/config/storage-backup.json` 可登记三个绝对 wrapper 路径；显式 `postgresql_tools` 参数优先，其次该私有文件，最后仍使用原 `PATH`。没有 PG 绑定时不读取此可选文件，默认 SQLite 流程不变。

```json
{
  "postgresql_tools": {
    "psql": "/ABS_PRIVATE_TOOLS/psql",
    "pg_dump": "/ABS_PRIVATE_TOOLS/pg_dump",
    "pg_restore": "/ABS_PRIVATE_TOOLS/pg_restore"
  },
  "postgres_container": {
    "docker": "/ABS_DOCKER_EXECUTABLE",
    "container_id": "REPLACE_WITH_EXACT_64_HEX_CONTAINER_ID",
    "image_id": "sha256:REPLACE_WITH_EXACT_64_HEX_IMAGE_ID",
    "endpoint": {"host": "198.51.100.10", "port": 25008, "container_port": 5432}
  }
}
```

该文件由协调者放在主机 root:root 0600，不挂入业务容器，也不保存 URL/密码。三个 wrapper 分别固定自己的工具名，使用已有主机 Python 执行同一份源码，例如 `pg_dump`：

```sh
#!/bin/sh
exec /ABS_EXISTING_PYTHON -B /ABS_TOOLING/ops/recovery/postgres_container.py --config /ABS_ROOT/config/storage-backup.json --tool pg_dump -- "$@"
```

每次调用先通过 Docker 的过滤 inspect 核对精确容器 ID、镜像 ID、运行状态和宿主/容器端口映射，不读取容器配置中的凭据。原 `PGHOST` / `PGPORT` 必须等于私有端点，保持原主机及 TLS 语义；不自动改成容器回环地址。协调者应先确认容器到该宿主发布端口的实际连通性。容器替换后须重新核对并更新明确身份，不能按名字自动选择新容器。

桥接接受现有原生冷备工具的参数，通过 `docker exec -i -e NAME` 只传白名单 PG 环境变量；密码值不进入 argv、公开清单或错误正文。PG 证书/密钥等文件参数仍由 libpq 解释，如使用，路径须在该客户端容器内有效。`psql` 的标准输入/输出按二进制原样流转。`pg_dump --format=custom --file=ABS_NEW_HOST_FILE` 移除容器文件参数，将 stdout 写入宿主独占新建的 0600 文件，成功后 fsync；已有文件不会覆盖。`pg_restore` 将宿主归档流入 stdin，归档路径不传入容器，只允许 `--dbname` 与当前 `PGDATABASE` 相同的 `ts_restore_<uuid>` 临时库。拒绝通过其他连接参数或位置参数覆盖目标。

调用失败/超时只返回固定错误码；部分 dump 可保留作失败证据，不产生原生恢复成功收据。该桥接仍沿用原冷备的维护账号、UUID 临时库和完整恢复验证，不能代替一次真实客户端小表导出/恢复/清理验收。模块和 wrapper 均不自行启动/替换容器、修改生产配置或执行完整冷备。

## 准备恢复文件与缓存代次

```text
python -B -m ops.recovery.postgres_backend --snapshot ABS_VERIFIED_COLD_SNAPSHOT --target ABS_NEW_DISABLED_TREE
```

此命令先复核原冷备收据、完整文件/原生 dump 摘要，只复制到新的禁用目录。拒绝原部署、已登记 owner 挂载和备份目录重叠。复制后推进后端绑定文件的缓存 epoch，保留安装 ID、业务数据及独立检查点，防止恢复数据命中旧 Redis 投影。收据为 `files_prepared_disabled`，不启动服务、不恢复 SQL 到已有数据库、不修改当前权威。外置原件/凭据等控制副本仍在 `controls-backup`，恢复接线时须一并映射，不能只复制 `backup`。

PG dump 的实际生产恢复、服务重接及来源恢复批准仍由原协调恢复流程安排。不得通过回滚 SQL 和独立 guard、移除 PG 绑定或删除 URL 自动回写旧 SQLite。Redis 没有持久业务事实，不作为灾备来源；缓存全部失去后按现有回源逻辑重建。

## 验证范围

本地使用独立 PostgreSQL 16 合成库验证原生导出/临时库恢复、全行摘要及序列、当前 guard、文件 hash、外置原件、自动私有配置发现和最新冷备保留。没有运行 NAS、Docker 镜像或实际产品灾难恢复；原生恢复校验通过不等于业务恢复已启用。

官方接口依据：[pg_dump](https://www.postgresql.org/docs/current/app-pgdump.html)、[pg_restore](https://www.postgresql.org/docs/current/app-pgrestore.html)、[libpq 环境变量](https://www.postgresql.org/docs/current/libpq-envars.html)、[Docker exec 环境及 stdin](https://docs.docker.com/reference/cli/docker/container/exec/)。
