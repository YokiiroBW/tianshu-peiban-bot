# CR11/13/14/15/17 能力域交接

状态：能力主体已实现，`implemented_not_tested`；渠道 C 另卡继续。基线 e034276dfb757b7dd64cd2f56e952d81193a5248；分支 codex/cr-capabilities；专属目录 src/tianshu/capabilities、packages/tianshu-plugin、tests/capabilities。

## 稳定的跨域端口

正式定义为 `tianshu.capabilities.ports`，值类型为 `tianshu.capabilities.dto`；Action/Attempt/Receipt/MediaRef/ArtifactRef/SourceRef/Coverage 一律消费 `tianshu.types`。

- AuthorityPort.check(context,source_refs=())：实时授权/来源复核，异常即拒绝，能力域不猜允许。
- SourcePort.resolve(context,MediaRef) 返回 (实际bytes,MIME)；协议端路径/文件ID/临时URL由对应来源适配器解释。
- SourcePort.capture(context,locator=...,raw=...,mime_type=...) 返回 SourceRef。locator 保留原URL、作者/楼层、获取时间、版本和原事件身份；来源域拥有其修订/遗忘语义。
- SourcePort.read(context,SourceRef,requested) 返回实际原件/表示及已覆盖范围；能力层不能把搜索摘要登记成全文。
- LifeSnapshotPort.scene(context,activity_id=None)：返回 SceneSnapshot 对应字段，天气包含 observed_at/expires_at/source；未知天气返回 null。生活域拥有活动与日程当前状态。
- LifeSnapshotPort.sharing(context,action_id=...,window=...)：返回 {decision:send|defer|archive|cancel,target_ref,due_at,reason}。生成完成不代表立即分享；target_ref 必须已由可信身份确定。
- ExpressionPort.send(context,ExpressionPlan) 返回 Receipt。只有表达/渠道出口真正发送，capabilities 不另发 QQ。
- ResultEvents.publish(connection,event)：使用动作结果的同一 PG 事务发布一次 capability.result；事件稳定身份为 receipt_id。Memory/Life 按自己的消费水位幂等消费，不建立第二动作队列。
- 模型/外部资源统一 Broker；Fish/Comfy/NAI/GPT 调用 `Broker.resource(ExternalRequest)`，提交/完成/unknown 由 permit 持久记录，不建局部账户/GPU semaphore。

## HTTP 约定

公开 POST `/api/v1/{plugins,content,images,speech,reactions,media,books,music,comics,devices,infrastructure}/{operation}`。读取返回真实领域 projection；命令唯一返回 foundation CommandResult `{client_request_id,revision,action_id,attempt_id,receipt,data}`，分页统一 Page。写入带 client_request_id，已有对象修改带 expected_revision。Cookie/CSRF/TrustedContext 由 identity 提供，不读取请求体自称权限。

原 media/books/music/comics 与 plugins 的有效 operation/public projection 迁入保留；去掉旧跨产品信封与旧 API 自动回退。插件自定义页 view-content 返回实际HTML/资源及限定bridge，不把页面HTML当JSON业务状态。

新域操作：

| 域 | 读取/操作 |
|---|---|
| speech | view、connections.save/connect/refresh/disconnect、voices.search/clone、profiles.save、preview/synthesize/transcribe、dictionaries.save |
| reactions | view、packs.import/export、assets.update/delete、index、annotate、search |
| images | view、engines.save、workflows.import、profiles.save、preview/generate、jobs/get/cancel/reconcile、album |
| content | view、discover、read、continue、video.observe |
| capabilities | discover/invoke/get/cancel/reconcile；upload；GET media/{media_id}原件 |

图包导入 body `{name,actor_ids,scope_ids,files:[{filename,media:MediaRef}]}`；小图仍接受 data_base64，ZIP优先 archive_media，亦接受小 archive_base64。packs.update 支持 name/enabled/actor_ids/scope_ids，assets.batch 以 items:[{id,expected_revision,description?,ocr?,actions?,purposes?,tags?,enabled?}] 同事务分类。普通上传不自动成为共享表情。语音试听 `{profile_id,text,connection_id?,model?,voice_id?}`；转写提供 MediaRef 和原 source_event_id。图像预览/生成 `{actor_id,engine_id,profile_id,prompt,purpose:selfie|creation,share,scene_revision?,outfit_id?,activity_id?,reference_ids?}`；真实触发来源从TrustedContext取得。

## 新投影字段

- speech/view：connections[{id,revision,transport:mcp|native,enabled,configured,account_id,team_id,workspace_id,quota_group,billing_source:plus|api|free,capabilities:{tts,asr,stream,timestamps,voices,clone,dictionaries},balance:null|{value,unit,observed_at},state,reason}]；profiles[{id,revision,actor_id,connection_id,voice_id,model,language,output_mode,dictionary_ids,vocal_defaults}]；dictionaries[{id,revision,name,entries:[{text,pronunciation}],provider_id,provider_version}]；default_route:null|{connection_id,model}。
- speech voices.search：{items:[{id,name,preview:MediaRef|null,visibility,owner_id}],next_cursor}；preview/synthesize 的 data 为 {artifacts:[ArtifactRef],alignment,spoken_text,model,billing_source,usage:null|真实返回}。
- reactions/view：{packs:[{id,revision,name,actor_ids,scope_ids,asset_count}],assets:[{id,revision,pack_id,media:MediaRef,description,ocr,actions,purposes,animated,index_state,embedding_profile}],index:{profile:null|string,pending,failed},next_cursor}。
- images/view：{engines:[{id,revision,engine:comfyui|nai|gpt_image,enabled,configured,model,quota_group,capabilities,state,reason}],profiles:[{id,revision,actor_id,engine_id,workflow_id,character,references,outfit_defaults}],workflows:[{id,revision,name,bindings,outputs}],identity:null|{actor_id,references},outfits,scene:null|SceneSnapshot,jobs:[{action_id,attempt_id,execution,effect,artifacts,reason}],album:[ArtifactRef]}。
- images/preview：{scene:SceneSnapshot,intent:ImageIntent,engine_id,compiled:{prompt,negative_prompt,parameters,references},warnings:[]}。
- GET /api/v1/capabilities/media/{media_id}：同源鉴权和来源/范围复核后返回实际字节；POST capabilities/upload 的 data={artifact:ArtifactRef}，不暴露本机路径/秘密或供应商永久链接。

## 验证边界

用户要求全部集成后统一验证，因此未运行测试/build/typecheck/真实模型或任何收费调用。源码实现不代表外部实机验收。已写定向用例，未执行；下述静态核对不计测试通过。


## 已实现的业务与复用

- CR11：单一 Actions/Catalog，原生 PG Action/Attempt/Receipt、稳定意图幂等、同事务 Procrastinate 与结果事件、取消及未知恢复。迁入成熟插件包安装/隔离依赖/stdio RPC/实例修订/角色绑定/动作/命令/工具/事件/页面/定时任务/升级回滚；SDK public import 仍为 `tianshu_plugin`（2.0.0）。插件只有 worker 进程拥有宿主，interactive 查询与真实提交 Action。
- CR13B：Fish Plus OAuth PKCE + Streamable HTTP MCP 动态 tools/list/inputSchema；原生 HTTP/MessagePack、SSE 累积时间轴、WS、s2-pro/s2.1-pro/s2.1-pro-free/drama3、固定 voice/reference、文件 ASR、私有克隆、音色检索、词典。另保留显式 Edge/OpenAI compatible 路线。不会因失败切另一个账户或模型。books 保留章节缓存、分段、播放时间轴、ffmpeg 拼接与进度，仅实际每段合成进入同一 speech Action/Broker。
- CR13A/C、CR15：图片/音频/视频的实际 ModelPart，GIF 原件与首尾在内多帧采样；实际视频按最多30秒页解码真实帧/音频/字幕、保留时间和缺失/继续位置及分析产物。ConsumptionReceipt.analysis_completed 是执行证据，不保证理解正确。图包保留 GIF、导入/导出/修订/批量分类/启停/去重/实际标注与单1024 embedding profile语义筛选。
- CR14：SearXNG、现有媒体目录及 B站搜索候选；真实网页/论坛正文、原作者/楼层/引用/分页与 Discourse；搜索摘要始终未读候选。B站实际原件通过已复用 yt-dlp/Bilibili 连接器受控获取与 Sources 登记，分页视频必须明确 CID。
- CR17A/B：冻结 Life 场景、活动/时间/天气有效期/穿搭/角色参考后，实际 Broker image.plan 只规划姿态/表情/镜头/光线；Comfy 受审 API 图与类型绑定、NAI 原生 ZIP/API、GPT Image JSON或multipart编辑均有真实适配。稳定UUID及服务端实际句柄分开登记；Comfy查询/取消不占GPU生成位。delivery_kind 明确 create_only/share_candidate/requested_delivery，能力不自行发送。
- CR17：成熟 B站订阅/下载/元数据/去重/整理/媒体服务器或外部资产发布；音乐多源获取/解码/标签/歌词/质量比较/Navidrome；Legado规则/仓库/书源/本地TXT及EPUB/章节/订阅/听书；Suwayomi/扩展/CBZ/Komga/漫画文件插件；基础设施现有节点协议/真实远端控制与观察；Home Assistant固定灯/开关模板的目标状态实读；GSCore真实语义命令与首片段协议。没有把这些换成通用 CRUD。

迁入来源固定为 execution/sources JSON 的 Platform 21bdb7b、Companion 72d2854、R67 application d352b92。LegadoParser 许可证与精确源码来源保留在 media/books/_vendor。新产品不导入原产品包、不依赖 projects、无 SQLite/qmark 转译或双后端路径。

## 装配、事件和读取

`module.build_module(runtime, *, settings, authority, sources, life, expression=None, result_events=None, authorize_actor=None, actor_directory=None, worker=None)` 返回 `(DomainModule, Capabilities)`。settings 必须只传 `runtime.settings.document.get('capabilities', {})`；worker 归属以真实 runtime.role 为准。先构造不依赖 Actions 的 LifePresentation，再构造能力/Channels/Expression 等，由根注入 Expression 和事件消费者。能力 start 在迁移后创建成熟 owner；register_tasks 在 readiness 前登记完整统一任务集合。schema.sql 由 foundation 总迁移注册，不由构造函数执行 DDL。

A/B 额外用例直接 `capabilities.catalog.register(handler)`，handler.definition=CapabilityDefinition；async availability(context)、execute(Action,Attempt)、reconcile(Action,Attempt)、cancel(Action,Attempt) 均返回正式类型。不存在第二工具目录。工具名必须合法，model_tool={name}，public_command={name,aliases}，long_running 明确影响 worker 份额。短前台工具用统一 Procrastinate INTERACTIVE，长图像/语音/下载/听书 ELASTIC，DUE只用于真正到期任务；根基础依赖6a02a97提供三份真实消费者。

Actions.get 返回 `{action:wire dict,attempt:wire dict,receipt:wire dict,result_revision:int,result_observed_at:aware datetime|None}`。HTTP转ISO；pending初始revision0不代表完成。save 持行锁递增实际结果修订，重放不改变观察时间，同一已确认结果不会被晚到pending/running/unknown覆盖。事件为 `{type:'capability.result',version:1,event_id:receipt_id,action_id,result_revision,result_observed_at:ISO,context:wire,source_refs:wire,receipt:wire}`，同事务发布终态变化。Memory以原action身份修订一个episode，不能把unknown与恢复成功计为两次经历。

顶层 Receipt.result.source_refs 汇总正式返回source/source_refs、consumption.source与artifact来源；读取/重放均重新核权，不扫描任意JSON的id。SDK显式 messages.present 固定 expression_id 后调用 Expression.send，回填真实 delivery；普通生成只给产物，由B消费一次表达。

`speech.expression_profile(ctx)` 返回 `{profile_id,revision,output_mode:text|audio|both|natural}`或None；固定角色ID默认profile/角色唯一profile，多个未选默认不猜音色。ReadingAccess.read 保持 active/grant_current/observed_at/catalog 形状，且明确人类阅读位置、75秒共享窗口、私聊和准确范围；有效excerpt通过Sources登记 human_reading_observation 来源，不能写成BOT完整读过。

## 部署设置与实际 API

能力 settings 子树：

- state_directory：绝对持久目录（默认 FileStore.root.parent/capabilities）；file_workers 默认2，只用于有界文件操作，与短PG仓储线程和网络/受管子进程分开。
- media：enabled/staging_directory/package_contract_directory（默认内置契约）/engine{python,ffmpeg,ffprobe,timeout_seconds,max_bytes}/targets/storage_mappings/import_roots/scan_page_limit/scan_item_limit/scan_timeout_seconds/workers；成熟媒体配置字段保留，凭据通过原安全配置或密封API写入。
- plugins：package_directory、comic_files{runtime_socket,defaults?}、resources[instance_id]{quota_group,provider_id,account_id}。物理资源组必须已在Broker登记；http.client是实际外部工具，也经Broker许可。配置缺失不另生本地池。
- infrastructure：directory/contract_directory/executor{command,directory}/control{contract_directory,executor}/poll_interval_seconds/stale_after_seconds/timeout_ms/platform_node_id；节点的真实外部账户/设备仍由原协议守护。
- speech/images/reactions/devices/content/game 的连接、profile、词典和素材由领域 PG 配置/密封凭据保存，不把全根设置或明文secret散发给插件。API秘密字段只写不回显，命令原文敏感输入加密存储，Action只保留稳定引用和摘要。

`POST /api/v1/capabilities/upload` 是受控 multipart（client_request_id首字段、file，默认最大512MiB）；先FileStore.put_async真实封存对象→Sources.register_media→Action登记artifact。不得用用户自报SHA认领原件。`GET|HEAD /api/v1/capabilities/media/{media_id}` 对 artifact: 与 source: 显式按归属授权，支持真实MIME/长度/Range206、416、ETag；范围/304前先核权，不跨域not_found回退，不绕HTTP解析自己原件。前端请求actor_id/conversation_id选择器由A核实，禁止scope/audience自报。

插件ZIP可先上传，再 install.source={type:'artifact',media:MediaRef}（≤6MiB）；图包 archive_media；books/library/import 支持 filename+media（≤64MiB）。旧小base64仅保留作为实际小件参数，不承担长音视频上传。插件 view-content 返回一次受权ticket URL，GET /plugins/page/{ticket}输出真实HTML/CSP；books/comics文件由同域files路径返回受控FileResponse。

系统公开 `POST /api/v1/system/{view,tasks,resources,logs}`，config.publish权限，真实委托runtime.diagnostics，logs带start_ns/end_ns/role?/limit?，不另造任务/日志账本。设备 view/connections.save/refresh/control，游戏 connections.save/query。其他成熟操作的完整枚举为 operations.MATURE_OPERATIONS（下表）。

| 域 | 正式 operation |
|---|---|
| media | accounts/check, accounts/import, accounts/qr/poll, accounts/qr/start, accounts/revoke, enqueue, jobs/control, jobs/delete, jobs/detail, jobs/list, jobs/metadata, jobs/refresh_metadata, resolve, rules/preview, subscriptions/control, subscriptions/destination-preview, subscriptions/save, subscriptions/scan, targets/remove, targets/save, targets/test, view |
| books | audio/control, audio/estimate, audio/generate, audio/manifest, audio/retry, audio/settings, audio/settings/save, audio/view, chapters, detail, downloads/create, feeds/control, feeds/refresh, feeds/save, library/import, library/rebuild, library/view, metadata/apply, metadata/search, preview, reading/close, reading/open, reading/progress, reading/view, search/cancel, search/results, search/start, settings/save, sources/control, sources/export, sources/import, sources/list, subscriptions/control, subscriptions/save, subscriptions/scan, view, warehouse/browse, warehouse/detail, warehouse/subscribe |
| music | accounts/qr/poll, accounts/qr/start, accounts/revoke, library/link, library/scan, library/view, maintenance/save, navidrome/disable, navidrome/playlists/control, navidrome/playlists/save, navidrome/save, navidrome/test, playlists/control, playlists/detail, playlists/download, playlists/downloads, playlists/export, playlists/list, playlists/scan, playlists/subscribe, settings/inspect, settings/save, view |
| comics | connections/probe, detail, downloads/enqueue, extensions/control, extensions/view, imports/browse, imports/enqueue, imports/inspect, library/view, metadata/save, search/cancel, search/results, search/start, settings/save, sources/control, sources/list, sources/refresh, subscriptions/control, subscriptions/save, subscriptions/scan, view |
| infrastructure | catalog/refresh, container, containers, control/action, control/detail, control/execute, control/operation, control/operations, control/plan, control/view, host, logs, logs/refresh, node, refresh, save, test, view |


## 已做的静态核对与真实剩余

已静态解析能力/SDK/必要用例的Python AST与JSON，核对相对模块存在；扫描旧产品导入、SQLite、旧/api/web路径、SQL占位和迁入JSON表达式；人工检查关键调用链与diff。没有运行pytest/unittest、build、typecheck、浏览器、数据库迁移、网络供应商或真实设备/消息。定向用例是待CR19执行的材料，不是通过证据。

源码已保留的真实限制：Legado webView浏览器脚本仍是原实现明确不支持（普通HTTP规则有效）；GSCore只保证真实首片段，complete=false，不冒称整段处理完成；MCP工具参数来自实际连接schema，不支持的reference/private字段准确拒绝；供应商没有查询句柄的丢失结果保持unknown，不能盲重生。Comfy/NAI/GPT/Fish未配置账号或quota_group为not_configured，均有实际适配，未标成未实现。没有账号的引擎本轮未做真实调用验收。

跨域实际装配、原生PG执行、打包静态资源、三队列混合负载/取消、A/B来源与Expression、channels OneBot/TG/外宿主桥、真实场景均由完整集成后的CR19集中验证；C组在本主体提交后继续。根pyproject/lock/总迁移/域启动由foundation单写，能力提交不包含共享文件。


取消控制补充：API持久写cancel_requested并请求同一Procrastinate原job abort；实际运行Action有短周期控制观察，取消传到真正的async I/O/受管ffmpeg或SDK plugin.cancel。插件reconcile/孤立cancel经统一interactive控制任务交唯一worker宿主，interactive进程不启动第二Host。安装包的封存/原子目录操作会先等已开始的本地文件步骤收尾，不把尚未停下的工作显示为已取消。显式resume新Attempt清除旧取消标记；unknown不自动续写或重收费。

本主体依赖链（只供集成人核对，不把这些文件重复纳入能力提交）：ec2fc7c、84ad865、220249b、94e2020、7f6ffc8、9864139、7acd976、6a02a97，均已在本工作树消费。后续B最终合同与C独立提交另行交接。


## 后续收口（仍 implemented_not_tested）

- Catalog.discover 自身刷新动态插件定义并按角色过滤；Actions 唯一入口保证 PluginHost 只在 worker 执行，短插件使用同一 Procrastinate interactive 份额。
- `await capabilities.prepare()` 只装配真实 owner、绑定稳定 `capabilities.reading` 和注册目录；`initialize()` 才启动 worker 宿主/后台。先 prepare，再构造 B 的 ReadingService/Expression/Conversation 和登记所有任务，最后启动。`bind_expression` / `bind_result_events` 为晚绑定正式端口。
- `images.generate`/preview 的 `intent` 是 ImageIntent；独立 `photo_intent` 是 PhotoIntent，字段 trigger/purpose/motivation/window/dedup_key/target_ref/artifact_ids/delivery_kind。B 冻结授权目标/时间窗作为输入，能力执行前保存原值，重对账不丢 requested_delivery；明确交付不经过普通随兴分享筛选。输出 delivery 始终 not_requested，由唯一 Expression 发送。
- 明确取消会中断实际 I/O 并调用原供应商 handle 的 cancel/reconcile；普通连接中断仍 unknown。Comfy 运行中只使用支持原 handle 的定向取消，旧端不调用影响其他任务的全局 interrupt。
- `Actions.resume(...,client_request_id=...)` 在同事务持久登记恢复命令→原新 attempt；同命令重投不会再创建计费尝试。
- `await capabilities.withdraw_sources(connection,refs)` 是 A 撤回事务内钩子：锁定来源派生写入、清理素材逻辑引用/语义索引/语音缓存/分析内容并标记未执行及进行中动作，晚到结果只能保留真实 effect/usage/身份。FileStore 共享字节不删除。所有 artifact 写入、语音缓存、分析与结果落账重新核来源；来源事务与派生写入统一使用 SourcePort.validate_persistence(conn,ctx,refs) 的权威 FOR SHARE/FOR UPDATE 顺序，不借第二连接或另建来源锁。按 source_dependencies 索引定向传播，不扫描全域正文。
- 配置与 Plugin Vault 复用 runtime.credentials.seal_bytes/unseal_bytes，外置唯一 master + 精确 domain/reference AAD，不再生成本地 key 文件。无旧密文兼容。
- 新编排源码以 formatter 展开为正常多行；未运行测试/build/typecheck/真实服务。

- 撤回精确匹配 source_id/revision，并复用 sources.dependencies 的片段相交规则；dependants 用单次原生 JSON recordset 批写。register_dependant 可直接注入批量 withdraw_sources(conn,refs,reason)。语音表达的 profile_revision 在实际执行前精确核对。Actions.load_for_execution 仅给已登记可信执行器恢复原Action/Attempt；跨进程取消会读原dispatch证据，不能把远端已提交误记not_started。

- 上传命令data正式包含artifact与media/source_ref；media是Sources.read回读的规范source:引用，用于人类聊天InputPart；artifact.media供能力产物/预览，不能作为客户端原件重新认领。表情编辑批量原子操作使用同conn Artifact读取，并先一次复核全部选中source再锁asset，不嵌套借池。视频required image映射Broker vision，继续仅使用ConsumptionReceipt.next_position覆盖start/end。


## 受控浏览器与输入证据补充（implemented_not_tested）

构造入口新增可选 `artifacts=唯一Artifacts实例`；根在 Identity/Sources 后创建一次并同时注入 B LifePresentation 与本模块，本模块不会另建实例。既有不注入的独立构造仍会创建真实 Artifacts。

`Action.current_input_refs` 完整 codec 持久化；Actions.invoke 按完整 SourceRef wire（包含 locator）核验它是已授权 source_refs 的子集。HTTP body 的同名字段不能提升为可信输入，主动/后台默认空。待集成用例覆盖 locator 扩大与 delivery_allowed 缺省拒绝。

`content.read` 参数增加 `render:auto|http|browser` 与 `session_id?`。真实 Playwright Chromium 读取在 `content/browser.py`：只允许已核公开 URL 与配置精确 origin 的 GET/HEAD/OPTIONS，请求资源有界，独立临时 context 重用封存 storage_state，取消关闭真实 context/browser/Playwright。提取真实渲染DOM、登记 rendered_dom 来源；HTTP静态正文仍优先。跨origin资源须由browser_origins显式列出，写入型POST应用不冒称已读。缺包/缺Chromium分别明确 unavailable，运行时不下载安装。

`content.providers.save` 支持 `browser_origins:[精确origin]`、`browser_selector?`、`browser_storage_state:{cookies,origins}`（秘密字段仅写），由已授权角色/范围/受众冻结访问。state密封到配置secret，Action仅保存sealed command引用；并发更新不会用旧浏览器会话覆盖新配置。根依赖锁Playwright，镜像构建执行匹配版本 `playwright install --with-deps chromium`。能力设置 `content.browser={enabled:true,contexts:1,timeout_seconds:35,chromium_sandbox:true,executable_path?:绝对路径}`。没有运行浏览器或任何测试。


## C 同批能力收口与天气（implemented_not_tested）

Browser公开静态CDN script/css/font/image可加载：独立DummyCookieJar客户端仅公开GET、去Cookie/Authorization、丢Set-Cookie后fulfill；文档导航和带账号请求仍只在明确origin。resources有数量/字节/时间边界，取消实际关闭全部I/O/进程。配置默认已能读取普通CDN脚本站，不要求新UI审批。

content.read登记真实HTML/DOM原件，另以支持该原件的normalized_text来源登记规范正文；result.source为规范正文，original_source为原件，source_refs都保留，Sources.read后续是真实可读正文。content.continue请求requested={position,max_characters}、next_position=int|null；网页分页另以continuation={capability_id:content.read,arguments:{url:实际next_url,mode:continue,render,session_id?}}描述。视频继续仍唯一ConsumptionReceipt.next_position，未混成字符位置。VideoObservation.reconcile按原Attempt取消费记录并授权读回原analysis_artifact_id，恢复analysis/Artifacts/model_attempt_id与原范围，不调用第二次模型。

WeatherService(runtime,*,authority,sources,configuration=None,models=None)可独立在Life前构造真实实例；caps.build_module新增weather=同实例并复用其唯一Configuration，构造时bind_actions真实登记weather.search/current。Life仍唯一保存weather_location，天气服务不复制角色绑定。观察真实QWeather Geo与/weather/v1/current坐标，专属Host/API-Key密封、128KiB/8秒、实际时区/归因/摄氏单位；所有外部QWeather读取经Broker.resource function=weather.search/weather.current、同Action/Attempt，已配置quota_group才提交。5分钟同一Action缓存复用，失败旧值明确stale且保留旧fetched/expires，observed_at始终null（v1未给观测时间）。Source.capture TOOL_RESULT保实际天气原JSON和源refs，天气事实需B并入Scene来源。

正式读POST /api/v1/weather/view→{connection:{id:qweather,revision,host?,quota_group?,account_id?,configured,enabled},configured,reason,server_time}；search {query}→{items:[{id,name,adm1,adm2,country,tz,utc_offset,latitude,longitude}],source_refs}；current {location_id}→{location,weather:{temp,feels_like,text,icon,wind_scale,attributions,observed_at:null},description,observed_at:null,fetched_at,expires_at,stale,code,source_refs}。未配置observe返回None，缺实时数据weather=null/真实code。连接写POST /api/v1/weather/connections.save={client_request_id,host,api_key?只写,clear_credential?,quota_group,account_id?,expected_revision,enabled?}返回统一CommandResult，权限external.manage。小端口weather.observe(ctx,location_id)为同投影但时间返回aware datetime。测试材料未执行；无真实和风Key调用。

HTTP全部actor/person/conversation selector只由A在query授权，body剥离，不误进工具schema，选择角色不附加config.publish。handler原permissions仍Actions实际复核。上传/原件/读取/命令使用同真实会话场景。


## 唯一Catalog角色策略与数据manifest来源（implemented_not_tested）

旧skills/native已由同一Catalog真实领域工具替代，旧skills/sources的有效数据发现与逐角色启停现在由CatalogConfiguration提供，绝不迁旧SkillRegistry/内部平台桥。Catalog.discover直接投影，Actions.invoke与尚未执行Action进入run前检查相同actor policy；resource外部协议另在TrackedPermit.submitted前复核。已开始执行（包括原生Model.generate在Broker内排队）的任务保持原回执与显式cancel，不承诺翻开关自动撤回已执行调用。B companion.actors.capabilities必须删除，不另取交集；插件安装/binding/供应商可用性依然是真实执行条件。配置管理入口本身不能被角色工具开关锁死。

正式POST /api/v1/capabilities/catalog/view（读）→{actor_id,revision,items:[{definition,availability,can_execute,enabled,source_ids}],sources}；/catalog/settings.save={client_request_id,expected_revision,capabilities:{canonical_id:{enabled:bool}}}只改启停；/catalog/sources.save={client_request_id,source_id,name?,manifest_url,enabled?,expected_sha256?,quota_group,token?只写,expected_revision}；/catalog/sources.refresh={client_request_id,source_id}。写均统一CommandResult与config.publish。新装无旧JSON/registry兼容。

来源manifest是受界64KiB JSON：{version?,capabilities:[{id,title?,description?,input_schema?,output_schema?}]}，最多256条、schema真实校验、sha pin、固定URL不跟重定向、token密封且只发该来源；刷新经过唯一Broker.resource function=catalog.sources.refresh。失败保存真实invalid/unreachable/error_code/last_refreshed_at，保上次有效目录但不称刚刚刷新成功。来源实际JSON登记TOOL_RESULT并在同Source事务索引依赖，撤回精确版本/片段清其entries/refs；actor/scope/audience与Source授权决定可见。

同canonical ID的声明只追加发现来源，不覆盖已安装工具的可信schema/description/权限；禁用发现源不等同卸载既有native/插件。未知adapter仅目录展示implemented=false/adapter_not_installed，不创建占位handler或远程导入代码。真正执行仍只允许已安装同一Catalog.handlers；source.config不是第二套模型/供应商配置，领域连接继续由各领域实际配置所有。

没有执行测试或来源网络请求；tests/capabilities/test_catalog_policy.py待集中验证，覆盖目录禁用后直接invoke拒绝、manifest不能覆盖真实schema或凭元数据安装执行器。C渠道Operation编排移到channels/operations.py，socket owner/service仍单独负责真实生命周期。


`POST /api/v1/capabilities/find` 是只读恢复原提交：{capability_id,client_request_id}，按当前principal+canonical capability+原intent_key的既有唯一索引查原Action，再走Actions.get原角色/范围/来源核验；存在返回与get同投影，没找到JSON null。不创建或重发请求，不要求config.publish；UI必须保留原三selector，不自行算hash。


## AssetLibrary真实只读资源（implemented_not_tested）

冻结Platform assets/web_assets/web_asset_queries有效实现已收敛到capabilities.resources：AssetLink五个真实公开读libraries.list/libraries.get/entries.browse/entries.get/assets.search，以固定HTTPS /assetlink/v1/control、独立密封token/可选CA、5秒/64KiB请求/1MiB响应、不跟重定向/不使用环境代理，精确request_id/control.result校验。原纯分页、cursor principal/connection/query绑定、相对路径、跨库结果拒绝与索引元数据投影复用；不读AssetLibrary私库、不复制其ACL或索引，不发写操作。此上游协议无媒体端口，preview.available=false/original_available=not_verified是实际功能边界。

POST /api/v1/resources/assets/{view,libraries,library,browse,search,entry}是读projection；后五项必须connection_id与实际public query。单一Catalog能力ID resources.assets.{libraries,library,browse,search,entry}与合法model_tool；权限沿用asset.read（或既有*），每次身份/显式连接收窄/上游独立权限复核，返回原TOOL_RESULT来源source_refs。实际HTTP仍唯一Broker.resource function=resources.assets.read，按连接quota_group准入，后台不会另建HTTP容量池。

配置写 assets/connections.save={client_request_id,id,name?,endpoint,token?只写,clear_credential?,quota_group,ca_file?,allowed_principals?,actor_ids?,expected_revision,enabled?}，external.manage，统一CommandResult；token进入唯一Configuration密封secret。HTTP不接受任意credential_ref借用其他凭据/部署主密钥。view给旧PageState中的connections/page_size/sort/kinds/search_scopes/preview及can_manage/configurations，选择连接只属于前端浏览状态，不授予权限；每次读显式connection_id，服务器重新核对。

渠道连接token_ref同样只允许自身sealed:channels:<id>命名空间；配置变化会刷新真实transport且在实际网络提交前核revision/原目标，不能在五秒owner刷新窗口向旧endpoint发送新内容。原首批根控制socket只是部署接线缺口，漫画两个实际worker业务main已在media.comics.source_service / publisher_service，可由runtime受管启动，不依赖旧BusinessHost。

## 四目录界面实际接线（implemented_not_tested）

- 插件中心：发现/安装（ZIP流式上传后传产物引用、Git固定提交、服务器开发目录）、创建/配置/密钥清除/启停/升级/卸载；逐角色绑定及覆盖参数；角色动作表单/自定义HTML页面/文件选择上传；原任务查询、明确取消/核对/续接、日志与周期任务读取。管理选择器明确global，动作选择已登记角色，只有同角色当前会话才带会话/person。
- 媒体中心：保留下载/账号/订阅/整理/书籍/音乐/漫画原有效表单与真实业务投影；媒体命令使用已声明操作类型和统一CommandResult/原Action轮询。书籍导入改multipart；听书只选择唯一Speech.voice_profile（含实际版本），删除平行语音密钥/音色配置；共享阅读只绑定当前可信私聊，关闭/后台状态明确同步。原件audio保持原生同源URL/Range播放。
- 基础设施：Docker/OpenWrt/受管Dockge读取与控制明确global；写入用统一Action monitor；原控制操作已有client_request_id只读恢复，不重新发送。显示原节点读回时间/未知/不可用，不把配置存在称已接通。
- 资产库：管理员可登记/修订HTTPS AssetLink连接与密封令牌；页面本地选择连接后按真实五类只读协议列库、翻页、浏览目录、搜索、读取详情；每次服务器重新核权，旧generation/退出登录清显示。协议没有原件端口，预览保持不可用。

以上仅完成源码与契约/diff核对，未运行测试、typecheck、build或浏览器；真实账号、外部节点、NAS媒体、插件进程及付费模型仍待根集中验收。

## Action执行所有者与绝对期限（implemented_not_tested）

`Actions.start/close/status` 由现有 capabilities.initialize/close 接线。每个实际进程有唯一PG execution_owners租约，运行前同一CAS绑定原Attempt与owner；短暂DB失联后自我围栏并取消实际本地任务，停止续租的owner过期后才扫描其running任务。同一个活interactive owner不会被worker恢复；只把原身份置unknown，存在原provider_handle时通过同一Procrastinate control原句柄核对，不自动重新execute/新建Attempt。提供者仍running的ownerless核对投影不冒充本地执行owner。

run保护覆盖handler加载/authority/availability及实际执行；拒绝后有终态回执，Action绝对deadline约束整个等待/外部调用，期限取消原I/O并有界调用原provider.cancel，无法确认的结果仍unknown。普通HTTP断开/停机不伪称外部取消。close也处理prepare完成而initialize尚未成功的文件执行器与owner资源。新增关键故障用例未运行；无后台第二业务队列。

## Fish协议收口（implemented_not_tested）

Fish MCP SSE取得匹配JSON-RPC id的结果后立即退出并关闭response，不等待服务器长连接EOF；普通JSON也核匹配id。MCP/native新增协议文件已整理多行。OAuth同连接跨进程刷新通过PG短租约合并，网络期间不持PG连接；等待方重读已密封新token，配置CAS冲突只在同一旧凭据仍未变化时合并。旋转令牌提交后失联/owner过期保持unknown并要求原连接重新授权，绝不重复旧refresh token盲重试；明确新授权可解除该状态。SSE长连接及失联刷新用例已写未运行。

Owner续租仅允许原租约尚未过期且未停止；过期身份永久不能续命，新进程必须新id。外部resource真实submitted之前/之后、实际网络写之前会在同control连接核原Action+Attempt仍属本owner且PG租约存活；失去归属时拒绝尚未使用的permit。原生Model等待则由owner丢失时取消实际Client请求，晚到真实结果仍按原Attempt保存/核对，不以旧owner复活准许新提交。

## Chat Audit真实历史读取（implemented_not_tested）

原有效`chat_history.py`/chat skill已折入唯一Catalog：`resources.archive.search/read`，模型工具名保留`chat_history_search/read`，权限memory.read。固定HTTPS档案根地址实际调用`/internal/evidence/search`、`read-locator`、`check`，严格复用原外部JSON schema，15秒/2MB响应/最多100记录；opaque摘要只与档案真实check结果比较，不猜本地text摘要。每次结果再经A `Sources.register_archive` 当前会话/真实账号/目标/别名/已忘屏障核权，完整原SourceRef贯穿Action结果。

新增配置kind archive_connection保留revision/自有密封token/CA/quota_group/mappings。mappings每项为 `{mapping_id,conversation_id,connection_id,channel:{namespace:'qq',binding_id,channel_conversation_id,thread_id:null}}`；channel和mapping从已登记配置取，模型不能传。未配置明确不可执行；只有实际HUMAN触发命令允许登记未见历史，TIMER/TOOL_RESULT后台仅能读已授权原件。已忘/撤回/片段遗忘任何explicit取值都不重新导入；别名不具可验证external身份的旧档案记录省略并给真实原因，不伪造转换。

HTTP POST `/api/v1/resources/archive/view`为当前会话projection（管理员另得configurations），`connections.save`真实CAS命令；`search`/`read`返回统一CommandResult。结果含 records(原作者/timestamp/locator/opaque摘要/原文/source_ref)、source_refs、next_cursor/search coverage或target_locator/context_complete、omitted及原因、archive_observed_at和本次observed_at。只说明核对当刻存档，不把旧消息升当前指令，不声称档案媒体原件可用。资源页已有显式当前会话查询、原文前后文/续页及管理员连接映射表单；正常聊天也可调用同目录工具。Broker function resources.archive.read统一已配置quota/RPM/requests资源，无本地池；每次读取不会启动后台档案扫描，清空后无自动学回。

权威代码：resources/archive.py（真实链路与来源交接）、archive_protocol.py/archive_read.json（原协议）、A sources/archives.py（精确别名与抑制）。未运行tests/build/typecheck/浏览器/Archive真实账户；协议用例只编写。


## 人物、记忆与Models界面（implemented_not_tested）

people改为真实Identity人物分页/别名、独立账号关联与当前会话准入策略；记忆页使用Claims/Episodes/Reflections/Matters与完整SourceRef。来源原件读取提供真实speaker_id/can_revoke，只有该权威许可才显示原件/Unicode字符片段遗忘。当前会话未选择时不退为角色全域；角色已授权范围为显式选择。群页以实际Identity群会话及group:<conversation_id>主体读取认识、话题/反思/事项，保留原作者，不复制旧观察解锁、好感八阶段或图谱统计。Chat Audit复用资源域实际接口并接受显式群scope。

Models设置消费唯一Models管理API：原生供应商/只写密钥、路由/参数与实际验证、共享容量、系统默认/功能路由/角色覆盖、固定embedding配置。原生模型枚举真实分页，保留client_request_id并可只读查询原账本；未知结果不靠重新枚举找回。供应商/路由删除、角色继承及embedding解绑为真实CAS操作。新operator依据带新observed_at，改变模型/参数无本次依据则清旧能力验证，后端A同时管验证失效。角色页公开ModelFunctionsPanel(actorId?)，不再旧Core/旧provider/model_function DTO。

Images新增只读workflows.inspect({graph})，候选来自唯一Workflow.EDITABLE及真实字面量规则，不执行/保存图；outfit_id传正式Life.scene仅冻结本次衣柜，不改生活权威。协议新增sender_display_name传真实昵称，live observation eligibility只在非归档/非旧revision/非重复准入且reply_owner=tianshu时为true；B仍核真实active策略/Contacts与Identity权限。

以上仅已编写源码、定向故障用例与格式整理/完整diff自查。未执行tests/build/typecheck/浏览器/付费模型/真实渠道。Projects/Knowledge仍在独立提交实施，不能据此宣称本轮全部完成。
