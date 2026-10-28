# 刻舟求剑 · 30 秒竖屏短片

[English](README.md) | **简体中文**

用 **三段独立生成的 10 秒片段**合成的 30 秒竖屏短片：MiniMax-H3 FL2VA，SGLang 后端，单张 12 GB 的 RTX 3080 Ti，三次 `t2va` 请求，最后在剪辑软件里硬切拼接。

题材是战国寓言《刻舟求剑》：楚人的青铜剑从行船上掉进江里，他不去船现在的位置找，反而在船舷上刻记号，记住剑落水的地方。

![成品 30 秒的三个节拍](preview.jpg)

**[▶ 观看 30 秒成品](kezhou-qiujian-30s.mp4)** —— 480×864，24 fps，30.417 秒，6.5 MB。

| | |
|---|---|
| 成品 | `kezhou-qiujian-30s.mp4` —— H.264 480×864，24 fps，730 帧，**30.417 秒**，AAC 44.1 kHz 立体声，6 765 192 字节 |
| 素材片段 | 三段 H3 渲染 —— H.264 480×864，24 fps，243 帧，每段 **10.125 秒**，AAC 32 kHz 立体声 |
| 生成环境 | 单卡（RTX 3080 Ti 12 GB），SGLang 0.5.22 + MiniMax-H3 FL2VA，`task=t2va`，无参考图 |
| 剪辑软件 | 剪映 / CapCut（待补充：版本与导出设置） |

## 九项固定参数

三条请求除提示词外完全一致：

| # | 参数 | 取值 |
|---|---|---|
| 1 | `task` | `t2va` |
| 2 | `conditions` | `[]`（纯文本；无图像、无音频参考） |
| 3 | `seconds` | `10` |
| 4 | `target.aspect_ratio` | `9:16` |
| 5 | `target.short_edge` | `480` |
| 6 | `num_inference_steps` | `20` |
| 7 | `flow_shift` | `12.0` |
| 8 | `audio_flow_shift` | `3.0` |
| 9 | `seed` | `1101` |

`seconds: 10` 只是请求的时长；H3 恒返回 **243 帧 = 10.125 秒**。若要精确卡到 30.000 秒，需在剪辑里把每段尾部约 0.125 秒裁掉。

## 三段提示词（原文）

下面是实际发给 `/v1/videos` 的字符串，一段一条。同时保存为 `segment-{1,2,3}-h3skill.txt`；包进完整请求体后是 `segment-{1,2,3}-h3skill.json`。

结构遵循 [`h3-prompt-writing`](../../.claude/skills/h3-prompt-writing/SKILL.md) 技能的 T2VA 模式：只保留三个字段（`integrated_multimodal_description`、`overall_soundscape`、`non_diegetic_music`），**首镜不写时间码**，后续镜头用递增切点（`At 00:03.000` / `At 00:07.000`）引出，运镜写成「类型 + 幅度 + 速度」，非叙事音乐写 `N/A`（配乐一律后期）。

### 第 1 段 · 0–10 秒

- 字符数 **3043** · sha1 `959131aec5`
- job `6ab8978b-e764-4bf1-9b53-a38f2bf8c9aa` · 推理 **875.9 秒**
- 内容：渡船行进中的船头全景：楚人双手负后站在船头，船夫在后撑篙。青铜剑脱鞘坠江，镜头跟拍下落直到水花，最后回到同一段船舷。

```text
integrated_multimodal_description: [Shot 1] 2D-animated, colored hand-painted scenery with stick figures, A5 historical-cartoon style, vertical 9:16 framing, Warring States Chu, a broad ink-green river under pale blue-white mist. A low-angle medium shot from the bow of a wooden ferry boat moving toward the top of the frame holds a 30–35-year-old Chu man standing at the bow with his hands clasped behind his back, his hemp robe and hem lifting in the wind. He has a round white head, a short white neck about one tenth of the head diameter, the white jaw continuing into the neck and collar with a black outline only at the sides, about four heads tall, and a head clearly narrower than his shoulders. His eyes are solid black vertical ovals, his nose a short shallow black arc, his mouth a short black line, and his eyebrows straight and slightly thick; he wears a topknot with no bangs and no beard. A deep-blue cross-collar inner robe sits under an earth-yellow hemp short jacket with a cloth belt and a hem above the knees; his bare forearms and calves are drawn with single thin black strokes, his palms are solid black ovals with one subtle thumb lobe at about eighteen percent of the head diameter and no fingers or finger gaps; he wears straw sandals and a bronze sword at his waist, the only metal glint in the frame. Behind him a 50–55-year-old boatman poles the boat: same head and neck construction, solid black vertical oval eyes, two short laugh lines at the eye corners, a slightly downturned mouth, and a short gray beard ringing the jaw without covering the neck connection; his topknot is hidden under a wide-brim bamboo hat, he wears an earth-brown coarse hemp jacket with rolled sleeves and a rope belt, his lower legs are bare, and he has straw sandals, the same black mitten-like palms, a bamboo pole, and a slight stoop. The camera pushes in with small amplitude at slow speed along the gunwale at a low angle, with wet wood, rope, and river water filling the foreground. [Shot 2] At 00:03.000, the camera cuts to a low-angle shot as the boat jolts: the bronze sword slips from its scabbard and falls vertically toward the river, turning once. The camera tracks the falling sword and tilts down with small amplitude at fast speed, keeping it on the central vertical axis while the Chu man's hand reaches too late and the boat keeps moving toward the top of the frame. [Shot 3] At 00:07.000, the camera cuts back to the same section of gunwale as the sword enters the water and a white splash bursts up into the foreground, briefly wiping across the lens; the river surface settles while the Chu man leans over the side with one black palm on the wood, staring down.

overall_soundscape: A broad river current runs under steady wind through hemp cloth, with the wooden hull creaking beneath the two men. A short metallic slip and hiss comes from the scabbard, followed by a clean splash and water droplets falling onto wet wood. Distant water birds call across the river as the surface settles.

non_diegetic_music: N/A
```

### 第 2 段 · 10–20 秒

- 字符数 **3108** · sha1 `45f6e8e7f5`
- job `54644b0b-fe3c-4158-af39-a55c134de0ad` · 推理 **873.6 秒**
- 内容：他蹲下，从袖中抽出短刀，在船舷上刻下一道横向记号。切到记号特写（木屑卷落），随后起身、用黑色手掌按住记号、另一手指向江面。

```text
integrated_multimodal_description: [Shot 1] 2D-animated, colored hand-painted scenery with stick figures, A5 historical-cartoon style, vertical 9:16 framing, Warring States Chu, a broad ink-green river under pale blue-white mist. A medium shot at water level holds a 30–35-year-old Chu man crouching beside the gunwale of a wooden ferry boat while the white splash on the river below him disperses. He has a round white head, a short white neck about one tenth of the head diameter, the white jaw continuing into the neck and collar with a black outline only at the sides, about four heads tall, and a head clearly narrower than his shoulders. His eyes are solid black vertical ovals, his nose a short shallow black arc, his mouth a short black line, and his eyebrows straight and slightly thick; he wears a topknot with no bangs and no beard. A deep-blue cross-collar inner robe sits under an earth-yellow hemp short jacket with a cloth belt and a hem above the knees; his bare forearms are drawn with single thin black strokes, his palms are solid black ovals with one subtle thumb lobe at about eighteen percent of the head diameter and no fingers or finger gaps, and a bronze sword stays at his waist as the only metal glint in the frame. He pulls a short knife from his sleeve. Behind him a 50–55-year-old boatman stands in the background and poles the boat with a bamboo pole, shaking his head: same head and neck construction, solid black vertical oval eyes, two short laugh lines at the eye corners, a slightly downturned mouth, and a short gray beard ringing the jaw without covering the neck connection; his topknot is hidden under a wide-brim bamboo hat, he wears an earth-brown coarse hemp jacket with rolled sleeves and a rope belt, his lower legs are bare, he has straw sandals and the same black mitten-like palms, and he carries a slight stoop. The boat keeps moving toward the top of the frame while the camera pushes in with small amplitude at slow speed toward the knife. [Shot 2] At 00:03.000, the camera cuts to a close-up of the knife tip carving one horizontal notch into the gunwale, with small wood shavings curling and falling away. The notch sits on the same gunwale section and stays centered in the vertical frame, and the knife is the only sharp metal glint besides the bronze sword at his waist. [Shot 3] At 00:07.000, the camera cuts back to the Chu man as he rises and presses one black palm over the notch, pointing at the river with his other hand, certain of himself. The boatman keeps poling in the background and shakes his head again, and the camera tilts up with small amplitude at slow speed from a low angle to a level view, keeping the notch centered while the boat continues toward the top of the frame.

overall_soundscape: River water laps the wooden hull beneath steady wind over the river. A short metallic slip sounds as the knife leaves the sleeve, followed by the soft scrape of the blade cutting wood and small shavings dropping onto the deck. The bamboo pole creaks with each stroke, and one quiet exhale leaves the crouching man.

non_diegetic_music: N/A
```

### 第 3 段 · 20–30 秒

- 字符数 **2782** · sha1 `2e1c8a985a`
- job `6350908b-a3ee-4bad-a5b7-7a2fc2239969` · 推理 **871.7 秒**
- 内容：船头靠上浅滩，他跳入水中；水下在记号下方摸索，河床只有沙石没有剑。他浮出水面摊开空手掌，镜头升高拉远成俯视大全景，收在远处江心的一点微光。

```text
integrated_multimodal_description: [Shot 1] 2D-animated, colored hand-painted scenery with stick figures, A5 historical-cartoon style, vertical 9:16 framing, Warring States Chu, a broad ink-green river under pale blue-white mist. The bow of a wooden ferry boat slides onto a shallow shore in a level side view, and a 30–35-year-old Chu man stands on deck pointing at one horizontal notch carved into the gunwale. He has a round white head, a short white neck about one tenth of the head diameter, the white jaw continuing into the neck and collar with a black outline only at the sides, about four heads tall, and a head clearly narrower than his shoulders. His eyes are solid black vertical ovals, his nose a short shallow black arc, his mouth a short black line, and his eyebrows straight and slightly thick; he wears a small topknot with no bangs and no beard on his chin, cheeks, or upper lip. A deep-blue cross-collar inner robe sits under an earth-yellow hemp short jacket with a cloth belt and a hem above the knees; his bare forearms and calves are drawn with single thin black strokes, his palms are solid black ovals with one subtle thumb lobe at about eighteen percent of the head diameter and no fingers or finger gaps; he wears straw sandals and a bronze sword at his waist, the only metal glint in the frame. He pushes off the gunwale and jumps into the river, and the camera tracks his jump with small amplitude at slow speed in a level side view until the splash swallows the frame. [Shot 2] At 00:03.000, the camera cuts to a low-angle underwater view in green river water: the Chu man gropes beneath the notch, his solid black vertical oval eyes open, his eyebrows straight and his mouth a short black line, bubbles rising past his white head and empty black palm. The riverbed below him is sand and small pale pebbles, and no sword lies on it. The camera holds a static shot as his hand sweeps across the sand with small amplitude at slow speed. [Shot 3] At 00:07.000, the camera cuts to the river surface as the Chu man breaks through it, wet and dripping, and holds up one empty black palm. The camera pulls out and pedestals up with large amplitude at slow speed into a high overhead view: the wooden ferry waits at the shallow shore, the ink-green river stretches away toward pale mist, and one tiny metal glint flashes at the distant center of the river. The shot ends on that distant glint and the wide river surface.

overall_soundscape: Water slaps the wooden hull as the bow touches the shallow shore under wind across the river. A jump is followed by a clean splash, then underwater bubbles and a muffled current before the surface breaks with water dripping from cloth. A single distant bird calls as the river surface settles.

non_diegetic_music: N/A
```

## 如何复现

### 1. 起 H3 服务

按 [`../../README.md`](../../README.md) 建好环境、下完 `FL2VA/` 权重，然后：

```bash
cd h3 && ./serve_recipe_a.sh        # SGLang，监听 127.0.0.1:30010
```

等到 `GET /health` 返回 `{"status":"ok"}`。

### 2. 逐段发请求

本目录下每个 JSON 都是可直接投递的完整请求体（提示词已包含）：

```bash
cd stickman/kezhou-qiujian
ID=$(curl -sS -X POST http://127.0.0.1:30010/v1/videos \
        -H 'Content-Type: application/json' \
        -d @segment-1-h3skill.json | python3 -c 'import json,sys;print(json.load(sys.stdin)["id"])')

# 轮询到 status == completed
curl -sS http://127.0.0.1:30010/v1/videos/$ID

# 下载 mp4
curl -sS -L http://127.0.0.1:30010/v1/videos/$ID/content -o segment-1.mp4
```

把 `segment-1-h3skill.json` 换成 `segment-2-h3skill.json`、`segment-3-h3skill.json` 各跑一次。**请求要一条一条发**——12 GB 卡上并发会直接把显存打满。

### 3. 合成 30 秒

1. 新建 **480×864、24 fps** 的竖屏工程。
2. 把三段按顺序首尾相接放到时间线上，**只用硬切，不加转场**。
3. 总长 = 3 × 10.125 秒 ≈ 30.375 秒；实际导出文件测得 30.417 秒 / 730 帧。
4. 导出前先把三条音轨拉到同一响度：原始片段的音量偏小且不一致（mean −31.9 / −32.5 / −33.2 dB），成品每 10 秒实测 mean −26.0 / −25.6 / −27.1 dB。
5. 导出（待补充：剪映导出参数，如码率 / 编码档位）。

## 这些参数各自意味着什么

- **每次 10 秒**：H3 单次支持 4–15 秒，30 秒不可能一次出，只能拆成三次。
- **`short_edge: 480`**：12 GB 卡上的务实档。实测单条推理 **871.7–875.9 秒**（38–39 秒/步）。第 1 段另有一条 `short_edge: 736` 的渲染，约 47 分钟，**没有用在这次成片里**（待补充：是否重出 736 版本）。
- **三段都用 `seed: 1101`**：并不等于角色一致。没有参考图时每次生成都是独立事件，早期第 3 段曾长出不存在的胡须，后来把锁定写得更显式才修掉。
- **`t2va` + `conditions: []`**：一切都得靠文字交代——画风、四头身的火柴人设计、船、青铜剑。这就是每条提示词 2.8–3.1 千字符、并完整重复角色锁定的原因。

## 提示词有两套，成片只用了其中一套

| 版本 | 文件 | 说明 |
|---|---|---|
| proposal 逐字稿版 | `segment-{1,2,3}.txt`、`segment-{1,2,3}.json`、`segment-{1,2,3}-480.json` | 完全按 `proposal-h3.md` §4 原文，四行，含一行技能结构里没有的 `Negative constraints` |
| 技能结构版（**成片用的就是这套**） | `segment-{1,2,3}-h3skill.txt` / `.json` | 按 `h3-prompt-writing` 的 T2VA 结构重写；角色与道具锁定完整重述，而不是写「remain identical to Segment 1」——独立请求里那句话等于没写 |

成品在多个时间点与两套渲染做过比对（第 1 段还做了按时间轴对齐的 PSNR 扫描），确认**用的是技能结构版**，不是逐字稿版。

## 这一版没做到的事

- 全程没有参考图：跨段角色一致性只靠文字，会漂，做不到像素级锁定（待补充：是否引入首帧参考重出）。
- 第 2 段那把「短刀」，实际渲染出来的刀刃比短刀大得多——H3 对尺寸措辞不敏感。
- 音频是生成的、不是配乐：H3 只出同期环境声，旁白与 BGM 属于后期（待补充：成品是否已含旁白 / BGM）。
- 成片是 480p，没有 736p 的成片母版。

## 文件说明

| 文件 | 内容 |
|---|---|
| `kezhou-qiujian-30s.mp4` | 30 秒成片 |
| `preview.jpg` | 三段的代表帧，供本 README 使用 |
| `proposal-h3.md` | 镜头方案与 H3 可执行提示词稿 |
| `proposal.md` | 原始故事提案（保留备查） |
| `segment-{1,2,3}-h3skill.txt` | 发给 H3 的三段提示词原文 |
| `segment-{1,2,3}-h3skill.json` | 同上，包成可投递的请求体 |
| `segment-{1,2,3}.txt` / `.json` / `-480.json` | 早期逐字稿版提示词 |
| `segment-{1,2,3}-generation.md` | 各段生成记录（job、耗时、产出情况） |

## 授权

模型为 [MiniMax-H3](https://modelscope.cn/models/MiniMax/MiniMax-H3)，在自有硬件上生成。仓库整体采用 MIT（待补充：成品视频与提示词的授权口径）。
