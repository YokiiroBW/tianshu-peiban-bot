# 首次全新合成安装的授权引导

DEP-G 新增容器入口见 [LINUX-VALIDATION.md](LINUX-VALIDATION.md)：
`synthetic_init.py` 创建新 scope，`linux_validate.py` 默认 plan；显式执行后由平台容器公开 CLI
初始化/签发，合成对话模式才 publish 专用模型。私有 ref 更新与 inventory 绑定、不确定结果
不重试，两种场景分别记录。下文 DEP-E 是既有本地进程路径，不替代 Linux 容器证据。

普通包 `init` 只准备配置/空卷，不产生可用来源，也不启动产品。
本轮自动执行边界是 `tests/release_acceptance/product_stack.py`：四个新数据库、随机私有凭据、
临时CA、loopback HTTPS、明确的 `provider-synthetic`。不能用于旧部署、真实账户或真实模型。

执行顺序：

1. 从清单固定Git提交导出产品快照，逐文件核验；合同按原字节复制并核hash。
2. 根据包模板登记平台operator及服务身份，网页密码由产品支持的scrypt格式保存。
   四个服务只接收各自需要的环境值，网关环境不包含管理员token。
3. 在平台未启动、数据库不存在时，`bootstrap.first_install` 调用产品
   `python -m services.platform --settings <配置> local --credential-env TS_ADMIN_TOKEN publish --input <发布文档>`。
   再调用相同入口的 `issue --input <含entry_id=config-entry的文档>`，真实平台生成来源引用。
   专用entry仅允许gateway→platform/config.snapshot；模型目标只允许显式loopback HTTPS。
4. 验证返回引用格式、带时区过期时间和启动余量；引用只保存在本次网关私有环境。
   尝试标记在产品调用前写入。失败/不确定结果不自动重试publish/issue，不重复制造授权。
5. Memory使用原CLI执行新库 `migrate-profiles`、`migrate-sources` 后启动四个原产品CLI。
   配置加载观测与每个产品自身的鉴权readiness必须同时满足，才开始Web测试。

TS109/110受限续期由真实产品执行：平台 `model_origin_renewal_http=true`、网关
`platform_origin_renewal=true`，默认关闭的两个产品开关在本组合中显式开启。只接受清单绑定的
正式 `model-origin-renewal/v1`；不添加管理员权限、签发接口或TTL参数。
初始ref有效时，产品在原entry范围/总期限内续期同一个ref；到期、撤销或永久认证失败不能自动引导新ref。
部署工具不将初始收据的过期时间当作续期后的寿命，也不根据时间经过推断续期成功；验收使用同一进程
在初始300秒过期后仍完成模型调用及真实投递的证据。连接/续期失败仍由产品拒绝。

`result.json` 只记录发布内容摘要、签发者、初始有效期和剩余预算，不包含来源ref/token/聊天。
短寿命凭据应在服务准备就绪后紧邻启动时签发；已过期或撤销需操作者通过原产品流程重新引导。
没有给正式部署提供自动在线管理员签发，也没有以无限TTL、循环重启或测试issuer替代产品授权。

该运行器的临时配置、CA私钥与SQLite在结束时清理，只保留去敏报告和产品诊断事件。
终止本次测试子进程不等于DEP-F正常停写收据；报告明确列出强制清理，不能用于恢复资格判断。
