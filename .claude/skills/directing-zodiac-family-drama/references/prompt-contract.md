# Prompt contract · 3 × 5s（生肖拟人亲子版 · 剧情槽位）

每条提示词必须**自包含**：单独复制到任意视频工具都能成立，不依赖上下文；只允许在「首帧状态」里引用上一段的具体状态。

## 1. 可变因素（不得写死）

每个项目在配置门确认后，把下列变量填进提示词；未确认时按标记约定占位：用户项 `（待确认：…）`、运行时项 `（待核验：…）`、缺依据 `（待补充：…）`；不得预设。

| 变量 | 取值 | 归属与处理 |
|---|---|---|
| 剧情来源 | 使用者提示词 / 示例 A / 示例 B / 默认提案（示例 B） | 使用者提示词优先；未给时用默认提案并标（待确认：剧情） |
| 剧情槽位绑定 | CA、CB、OBS、STRIKER、STRUCK、APPEASER、TARGET | （待确认：按使用者剧情或选定示例绑定；约束见 `storyboard-15s.md` 第 1 节） |
| 角色数量 | 家长 1–2、孩子 1–2 | （待确认：组合；示例 A 需 2 孩子，示例 B 需 3 角色） |
| 角色生肖 | 鼠/牛/虎/兔/龙/蛇/马/羊/猴/鸡/狗/猪 | （待确认：每个槽位各一个，可同可不同） |
| 角色卡 | 生肖 + 体型/拟人程度 + 主色纹样 + 面部特征 + 服装配饰 | 助手默认提案、用户改（待确认：外观）；确认后逐段整块复用 |
| 配音声线 | 按该角色生肖（见 `SKILL.md` 第 7 节） | （待确认：服务/音色）＋（待核验：音色可用性、实际 voice ID、试听） |
| 比例/语言/字幕/BGM/场景 | — | （待确认；比例与场景由助手先给默认提案） |

## 2. 生成单元规则

- 默认 3 段 × 5.0s：`0–5` / `5–10` / `10–15`；每段对应一个剧情节拍（发火 / 动手 / 示好）。
- 工具只支持 8s：生成 8s，取前 5.0s 使用，剩余作余量；在提示词外注明裁剪点。
- 工具只支持 4s/6s：优先 6s 生成再裁；仍不够则先向用户说明并重排单元，**不得静默改 15s**。
- 每段首帧优先用已确认的角色图/首帧图作参考输入；工具不支持参考图时，明确告知一致性风险。
- 首帧图准备顺序：先出「剧情站位图」（按剧情槽位绑定）确认角色一致性，再为 Clip 2、Clip 3 各出一张首帧图。

## 3. 9 段式提示词模板

```
1) Output specification: approximately 5 seconds, {比例}, 720p, 24 fps, synchronized audio.
2) Environment and lighting: {场景块}
3) Character lock (repeat every visible character's full card, verbatim):
   {P1 角色卡} {P2 角色卡（若有）} {C1 角色卡} {C2 角色卡（若有）}
4) First-frame state: {首帧状态：角色位置、朝向、视线、手部与道具状态；标注 CA/CB/OBS/STRIKER/STRUCK}
5) Timed action beats: [0–2.5s] {第一拍} [2.5–5.0s] {第二拍}
6) Audio: {每个角色的台词（仅音频，引号内逐字）+ 该角色生肖声线；或 silent actor} + {音效}
7) Final-frame state inherited by the next clip: {尾帧状态}
8) Negative constraints: {负面块}
9) No visible text: no words, letters, numbers, captions, subtitles, interface copy, logos or watermarks.
```

未确认时按标记约定占位：用户项 `（待确认：…）`、运行时项 `（待核验：…）`、缺依据 `（待补充：…）`；不得自行填成 16:9 或默认中文/英文。

## 4. 各段提示词要点

> 下列 CA/CB/OBS/STRIKER/STRUCK/APPEASER/TARGET 均按项目绑定替换；示例 A（两个孩子吵架、爸妈旁观）与示例 B（妈妈和孩子吵架、爸爸旁观）的绑定见 `storyboard-15s.md` 第 2 节。

### Clip 1（0–5s）· 冲突发火

- 首帧：CA 与 CB 面对面（按绑定），CA 前倾；OBS 在后景或边缘；光线按参考图方向。
- 两拍：`[0–2.5s]` CA 抬手指向 CB、爆发式发火，CB 愣住后回击，OBS 反应 + `[2.5–5s]` 双方越说越急、跺脚挥臂，STRIKER 握拳（若不在冲突中），尾帧 STRIKER 抬手/迈步介入。
- 尾帧：STRIKER 手臂抬起/已上前，CA/CB 侧对镜头喘气。
- 参考输入：剧情站位图（若已有）。

### Clip 2（5–10s）· 家长动手

- 首帧：延续 Clip 1 尾帧——STRIKER 手臂已抬起/已上前，STRUCK 僵住/后退半步，OBS 按绑定反应。
- 两拍：`[0–2.5s]` STRIKER 抬臂逼近、STRUCK 抬臂挡、OBS 欲拦未拦 + `[2.5–5s]` 落手一击（非写实：遮挡/剪影/快速横摇带过，接触点不入画，只给一次短促「啪」）、随后 0.5s 静场、STRUCK 抬头。
- 尾帧：STRUCK 慢慢抬头，眼神复杂，STRIKER 怔住看手。
- 参考输入：Clip 1 尾帧截图（若工具支持首帧）。
- **红线**：不展示接触点、红肿、伤痕、血迹；不慢放、不重复击打、不做哭嚎特写。
- **动作默认提案**：手掌/前爪一挥、非写实带过；可改道具/推搡（待确认：动作方式）。

### Clip 3（10–15s）· 孩子示好

- 首帧：延续 Clip 2 尾帧——STRUCK/APPEASER 抬头、手放下，TARGET 仍僵着。
- 两拍：`[0–2.5s]` APPEASER 吸鼻子、靠近半步、伸手拉 TARGET 的手（默认提案；备选递物件，待确认：示好方式） + `[2.5–5s]` TARGET 蹲下/被拉低、抱住 APPEASER，14.5s 起定格。
- 尾帧：定格画面（剪辑出点）。
- 参考输入：Clip 2 尾帧截图 + 剧情站位图。

## 5. 音频与配音

- 台词只作音频：`Audio voiceover only, strictly no speech bubbles, no dialogue boxes.`
- 引号内逐字写出已确认台词；禁止增删改、重复、重新排序；禁止画面转写。
- **每个角色按生肖配声**：在提示词的角色卡里写明该角色的生肖声线（音色/音高/语速方向），并逐段整块重复；同一角色全片不换声线，只随剧情变语气。
- 旁观者（OBS）若有劝架/反应台词或叹气，按其本人生肖声线，不因「旁观」换嗓。
- 依据该生肖动物的自然发声特征做拟人化，不得使用与生肖无关的通用人类音色；龙、兔等缺乏稳定自然声源的标（待补充：参考声源/用户确认）。
- 具体音色、provider/model/voice ID 在项目试听后记录（待核验：可用性）；未试听前不写死参数。
- 默认无台词：写 `silent actor, no speech, mouth stays a small stable line`；呼吸、叹气、哭腔仍按该角色生肖声线，音效单独列。
- 旁白（若启用）：独立音色，不占用角色生肖声线；语言（待确认）。
- BGM 未确认前不给音乐指令；确认后从第 1 段起贯穿，不中途换风格。

## 6. 负面约束块（每段重复）

```
Negative constraints: no photoreal wildlife documentary look, no flat 2D illustration, no low-poly or plastic-toy rendering, no human faces or human hands, no species swap, no changed zodiac species or fur/scale colors, no extra limbs or duplicated tails, no size or proportion drift, no visible strike contact point, no injury, no red marks, no bruises, no slow-motion replay of the strike, no text, letters, numbers, captions, subtitles, logos or watermarks, no speech bubbles or dialogue boxes, no abstract liquid morphing, no sudden camera cuts inside one clip.
```

## 7. 输出顺序（Phase B 交付）

1. 全局连续性块（比例、时长、画风锁摘要、剧情来源与槽位绑定、角色卡与生肖声线、字幕/BGM 配置、拼接策略）
2. 3 条独立提示词（Clip 1 → Clip 3）
3. 首帧参考图指示
4. 拼接与剪辑说明
5. 验收清单与待处理项

## 8. 验收清单

- [ ] 三段时长加总 = 15.0s；每段提示词写「约 5 秒」且两拍时间相加 = 5.0s
- [ ] 三段内容严格对应：发火 → 动手 → 示好，未调换、未省略
- [ ] 剧情槽位绑定完整且合法：CA≠CB、STRIKER 为家长、STRUCK 为孩子、OBS 为冲突双方以外角色
- [ ] 使用者传入剧情时以该剧情为准，未回退默认剧情
- [ ] 示例 A（两个孩子吵架、爸妈旁观）与示例 B（妈妈和孩子吵架、爸爸旁观）都能直接套用本契约生成
- [ ] 角色数量/剧情与契约冲突时已指出并标（待确认：取舍），未擅自加角色或改契约
- [ ] 每个可见角色都有完整角色卡（含生肖），逐字一致；无换种、换色
- [ ] 每条提示词写明该角色生肖声线；配音与生肖一一对应；未试听项已标（待核验）
- [ ] 动手段为非写实处理，负面约束含「不展示接触点/伤痕/慢放」
- [ ] 首帧状态 = 上一段尾帧状态；尾帧能接住下一段
- [ ] 每段 ≥2 个视觉装置、1.5–2s 一次视觉变化、无站桩
- [ ] 台词（若有）与确认稿逐字一致；无台词时明确 silent actor
- [ ] 画面无文字、无字幕、无 logo、无水印；参考图标签未烧录（除非确认保留）
- [ ] 负面约束齐全，无 hex/RGB/Pantone，无抽象液态形变
- [ ] 标记使用符合约定：用户项（待确认）、运行时项（待核验）、无依据项（待补充）未混用
- [ ] 生成工具是否支持参考图/首帧已核实，不支持时已说明风险
- [ ] 技术检查 ≠ 用户验收；成片仍待用户确认
