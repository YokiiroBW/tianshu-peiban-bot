# 小屋受版本管理的视觉基准

2026-09-26 从原本被 `/output/` 忽略的本地概念图逐字节归档。它们是本项目已有的图像生成参考，不是运行截图或已制作的三维资产。用户要求小屋以这些概念为视觉标准。

| 文件 | 原始位置 | 职责 |
| --- | --- | --- |
| [primary.png](primary.png) | output/ui/product-pages-v1/01-character-home.png | 构图、家具、人物姿态、页面层级的主基准 |
| [day-cycle.png](day-cycle.png) | output/ui/room-environment-v1/day-cycle-study.png | 清晨、午后、黄昏和夜晚气氛，不能替代主图几何 |
| [character.png](character.png) | output/ui/management-v2/character-art.png | 同一虚构人物的脸与发型参考，服装与阅读姿态以主图为准 |

主图/环境图指纹与锚点：../../development/room-r1-visual-baseline-2026-09-26.json。

[打开并排/叠加对照工具](review.html)：选择实际浏览器原始截图，不改原图文件。对照工具不会自动宣布美术通过。

图中日程、心情、聊天内容与旧语音图标是概念示意。它们不构成真实数据，也不覆盖 V2 当前功能范围或用户的新指令。图中生成的透视/几何存在不一致时，动态场景必须采用统一可解释的几何；偏差须记录，不能悄悄换成低质量灰模。

正式视觉验收看真实浏览器画面。不得将这些图片当整张场景背景，假称完成可动人物、窗帘、灯光或状态接入。
