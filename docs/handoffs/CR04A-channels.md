# CR04A 原生渠道与外宿主桥

状态：implemented_not_tested。基线依赖能力 e32ae3f→283811a→3a329d0→3d48548→6ad9b57→1e56f97，共享正式类型 f0480a4、A fb6335d/10daeda、B eff68f5；最终根集成消费 A711/B最终合同。未运行测试/build/typecheck/浏览器/真实QQ/TG/付费调用。此交接不等于外部账户验收。

## 实际业务与标准覆盖

`onebot/registry.py` 固定 OneBot v11 d4456ee706f9ada9c2dfde56a2bcfc69752600e4：23个标准消息段条目（contact/music/node各含变体，20种段名）、38个公开API及隐藏 `.handle_quick_operation`。采用真实 NoneBot OneBot Message/MessageSegment 解码 CQ 和消息数组，保留数组顺序、未知段、引用原作者、合并转发及全部其他有效内容。标准发送使用数组；普通text不会重新解析为CQ。原生图片/录音/视频必须由授权MediaRef编译；嵌套转发可携带实际MediaRef，不透传本机任意路径。

完整消息/notice/request/meta_event 均有接收路径。notice/request/heartbeat/lifecycle只写真实运维观察，不假造人类发言；撤回通知用Identity.channel_context→Sources.recall_event。入站每次实际连接固定self_id；producer为登记namespace+":"+connection_id。文本/image/audio/video/file/reaction/mention/mention_all/reply/forward/reference原序进入B InputPart，未知结构保留为reference。匿名身份不猜真人。OneBot标准没有提供消息编辑revision时不能假称能发现编辑；提供revision的供应商走同event高revision。TG编辑以真实update_id+1修订，避免秒级碰撞。

HTTP、反向HTTP、正向WS、反向WS均有实际aiohttp实现；反向WS支持Universal/API/Event。HTTP回推固定独立Bearer或X-Signature sha1，反向WS验证Bearer/X-Self-ID/X-Client-Role；HTTP入站同事务PG inbox+Procrastinate提交后才ACK。WS标准本身没有持久重放/ACK保证，供应商不重放时断线期间事件不能冒称无丢失。

NapCat群公告、群文件、转发消息、@全体剩余额度等扩展有原生API定义与调用；SnowLuma只使用明确声明extension_apis，不因v11信封推断NapCat全部兼容。@全体固定当前群/明确权限与配置；NapCat或声明额度扩展时读取实际额度，标准泛用对端由真实发送回执反映拒绝。隐藏快速操作的reply剥离后经过唯一Expression，其他实际管理操作才走隐藏API，不形成第二发送出口。

TG为新实际Bot API实现（旧冻结仅有文本工具且未实接）：getMe固定账号核对、webhook与长轮询、更新持久ACK/offset、文本UTF16实体/@、照片/GIF动画/录音/音频/视频/文档/贴纸、回复和原作者归因、编辑修订、原件getFile和真实流式multipart发送。没有把TG当全OneBotAPI兼容；不支持的适配器/原生段准确拒绝。

## 唯一身份、恢复与真实效果

ChannelDelivery.deliver(context,ExpressionPlan)->Receipt，get/reconcile(context,expression_id)->Receipt|None。B逻辑target_ref必须等于context.conversation_id；C每次用Identity.channel_target重新解析实际connection/target，保留delivery_allowed缺省拒绝，绝不把逻辑ID当QQ地址。当前连接账号在实际效果前用get_login_info/getMe核对；外宿主固定SDK账号与host_instance_id。管理API不得跨该会话绑定的另一个连接。

唯一Actions登记channels.deliver；expression_id稳定映射Action。每次外部调用在实际网络前封存原call_id/action_id/attempt_id/API/参数；整次有序表达预先登记expected_count，再顺序发送，只有全部真实确认才delivered。retcode1/statusasync只是accepted；5xx、socket断线/echo超时、SDK网络中断保留unknown。重连、进程重启或reconcile不重发unknown。晚到echo/SDK回执更新原call和原Attempt，Actions单调result_revision保留；旧pending不能覆盖已确认。

interactive是唯一真实socket/TG轮询owner，以PG租约+epoch续期；worker通过固定配置、认证internal RPC只传已登记call_id，不传任意URL/QQ目标。长工具不占此owner的DB事务。取消中断原owner实际I/O/原SDK任务，不伪造撤销消息；已发消息删除另属明确授权管理Action。来源检查在短同conn Sources.validate_persistence后再锁自己的行，网络与媒体处理期间不持PG连接。

web通过独立B WebPublication.publish真实提交conversation.messages后才给delivered，C先持久登记web.publish原调用；未知结果只用WebPublication.get查原expression_id，不重复发布。C不读写B内部表。原生channels.api明确发送同样委托唯一Expression，reconcile按原表达查询。

## 原件与来源

`media.py` 真实流式FileStore封存→Sources.register_media→回读canonical source:。QQ SILK/AMR原件保留，实际get_record WAV转换以同原SourceRef的Artifact登记，metadata.analysis_media给B感知；original_media和实际representation并存。不能取到原件时明确original_unavailable/provider_transcode，不称拿到原始录音。GIF/PNG/JPEG/WebP/WAV/SILK/AMR/OGG/MP3/MP4/WebM用实际magic识别。

供应商远程文件必须明确允许域或remote_root→local_root映射，防路径越界。输出小媒体实际base64，长件用明示共享路径映射；TG流式multipart，不全量载入音视频。合并转发展开保留原作者/原节点SourceRef、深度/数量/遗漏范围；输入SourceRefs直接消费Conversation.ingest返回值，不重复登记一次聊天原件。事件与调用的版本/locator依赖索引支持A同事务withdraw_sources，清原payload/缓存/未执行请求、阻止晚到私密写回，保留实际效果/IDs/retcode；不删共享FileStore字节。

## 构造、任务、设置

`channels.module.build_module(runtime,*,settings,identity,sources,actions,catalog,artifacts,configuration,web=None)`→(DomainModule,Channels)，Migration精确名`channels-0001`，SQL资源`src/tianshu/channels/schema.sql`。

装配：Identity/Sources→唯一Artifacts→B独立WebPublication→C能力/稳定Weather→B LifePresentation→ChannelDelivery→B Expression→bind_expression→B Conversation→bind_conversation→登记全部tasks→start。channels.status()->{ready,owner,connections,role}；ready在interactive核实际租约/owner控制循环健康，在worker核绑定完成；未配置外部账号不阻断产品就绪，connection.available明确false/unknown，不能配置成功就称外部连通。

根settings.channels={owner_url:interactive固定HTTP地址,owner_token_ref:外置或sealed凭据引用}。C没有另起后台队列；统一任务channels.ingest/channels.cancel，短工作INTERACTIVE。普通生成/长下载仍能力ELASTIC，due只承载真正到期。

外宿主：SDK `tianshu_plugin.channel_bridge`，NoneBot==2.5.0+adapter==2.4.0 publichooks和原bot.call_api；AstrBot==4.27.3 aiocqhttp原call_action。NoneBot任一外部matcher/未计入postprocessor保留external owner；AstrBot普通消息只观察，显式/tianshu高优先级stop_event才交天枢。宿主最小SQLite receipt/outbox是被明确批准的独立SDK日志，不访问核心PG，不包含旧Core桥。跨重启unknown与ACK擦私人正文见SDK README/metadata/schema，不能把外部不透明规则链说成已证明无双答。

## 公开管理API与前端投影

所有actor_id/person_id/conversation_id selector放query，由A统一中间件授权，body剥离；读/命令/媒体/上传均如此。管理API现有external.manage；实际body不生成TrustedContext。

- GET `/api/v1/channels/view`→{connections,matrix,owner,deliveries,observations,adapters}。每connection有真实revision/configured/state/reason、具体matrix，api项区分implemented/provider_supported/verified_at。verified仅该配置revision实际成功回执；初始没有实测时间。
- POST `/api/v1/channels/connections/save`→统一CommandResult。body={client_request_id,id,expected_revision,adapter:onebot|telegram|nonebot|astrbot,transport,provider,endpoint?,api_endpoint?,enabled?,token?只写|token_ref?,extension_apis?,api_support?,segment_support?,media_path_mappings?,allowed_media_hosts?,max_media_bytes?,max_inline_bytes?,allow_at_all?,bridge_instance_id?,webhook_secret_ref?}。身份namespace/actor/self_id只从已登记Identity连接派生。
- POST `/api/v1/channels/connections/probe` {client_request_id,connection_id}→真实get_login_info/version/status/can_send或getMe观察，统一Action/Receipt。
- POST `/api/v1/channels/actions/invoke` {client_request_id,connection_id,api_name,params,source_refs?}→统一CommandResult。API凭据结果只对明确管理员读取no-store，Action结果只保存敏感result_ref，不给模型工具。

传输独立认证路由：POST /channels/onebot/v11/{id}/events，GET /channels/onebot/v11/{id}/ws，POST /channels/telegram/{id}/webhook，POST /channels/bridge/{id}/{events,receipts}，POST /internal/channels/{execute,cancel,reconcile}。它们不要求Web Cookie，管理/api/v1仍统一Cookie/CSRF。

## 验证材料与限制

仅做AST源码解析、JSON/TOML读取、相对引用/调用签名与diff检查；没有执行用例。已写tests/channels涵盖固定标准目录、未知段/归因/引用/@、CQ隔离、异步接受/unknown、HTTP取消与WS断线不重发、媒体路径/GIF/SILK、TG编辑UTF16/顺序multipart、SDK跨重启unknown/幂等/ACK隐私擦除。CR19需集中运行并补实际PG、三角色owner/恢复、B web publication与真实已授权账户验证；无真实群公告/@全体发送验收授权。

不能宣称已实测任一NapCat/SnowLuma API、NoneBot/AstrBot闭环、TG媒体或真实网络账号。供应商没有message_id/原handle查询的unknown保持unknown；不靠自动再发改善成功率。天气/浏览器/视频分析恢复补充见CR-capabilities.md。
