# gen-studio

[English](README.md) | **简体中文**

在本机（2 × RTX 3080 Ti 12 GB，driver 580.196.07）上**可复现地部署开源生图 / 生视频模型**，并规划一个访问它们的 **web 生成工作台**：环境构建 → 权重下载 → 起服务 → 发请求 → 界面。

只提交脚本与文档；conda 环境、模型权重、推理产物、日志均不入库（见 `.gitignore`）。

- 主远端：GitHub `git@github.com:huihui308/gen-studio.git`（`origin`）
- 镜像远端：Gitee `git@gitee.com:huihui308/deploy-models.git`（`gitee`，仓库仍用旧名，改名后同步更新）
- 本机工作目录仍为 `/mnt/sdb1/soft/deploy-models`：conda 环境（`h3/env`、`h3/env312`）里已烘焙绝对路径，**暂不改目录名**，改名会让环境失效

## 目录

```
h3/                     MiniMax-H3（FL2VA：图/文 → 视频 + 音频）
  download_root.sh      下载 sglang 所需的“根布局”权重，断点续传（默认 8 并发）
  download_fl2va.sh     旧版：按 FL2VA/ 子目录布局下载
  serve_recipe_a.sh     SGLang Recipe A（12 GB 显存档）起服务
  request_480p.sh       发一条 864×480 / 5s / 20 步 t2va 请求并下载 mp4
web/                    （规划中）生成工作台：任务队列 + 画廊 + 多模型 adapter
```

## h3

- 模型：MiniMaxAI/MiniMax-H3，来源 [ModelScope](https://modelscope.cn/models/MiniMax/MiniMax-H3)，FL2VA 全量约 144 GB
- 推理框架：SGLang 0.5.21（`sglang[diffusion]`）
- 起服务参数：`--model-variant fl2va --performance-mode memory` +
  `--layerwise-offload-components dit,text_encoder,vae`（DiT / 文本编码器 / VAE 全部 layerwise offload），
  监听 `127.0.0.1:30010`
- 环境：conda 独立环境（`h3/env312`，Python 3.12），依赖装在该环境内（`h3/env312/bin/sglang`）

### 1. 环境

```bash
cd h3
conda create -p ./env312 python=3.12 -y
./env312/bin/pip install -i https://pypi.tuna.tsinghua.edu.cn/simple "sglang[diffusion]"
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

## web（规划中）

`web/` 将在模型跑通后加入：FastAPI + 单页前端，任务队列串行调度（单卡一次只跑一个生成），
每个模型一个薄 adapter，产物落 `web/data/<job_id>/`。设计见 `second-brain/notes/web-studio.md`。

---

[English README](README.md)
