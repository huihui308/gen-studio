# 《刻舟求剑》· H3 / FL2VA 生成稿（Phase B）

- 状态：可投喂（按本机已跑通参数固化）
- 日期：2026-10-27
- 适用：MiniMax-H3 **FL2VA** / SGLang 0.5.22 / `t2va` / 单卡 RTX 3080 Ti 12 GB
- 原提案：`stickman/kezhou-qiujian/proposal.md`（保持原样，未改动；本文件为另存稿）

本稿只做 H3 可执行化：保留原提案的三段式故事、三拍动作、镜头意图与 A5 历史卡通风；把所有依赖不可用能力的表述改成纯文本 `t2va` 可执行方案。旁白与 BGM 统一改为后期制作，H3 只生成画面与同期环境声。

---

## 1. H3 约束与本稿采用的参数

| 项 | 本稿取值 | 依据 / 说明 |
|---|---|---|
| 单次生成时长 | **10 秒 × 3 段 = 30 秒** | H3 单次支持 4–15 秒；30 秒不能一次生成 |
| 画幅 | **9:16 竖屏** | H3 支持 9:16；本机 9:16 已验证 |
| 帧率 | **24 FPS** | H3 固定输出，不需要在请求里另设 |
| 音频 | **32 kHz 立体声** | H3 固定输出；本稿让 H3 只生成环境声与 SFX |
| 任务 | **`t2va`** | 本机仅部署 FL2VA；纯文本、无图像/音频参考 |
| 参考输入 | **无** | 不要上传角色图、首帧、尾帧或音频；`conditions: []` |
| 分辨率 / short_edge | **736**（交付档）；**480** 可作快速预览 | 736×1312 渲染后可裁 720×1280；两档均不改提示词 |
| 步数 | **20** | 与 `h3/payloads/*.json` 已跑通请求一致；更高步数未验证 |
| flow_shift / audio_flow_shift | **12.0 / 3.0** | 与已跑通 payload 一致 |
| seed | **1101** | 与已跑通 payload 一致；三段可用同值，也可改为 1101/1102/1103，不改变提示词 |
| 输出 | 约 **243 帧 / 10.125 秒 / 24 fps / 32 kHz 立体声** | 3 段约 30.375 秒；后期每段尾部裁约 0.125 秒，或接受误差 |
| 本机耗时 | 736p 约 48 分钟/条；480p 约 15 分钟/条 | 3 条约 2.4 小时（736p）或 45 分钟（480p）；H3 与 Qwen 不要同时加载 |

> `short_edge` 与步数是本稿采用的**已跑通默认值**，不是留空项。若最终交付必须用 480，只改 `short_edge` 即可，提示词不动。更高步数（如 30/40）属于新增试验，需先单段验证（待补充：是否追加步数试验）。

---

## 2. 声音策略：旁白与 BGM 全部后期

### 2.1 为什么不让 H3 生成旁白或 BGM

本机 FL2VA 只有文本输入，没有音频参考输入；三次独立生成无法保证同一说话人音色，也无法保证三条 BGM 在硬切处无缝。因此：

- **H3 只生成同期环境声 / SFX**；提示词明确 `no speech`、`no narration`、`no music`。
- **旁白**：后期统一录制或统一 TTS，同一位配音员/同一音色贯穿三段。
- **BGM**：后期铺一条完整的 30 秒主题，三段硬切处不重启音乐。
- **不要**在提示词里要求 H3 说中文旁白，也不要上传音频参考。

这样既满足 H3 能力边界，也消除了“三段旁白完全一致”的不可执行承诺。

### 2.2 后期旁白文案（压缩版）

原提案约 41 字/段，按 4 字/秒约 10.25 秒，存在超时风险。下面压到 **34–36 字/段（不含标点）**；按 4 字/秒约 **8.5–9.0 秒/段**，留出停顿与呼吸，30 秒内可完成。

| 段 | 旁白（后期配音） | 字数（不含标点） | 4 字/秒参考 |
|---|---|---|---|
| 1 | 剑掉进江里，他没下水，反而掏出了刀。楚人坐船过江，船到江心，腰间青铜剑滑落沉底。 | 34 | 8.5 秒 |
| 2 | 他掏出小刀，在船舷刻下一道记号，认定剑就丢在这儿。船继续前行，他觉得自己聪明极了。 | 36 | 9.0 秒 |
| 3 | 船靠岸，他照着记号潜下去，摸了半天，空手浮上来。船已远去，剑还在原处。这就是刻舟求剑。 | 36 | 9.0 秒 |

- 配音总字数约 106 字；按 4 字/秒约 26.5 秒，加停顿可落在 30 秒内。
- 若必须保留原 123 字文案，按 4 字/秒总长约 30.75 秒，需要后期提速或删字；本稿采用上表压缩版。
- **待补充**：具体配音员或 TTS 选型、授权与终混标准尚未确定。

### 2.3 BGM 方案

- 后期铺一条 **30 秒** 的古琴为主、箫声点缀、低频堂鼓的主题；三段硬切处不重新起音乐。
- 第 1 段古琴单音起、一声闷鼓；第 2 段延续并略快；第 3 段堂鼓收住，余下水声。
- H3 提示词的 `non_diegetic_music` 一律写 `None`，避免模型自行生成竞争性音乐。
- **待补充**：BGM 曲目/授权与终混音量标准尚未确定。

---

## 3. 全局视觉锁定（三条提示词内均完整重复）

**画风**：A5 历史卡通风；彩色手绘场景 + 火柴人；战国楚国、宽阔江面；江水墨绿、木船赭褐、麻衣土黄、青铜暗金、雾白天青；画面内不出现任何文字。

**楚人（主角，30–35 岁男性，平民）**：圆白脑袋；短白颈约头径 1/10；白色下颌自然延伸成短颈没入衣襟，颈两侧才见黑描边；约四头身，头明显窄于肩宽；实心黑竖椭圆眼；鼻为一条短浅黑弧线；嘴为短黑线；平直略粗的眉；束发髻、无刘海、无胡须；深青交领内衬 + 土黄麻布短褐、布腰带、下摆到膝上；裸露小臂小腿为单根细黑笔画；实心黑椭圆手掌带轻微拇指凸起，约头径 18% 长，无手指无指缝；草鞋；腰间佩青铜剑，全片唯一金属反光。

**船夫（50–55 岁男性）**：同基础头身与头颈连接；实心黑竖椭圆眼；眼角两道短笑纹；嘴角略向下；花白短须绕下颌一圈，不遮颈连接；发髻藏在宽檐竹斗笠下；土褐粗麻短褐、袖口卷起、麻绳腰带；裸露小腿、草鞋或赤脚；同款黑手掌；持竹篙；背略驼。

**时代包**：战国楚国境内一条宽阔江面（寓言场景，非具体考证地点）；江心渡船（第 1–2 段）→ 下游岸边浅滩（第 3 段）；船始终朝画面上方行驶；青铜剑 ×1、短刀 ×1、船舷刻痕 ×1、竹篙 ×1、木渡船 ×1。

**9:16 构图**：剑的下落、水花、刻痕、入水点都压在画面中轴线上；上方 1/4 天空雾气留字幕安全区，下方 1/3 是江水；水花、船桨、竹篙从镜头前掠过，增加竖屏层次。

**统一负面约束**：无画面文字/字母/数字/字幕/招牌/logo/水印；无对话气泡；无写实或 3D 人物；无真实五官；无多余肢体、手指或断手；无颈部断口；无胡须遮住颈连接；无多余角色或现代物件；无画风漂移、形变、镜头抖动；不使用 hex/RGB 等技术色标。

---

## 4. 三条 10 秒 H3 提示词

> 每段都是独立、自包含的 `t2va` 文本提示词。复制时保留换行与标点；不要附加“参考图”“音频参考”“旁白由模型生成”等描述。

### Segment 1 · 0–10s｜黄金钩子 + 起因

```text
integrated_multimodal_description: Vertical 9:16 A5 historical-cartoon film, colored hand-painted scenery with stick figures, Warring States Chu, a broad ink-green river under pale blue-white mist. [Shot 1] From 00:00.000 to 00:03.000: low-angle medium shot from the bow of a wooden ferry boat moving toward the top of the frame. A 30–35-year-old Chu man stands at the bow with his hands clasped behind his back, his hemp robe and hem lifting in the wind. He has a round white head, a short white neck about one tenth of the head diameter, the white jaw continuing into the short neck and into the collar with black outline only at the sides, about four heads tall, his head clearly narrower than his shoulders. Solid black vertical oval eyes, a short shallow black-arc nose, a short black-line mouth, straight slightly thick eyebrows, a topknot, no bangs, no beard. Deep-blue cross-collar inner robe under an earth-yellow hemp short jacket, cloth belt, hem above the knees; bare forearms and calves drawn with single thin black strokes; solid black oval mitten-like palms with one subtle thumb lobe, about 18% of the head diameter, no fingers or finger gaps; straw sandals; a bronze sword at his waist, the only metal glint in the frame. The boatman, 50–55, stands behind him poling: same head and neck construction, solid black vertical oval eyes, two short laugh lines at the eye corners, slightly downturned mouth, a short gray beard ringing the jaw without covering the neck connection, a topknot hidden under a wide-brim bamboo hat, an earth-brown coarse hemp jacket with rolled sleeves and a rope belt, bare lower legs, straw sandals, the same black mitten palms, a bamboo pole, and a slight stoop. The camera pushes slowly forward along the gunwale at a low angle; wet wood, rope, and river water fill the foreground. [Shot 2] At 00:03.000, the boat jolts. The bronze sword slips from its scabbard and falls vertically toward the river, turning once. The camera tracks the falling sword and tilts down, keeping it on the central vertical axis; the Chu man's hand reaches too late. The boat keeps moving toward the top of the frame. [Shot 3] At 00:07.000, the sword enters the water and a white splash bursts up into the foreground, briefly wiping across the lens. The camera lands back on the same section of gunwale. The Chu man leans over the side, one black palm on the wood, staring down. End on the same gunwale segment as the river surface settles.
overall_soundscape: Diegetic only: broad river current, wind through hemp cloth, wood creaking, a short metallic slip and hiss, a clean entry splash, water droplets falling on wood, distant water birds. No speech, no voiceover, no narration, no dialogue, no music.
non_diegetic_music: None. Do not generate music; the final guqin, xiao, and low drum score is added in post-production.
Negative constraints: no on-screen text, letters, numbers, captions, subtitles, signage, logos, or watermarks; no speech bubbles; no photorealistic or 3D humans; no realistic facial features; no extra limbs, fingers, or detached hands; no neck gaps; no beard covering the neck connection; no extra characters, modern objects, or style drift; no morphing, camera shake, or color notation.
```

### Segment 2 · 10–20s｜打破常识 + 升级

```text
integrated_multimodal_description: Vertical 9:16 A5 historical-cartoon film, colored hand-painted scenery with stick figures, Warring States Chu, a broad ink-green river under pale blue-white mist. [Shot 1] From 00:00.000 to 00:03.000: at water level, the white splash from the previous shot disperses. The same 30–35-year-old Chu man pulls a short knife from his sleeve and crouches beside the same section of gunwale; his dark-blue cross-collar inner robe, earth-yellow hemp short jacket, cloth belt, bare forearms, black mitten-like palms, topknot, and bronze sword at his waist remain identical to Segment 1. The same 50–55-year-old boatman stands behind him in the background, poling with a bamboo pole and shaking his head; his wide-brim bamboo hat, short gray beard, earth-brown hemp jacket, rope belt, bare lower legs, and slight stoop remain identical. The boat still moves toward the top of the frame. [Shot 2] At 00:03.000, cut to a close-up of the knife tip carving one horizontal notch into the gunwale; small wood shavings curl and fall. The notch is on the same gunwale section, centered in the vertical frame. The knife is the only sharp metal glint besides the bronze sword at his waist. [Shot 3] At 00:07.000, the Chu man rises, presses one black palm over the notch, and points at the river with the other hand, certain of himself. The boatman keeps poling in the background and shakes his head again. The camera rises from a low angle to a level view, keeping the notch centered; the boat continues toward the top of the frame.
overall_soundscape: Diegetic only: river water lapping the hull, a short metallic slip as the knife leaves the sleeve, a soft scrape of the blade cutting wood, wood shavings falling, bamboo pole creaking, a quiet exhale, wind over the river. No speech, no voiceover, no narration, no dialogue, no music.
non_diegetic_music: None. Do not generate music; the final guqin, xiao, and low drum score is added in post-production.
Negative constraints: no on-screen text, letters, numbers, captions, subtitles, signage, logos, or watermarks; no speech bubbles; no photorealistic or 3D humans; no realistic facial features; no extra limbs, fingers, or detached hands; no neck gaps; no beard covering the neck connection; no extra characters, modern objects, or style drift; no morphing, camera shake, or color notation.
```

### Segment 3 · 20–30s｜反转揭晓 + 点题

```text
integrated_multimodal_description: Vertical 9:16 A5 historical-cartoon film, colored hand-painted scenery with stick figures, Warring States Chu, a broad ink-green river under pale blue-white mist. [Shot 1] From 00:00.000 to 00:03.000: the bow of the same wooden ferry boat reaches a shallow shore. The same 30–35-year-old Chu man points at the horizontal notch on the gunwale and jumps into the water; his dark-blue cross-collar inner robe, earth-yellow hemp short jacket, cloth belt, topknot, black mitten-like palms, and bronze sword at his waist remain identical. The camera follows his jump in a level side view, then the splash swallows the frame. [Shot 2] At 00:03.000, cut to an underwater low-angle view. The Chu man gropes beneath the notch in the green river water, bubbles rising past his white head; his eyebrows are straight, his mouth a short black line, his black palm empty. The riverbed is sand and small pebbles; there is no sword. [Shot 3] At 00:07.000, he surfaces, wet and dripping, and holds up one empty black palm. The camera pulls up and back to a high overhead view: the ferry boat waits at the shallow shore, the river stretches away, and one tiny glint flashes at the distant center of the river. End on the distant glint and the wide river surface.
overall_soundscape: Diegetic only: water slapping the hull, a jump and a clean splash, underwater bubbles and muffled current, water dripping from cloth, wind across the river, a distant bird. No speech, no voiceover, no narration, no dialogue, no music.
non_diegetic_music: None. Do not generate music; the final guqin, xiao, and low drum score is added in post-production.
Negative constraints: no on-screen text, letters, numbers, captions, subtitles, signage, logos, or watermarks; no speech bubbles; no photorealistic or 3D humans; no realistic facial features; no extra limbs, fingers, or detached hands; no neck gaps; no beard covering the neck connection; no extra characters, modern objects, or style drift; no morphing, camera shake, or color notation.
```

---

## 5. 请求模板（本机可直接复制）

把上面对应 Segment 的完整提示词保存为 `segment-1.txt` / `segment-2.txt` / `segment-3.txt`（保留换行），再用同一组固定参数生成 JSON：

```bash
python3 - <<'PY'
import json
from pathlib import Path

prompt = Path("segment-1.txt").read_text(encoding="utf-8")
body = {
    "model": "/mnt/sdb1/soft/deploy-models/h3/models/MiniMax-H3",
    "task": "t2va",
    "conditions": [],
    "seconds": 10,
    "target": {
        "short_edge": 736,
        "aspect_ratio": "9:16",
        "duration_seconds": 10.0,
    },
    "num_outputs_per_prompt": 1,
    "num_inference_steps": 20,
    "flow_shift": 12.0,
    "audio_flow_shift": 3.0,
    "seed": 1101,
    "prompt": prompt,
}
Path("segment-1.json").write_text(
    json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8"
)
PY

curl -sS -X POST http://127.0.0.1:30010/v1/videos \
  -H 'Content-Type: application/json' \
  -d @segment-1.json
```

- 三条分别替换 `segment-N.txt` 与 `segment-N.json`；**不要**改 `task`、`conditions`、`target`、`num_inference_steps`、`flow_shift`、`audio_flow_shift`。
- 480 快速预览只把 `short_edge` 改为 `480`，提示词不动。
- 提交后按 `GET /v1/videos/{id}` 轮询，完成后 `GET /v1/videos/{id}/content` 下载 mp4。
- 本机服务需先在控制台启动 H3（GPU1，绑 `0.0.0.0` 或 `127.0.0.1` 均可；本机生成用 `127.0.0.1:30010`）。

---

## 6. 后期拼接与验收

### 6.1 拼接

- 三段**硬切**；第 1 段结束在同一段船舷，第 2 段从水花散开处接；第 2 段结束在刻痕居中，第 3 段从船头靠岸接。
- 每段 H3 输出约 10.125 秒；若成片要求精确 30.000 秒，每段尾部裁约 0.125 秒，或整体接受 30.375 秒。
- 旁白按第 2.2 节后期统一配音；BGM 按第 2.3 节后期铺一条完整 30 秒主题；字幕/标题后期叠加，不写进提示词。

### 6.2 验收检查

- [ ] 三条请求均为 `task=t2va`、`conditions=[]`、9:16、10 秒、736（或 480）、20 步，无图像/音频参考。
- [ ] 每条 prompt 均含 `integrated_multimodal_description`、`[Shot 1]`/`[Shot 2]`/`[Shot 3]`、时间码、`overall_soundscape`、`non_diegetic_music`。
- [ ] 画面无文字、招牌、字幕、水印；若 H3 自行补出文字，后期裁切/遮盖或重跑该段。
- [ ] 楚人/船夫头颈连接、黑手掌、无手指、四头身、衣饰与青铜剑连续；硬切后允许轻微漂移，但需人工验收。
- [ ] 船舷刻痕位置不变；船始终朝画面上方；第 3 段远处江心有且仅有一点金属微光。
- [ ] 旁白 34/36/36 字，按 4 字/秒可落在 30 秒内；若配音超时，优先删形容词而非改画面。

### 6.3 已知风险 / 待确认

- **待补充**：最终交付用 736 还是 480（本稿默认 736；480 仅快速预览）。
- **待补充**：配音员/TTS 选型与授权、BGM 曲目/授权、终混标准。
- **待补充**：更高步数（30/40）是否值得单段试验；本文只用已跑通的 20 步。
- **待补充**：本机已验证的 payload 较短（约 120 词），本稿 prompt 更长；H3 对长 prompt 的遵循度与长度上限未做专项验证。若接口报错，优先删减环境形容词，保留角色锁定、三拍动作、音频结构。
- 角色一致性没有参考图锁定，只能靠逐段完整文字锁定与人工验收；不承诺“完全一致”。
- H3 可能生成画面文字；提示词已写负面约束，但仍需逐段检查。

---

## 7. 本次修改清单

1. **删除角色参考图依赖**：原“每段附同一张确认图”改为纯文本锁定；明确本机 FL2VA 无图像/音频参考输入。
2. **删除“三段旁白完全一致”的承诺**：改为后期统一配音，H3 不生成旁白；配音员/TTS 待补充。
3. **BGM 降级为后期**：H3 只生成环境声/SFX，后期铺一条 30 秒主题，硬切处不重启；曲目/授权待补充。
4. **旁白压缩**：从约 41 字/段压到 34/36/36 字/段，按 4 字/秒约 8.5–9 秒/段，消除 10.25 秒超时风险。
5. **补齐参数**：`t2va`、`conditions: []`、`seconds: 10`、`short_edge: 736`（480 可选）、`num_inference_steps: 20`、`flow_shift: 12.0`、`audio_flow_shift: 3.0`、`seed: 1101`。
6. **重写提示词**：每条按 `integrated_multimodal_description` + `[Shot N]` + 时间码 + `overall_soundscape` + `non_diegetic_music` 组织；保留原三拍、运镜与竖屏构图。
7. **保留原提案**：`proposal.md` 未改动；本文件为独立另存稿。
