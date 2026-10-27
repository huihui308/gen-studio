# 《刻舟求剑》Segment 1（0–10s）生成记录

- 日期：2026-10-27
- 依据：`stickman/kezhou-qiujian/proposal-h3.md` §4「Segment 1 · 0–10s」、§5 请求模板
- 范围：**仅第 1 段（0–10s）**；Segment 2（10–20s）与 Segment 3（20–30s）**未生成**
- 提示词：`segment-1.txt`（3235 字符 / 530 词，逐字取自 proposal §4 代码块，保留 4 行结构）

## 请求参数（两条仅 `short_edge` 不同）

| 字段 | 值 |
|---|---|
| model | `/mnt/sdb1/soft/deploy-models/h3/models/MiniMax-H3` |
| task | `t2va` |
| conditions | `[]` |
| seconds / target.duration_seconds | `10` / `10.0` |
| aspect_ratio | `9:16` |
| short_edge | **736**（交付档）/ **480**（快速预览） |
| num_outputs_per_prompt | `1` |
| num_inference_steps | `20` |
| flow_shift / audio_flow_shift | `12.0` / `3.0` |
| seed | `1101` |

请求体：`segment-1.json`（736）、`segment-1-480.json`（480）。

## 任务与耗时（服务：SGLang 0.5.22 + MiniMax-H3 FL2VA，GPU1）

| 档位 | job id | 结果 | 耗时 | 峰值显存 |
|---|---|---|---|---|
| 736 | `4d2af2c0-8e00-4d29-942d-b22482a5ffb4` | 完成（曾被 DELETE，但推理已启动、仍跑完并落盘） | 11:14→12:01，约 47 min | 11.7 GB |
| 480 | `915cb566-8ff5-4087-aefb-dd257ce76049` | 完成 | `inference_time_s` = 903.1 s ≈ 15 min | 10814 MB |

- 736 任务在提交后约 0.8 min 即被服务取走开跑；中途 `DELETE /v1/videos/{id}` 只删除 API 记录，**不中断已开始的推理**，故其按 736 速度跑完全程。
- 480 任务在 736 之后排队自动开始，二者串行，未并发抢占显存。

## 产物（`h3/output/`）

| 文件 | 画布 | 说明 |
|---|---|---|
| `kezhou_qiujian_seg1_480x864_10s_s1101.mp4` | 480×864 | 480 快速预览原片（原生，无裁切） |
| `kezhou_qiujian_seg1_736x1312_10s_s1101.mp4` | 736×1312 | 736 交付档原生渲染 |
| `kezhou_qiujian_seg1_720x1280_10s_s1101.mp4` | 720×1280 | 736 档裁切交付版（`crop=720:1280:8:16`，音轨 copy） |
| `kezhou_seg1_480_sheet.jpg` | — | 480 版 1s 步长联络图 |
| `kezhou_seg1_sheet.jpg` | — | 736 版 1s 步长联络图 |
| `kezhou_seg1_736_vs_480.jpg` | — | 两档在 t≈0.2/3.2/6.2/9.8 s 的对照 |

## 技术核验（ffprobe）

两档均与本机基线一致：

- 视频 h264，`r_frame_rate=24/1`，`nb_frames=243`，`duration=10.125000`
- 音频 aac，32000 Hz，stereo，duration 10.125000
- 容器 `format.duration=10.157`（尾部 mux 余量），无 10s 之后的内容
- 音量（volumedetect）：736 版 mean −31.6 dB / max −6.3 dB；480 版 mean −38.0 dB / max −11.0 dB，无削波

## 内容观察

- 两档均完整落在 Segment 1 两拍内：`[Shot 1]` 0–3s 船头中景（楚人 + 船夫）、`[Shot 2]` 3s 起青铜剑脱鞘、翻转下落、入水水花，结尾回到同一段船舷与水波。
- 角色锁定成立：圆白脑袋、束发髻、深青交领 + 土黄短褐、黑色椭圆手掌、腰间青铜剑（全片唯一金属反光）；船夫宽檐竹斗笠、花白短须、竹篙。
- 画面无文字/字幕/水印（逐帧抽查未发现 H3 自行补出的文字，优于 2026-10-26 面摊 V3 的招牌问题）。
- 偏差（需人工验收，不阻塞「覆盖第一个 10s」这一验收项）：`[Shot 1]` 的低角度前推被弱化为接近平视的固定机位；736 与 480 因 seed 相同但分辨率不同，构图与个别中间帧（如 480 版 9.8s 右下黑块入画）存在差异，硬切拼接时需注意衔接。
- 提示词 530 词，远长于本机已验证的约 120 词 payload；H3 未报错，且两拍动作与角色锁定均被遵循。

## 未做的事

- 未生成 Segment 2 / Segment 3，未做 30s 拼接。
- 未做旁白、BGM、字幕（按 proposal §2 全部属后期）。
- 未写 `h3/payloads/` 副本；请求体留在本目录 `segment-1*.json`。
