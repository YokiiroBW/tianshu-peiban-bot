# CR11/13/14/15/17 能力域交接

状态：开发中，`implemented_not_tested`。基线 e034276dfb757b7dd64cd2f56e952d81193a5248；分支 codex/cr-capabilities；专属目录 src/tianshu/capabilities、packages/tianshu-plugin、tests/capabilities。

## 稳定的跨域端口

正式定义为 `tianshu.capabilities.ports`，值类型为 `tianshu.capabilities.dto`；Action/Attempt/Receipt/MediaRef/ArtifactRef/SourceRef/Coverage 一律消费 `tianshu.types`。

- AuthorityPort.check(context,source_refs=())：实时授权/来源复核，异常即拒绝，能力域不猜允许。
- SourcePort.resolve(context,MediaRef) 返回 (实际bytes,MIME)；协议端路径/文件ID/临时URL由对应来源适配器解释。
- SourcePort.capture(context,locator=...,raw=...,mime_type=...) 返回 SourceRef。locator 保留原URL、作者/楼层、获取时间、版本和原事件身份；来源域拥有其修订/遗忘语义。
- SourcePort.read(context,SourceRef,requested) 返回实际原件/表示及已覆盖范围；能力层不能把搜索摘要登记成全文。
- LifeSnapshotPort.scene(context,activity_id=None)：返回 SceneSnapshot 对应字段，天气包含 observed_at/expires_at/source；未知天气返回 null。生活域拥有活动与日程当前状态。
- LifeSnapshotPort.sharing(context,action_id=...,window=...)：返回 {decision:send|defer|archive|cancel,target_ref,due_at,reason}。生成完成不代表立即分享；target_ref 必须已由可信身份确定。
- ExpressionPort.present(context,expression_id=...,artifacts=...,public_parts=...) 返回 Receipt。只有表达/渠道出口真正发送，capabilities 不另发 QQ。
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

图包导入 body `{name,actor_ids,scope_ids,files:[{filename,media_type,data_base64}]}`，ZIP可用 archive_base64。普通上传不自动成为共享表情。语音试听 `{profile_id,text,connection_id?,model?,voice_id?}`；转写提供 MediaRef 和原 source_event_id。图像预览/生成 `{actor_id,engine_id,profile_id,prompt,purpose:selfie|creation,share,scene_revision?,outfit_id?,activity_id?,reference_ids?}`；真实触发来源从TrustedContext取得。

## 新投影字段

- speech/view：connections[{id,revision,transport:mcp|native,enabled,configured,account_id,team_id,workspace_id,quota_group,billing_source:plus|api|free,capabilities:{tts,asr,stream,timestamps,voices,clone,dictionaries},balance:null|{value,unit,observed_at},state,reason}]；profiles[{id,revision,actor_id,connection_id,voice_id,model,language,output_mode,dictionary_ids,vocal_defaults}]；dictionaries[{id,revision,name,entries:[{text,pronunciation}],provider_id,provider_version}]；default_route:null|{connection_id,model}。
- speech voices.search：{items:[{id,name,preview:MediaRef|null,visibility,owner_id}],next_cursor}；preview/synthesize 的 data 为 {artifacts:[ArtifactRef],alignment,spoken_text,model,billing_source,usage:null|真实返回}。
- reactions/view：{packs:[{id,revision,name,actor_ids,scope_ids,asset_count}],assets:[{id,revision,pack_id,media:MediaRef,description,ocr,actions,purposes,animated,index_state,embedding_profile}],index:{profile:null|string,pending,failed},next_cursor}。
- images/view：{engines:[{id,revision,engine:comfyui|nai|gpt_image,enabled,configured,model,quota_group,capabilities,state,reason}],profiles:[{id,revision,actor_id,engine_id,workflow_id,character,references,outfit_defaults}],workflows:[{id,revision,name,bindings,outputs}],identity:null|{actor_id,references},outfits,scene:null|SceneSnapshot,jobs:[{action_id,attempt_id,execution,effect,artifacts,reason}],album:[ArtifactRef]}。
- images/preview：{scene:SceneSnapshot,intent:ImageIntent,engine_id,compiled:{prompt,negative_prompt,parameters,references},warnings:[]}。
- GET /api/v1/capabilities/media/{media_id}：同源鉴权和来源/范围复核后返回实际字节；POST capabilities/upload 的 data={media:MediaRef}，不暴露本机路径/秘密或供应商永久链接。

## 验证边界

用户要求全部集成后统一验证，因此未运行测试/build/typecheck/真实模型或任何收费调用。当前文件存在和源码实现不代表外部实机验收。后续提交将更新本记录的实际能力和待接线项。
