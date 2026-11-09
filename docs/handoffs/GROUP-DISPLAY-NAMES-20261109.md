# 群画像真实群名显示

## 发布结果

产品 `d3071fe095e609067903c94bcef062837c5218d0`，分支 `codex/group-display-names-20261109`，镜像 `sha256:5b6abf20fa11311a0da6ade76343016eaacc019d8a9afb089fa051e3c43cbaf8`，
容器 `9cde0578652454423f58dda707dbe3c32e3cbeb7d41a876eb3eca787ef5a1930`。北京时间 2026-11-09 00:46:17 六角色 ready、HTTP200、0重启/无OOM，
容量守护 healthy，容量守护和独立巡检 active。UI 正常安装激活至序号 12。
4个安装模块和10项当前/旧UI资源SHA匹配，正式入口与当前manifest匹配。

## 修改

原页面已使用会话 label，但配置的 label 是“QQ 群 + 群号”，未读取真实连接器群名。
新增 Channels 的群名称读取，复用 Identity 的已授权会话与 archive_scene_on、原 Actions/Channels 读取账本和现有连接 owner，
通过 get_group_info（Telegram 为 getChat）取得实际名称，校验返回群身份。
缓存复用 channels.observations，按连接、目标、连接 revision 隔离；成功缓存5分钟，失败短缓存15秒；
刷新群会话主动重新读取，不更新 Identity 配置 label、策略、权限、来源或画像内容。

前端独立获取名称，不阻塞画像内容；真实名称优先，群号留在次要行，群名和群号均可搜索。
缺失/失败保留已有名称和号码。角色或分页切换沿用原 useDomainResource 的范围隔离。
原生读取仍按原渠道传输预算执行，不额外设置较短的调用方中断时间。

## 验证及限制

29个后端用例通过，其中11个群名用例覆盖授权/连接隔离、缓存修订、刷新改名、错群响应、失败回退及Telegram群。
另外4个旧夹具失败在7e683357原代码复现（input_id缺失及旧管理页execute签名），未扩大本批修复。
12个桌面/手机用例通过，覆盖真实群名优先、群号保留、两种搜索、刷新改名及原群画像/权限流程。
TypeScript、本地构建及正式Linux镜像构建通过；合入人物存储基线后相关未变用例结果复用，并核对TypeScript。
正式安装后，以现有管理员重新取得Identity上下文，经真实Actions及既有连接owner读取，4个已授权群全部取得真实名称；
首次四群完整查询11.136秒，再次读取命中缓存0.092秒，结果一致；群名加载独立于画像内容。
本地明细 `.runtime/group-display-names-20261109/names-readback.json`，不将群名及凭据提交仓库。
验收浏览器未登录，未声称已登录生产页面视觉验收。

首版49e28fe5的5秒限时在四群并发真实读取时过早取消；单群正式原生读取约4.2秒，
因此补修移除额外限时，沿用原传输预算。两条提交后的元数据读取unknown回执保留，未改写或重放原尝试；
本次重新查询是新的独立只读请求。首次读取失败与补修证据均保留在本地final-receipt。

## 串行发布与保留

发布前检测到另一窗口正在发布人物存储cb4f3aa5，等待其完成后，以该实际线上版本合入本批，未覆盖人物改动。
最终相对上一阶段49e28fe5仅改变group_names.py的调用方限时。无本批新增迁移或业务清理。
配置文件、模型绑定、15项媒体配置、两连接、代际、迁移摘要、12挂载、外层owner及另外2个固定容器保持。
其他窗口临时巡检容器仅观察，未操作，其正常退出不触发产品回滚。
两个切换阶段之前的290模型unknown、59Action unknown原事实逐条保持；全程新增Action unknown为4条，
包含前述2条元数据读取unknown和2条media.acquire interrupted；另有2条memory.extract因broker_stopping转模型unknown，及上述两条读取对应的2条渠道调用unknown，
见本地最终回执、new-actions.json和new-models.json。原回执均保留，未重放，不称整体业务收敛。
未主动发送QQ消息或调用模型，未重放未知效果；原镜像、UI、工作树及根目录其他窗口混合修改保留。
