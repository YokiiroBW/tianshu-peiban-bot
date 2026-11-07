# 模型图片输入按需压缩

用户要求图片自动压缩并尽量保留细节，同时继续查明数据库连接超时。图像产品提交：`e3087928dc03bc331d413413619c08fe29c2aca1`；基线 `b24eb825`（线上产品 `b3c76eee`）。工作树 `C:/Users/Administrator/.codex/worktrees/model-image-compression/tianshu-peiban-bot`，分支 `codex/model-image-compression-20261107`。本说明是实现交接，不表示已运行验证或部署。

## 实现与复用

- 在现有 Models NativeCompiler 准备线程内调用 `image_preparation.prepare_inline_images`，复用已安装 Pillow、FileStore 和 Broker 的 MediaResolver/prepare_slots。无新依赖、配置、开关、迁移或共享装配。
- 请求总原始媒体超过32MiB才启动图片处理。优先保持全分辨率无损编码；不足时按94/90/86质量编码，仍不足才按原始像素的90%或80%缩放。缩放后的长边低于2048则不缩；不裁切。长截图超WebP编码维度时优先同尺寸PNG/JPEG编码。
- 透明度、视觉方向、原始宽高比例保持；动画不被换成第一帧，带原生签名的部件和整个native_payload不改写。无法满足质量下限及32MiB时仍明确失败，不丢图、不无限降低质量。
- 副本只存在于此次编译的内存读视图。源文件、原MediaRef、冻结ModelRequest、SourceRef、ID、预算、native延续元数据保持；不注册新的公共来源或永久文件。实际编码MIME与字节匹配，wire_digest仍约束真实发送体；原route_selection追加压缩策略、原/副本SHA、字节数、尺寸、质量和比例等无正文事实。
- Memory不再按原图单张/总字节提前丢图或拒绝；元数据有效性和128部件上限保留。32MiB实际载荷由Models准备副本后核对。`_input_budget_record`加入策略代号，供原无step闭合批通过已有恢复路径识别输入准备规则改变。

## 本地静态结果

- 新模块/新测试及protocols、budgets、material_media的Ruff通过。
- 8个改动Python文件AST解析、`git diff --check`通过，已人工审阅变更范围。
- broker、consolidation和原material_media测试文件的既有全文件Ruff告警未扩展修理。
- 实现者没有运行pytest、构建、烟测、真实模型/QQ、生产读写或部署。

## 根集中验证与上线事项

建议必要用例：`tests/models/test_image_preparation.py`、`tests/models/test_native_protocols.py`、`tests/memory/test_material_media.py`，其余只补实际装配/失败引出的受影响检查。

新增行为验证覆盖四种生成协议的实际编码MIME/载荷大小，原图/请求身份保留、逐像素无损文字/alpha、方向、同尺寸高质量编码的灰度误差上界、过小目标不降成缩略图、长截图、动画、混合音频及原来源鉴权。

发布前请用原26图在同Linux镜像依赖上离线准备一次副本，测量原38,023,468字节降到32MiB内后的实际尺寸、质量和耗时；保留原图SHA并验证来源，无模型调用。不能用合成图测试冒称真实图片理解质量验收。

原容量对象 `memory_batch_0b6711a46b7943f99e938f317e9fd37c` 尚无模型step。上线后只有唯一生产写者通过正常Memory恢复入口、当前来源/权限/版本/CAS检查恢复该对象；不修改原failed账本为成功，不重放任何已发或unknown请求，不重置恢复预算。

## 数据库原因另案说明

既定生产协调者只读查到：历史16:06—16:36 Models control池2槽新增5次取连接超时，其他可观测池无新增；Sources重证明、结果提交/统计与轻量状态/心跳共池，借连接5秒与事务内SQL/锁期限存在不匹配。16:33当秒持槽归属没有记录，不能断言某条SQL、连接泄漏、全局PG满或死锁。本卡没有修改数据库连接池或恢复策略。完整证据在根 `.runtime/db-pool-cause-20261107/summary.md`。
