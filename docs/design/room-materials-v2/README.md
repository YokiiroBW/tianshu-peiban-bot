# 房间资产返修：布料原始贴图

2026-09-26，使用内置 imagegen 生成，非 CLI。`lavender-botanical-cotton-v1.png` 是淡紫小花棉织物的 base-color 原始候选，供真实被褥网格使用，不是场景截图或角色模型。原始输出逐字节保留，只有在实际模型 UV/光照下通过检查才采用。不要用贴图伪造几何褶皱或固定日照。

提示词（原样）：

Use case: stylized-concept. Asset type: a production flat base-color textile texture for a real 3D duvet, not a scene or mockup. Generate a square seamless repeating fabric swatch filling the whole image edge to edge. Palette muted pale dusty lavender and ivory, sophisticated soft anime interior illustration textile. Delicate low-contrast painted botanical print, scattered tiny ivory five-petal blossoms and thin grey-lilac leafy stems, much negative space, fine cotton weave visible but subtle. The flowers should be small and sparse, approximately 20 little sprigs across the width, not big decorative bouquets. Perfectly flat orthographic fabric surface, diffuse even neutral illumination. No wrinkles, folds, perspective, drapery, furniture, objects, shadows, highlights, borders, text or watermark. This image will be UV-mapped onto independently modeled draped cloth and dynamically lit; do not paint any directional illumination or geometry into it. Prefer 1024x1024.

初步目视：具备细织纹和淡色花枝，无定向光和家具背景；输出花枝比提示的二十簇横排更大，应在真实 UV 下检查尺度和接缝，不能直接声称已无缝或视觉达标。

## 窗外远景

`window-city-vista-v1.png` 同样用内置 imagegen 生成，只作为远处窗外图层。室内几何、窗框、玻璃、帘与日照仍真实计算，不把该图冒充整个三维场景。图中的天空和植被需要按昼夜降低亮度/调色，不能夜间保持白天发光。

提示词（原样）：

Use case: stylized-concept. Asset type: painted distant exterior backdrop texture seen through a window in a real-time 3D anime room. Create ONLY the outside view, a landscape 3:2 illustration. Soft high-quality Japanese animated-film background painting, refined small details. Upper third is pale hazy blue and ivory sky with delicate clouds; middle distance a quiet contemporary city skyline of varied modest apartment buildings, atmospheric perspective in muted lavender-grey and warm ivory, delicate readable tiny windows and rooftops; lower half leafy treetops with naturally varied small leaf clusters in muted sage and olive, gentle pale new leaves. Rich painted depth, soft edges in distance, closer foliage detailed but not photorealistic. It should feel like the serene view from an upper-storey bedroom. Diffuse neutral daylight, no strong sun direction or baked cast shadows, no glow bloom. No interior, no window frame, no glass, no furniture, no people, no text, no UI, no borders, no watermark. Artwork fills image edge to edge. This is one distant background element of a dynamic 3D scene, not a replacement screenshot or whole room. Fine painterly texture, avoid flat vector paper shapes, low-poly objects, plastic, oversaturation.

## 浅木纹

`pale-ash-wood-v1.png`，内置 imagegen，未加工原始素材。纵向细纹，供浅木色家具，避免大幅正弦条纹。是否采用以真实 UV 与浏览器样片为准。

提示词（原样）：

Use case: stylized-concept. Asset type: seamless flat base-color texture for pale ash wood furniture in a refined pastel anime bedroom. Square image completely filled with one continuous sheet of pale natural ash veneer, warm ivory-beige and very light honey tones. Elegant extremely fine longitudinal grain running vertically; subtle irregular growth lines and occasional very faint narrow cathedrals. Low contrast, light Scandinavian furniture finish, hand-painted background-art delicacy with believable fine wood fibers. Diffuse perfectly even neutral lighting. No plank seams, no border, no knots with dark rings, no wavy zebra stripes, no scratches, no glossy highlights, no shadows, no beveled edges, no objects, no text or watermark. This is an albedo map to put on actual 3D furniture; no shading or perspective painted in. Prefer 1024x1024.
