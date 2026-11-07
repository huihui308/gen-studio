# Style lock · 参考图 3D 卡通家庭风（生肖拟人版）

**来源**：用户提供的参考图 `assets/reference-zodiac-family-style.jpg`（1080×2400 手机截图，画面为竖屏）。
**状态**：渲染风格据参考图整理、**待用户核对**；角色阵容不锁定参考图角色，按项目选定的生肖组合生成（见第 2 节）。比例、场景是否沿用、拟人程度、画面文字是否保留等待确认。

## 1. 视觉 DNA（每段提示词重复这段英文块）

```text
Stylized 3D animated feature-film still, semi-realistic soft fur and scales with gentle subsurface warmth, rounded cartoon proportions, large expressive eyes, clearly readable brows and oversized cartoon facial acting. Warm cinematic lighting with strong diagonal god rays, warm gold rim light along fur and scales, soft volumetric light, gentle contact shadows and shallow depth of field. Rich amber, gold and cream palette with teal-green and soft pink accents. High-detail character rendering in a family-animation key, cinematic low camera, telephoto compression. No photoreal wildlife-documentary texture, no 2D flat illustration, no low-poly or plastic-toy surfaces, no human faces.
```

> 沙漠/室内等**场景块**按项目确认后替换（见第 3 节）；上面的渲染、材质、光照方向与配色是风格锁，不随场景改。

## 2. 生肖角色卡（每个项目按选定生肖生成，逐段整块复用）

本技能**不锁定固定角色**。参考图里的虎、龙、幼崽只示范「3D 半写实渲染 + 生肖拟人」的观感，不是固定阵容。每个槽位（P1/P2/C1/C2）在配置门确认后生成一张角色卡：

| 字段 | 取值 | 状态 |
|---|---|---|
| 生肖 | 鼠/牛/虎/兔/龙/蛇/马/羊/猴/鸡/狗/猪 任选其一 | （待确认：每个槽位各选一个，可同可不同） |
| 体型与拟人程度 | 直立拟人 / 参考图式四足；默认直立拟人以便手势表演 | （待确认：体型方案；四足时 Part 2/3 动作需改写） |
| 主色与纹样 | 按生肖；示例（可替换）：虎橙黑纹、龙青绿鳞、鼠灰褐、马棕白 | 默认提案：按生肖原生配色（待确认） |
| 面部 | 大眼、清晰眉弓、夸张但可信的卡通表情 | 固定渲染要求 |
| 服装/配饰 | 默认无固定服装，仅必要配饰 | 默认提案不穿衣、仅必要配饰（待确认） |
| 声音 | 按生肖声线（见 `SKILL.md` 第 7 节） | （待核验：试听确认） |

**留白要求**：角色卡一旦确认，全片禁止换生肖、换主色、换年龄、换拟人程度；镜头间只变表情、姿态、视线、手势。同一家庭可混搭不同生肖（如鼠爸 + 马妈 + 虎孩子），不做「一家同生肖」预设。**剧情槽位只决定谁冲突/旁观/动手/被打/示好，不改变角色卡与画风。**

## 3. 场景与构图

- 单一连续场景；默认待确认（参考图为暖光沙地；可改为客厅等室内单一场景）。
- 低机位（略仰），冲突双方（CA/CB）面对面；旁观者（OBS）在侧后方或画面边缘，按剧情槽位绑定调整。
- 光线方向固定：god rays 从**右上方**斜射；主角受光、配角逆光轮廓；浅景深。
- 运镜：缓慢推近、短横移、变焦跟焦；15 秒内不超过 3 次运镜变化，避免炫技。
- 竖屏时纵向堆叠（家长上方、孩子下方或面对面），横屏时左右分站。

## 4. 参考图上的文字标注（默认不进入画面）

参考图带有三行白字黑描边的中文标签（标注角色生肖与称呼），属于**参考图的说明性注记**，默认不烧录到生成画面；生成画面本身不得出现任何文字、字母、数字、字幕或水印。若用户要求保留，作为后期叠加条目单独列出，并确认语言与位置。

## 5. 负面约束（每条提示词都带）

```text
Negative constraints: no photoreal wildlife documentary look, no flat 2D illustration, no low-poly or plastic-toy rendering, no human faces or human hands, no species swap, no changed zodiac species or fur/scale colors, no extra limbs or duplicated tails, no size or proportion drift, no visible strike contact point, no injury, no red marks, no bruises, no slow-motion replay of the strike, no text, letters, numbers, captions, subtitles, logos or watermarks, no speech bubbles or dialogue boxes, no abstract liquid morphing.
```

## 6. 核对清单（出提示词前 / 出片后各做一次）

- [ ] 材质：毛发与鳞片是柔和半写实 3D，而非写实纪录片或 2D 平涂
- [ ] 五官：大眼、清晰眉弓、夸张但可信的卡通用力表情
- [ ] 光照：右上方斜射体积光 + 暖金轮廓光 + 浅景深
- [ ] 配色：暖沙金/奶油白为主，青绿与粉色为点缀
- [ ] 角色：每个槽位与项目确认的生肖、主色、拟人程度一致；无换种、换色
- [ ] 无文字、无 logo、无水印、无人类特征
- [ ] 剧情槽位绑定（CA/CB/OBS/STRIKER/STRUCK）的站位、视线与画面左右关系符合分镜
- [ ] 动手段无接触点/伤痕特写，未慢放

## 7. 待确认

- 参考图是「纯渲染风格参考」，还是同时锁定沙地场景？
- 成片比例：参考图为竖屏手机画面，建议 9:16；是否采用？
- 拟人程度：直立拟人（便于手势表演）还是沿用参考图的四足体型？
- 参考图上的中文标签是否要出现在成片里？（默认不出现）
- 角色服装/配饰：是否需要穿衣，还是保持动物原生外观？
