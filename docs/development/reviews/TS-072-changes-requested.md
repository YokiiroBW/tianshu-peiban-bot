# TS-072 ComfyUI产物注解边界

2026-09-14，交付93cc5b4（实现3d8cff5）暂不集成。协调22图像专项通过9.12秒及完整diff通过；只读实际工作流节点/迁移，独立审查未知/取消/产物发现一处阻断。

filename="secret.png [input]"、type="output"可通过客户端校验。本机ComfyUI先按文件名注解选择目录，[input]/[temp]会覆盖type参数。隔离反例已接收PNG，突破仅output读取限制。须拒绝服务可识别的类型控制注解，仅构造filename/subfolder/type三个参数，不透传任意history字段，不剥除后改读别名。修复后集中跑对应图像边界，不重复不变全套。

实际两份Anima图为UI格式，角色LoRA未确认、API导出与真实GPU未验，保留这些限制；本问题修复不代表真实生图或渠道图像下行已完成。
