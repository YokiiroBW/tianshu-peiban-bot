# 小屋生活接入核对（本轮只读）

2026-09-26，伴随视觉重做核对本地既有代码；无 NAS 查询或写入，无新接口发布。

- 平台 `apps/web/src/features/room/RoomPage.tsx` 当前仅浏览器本地预览，不能将它改名后便宣称实时角色生活。
- 陪伴核心 `src/tianshu_companion/life.py` 有房间权威状态；当前控制标识为 `window / sheer / curtain / desk_light / ceiling_light`。当前网页预览采用 `window / sheer / blackout / desk / bedside`，两者不是可直接透传的同一合同，特别是顶灯不等于床头灯。
- `life_read.py::_snapshot` 只读投影 actor/world/room 版本、时区、activity/mood/outfit_ref、changed_at/observed_at 与 `state_basis=last_persisted`；不返回可直接控制的完整窗灯帘对象列表，也不证明快照代表连续实时执行后的状态。
- 不能为了视觉重做让浏览器直读核心数据库、带服务凭据调用内部接口，或自行模拟核心已经确认的状态。

下一阶段接入需要独立发布并双边验证：明确物件 ID 与灯种映射、权威快照时基/状态版本、手动保持、命令确认与冲突语义、用户会话到核心读写授权的适配。优先复用已有授权机制，不能只设计前端 DTO 然后假称后端支持。

本轮页面可以重排布局、显示准确未接入空态并维持本地环境预览。生活真实接入与视觉样片是分开的验收项，两项都完成后才能称“角色生活小屋可用”。
