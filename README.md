# gen-studio

**English** | [简体中文](README.zh-CN.md)

Reproducible deployment of open-source **image and video generation models** on a single workstation (2 × RTX 3080 Ti, 12 GB each, driver 580.196.07), plus a **web studio** to drive them (planned): environment build → weight download → serve → request → UI.

Only scripts and docs are committed. Conda envs, model weights, inference outputs and logs stay out of git (see `.gitignore`).

- Primary remote: GitHub `git@github.com:huihui308/gen-studio.git` (`origin`)
- Mirror remote: `gitee` (private, address omitted)
- The local working directory is still `/mnt/sdb1/soft/deploy-models`: the conda envs (`h3/env`, `h3/env312`) bake absolute paths, so **the directory is intentionally not renamed** — moving it breaks both envs

## Layout

```
h3/                     MiniMax-H3 (FL2VA: image/text → video + audio)
  download_root.sh      Download the "root layout" weights SGLang expects (resumable, 8 jobs by default)
  download_fl2va.sh     Legacy: download by the FL2VA/ sub-directory layout
  serve_recipe_a.sh     Serve with SGLang Recipe A (12 GB VRAM profile)
  request_480p.sh       Send one 864×480 / 5 s / 20-step t2va request and save the mp4
web/                    (planned) generation studio: job queue + gallery + per-model adapters
```

## h3

- Model: MiniMaxAI/MiniMax-H3 from [ModelScope](https://modelscope.cn/models/MiniMax/MiniMax-H3); full FL2VA weights ≈ 144 GB
- Inference: SGLang 0.5.21 (`sglang[diffusion]`)
- Serve flags: `--model-variant fl2va --performance-mode memory` +
  `--layerwise-offload-components dit,text_encoder,vae` (DiT / text encoder / VAE all layerwise-offloaded),
  listening on `127.0.0.1:30010`
- Environment: dedicated conda prefix (`h3/env312`, Python 3.12), dependencies installed inside it (`h3/env312/bin/sglang`)

### 1. Environment

```bash
cd h3
conda create -p ./env312 python=3.12 -y
./env312/bin/pip install -i https://pypi.tuna.tsinghua.edu.cn/simple "sglang[diffusion]"
```

### 2. Weights

```bash
./download_root.sh          # root layout (recommended): transformer/ text_encoder/ vae/ audio_vae/ tokenizer/ processor/ + root json
# legacy layout: ./download_fl2va.sh
```

### 3. Serve

```bash
./serve_recipe_a.sh         # stays in the foreground; open another terminal to send requests
```

### 4. Request

```bash
./request_480p.sh           # writes h3/output/h3_480p_<HH:MM:SS>.mp4
```

API flow: `POST /v1/videos` creates a job → `GET /v1/videos/{id}` polls `status` →
`GET /v1/videos/{id}/content` downloads the mp4.

## web (planned)

`web/` will be added once a model runs end to end: FastAPI + single-page frontend, a serial job queue (one generation at a time on a single GPU), one thin adapter per model, artifacts under `web/data/<job_id>/`. Design notes are kept in a private local knowledge base (not published).

## License

MIT — see [LICENSE](LICENSE).

---

[简体中文说明](README.zh-CN.md)
