# 更新、备份与恢复门禁

截至本轮只读取现有 NAS 元数据，没有执行备份、恢复、迁移、更新或真实消息。实际 resident 为五核心（Platform、Memory、Companion、Gateway、Knowledge）加五观测 owner；容量 guard 为 knowledge-ten。五核心实际 healthy，观测服务只确认 running，没有健康检查不能称 healthy。当前镜像完整 ID/digest 和 Compose/挂载清单保留在操作者本机私有发布证据，不将生产清单复制到公开仓库。

## 执行顺序

1. 先完成公开源码与可达历史审查，使用既有正常 Git 认证推送，读回每个产品和协调仓库的完整 SHA。保留原本地历史、原目录未提交修改与 Chat Audit 现有远端提交；禁止 force push、mirror 或上传整个 BOT 工作区。核对实际配置的 CI：缺少工作流不是 CI 已通过；现有 AssetLibrary 工作流必须按自己的门禁核验。
2. 以已验证远端 SHA 构建候选，记录每个 OCI revision、镜像 ID、registry digest、基础镜像和合同哈希。发布源码成功之前不更新 NAS。Knowledge 虽来自 Memory 仓库，仍是独立运行 owner；当前 Knowledge 镜像来源与 Memory 生产来源不同，不自动替换。AssetLibrary/Chat Audit 未在本次 resident 十服务内，不扩大生产范围。
3. 在原 guard、精确容器 ID、镜像、Compose origin、全部挂载和容量都通过只读复核后，才进入停写窗口。正常停止 capacity 单元并等待 ExecStopPost 完成；逐个确认十 owner 停止、restart 禁用且退出可确认。未知退出、143/137、替换 ID 或磁盘保护异常都不得继续。不得 docker down 或清理无关容器。
4. 做同一停写点的一致备份：Platform 全部角色/alias/模型/家庭等 sidecar；Memory DB 与 source-guard；Companion DB、owner 与发送/unknown 账本；Gateway 账本；Knowledge DB、其检查点与状态；五个观测状态卷（含内部 WAL）、业务归档日志，以及精确 Compose/guard/unit/合同/代码版本。私有配置和证书只能作为受保护备份保留，不能输出、上传 GitHub或在对话中复制。另存封闭文件集、大小、原字节 SHA256 与备份收据；不得以单 DB 或单镜像备份代替。
5. 在此前不存在、保持禁用且隔离外联的目标恢复整组状态，核对文件集合、SQLite integrity/foreign-key、source-guard/DB 配对及 owner/watermark/unknown 事实。核对原当前 authority 未发生进展；旧备份不能复活之后的遗忘、撤销或未知发送。恢复副本须有独立合规配置和显式许可，不能自行生成 grant、复制生产授权冒充合成权限或直接激活原恢复目标。既有 ops/recovery Linux 入口登记的是四产品+五日志、明确 synthetic，不能当作 knowledge-ten 生产恢复已通过；本轮十 owner 实际恢复尚未验证。
6. 只在恢复门禁通过后执行明确关系/alias 迁移。Memory migrate_relationships 是显式停写步骤，旧关系来源或 actor/person 映射不明须保持 pending，不能猜账号、合并范围或清零。新版 main 含尚未部署的 QQ 身份；必须选已核候选并检查现有权限和配置，不盲目以 main 替换生产，不自动新增关系管理凭据或角色授权。
7. 依既有受审更新路径切换固定镜像，保留旧镜像与原配置，建立新的 guard 状态代次并核精确十服务 ID/挂载，回读 guard ready、五核心 readiness、匿名门禁、受权只读、日志持久与 LAN 静态资源。健康探针只证明就绪，不能代替现有用户登录和真实业务验收。QQ/模型消息只在请求范围和既有明确绑定下执行，不能猜橙汐原账号。

## 回滚边界

没有迁移且读写结构经核兼容时，可以只回滚代码镜像并保留当前数据；这必须有固定兼容声明。已有新结构或授权/发送/遗忘等业务进展后，不能只降镜像或覆盖一个旧 DB。整组恢复只能在停写、原 authority 可核、所有状态配对且没有后续进展时按已验证恢复方案执行。若无法证明，保持停写并保留诊断证据，不清除 guard/owner/水位来取得绿灯。

本轮的剩余实质门禁：Git HTTPS 登录及推送读回，FFmpeg 二进制许可范围处理，候选 Linux 镜像/CI，十 owner 恢复演练与现有生产权限/迁移预检。完成这些之前本清单不授权工具自行越过门禁。
