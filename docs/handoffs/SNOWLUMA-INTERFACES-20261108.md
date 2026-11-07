# SnowLuma 统一接口交付

用户已授权推送部署。开发入口见 [接口文档](../development/snowluma-native-interfaces.md)，
README 已提供链接。实现提交 `8cc85e26f364f70452f791919fa75a05fb457c1b`，最终产品
`40e32ff1cc5c35e6710622940664ed4ab7cf416b` 已推送并部署，包含私聊三态、管理员入口、
订阅扫描及最终概览补修 `42251a33`，未覆盖其他窗口已上线能力。

实现复用现有 Actions、Expression、Channels、FileStore 和插件 capability 端口，
不新增业务队列或数据库迁移。固定上游 `f2ea3354f8bbe8672cbb4ae6a2b4e1fbeff44faf`：
222 个动作、6 个别名；213 个动作有执行路径，9 个上游占位动作明确拒绝执行。

59 个不同定向用例通过，复用未变有效结果；全部 222 份输入/输出 schema、223 份官方示例参数、
目录字段完整性及 wheel 资源打包已检查。管理页既有 3 个 execute mock 签名错误已在原代码复现。
未执行真实模型或 QQ 消息测试，不能把框架覆盖当作全部动作的真机验收。

## 上线核验

- 镜像 `sha256:1b048f5e79f13c6076a75b3d86a5c266ec093b91362ce5a21098a13a776216a9`。
  北京时间 2026-11-08 01:05:25 六角色 ready、HTTP200、0 重启、无 OOM；容量守护 healthy，守护和巡检服务 active。
- 20 个受影响安装文件（含原生目录 JSON）与不可变 Git archive 的 SHA256 一致；
  正式页面 5 项 JS/CSS 资源读回匹配。
- 使用实际 Identity 权限只读调用 `ChannelNativeApi.describe`：两条现有 SnowLuma 连接均为 revision 2、
  configured=true，各返回 222 个公开动作、213 个可执行动作、6 个别名；单动作 schema 可读，9 个占位状态准确。
  `channels.native` 的 describe/invoke/get 插件端口登记存在，未发出外部 QQ 或模型请求。
- 配置文件、两条连接配置、15 份媒体配置、模型绑定、原 data_generation、迁移登记、11 个挂载和其他 2 个容器保持。
  原 245 条模型 unknown、4 条 Action unknown 的身份与关键事实摘要逐项保持；频道原无 unknown，本次未新增。
- 切换期间 `memory.extract` 的一个在途模型 attempt 因 `broker_stopping` 新增 unknown，
  一个 `media.acquire` Action 因 `interrupted` 新增 unknown。保留原请求与回执，未重放或冒称业务全部收敛。
  原音乐扫描任务仍为 `ba540bae-25ca-463c-b516-6184900b65fe`、scanning，属于独立媒体业务，未改其任务身份。

完整构建、切换、只读核验和保留对账回执位于本工作树
`.runtime/snowluma-interfaces-release-20261108/final-receipt.json`，新增 unknown 的类型和原因见同目录 `new-unknown.json`。
