# deploy-models

本仓库记录在本机（2 × RTX 3080 Ti 12 GB，driver 580.196.07）上部署各类开源大模型的**可复现脚本**：
环境构建 → 权重下载 → 起服务 → 发请求。

只提交脚本与文档；conda 环境、模型权重、推理产物、日志均不入库（见 `.gitignore`）。

## 目录

```
h3/                     MiniMax-H3（FL2VA：图/文 → 视频 + 音频）
  download_root.sh      下载 sglang 所需的“根布局”权重，断点续传（默认 8 并发）
  download_fl2va.sh     旧版：按 FL2VA/ 子目录布局下载
  serve_recipe_a.sh     SGLang Recipe A（12 GB 显存档）起服务
  request_480p.sh       发一条 864×480 / 5s / 20 步 t2va 请求并下载 mp4
```

## h3

- 模型：MiniMaxAI/MiniMax-H3，来源 [ModelScope](https://modelscope.cn/models/MiniMax/MiniMax-H3)，FL2VA 全量约 144 GB
- 推理框架：SGLang 0.5.21（`sglang[diffusion]`）
- 起服务参数：`--model-variant fl2va --performance-mode memory` +
  `--layerwise-offload-components dit,text_encoder,vae`（DiT / 文本编码器 / VAE 全部 layerwise offload），
  监听 `127.0.0.1:30010`
- 环境：conda 独立环境 `h3/env`（Python 3.10.22），依赖装在该环境内（`h3/env/bin/sglang`）

### 1. 环境

```bash
cd h3
conda create -p ./env python=3.10 -y
./env/bin/pip install -i https://pypi.tuna.tsinghua.edu.cn/simple "sglang[diffusion]"
```

### 2. 权重

```bash
./download_root.sh          # 根布局（推荐）：transformer/ text_encoder/ vae/ audio_vae/ tokenizer/ processor/ + 根 json
# 旧布局：./download_fl2va.sh
```

### 3. 起服务

```bash
./serve_recipe_a.sh         # 前台常驻，另开终端发请求
```

### 4. 发请求

```bash
./request_480p.sh           # 产物落到 h3/output/h3_480p_<时:分:秒>.mp4
```

接口流程：`POST /v1/videos` 建任务 → `GET /v1/videos/{id}` 轮询 `status` →
`GET /v1/videos/{id}/content` 下载 mp4。
