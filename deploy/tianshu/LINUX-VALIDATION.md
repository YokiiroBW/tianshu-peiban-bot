# DEP-G：新合成部署的 Linux 执行入口

默认均为计划，不是发布/NAS验收。需要 Python 3.12、当前目录的打包依赖、Linux 本机
Docker/Compose。没有 Docker 时只能运行本地边界测试和平台 CLI 验证，不能写镜像通过。

## 隔离依赖与输入

在协调者已选的新 synthetic scope 旁准备专属工具 venv，不给宿主全局 pip 安装包：

```sh
python3.12 -m venv /explicit/tooling/dep-g-venv
/explicit/tooling/dep-g-venv/bin/python -m pip install -r deploy/tianshu/requirements.txt
```

若宿主没有 Python 3.12/venv，这两条命令尚不可执行；由协调者提供隔离的 3.12 工具运行时，
不要以系统 Python 3.8 冒充，不自动 apt/pip 全局安装。工具 venv 不作为任何产品运行依赖证明。

合同必须从协调根先按被测 manifest 的 manifest/files SHA256 核原字节，再复制到私有快照；
`release.py export-sources` 及 `synthetic_init.py` 均会复核。不可用根 Git 历史合同替代。
仓库映射 JSON 为四个键 platform/companion/memory/gateway，值为可只读访问固定 Git 对象的
仓库路径。导出只用 Git archive，不读取他人的活动源码。

```sh
/explicit/tooling/dep-g-venv/bin/python deploy/tianshu/release.py export-sources \
  --manifest /explicit/candidate.json --repos /explicit/repos.json \
  --contracts-root /explicit/private-contract-snapshot --output /explicit/new-contexts
```

`source-inventory.json` 的 `git_files` 与 `injected_contract_files` 分开，`files` 是两者并集。
平台 Dockerfile 必须用此私有 context（包含核过原字节的合同）构建，不能直接 build 裸产品仓库。
不要在这个 context 内 pip install 或运行产生缓存的命令；改动后的 context 会拒绝执行。

## 创建新 scope（不会启动服务）

以下路径、项目名、私有网段和端口必须由协调者先确认无占用；不使用 Dockge 无写权限目录。
`--scope` 必须不存在，其父目录已存在。省略 `--execute` 只显示计划。

```sh
/explicit/tooling/dep-g-venv/bin/python deploy/tianshu/synthetic_init.py \
  --scope /explicit/new-synthetic-scope --manifest /explicit/candidate.json \
  --contracts-root /explicit/private-contract-snapshot --project tianshu-qa-unique \
  --subnet 172.30.89.0/24 --web-port 19443 --execute
```

输出 scope/inputs 及 scope/deployments/source，兼容 J 的 scope 布局，不搬动已有部署。
随机凭据、临时 CA、私钥仅存于受保护合成目录；CA 签名私钥不落盘。应用和日志证书包含
各自 `*.internal` 及 127.0.0.1 SAN；日志文件在 inputs/tls/logs/server.pem、server.key，
CA 在 inputs/tls/ca.pem，供 I 的显式合成配置使用，不改变真实证书策略。
另有 inputs/tls/obs-guard、obs-loki、obs-vector、obs-prometheus、obs-client 各自的
server.pem/server.key；SAN 分别含同名服务及127.0.0.1，共享本次合成 CA。
I 的 settings 将这些显式映射到原 guard.pem/loki.pem/vector.pem/prometheus.pem/client.pem
及其 key，grafana.pem 使用 logs/server.pem，client-ca.pem 使用同一合成 ca.pem。

此后在**这个新 bundle**内按现有 README 的 Linux 权限要求准备挂载：四产品 data/logs
owner 10001:10001，config 对运行 UID/GID 可读且无 world 权限，private 保持操作者 0700/0600。
工具不自动 sudo/chown 宿主目录。核心单独烟测可尚未配置 observability；默认发布检查仍要求
完整九 owner，只有本 Linux 核心烟测显式选择 core_only 权限检查。

需要给 I/J 交付五日志卷时，先通过既有 `release.py configure-observability` 公开入口
配置到 bundle/observability，并按原 1.1.0 五卷权限准备；五卷必须全新为空。
G 只 inspect 本机已有五个日志镜像，不 pull/启动这些容器。缺镜像时执行失败，由协调者
按明确计划先准备。G 不修改日志服务代码/策略，不宣称日志或恢复通过。

## 计划与执行

```sh
/explicit/tooling/dep-g-venv/bin/python deploy/tianshu/linux_validate.py \
  --bundle /explicit/new-synthetic-scope/deployments/source --contexts /explicit/new-contexts

/explicit/tooling/dep-g-venv/bin/python deploy/tianshu/linux_validate.py \
  --bundle /explicit/new-synthetic-scope/deployments/source --contexts /explicit/new-contexts \
  --report-relative reports/linux-executed.json --execute
```

执行仅接受 Unix socket 的本机 Linux daemon；拒绝用户 DOCKER_HOST/CONTEXT/TLS 覆盖，
并在执行时固定已核 socket，避免之后切换 context。G/I/J 共用 .runtime-owner.lock 非阻塞
flock，全程持锁。重复执行 marker、已有项目（含孤儿）、非空产品状态/日志都会拒绝。
镜像 tag 必须尚不存在，避免覆盖其他项目的镜像；第二个合成 scope 使用新 candidate tag。
构建串行，四服务各 1 GiB / 2 CPU / 128 PID，只读根、cap_drop ALL、UID/GID 10001。

默认 liveness 模式没有 provider，也不开网页对话。平台公开 `local issue` 初始化全新库并
签发 config-entry 来源；model 合同要求非空 provider，因此该模式不伪造空配置发布。
仅产品收据校验成功后，才将真实私有 ref 写入 gateway.env 并受控更新 inventory；ref/token
不进入报告。失败/超时/未知结果不重签/不重放；保持 attempt 标记和证据，用全新 scope 重试。

显式合成对话另用一个全新 scope，执行增加 `--scenario synthetic-dialogue`。该模式仅修改
这个被测 candidate 的 Web 标志/合成模型注册（默认仓库样例仍关闭），由真实平台容器
`local publish`、`local issue`，网关容器内运行本工具专用 TLS 合成模型；没有真实模型。
通过真实 Web 登录/CSRF、四服务链和可读发送回执核固定合成回答，不属于浏览器渲染验收。
source/config/observability 投影摘要按受控步骤更新，保留 candidate/unverified，不回填默认例子。

四服务存活后，用独立诊断凭据检查 /health/ready 并确认匿名请求被拒。镜像检查分别记录
linux/amd64、实际 local image ID、实际 RepoDigests（可空）、已安装解释器/依赖、容器 UID/GID。
Memory pip-free venv 用官方基础 Python pip 的 `--python <应用解释器> check` 验证，不向 venv 装 pip。

## 停止与交接

无论成功或失败，只向本次项目且工作目录/服务标签吻合的容器发 SIGTERM，最多观察45秒。
不 down、不删卷/日志、不 prune、不 SIGKILL；未知归属/停止超时保留 stop_unconfirmed。
容器、状态、应用原始日志和去敏步骤报告全部保留；非零退出会使本次校验失败。

reports/runtime-identity.json 遵循固定接口，执行报告绑定其原字节 SHA256。
镜像 ID 不当 registry digest，镜像已 inspect 不代表容器已观测。未配置 obs 的身份仍有
五 owner 占位 null；不能交 I 当完整执行输入。I 开始前必须独立确认 core 正常停止，保留
G 的合成启动日志作为基线；J 必须独立 prepare authority/inventory，再验证九 owner，恢复后不激活。
即使 G 报 stopped，也不替代 J 的正常停写收据。

尚未执行维度必须逐项保留：Linux 镜像构建/运行、Linux 权限及 flock、真实 Compose 生命周期、
Linux 四服务合成对话、日志全链、正常恢复、NAS、真实模型、浏览器。只有实际执行的相应项
才可另附证据；默认样例的 release_ready 仍 false。
