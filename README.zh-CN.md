# gen-studio

[English](README.md) | **简体中文**

在本机（2 × RTX 3080 Ti 12 GB，driver 580.196.07）上**可复现地部署开源生图 / 生视频模型**，并提供访问它们的 **web 控制台**：环境构建 → 权重下载 → 起服务 → 发请求 → 登录后按所选显卡启停模型、核对运行状态。

只提交脚本与文档；conda 环境、模型权重、推理产物、日志均不入库（见 `.gitignore`）。

- 主远端：GitHub `git@github.com:huihui308/gen-studio.git`（`origin`）
- 镜像远端：`gitee`（私有仓，地址略）
- 本机工作目录仍为 `/mnt/sdb1/soft/deploy-models`：conda 环境（`h3/env`、`h3/env312`）里已烘焙绝对路径，**暂不改目录名**，改名会让环境失效

## 目录

```
h3/                     MiniMax-H3（FL2VA：图/文 → 视频 + 音频）
  download_fl2va.sh     下载 sglang 所需的 FL2VA/ 子目录权重，断点续传（默认 8 并发）
  download_root.sh      下载“根布局”权重（diffusers modular 格式，给 ComfyUI/diffusers 用；sglang 读不了）
  serve_recipe_a.sh     SGLang Recipe A（12 GB 显存档）起服务，已内置 CUDA JIT 修补
  request_480p.sh       发一条 864×480 / 5s / 20 步 t2va 请求并下载 mp4
qwen-image/             Qwen-Image 2.1 Turbo 文生图（ComfyUI 0.42.0，int8_convrot），监听 127.0.0.1:30020
  download_turbo.sh     从 ModelScope 拉 DiT / 文本编码器 / VAE（断点续传，按字节数校验）
  serve.sh              在 GPU0 上起 ComfyUI，端口 30020，产物落 qwen-image/output
  t2i.py                提交一次文生图 workflow、轮询、报耗时与显存峰值
web/                    登录式控制台：按所选显卡启停两个模型，查看真实运行状态
```

## h3

- 模型：MiniMaxAI/MiniMax-H3，来源 [ModelScope](https://modelscope.cn/models/MiniMax/MiniMax-H3)，FL2VA 分区约 144 GB
- 推理框架：SGLang **0.5.22**（`sglang[diffusion]`，torch 2.14.1+cu130）；更早的 0.5.9 没有 H3 代码
- 起服务参数：`--model-variant fl2va --performance-mode memory` +
  `--layerwise-offload-components dit,text_encoder,vae`（DiT / 文本编码器 / VAE 全部 layerwise offload），
  监听 `127.0.0.1:30010`
- 环境：conda 独立环境（`h3/env312`，Python 3.12），依赖装在该环境内（`h3/env312/bin/sglang`）

### 1. 环境

```bash
cd h3
conda create -p ./env312 python=3.12 -y
uv pip install --python ./env312/bin/python --prerelease=allow "sglang[diffusion]==0.5.22"
uv pip install --python ./env312/bin/python ninja
```

- 用 **uv** 而不是 pip：本机到 PyPI 镜像只有 ~1 MB/s，uv 并行拉 wheel 快一个数量级
- `--prerelease=allow` 必须加：依赖链里有 `cuda-tile==1.6.0rc5`
- 必须 Python ≥ 3.11（`sglang[diffusion]==0.5.22` 只发 cp311/cp312/cp313 wheel，3.10 会掉到没有 H3 的 0.5.9）

### 2. 权重

```bash
./download_fl2va.sh         # FL2VA/ 子目录布局（sglang 唯一认的布局）
```

```bash
# 也可以只补 FL2VA/（本机已复用根布局里的 text_encoder/tokenizer/processor，34 个文件同名同大小）
# 下载源：https://modelscope.cn/models/MiniMax/MiniMax-H3/resolve/master/<path>，支持 -C - 断点续传
```

**为什么要 FL2VA/ 布局**：`--model-variant fl2va` 会把模型路径解析成 `<model-path>/FL2VA`，并要求其中的
`model_index.json` 带 `_minimax_h3` 元数据（partition / tasks / sigma_shift_scales）。根布局是 diffusers
modular 格式（`MiniMaxH3ModularPipeline`），既没有 `_minimax_h3`，transformer 的权重名与分片（`diffusion_pytorch_model-0000N-of-00014`
vs FL2VA 的 `model-0000N-of-00013`）也完全不同，**不能靠软链或改名互转**。

### 3. 起服务

```bash
./serve_recipe_a.sh         # 前台常驻，另开终端发请求
```

脚本内置三个 JIT 修补（缺任一都会在第一条请求 build kernel 时失败，详见「已知坑」）：
`CUDA_HOME` 指向 env312 内 pip 的 cu13 toolkit、补 `lib64`/`libcudart.so` 软链、确保 `ninja` 在 PATH。

### 4. 发请求

```bash
./request_480p.sh           # 产物落到 h3/output/h3_480p_<时:分:秒>.mp4
```

接口流程：`POST /v1/videos` 建任务 → `GET /v1/videos/{id}` 轮询 `status` →
`GET /v1/videos/{id}/content` 下载 mp4。README 里的 `model` 字段填本地模型路径或任意字符串均可（服务端不校验）。

### 5. 实测（本机，单卡 GPU1 3080 Ti 12 GB）

| 目标 | 步数 | 每步 | 去噪阶段 | 解码阶段 | 端到端 |
|---|---|---|---|---|---|
| 864×480 / 5 s | 20 | 18.1 s/it | 364 s | 30 s | **≈ 7 min** |
| 864×480 / 4 s | 2 | 18.5 s/it | 61 s | 60 s | ≈ 2.5 min |
| 1344×768 / 4 s | 2 | 42.8 s/it | 88 s | 42 s | ≈ 2.5 min |

- 峰值显存 **11.2 GiB / 12 GiB**（请求结束回落到 ~3 GiB）；768p 20 步推算 ≈ 14–15 min/条
- 产物：864×480 h264 + AAC 32 kHz 立体声，5.17 s，415 KB；画面与提示词（红熊猫咖啡师拉花）一致
- 主机内存：transformer 常驻 51.2 GiB + pageable 10.3 GiB、text_encoder pageable 47.4 GiB、video VAE mmap 4.5 GiB；
  decode 阶段有 4.6 GiB 被挤出 page cache 后从磁盘重读（/mnt/sdb1 RAID 实测顺序读 341 MB/s）

### 6. 已知坑

1. 系统 `/usr/local/cuda` 原指向 11.8 → JIT 编译报 `nvcc fatal : Value 'c++20' is not defined for option 'std'`；
   改用 env312 内 pip 的 cu13 toolkit（nvcc 13.4）即可。
2. JIT 链接写死 `-L$CUDA_HOME/lib64 -lcudart`，而 pip 包只有 `lib/`、且无无版本号 `libcudart.so`
   → 补 `lib64 -> lib`、`libcudart.so -> libcudart.so.13` 两个软链。
3. 缺 `ninja` 时 warmup 报 `FileNotFoundError: 'ninja'`：服务能起，但第一条请求必失败。
4. 漏了 `--model-variant fl2va` → `Model directory .../FL2VA does not contain model_index.json`。
5. 首条请求含 JIT 编译，比后续同参数请求慢 1–2 min。
6. 12 GB 是硬上限：480p 稳定；768p 能跑但显存更紧，再往上一档（1024p+）没有余量。

## qwen-image

单张 12 GB 卡上的 **Qwen-Image-2.1-Turbo** 文生图，框架用 **ComfyUI 0.42.0**（`/prompt` HTTP API）。

- 模型：`Comfy-Org/Qwen-Image-2.1`（[ModelScope](https://modelscope.cn/models/Comfy-Org/Qwen-Image-2.1)）——**官方没有 Qwen-Image-2.0**，2.x 开源只有 `Qwen/Qwen-Image-2.1` 与 `Qwen/Qwen-Image-2.1-Turbo`
- 为什么 Turbo：与 base 同架构、同 14.23 GB bf16 DiT（**不省显存**），但 8 步 schedule 烘在 checkpoint 里、`cfg=1` 运行，比 base（40 步 + CFG ≈ 80 次 DiT 前向）快约 10×。代价：`cfg=1` 下负向提示词无效
- 为什么要量化：bf16 全家桶 33.13 GB（DiT 14.23 + 文本编码器 17.53 + VAE 0.68）。Ampere（SM 8.6）**没有原生 FP8**，可用的 native op 只有 `int8_tensorwise` / `int8_convrot` / `asym_w4a8_int8`；`comfy-kitchen` 把 fp8/mxfp8/nvfp4 标为 *emulated*
- 为什么用 ComfyUI 而不是 diffusers：`QwenImage21Pipeline` 要 diffusers ≥ 0.41.0.dev，且本机 GitHub/HuggingFace 不通；ComfyUI 0.42.0 原生带 `image_model: qwen_image21`

### 1. 环境

```bash
cd qwen-image
git clone https://gitee.com/mirrors/ComfyUI.git ComfyUI   # 本机 GitHub 不通；固定 commit 0dfb874b == 0.42.0
git -C ComfyUI checkout 0dfb874b0bf8301ccf0dbb59264d9733fec9137e
conda create -y -p ./env python=3.12
# torch 必须 cu130（comfy-kitchen 的要求）；nvidia 依赖别让 pip 挑清华源（那里只有 230 KiB/s）
./env/bin/pip install --index-url https://pypi.tuna.tsinghua.edu.cn/simple \
  filelock typing_extensions sympy networkx jinja2 fsspec numpy pillow
./env/bin/pip install --index-url https://download.pytorch.org/whl/cu130 --no-deps torch torchvision torchaudio
./env/bin/pip install --index-url https://download.pytorch.org/whl/cu130 setuptools cuda-toolkit==13.0.3 \
  "cuda-toolkit[cublas,cudart,cufft,cufile,cupti,curand,cusolver,cusparse,nvjitlink,nvrtc,nvtx]==13.0.3" \
  "cuda-bindings>=13.0.3,<14" nvidia-cudnn-cu13==9.24.0.43 nvidia-cusparselt-cu13==0.8.1 \
  nvidia-nccl-cu13==2.30.7 nvidia-nvshmem-cu13==3.4.5 "triton~=3.8.0"
grep -v comfyui-workflow-templates ComfyUI/requirements.txt > /tmp/req_fast.txt   # 约 600 MB 的 UI 媒体包，起服务用不到
./env/bin/pip install --index-url https://pypi.tuna.tsinghua.edu.cn/simple -r /tmp/req_fast.txt
```

### 2. 权重

```bash
./download_turbo.sh        # 合计 14.5 GB，断点续传，按字节数校验
./fetch_te_int8.sh         # 可选：官方模板默认用的 int8_convrot 文本编码器
```

| 角色 | 文件 | 字节 | 落盘 |
|---|---|---|---|
| DiT（Turbo, int8_convrot） | `qwen_image_2.1_turbo_int8_convrot.safetensors` | 7 256 787 829 | `ComfyUI/models/diffusion_models/` |
| 文本编码器（w4a8，默认） | `qwen3vl_8b_w4a8.safetensors` | 6 312 105 364 | `ComfyUI/models/text_encoders/` |
| 文本编码器（int8_convrot，模板默认） | `qwen3vl_8b_int8_convrot.safetensors` | 9 350 798 360 | `ComfyUI/models/text_encoders/` |
| VAE | `qwen_image_2.1_vae_bf16.safetensors` | 675 509 688 | `ComfyUI/models/vae/` |
| Turbo LoRA（可选） | `qwen_image_2.1_turbo_lora_avg_rank_178_bf16.safetensors` | 913 314 512 | `ComfyUI/models/loras/` |

9.35 GB 的 int8 文本编码器与 6.76 GB 的 DiT 在 12 GB 卡上无法同时驻留，换成 5.88 GB 的 w4a8 后首张 1024² 从 119 s 降到 51 s（峰值显存基本相同）。两个文件都留在盘上，`t2i.py --te` 选择。

### 3. 起服务 / 发请求

```bash
./serve.sh                                                  # CUDA_VISIBLE_DEVICES=0，127.0.0.1:30020
./t2i.py --width 1024 --height 1024 --prompt "a cozy bookshop cafe at dusk"
```

ComfyUI 首次启动约 4 分钟（import + CUDA JIT），日志停在 `Using pytorch attention` 属正常。`t2i.py` 提交的图等价于官方模板 `image_qwen_image_2_1_turbo_t2i.json`（去掉子图与提示词增强器）：

`UNETLoader → TextEncodeQwenImage21(type=qwen_image) → CFGGuider(cfg 1) → SamplerCustomAdvanced(ManualSigmas, euler, RandomNoise) → VAEDecode → SaveImage`

采样用模板里的 Turbo 8 步 schedule：sigmas `1.0, 0.978453, 0.95418, 0.926626, 0.89508, 0.845148, 0.704534, 0.414568, 0`，sampler `euler`，`cfg=1`。单独改步数没有意义——8 步够用正是因为这条 schedule。

### 4. 本机实测（GPU0，3080 Ti 12 GB）

| 分辨率 | 耗时 | 峰值显存 |
|---|---|---|
| 1024×1024 | 51 s（预热那轮）/ 28 s（热跑） | 11 739 / 11 709 MiB |
| 1328×1328 | 24 s | 11 755 MiB |
| 1536×1536 | 34 s | 11 725 MiB |
| 2048×2048（原生 2K） | 50 s | 11 741 MiB |

四档全部不 OOM；峰值贴着 11.7/12 GiB，因为 ComfyUI 把量化权重压在 pinned RAM 里按需搬。中文文字渲染准确（「千问图像 2.1」逐字正确）；提示词里没指定的招牌会变伪汉字，属正常。

### 5. 已知坑

1. 本机清华源对 `nvidia-*` 轮子限速到 ~230 KiB/s，同一个包 `download.pytorch.org` 是 12 MB/s —— torch 系依赖走 pytorch cu130 index。
2. ComfyUI 首次启动约 4 分钟（CUDA JIT），日志停在 `Using pytorch attention` 时别杀进程。
3. `comfyui-workflow-templates` 会拖 ~600 MB 的 UI 媒体包，且它的 `media-assets-02` 版本在镜像上缺失 —— 跳过，只影响 UI 模板面板。
4. `cfg=1` 意味着负向提示词被忽略；要用负向提示词得换 base（40+ 步）。
5. 峰值显存只剩 ~0.5 GiB 余量：再叠提示词增强器（6.7–9.5 GB）或放大/超分不会一次装进 12 GB。

## web 控制台

`web/` 是 FastAPI + Jinja2 服务端渲染的控制台（无前端构建），只监听 `127.0.0.1:8090`，范围严格限定为 5 项运维需求：

1. **登录** — 仅用户名 + 密码，默认 `admin / admin`；密码以 PBKDF2 哈希落盘，服务端会话；未登录访问任一功能页统一跳转 `/login`。
2. **密码设置** — `/password` 修改密码（校验当前密码、策略与 CSRF），并注销其它会话。
3. **模型控制** — 启动 / 停止 **MiniMax H3** 与 **Qwen-Image 2.0**；启动必须显式选择显卡并注入 `CUDA_VISIBLE_DEVICES`；每个模型还可独立设置监听地址（`127.0.0.1` / `0.0.0.0`），下次启动时注入 `GEN_STUDIO_MODEL_HOST`（默认回环）；`nvidia-smi` 取不到显卡时明确提示并拒绝启动，绝不静默使用默认卡。
4. **运行状态** — 数据来自 `/proc`、`nvidia-smi` 与模型自身 HTTP API；无法获取的指标标注「不可用」并说明原因（例如 SGLang 未暴露队列接口）。
5. **商务风界面** — 深海军蓝 + 暗金、白色卡片、状态药丸、响应式布局。

```bash
cd web
uv venv .venv
uv pip install -r requirements.txt
./serve.sh          # http://127.0.0.1:8090
```

不提供生成请求、任务队列或作品画廊。逐条需求验证方式、安全说明与测试见 [`web/README.md`](web/README.md)。

## License

MIT，详见 [LICENSE](LICENSE)。

---

[English README](README.md)
