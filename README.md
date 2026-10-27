# gen-studio

**English** | [简体中文](README.zh-CN.md)

Reproducible deployment of open-source **image and video generation models** on a single workstation (2 × RTX 3080 Ti, 12 GB each, driver 580.196.07), plus a **web console** to operate them: environment build → weight download → serve → request → login-protected UI for starting/stopping models on a chosen GPU and watching their status.

Only scripts and docs are committed. Conda envs, model weights, inference outputs and logs stay out of git (see `.gitignore`).

- Primary remote: GitHub `git@github.com:huihui308/gen-studio.git` (`origin`)
- Mirror remote: `gitee` (private, address omitted)
- The local working directory is still `/mnt/sdb1/soft/deploy-models`: the conda envs (`h3/env`, `h3/env312`) bake absolute paths, so **the directory is intentionally not renamed** — moving it breaks both envs

## Layout

```
h3/                     MiniMax-H3 (FL2VA: image/text → video + audio)
  download_fl2va.sh     Download the FL2VA/ sub-directory weights SGLang needs (resumable, 8 jobs by default)
  download_root.sh      Download the "root layout" (diffusers modular format, for ComfyUI/diffusers; SGLang cannot read it)
  serve_recipe_a.sh     Serve with SGLang Recipe A (12 GB VRAM profile), CUDA JIT fixes included
  request_480p.sh       Send one 864×480 / 5 s / 20-step t2va request and save the mp4
qwen-image/             Qwen-Image 2.1 Turbo, text to image (ComfyUI 0.42.0, int8_convrot), listens on 127.0.0.1:30020
  download_turbo.sh     Fetch DiT / text encoder / VAE from ModelScope (resumable, byte-size verified)
  serve.sh              Serve ComfyUI on GPU 0, port 30020, outputs under qwen-image/output
  t2i.py                Submit one t2i workflow, poll, report wall time and peak VRAM
web/                    Login-protected console: start/stop both models on an explicitly chosen GPU, live status
```

## h3

- Model: MiniMaxAI/MiniMax-H3 from [ModelScope](https://modelscope.cn/models/MiniMax/MiniMax-H3); FL2VA partition ≈ 144 GB
- Inference: SGLang **0.5.22** (`sglang[diffusion]`, torch 2.14.1+cu130); 0.5.9 and older have no H3 code
- Serve flags: `--model-variant fl2va --performance-mode memory` +
  `--layerwise-offload-components dit,text_encoder,vae` (DiT / text encoder / VAE all layerwise-offloaded),
  listening on `127.0.0.1:30010`
- Environment: dedicated conda prefix (`h3/env312`, Python 3.12), dependencies installed inside it (`h3/env312/bin/sglang`)

### 1. Environment

```bash
cd h3
conda create -p ./env312 python=3.12 -y
uv pip install --python ./env312/bin/python --prerelease=allow "sglang[diffusion]==0.5.22"
uv pip install --python ./env312/bin/python ninja
```

- Use **uv**, not pip: the PyPI mirror is ~1 MB/s here and uv fetches wheels in parallel (an order of magnitude faster)
- `--prerelease=allow` is required: the dependency chain pulls `cuda-tile==1.6.0rc5`
- Python ≥ 3.11 is required (`sglang[diffusion]==0.5.22` only ships cp311/cp312/cp313 wheels; on 3.10 you fall back to 0.5.9, which has no H3)

### 2. Weights

```bash
./download_fl2va.sh         # FL2VA/ sub-directory layout — the only one SGLang accepts
```

```bash
# You can also fetch just FL2VA/ and reuse the root layout's text_encoder/tokenizer/processor (34 identical files)
# Source: https://modelscope.cn/models/MiniMax/MiniMax-H3/resolve/master/<path>, resumable via `curl -C -`
```

**Why the FL2VA/ layout**: `--model-variant fl2va` resolves the model path to `<model-path>/FL2VA` and requires a
`model_index.json` there that carries `_minimax_h3` metadata (partition / tasks / sigma_shift_scales). The root layout is
diffusers modular format (`MiniMaxH3ModularPipeline`): it has no `_minimax_h3`, and its transformer weight names and shards
(`diffusion_pytorch_model-0000N-of-00014` vs FL2VA's `model-0000N-of-00013`) are entirely different — you cannot symlink
or rename your way between the two.

### 3. Serve

```bash
./serve_recipe_a.sh         # stays in the foreground; open another terminal to send requests
```

The script bakes in three JIT fixes (missing any of them fails on the first request, see Known issues):
`CUDA_HOME` pointing at the pip-installed cu13 toolkit inside `env312`, the `lib64` / `libcudart.so` symlinks, and `ninja` on PATH.

### 4. Request

```bash
./request_480p.sh           # writes h3/output/h3_480p_<HH:MM:SS>.mp4
```

API flow: `POST /v1/videos` creates a job → `GET /v1/videos/{id}` polls `status` →
`GET /v1/videos/{id}/content` downloads the mp4. The `model` field may be the local model path or any string (the server does not validate it).

### 5. Measured on this box (single GPU1, 3080 Ti 12 GB)

| Target | Steps | Per step | Denoise stage | Decode stage | End to end |
|---|---|---|---|---|---|
| 864×480 / 5 s | 20 | 18.1 s/it | 364 s | 30 s | **≈ 7 min** |
| 864×480 / 4 s | 2 | 18.5 s/it | 61 s | 60 s | ≈ 2.5 min |
| 1344×768 / 4 s | 2 | 42.8 s/it | 88 s | 42 s | ≈ 2.5 min |

- Peak VRAM **11.2 GiB / 12 GiB** (drops back to ~3 GiB when idle); at 768p a 20-step clip extrapolates to ≈ 14–15 min
- Output: 864×480 h264 + AAC 32 kHz stereo, 5.17 s, 415 KB; the frame matches the prompt (red-panda barista pouring latte art)
- Host RAM: transformer resident 51.2 GiB + 10.3 GiB pageable, text encoder 47.4 GiB pageable, video VAE mmap 4.5 GiB;
  during decode ~4.6 GiB gets evicted from page cache and is re-read from disk (/mnt/sdb1 RAID measured at 341 MB/s sequential)

### 6. Known issues

1. System `/usr/local/cuda` pointed at 11.8 → JIT build failed with `nvcc fatal : Value 'c++20' is not defined for option 'std'`;
   use the cu13 toolkit shipped inside `env312` (nvcc 13.4) instead.
2. The JIT link line hard-codes `-L$CUDA_HOME/lib64 -lcudart`, but the pip package only has `lib/` and no unversioned
   `libcudart.so` → add `lib64 -> lib` and `libcudart.so -> libcudart.so.13` symlinks.
3. Without `ninja`, warmup reports `FileNotFoundError: 'ninja'`: the server still starts, but the first request always fails.
4. Omitting `--model-variant fl2va` → `Model directory .../FL2VA does not contain model_index.json`.
5. The first request compiles JIT kernels and is 1–2 min slower than later identical requests.
6. 12 GB is a hard ceiling: 480p is comfortable; 768p runs but leaves little headroom; anything above that does not fit.

## qwen-image

Text-to-image with **Qwen-Image-2.1-Turbo** on a single 12 GB card, driven through **ComfyUI 0.42.0** (`/prompt` HTTP API).

- Model: `Comfy-Org/Qwen-Image-2.1` from [ModelScope](https://modelscope.cn/models/Comfy-Org/Qwen-Image-2.1) — **there is no Qwen-Image-2.0 release**; the open 2.x line is `Qwen/Qwen-Image-2.1` and `Qwen/Qwen-Image-2.1-Turbo`
- Why Turbo: same architecture and same 14.23 GB bf16 DiT as the base model (**no VRAM saving**), but its 8-step schedule is baked into the checkpoint and runs at `cfg=1`, so it is ~10× faster than base (40 steps + CFG ≈ 80 DiT passes vs 8). Trade-off: no negative prompt at `cfg=1`
- Why int8: bf16 stack is 33.13 GB (DiT 14.23 + text encoder 17.53 + VAE 0.68). Ampere (SM 8.6) has **no native FP8**, so the usable native ops are `int8_tensorwise` / `int8_convrot` / `asym_w4a8_int8`; `comfy-kitchen` reports fp8/mxfp8/nvfp4 as *emulated*
- Why ComfyUI instead of diffusers: `QwenImage21Pipeline` needs diffusers ≥ 0.41.0.dev and GitHub/HuggingFace are unreachable from this box, while ComfyUI 0.42.0 ships `image_model: qwen_image21` natively

### 1. Environment

```bash
cd qwen-image
git clone https://gitee.com/mirrors/ComfyUI.git ComfyUI   # GitHub is unreachable here; pinned commit 0dfb874b == 0.42.0
git -C ComfyUI checkout 0dfb874b0bf8301ccf0dbb59264d9733fec9137e
conda create -y -p ./env python=3.12
# torch must be cu130 (comfy-kitchen requirement); do not let pip pick the slow mirror for nvidia wheels
./env/bin/pip install --index-url https://pypi.tuna.tsinghua.edu.cn/simple \
  filelock typing_extensions sympy networkx jinja2 fsspec numpy pillow
./env/bin/pip install --index-url https://download.pytorch.org/whl/cu130 --no-deps torch torchvision torchaudio
./env/bin/pip install --index-url https://download.pytorch.org/whl/cu130 setuptools cuda-toolkit==13.0.3 \
  "cuda-toolkit[cublas,cudart,cufft,cufile,cupti,curand,cusolver,cusparse,nvjitlink,nvrtc,nvtx]==13.0.3" \
  "cuda-bindings>=13.0.3,<14" nvidia-cudnn-cu13==9.24.0.43 nvidia-cusparselt-cu13==0.8.1 \
  nvidia-nccl-cu13==2.30.7 nvidia-nvshmem-cu13==3.4.5 "triton~=3.8.0"
grep -v comfyui-workflow-templates ComfyUI/requirements.txt > /tmp/req_fast.txt   # ~600 MB of UI media, not needed to serve
./env/bin/pip install --index-url https://pypi.tuna.tsinghua.edu.cn/simple -r /tmp/req_fast.txt
```

### 2. Weights

```bash
./download_turbo.sh        # 14.5 GB total, resumable, verifies exact byte sizes
./fetch_te_int8.sh         # optional: the int8_convrot text encoder the official template uses
```

| Role | File | Bytes | Destination |
|---|---|---|---|
| DiT (Turbo, int8_convrot) | `qwen_image_2.1_turbo_int8_convrot.safetensors` | 7 256 787 829 | `ComfyUI/models/diffusion_models/` |
| Text encoder (w4a8, default) | `qwen3vl_8b_w4a8.safetensors` | 6 312 105 364 | `ComfyUI/models/text_encoders/` |
| Text encoder (int8_convrot, template default) | `qwen3vl_8b_int8_convrot.safetensors` | 9 350 798 360 | `ComfyUI/models/text_encoders/` |
| VAE | `qwen_image_2.1_vae_bf16.safetensors` | 675 509 688 | `ComfyUI/models/vae/` |
| Turbo LoRA (optional) | `qwen_image_2.1_turbo_lora_avg_rank_178_bf16.safetensors` | 913 314 512 | `ComfyUI/models/loras/` |

The 9.35 GB int8 text encoder and the 6.76 GB DiT cannot be resident at once on 12 GB, so the 5.88 GB w4a8 encoder roughly halves the time to first image (119 s → 51 s at 1024², same peak VRAM). Both stay on disk; `t2i.py --te` selects one.

### 3. Serve / request

```bash
./serve.sh                                                  # CUDA_VISIBLE_DEVICES=0, 127.0.0.1:30020
./t2i.py --width 1024 --height 1024 --prompt "a cozy bookshop cafe at dusk"
```

ComfyUI takes ~4 minutes on the first start (import + CUDA JIT); a log stuck at `Using pytorch attention` is normal. The graph `t2i.py` submits mirrors the official template `image_qwen_image_2_1_turbo_t2i.json` minus its subgraph and prompt enhancer:

`UNETLoader → TextEncodeQwenImage21(type=qwen_image) → CFGGuider(cfg 1) → SamplerCustomAdvanced(ManualSigmas, euler, RandomNoise) → VAEDecode → SaveImage`

Sampling is the Turbo 8-step schedule baked into the template: sigmas `1.0, 0.978453, 0.95418, 0.926626, 0.89508, 0.845148, 0.704534, 0.414568, 0`, sampler `euler`, `cfg=1`. Changing the step count alone does nothing — the schedule is what makes 8 steps enough.

### 4. Measured on this box (GPU0, 3080 Ti 12 GB)

| Size | Wall time | Peak VRAM |
|---|---|---|
| 1024×1024 | 51 s (warm-up run) / 28 s (warm) | 11 739 / 11 709 MiB |
| 1328×1328 | 24 s | 11 755 MiB |
| 1536×1536 | 34 s | 11 725 MiB |
| 2048×2048 (native 2K) | 50 s | 11 741 MiB |

All sizes fit; peak VRAM stays pinned at ~11.7/12 GiB because ComfyUI streams quantized weights from pinned RAM. Chinese text renders accurately (`千问图像 2.1` came out character-perfect); unspecified signage in a prompt becomes pseudo-glyphs, which is expected.

### 5. Known issues

1. The PyPI mirror here throttles `nvidia-*` wheels to ~230 KiB/s while `download.pytorch.org` gives 12 MB/s for the same file — install torch and its CUDA deps from the pytorch cu130 index.
2. The first ComfyUI start takes ~4 min (CUDA JIT); do not kill it while the log sits at `Using pytorch attention`.
3. `comfyui-workflow-templates` pulls ~600 MB of UI media and its `media-assets-02` pin is missing on the mirror — skip it; only the UI template panel is affected.
4. `cfg=1` means the negative prompt is ignored; you need the base model (40+ steps) for negative prompting.
5. Peak VRAM leaves ~0.5 GiB of headroom: adding the prompt-enhancer model (6.7–9.5 GB) or tiling/upscaling on top will not fit in one pass.

## web console

`web/` is a FastAPI + Jinja2 console (no frontend build step) bound to `127.0.0.1:8090` by default. Its original scope is five operational requirements; a sixth page for H3 video generation was added later:

1. **Login** — username/password only; default `admin / admin`, PBKDF2 hashes on disk, server-side sessions; every functional page redirects to `/login` when unauthenticated.
2. **Password settings** — `/password` changes the password (current password + policy + CSRF) and revokes other sessions.
3. **Model control** — start/stop **MiniMax H3** and **Qwen-Image 2.0**; starting requires an explicit GPU selection and injects `CUDA_VISIBLE_DEVICES`; each model also has its own listen-address setting (`127.0.0.1` / `0.0.0.0`) injected as `GEN_STUDIO_MODEL_HOST` on the next start (default loopback); when `nvidia-smi` yields no GPUs the page says so and refuses to start rather than using a default.
4. **Run status** — reads `/proc`, `nvidia-smi`, and each model's own HTTP API; unavailable metrics are labelled unavailable with a reason (e.g. SGLang exposes no queue endpoint).
5. **Business-style UI** — navy/gold console styling, white cards, status pills, responsive layout.
6. **H3 video generation (later extension)** — `/generate` submits a text prompt to the local H3 `/v1/videos` API, polls the job, shows progress, then plays the downloaded MP4 and echoes the prompt and parameters. Defaults: 480 short edge, 10 seconds, fixed 16:9; duration is 4–15 seconds. Jobs live in console memory and there is no gallery or history list.

Each model runs in its own transient systemd user unit (`gen-studio-minimax-h3`, `gen-studio-qwen-image2.0`). Restarting the console therefore does not stop or reload a running model; the console reconnects to the existing unit after a restart.

```bash
cd web
uv venv .venv
uv pip install -r requirements.txt
./serve.sh          # http://127.0.0.1:8090
```

Details, requirement-by-requirement verification, security notes and tests: [`web/README.md`](web/README.md).

## License

MIT — see [LICENSE](LICENSE).

---

[简体中文说明](README.zh-CN.md)
