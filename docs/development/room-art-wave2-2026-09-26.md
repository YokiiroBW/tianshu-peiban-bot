# 小屋资产第二轮返修

用户本轮明确：页面布局可以，物件视觉仍不行、效果看不出变化。以此覆盖上一轮建议中的页面收口：冻结页面组件、CSS、导航和业务状态。本轮仅资产/渲染；不部署 NAS、不宣称视觉已达标。

基线为 ROOM-INTEGRATION `cbddfe3b5cf20a5cbf253d57e5ff23a9d282b467`。主图继续 `docs/design/room-reference-v1/primary.png`；上一版实际画面 `evidence/candidate-b-desktop.png`。这次必须同时对照原图与 B，证明物件轮廓和表面有可辨改善，不能仅调曝光后交付。

## 独立任务

- ROOM-ART-A：`worktrees/ROOM-ART/tianshu-platform`，`codex/room-r2-art-20260926`；原 room_environment 执行。独占环境主生成器、环境 GLB/源、manifest、scene/renderer。优先白被/淡紫薄毯的厚度、多尺度褶皱和垂边、鼓起枕头及滚边、软包椅轮廓与压陷、细木纹及物理 UV。先床椅局部，后整屋。只在完成模块交付后接入 B 模块。
- ROOM-ART-B：`worktrees/ROOM-PROPS/tianshu-platform`，`codex/room-r2-botanical-20260926`；原 room_character 执行，本轮不继续通用人物拼装。独占新 `tooling/room-assets/environment/botanical_props.py`、`props-source/**` 和交接。`enhance_props(globals())` 在家具生成后、保存可编辑源与合批前调用，精确替换植物/书本，不碰家具。真实弯曲叶片、分层枝干、花器白花、错落书脊；共享材质合批。
- 独立审查：room_page 只读审查 UV/材质/光照根因，已给 A 五项具体建议；不改页面。
- 总控：提供真正绘制的棉布与窗外远景贴图，记录内置 imagegen 提示词及原始文件，检查局部/浏览器效果，串行挑入候选并完成必要回归。生成图仅作布料 albedo 和窗外远处图层，绝不铺整张房间参考图伪装动态场景。

## 资产边界与验收

`docs/design/room-materials-v2/` 保存两张生成素材与提示词，产品必须复制必要源到自己仓库并可重复构建。贴图在 UV/真实光照下验收，白昼远景夜间必须暗下来。现有独立窗、纱帘、遮光帘和灯继续真实运作；不引入第二引擎、网络素材依赖、付费工具或新的用户目录配置。

页面文件与基线逐文件 Git diff 应为空。对比截图统一 1586×992、14:00、浅色、减少动态；增加床椅、书架/植物与窗景局部。拒绝明显穿插、悬空、卡通方块/正弦条纹代替织物、整齐复制的叶片和夜间仍亮的白天背景。独立行为通过只说明操作未坏，最终美术一致性另判。
