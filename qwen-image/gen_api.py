#!/usr/bin/env python3
"""Qwen-Image-2.1-Turbo HTTP 生图服务（文生图 + 参考图生图/编辑）

对外提供 HTTP 接口，供其他服务调用；底层把请求拼成 ComfyUI API 格式工作流，
提交到本机已部署的 ComfyUI（默认 127.0.0.1:30020），能力与官网
Qwen-Image-2.1-Turbo 对齐：提示词 + 参考图（≤16 张）→ 生成/编辑。

启动:
  ./gen_api.py                          # 0.0.0.0:30021（局域网设备可直连；本机用设 --host 127.0.0.1）
  ./gen_api.py --port 30021 --host 0.0.0.0
  GEN_STUDIO_IMAGE_API_HOST=0.0.0.0 ./gen_api.py

────────────────────────────────────────────────────────────────
接口
────────────────────────────────────────────────────────────────
GET  /health      存活与后端可达性（响应含 auth_required：鉴权是否开启）
GET  /result      下载产物：/result?filename=xx.png&subfolder=qwen-image-2.1-turbo
POST /generate    生成图片（JSON 或 multipart/form-data 上传）

POST /v1/images/generations  OpenAI 兼容文生图（见文末「OpenAI 兼容端点」）
GET  /v1/models              OpenAI 兼容模型列表（鉴权开启时受保护）

────────────────────────────────────────────────────────────────
请求参数（JSON 示例）
────────────────────────────────────────────────────────────────
{
  "prompt":      "把 <image1> 重绘成水彩绘本画风，保持构图不变",   # 必填
  "images":      ["/abs/ref1.png", {"path": "ref2.png"}, {"b64": "<base64>"}],
                                                   # 可选，≤16 张；image_1 为编辑目标
  "resolution":  1024,        # 可选，参考图面积预算（边长），64~2048 且为 32 倍数
  "width":       1024,        # 可选，输出宽；给了必须与 height 成对，32 倍数 ≤2048
  "height":      1024,        # 可选，输出高
  "seed":        42,          # 可选，缺省随机
  "cfg":         1.0,         # 可选；Turbo 官方路径 cfg=1（负向提示词不生效）
  "negative_prompt": "",      # 可选，仅 cfg>1 时有意义
  "tag":         "i2i",       # 可选，产物文件名前缀标记
  "return_b64":  false,       # 可选，true 时返回 base64
  "timeout_s":   600          # 可选，等待生成的超时
}

multipart/form-data：同名普通字段同上；任意文件字段（file/ref/... 均可）作为参考图，
按出现顺序对应 image_1..image_N。

尺寸语义（对齐官方模板）：
  - 给了 width/height：按该尺寸出图（custom_size 路径，编辑模式下需与 image_1
    尺寸接近，否则画面会漂移）。
  - 未给 + 有参考图：输出画布跟随 image_1，面积 ≈ resolution²（保持宽高比）。
  - 未给 + 无参考图：输出 resolution × resolution 方图。

────────────────────────────────────────────────────────────────
响应
────────────────────────────────────────────────────────────────
成功 200:
{
  "ok": true, "prompt_id": "...", "seed": 42,
  "width": 1024, "height": 1024,          # 实际产物尺寸
  "resolution": 1024, "elapsed_s": 31.4, "peak_vram_mib": 11741,
  "images": [
    {"path": "/mnt/.../qwen-image/output/qwen-image-2.1-turbo/i2i-...png",
     "filename": "i2i-..._00001_.png", "subfolder": "qwen-image-2.1-turbo",
     "url": "http://127.0.0.1:30020/view?filename=...&subfolder=...&type=output"}
  ],
  "images_b64": ["data:image/png;base64,..."]   # 仅 return_b64=true
}
失败 4xx/5xx:
{"ok": false, "error": {"code": "missing_prompt", "message": "...", "details": ...}}

错误码：missing_prompt / invalid_param / too_many_images / image_not_found /
unsupported_image_type / payload_too_large（4xx）；invalid_api_key / key_disabled（401，
鉴权拒绝，带 WWW-Authenticate: Bearer）；comfy_unreachable（503）/
generation_timeout（504）/ comfy_rejected / generation_failed（502）/ internal_error（500）

────────────────────────────────────────────────────────────────
调用示例
────────────────────────────────────────────────────────────────
# 文生图
curl -s http://127.0.0.1:30021/generate -H 'Content-Type: application/json' \
  -d '{"prompt":"一只雪豹站在雪山之巅，写实摄影","resolution":1024}'

# 参考图生图（本地路径；提示词里用 <image1> 指代）
curl -s http://127.0.0.1:30021/generate -H 'Content-Type: application/json' \
  -d '{"prompt":"把 <image1> 重绘成水彩绘本画风，保持构图与主体不变",
       "images":["/mnt/sdb1/soft/deploy-models/qwen-image/output/qwen-image-2.1-turbo/t2i1024-warm-1024x1024-12345_00001_.png"],
       "resolution":1024}'

# 上传参考图（multipart）
curl -s http://127.0.0.1:30021/generate -F prompt='把 <image1> 变成吉卜力动画风' \
  -F resolution=1024 -F file=@ref.png

────────────────────────────────────────────────────────────────
接口鉴权（实际行为）
────────────────────────────────────────────────────────────────
- key 文件：web/data/api_keys.json（web 控制台签发/启停，本服务只读）；路径可用
  env GEN_STUDIO_API_KEYS_FILE 覆盖。
- 启用规则：文件存在 → 鉴权开启；不存在 → 关闭（兼容旧用法）；JSON 损坏 → 视为
  「开启且零 key」fail-closed（远程全拒，日志提示一次）。每请求前 stat 该文件，
  mtime/size 变了就重读——控制台建 key 后无需重启服务。
- 受保护端点（鉴权开启时）：POST /generate、POST /v1/images/generations、
  GET /v1/models、GET /result；GET /health 与根路径永远开放。
- 传 key（按顺序取）：Authorization: Bearer <key> → X-API-Key: <key> →
  查询参数 ?api_key=<key>；hmac.compare_digest 比对且必须命中 enabled=true 条目：
  缺失/不匹配 → 401 invalid_api_key；命中但 enabled=false → 401 key_disabled
  （两种 401 均带 WWW-Authenticate: Bearer；日志里 api_key=*** 脱敏并带 key id）。
- 回环豁免：客户端 127.0.0.1 / ::1 跳过一切校验（控制台代理与本机脚本零改动）；
  env GEN_STUDIO_API_KEYS_TRUST_LOOPBACK=0 关闭豁免（严格模式/测试用）。
- 调用统计：凭 key 通过鉴权且生成成功的请求记入 web/data/api_keys_usage.json
  （env GEN_STUDIO_API_KEYS_USAGE_FILE 覆盖，默认同目录 api_keys_usage.json；
  0600 原子写，写失败只记日志，绝不影响响应）。

────────────────────────────────────────────────────────────────
端口对外暴露（实际行为）
────────────────────────────────────────────────────────────────
默认绑 127.0.0.1；跨机调用设 GEN_STUDIO_IMAGE_API_HOST=0.0.0.0（或 --host），
配合上面的鉴权一起用；key 文件不存在时服务不设防，仍建议仅在内网/防火墙内开放。

────────────────────────────────────────────────────────────────
OpenAI 兼容端点（路径前缀 /v1，鉴权开启时同样受保护）
────────────────────────────────────────────────────────────────
POST /v1/images/generations  文生图：model / prompt / n（必须为 1）/ size /
                             response_format(url|b64_json) / seed；未知字段忽略；
                             错误体为 OpenAI 风格 {"error":{message,type,code}}
GET  /v1/models              模型列表 {"object":"list","data":[...]}

# 鉴权开启时的 curl 示例（key 从 web 控制台拿）
curl -s http://127.0.0.1:30021/generate -H 'Content-Type: application/json' \
  -H 'Authorization: Bearer sk-...' \
  -d '{"prompt":"一只雪豹站在雪山之巅，写实摄影"}'

curl -s http://127.0.0.1:30021/v1/images/generations -H 'Content-Type: application/json' \
  -H 'Authorization: Bearer sk-...' \
  -d '{"prompt":"一只雪豹站在雪山之巅","size":"1024x1024"}'
"""
import argparse
import base64
import hmac
import json
import os
import re
import struct
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_COMFY = os.environ.get("GEN_STUDIO_COMFY_HOST", "127.0.0.1:30020")
COMFY_INPUT = os.path.join(BASE, "ComfyUI", "input")
OUTPUT_DIR = os.path.join(BASE, "output")
OUTPUT_SUBDIR = "qwen-image-2.1-turbo"
GPU_INDEX = "0"                     # 与 serve.sh 的 CUDA_VISIBLE_DEVICES=0 对应

TURBO_SIGMAS = "1.0, 0.978453, 0.95418, 0.926626, 0.89508, 0.845148, 0.704534, 0.414568, 0"
DIT = "qwen_image_2.1_turbo_int8_convrot.safetensors"
TE_DEFAULT = "qwen3vl_8b_w4a8.safetensors"
VAE = "qwen_image_2.1_vae_bf16.safetensors"

MAX_IMAGES = 16                      # TextEncodeQwenImage21 Autogrow 上限（提示：官方模板只用 10）
MAX_BODY = 128 * 1024 * 1024         # 请求体上限（含 base64 参考图）
MIN_SIZE, MAX_SIZE, SIZE_STEP = 64, 2048, 32

# ───────────────────────── API Key 鉴权 ─────────────────────────
# key 由 web 控制台写入 web/data/api_keys.json（本服务只读）：文件存在=开启鉴权，
# 不存在=关闭（兼容旧用法），JSON 损坏=开启且零 key（fail closed，远程全拒）。
KEYS_FILE = (os.environ.get("GEN_STUDIO_API_KEYS_FILE")
             or os.path.join(os.path.dirname(BASE), "web", "data", "api_keys.json"))
USAGE_FILE = (os.environ.get("GEN_STUDIO_API_KEYS_USAGE_FILE")
              or os.path.join(os.path.dirname(KEYS_FILE) or ".", "api_keys_usage.json"))
TRUST_LOOPBACK = os.environ.get("GEN_STUDIO_API_KEYS_TRUST_LOOPBACK", "1") != "0"
LOOPBACK = ("127.0.0.1", "::1")        # 这些来源跳过校验（控制台代理/本机脚本）

# 鉴权开启时的受保护端点；GET /health 与根路径永远开放
PROTECTED_POST = {"/generate", "/v1/images/generations"}
PROTECTED_GET = {"/v1/models", "/result"}

MODELS = ("qwen-image-2.1-turbo", "qwen-image")   # /v1 接受的 model 取值
DEFAULT_MODEL = "qwen-image-2.1-turbo"


class KeyStore:
    """api_keys.json 读取：每请求前 stat，mtime/size 变了就重读（控制台建 key 免重启）"""

    def __init__(self, key_file):
        self.key_file = key_file
        self._sig = ("absent",)         # 与任何 stat 结果都不同，强制首次加载
        self._entries, self._enabled = [], False
        self._corrupt_logged = False
        self._lock = threading.Lock()

    def refresh(self):
        try:
            st = os.stat(self.key_file)
            sig = (st.st_mtime_ns, st.st_size)
        except OSError:
            sig = None
        with self._lock:
            if sig == self._sig:
                return
            self._sig = sig
            if sig is None:
                self._enabled, self._entries = False, []
                self._corrupt_logged = False
                return
            self._enabled = True        # 文件存在即开启鉴权
            try:
                with open(self.key_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                keys = data.get("keys") if isinstance(data, dict) else None
                self._entries = [k for k in (keys or []) if isinstance(k, dict)]
                self._corrupt_logged = False
            except (OSError, ValueError) as e:   # 损坏 → 开启且零 key，fail closed
                self._entries = []
                if not self._corrupt_logged:
                    self._corrupt_logged = True
                    print(f"[auth] key 文件损坏，按「零 key」fail-closed 远程全拒: "
                          f"{self.key_file} ({e})", flush=True)

    @property
    def enabled(self):
        """鉴权是否开启（= key 文件是否存在）"""
        self.refresh()
        return self._enabled

    def authenticate(self, provided):
        """→ (条目, None) 命中 enabled key；(None, None) 鉴权未开启；
        (None, code) 拒绝（invalid_api_key / key_disabled）"""
        self.refresh()
        if not self._enabled:
            return None, None
        if not provided:
            return None, "invalid_api_key"
        blob = provided.encode("utf-8", "replace")
        with self._lock:
            for e in self._entries:
                k = e.get("key")
                if isinstance(k, str) and k and hmac.compare_digest(
                        k.encode("utf-8"), blob):
                    if e.get("enabled") is True:
                        return e, None
                    return None, "key_disabled"
        return None, "invalid_api_key"


KEY_STORE = KeyStore(KEYS_FILE)

_USAGE_LOCK = threading.Lock()


def record_usage(key_id):
    """生成成功后给对应 key 累计一次调用；任何失败只记日志，绝不影响响应"""
    if key_id is None:
        return
    with _USAGE_LOCK:
        try:
            usage = {}
            try:
                with open(USAGE_FILE, "r", encoding="utf-8") as f:
                    old = json.load(f)
                if isinstance(old, dict) and isinstance(old.get("usage"), dict):
                    usage = old["usage"]
            except (OSError, ValueError):
                pass                        # 不存在/损坏 → 从零开始
            ent = usage.get(str(key_id)) or {}
            try:
                calls = int(ent.get("calls") or 0)
            except (TypeError, ValueError):
                calls = 0
            usage[str(key_id)] = {
                "calls": calls + 1,
                "last_used_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            }
            data = {"version": 1, "usage": usage}
            tmp = f"{USAGE_FILE}.{uuid.uuid4().hex}.tmp"
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, USAGE_FILE)    # 原子落盘
        except Exception as e:             # noqa: BLE001
            print(f"[usage] 写入失败（不影响响应）: {USAGE_FILE}: {e}", flush=True)


class ApiError(Exception):
    def __init__(self, status, code, message, details=None):
        super().__init__(message)
        self.status, self.code, self.message, self.details = status, code, message, details


# ───────────────────────────── ComfyUI 交互 ─────────────────────────────

def comfy_url(path, host):
    return f"http://{host}{path}"


def post_json(host, path, payload, timeout=60):
    data = json.dumps(payload).encode()
    req = urllib.request.Request(comfy_url(path, host), data=data,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def get_json(host, path, timeout=30):
    with urllib.request.urlopen(comfy_url(path, host), timeout=timeout) as r:
        return json.load(r)


def gpu_used_mib():
    try:
        out = subprocess.check_output(
            ["nvidia-smi", f"--id={GPU_INDEX}",
             "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
            text=True, timeout=5)
        return int(out.strip().splitlines()[0])
    except Exception:
        return 0


class PeakWatcher(threading.Thread):
    """与 t2i.py 相同：0.2s 采样一次显存峰值"""

    def __init__(self):
        super().__init__(daemon=True)
        self.peak = 0
        self.base = 0
        self.stop_flag = False

    def run(self):
        self.base = gpu_used_mib()
        self.peak = self.base
        while not self.stop_flag:
            self.peak = max(self.peak, gpu_used_mib())
            time.sleep(0.2)


# ───────────────────────────── 工作流拼装 ─────────────────────────────

def build_workflow(prompt, negative_prompt, resolution, seed, cfg, sigmas, prefix,
                   te, ref_names, width, height):
    """拼 ComfyUI API 格式工作流。

    ref_names 非空 → 编辑/参考图路径：LoadImage(image_1..N) → TextEncodeQwenImage21
    （Autogrow 槽位在提示词里是 `images.image_N` 键，已实测：平铺 image_N 会以
    TypeError 在 execute 处失败），采样潜空间用
    该节点的第 3 个输出（latent：按 image_1 尺寸/宽高比生成的空潜空间）。
    ref_names 为空 → 纯文生图：EmptyLatentImage，等价于 t2i.py 的节点图。
    """
    wf = {
        "1": {"class_type": "UNETLoader",
              "inputs": {"unet_name": DIT, "weight_dtype": "default"}},
        "2": {"class_type": "CLIPLoader",
              "inputs": {"clip_name": te, "type": "qwen_image", "device": "default"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": VAE}},
    }
    te_inputs = {"clip": ["2", 0], "prompt": prompt, "negative_prompt": negative_prompt,
                 "vae": ["3", 0], "resolution": resolution}

    if ref_names:
        for i, name in enumerate(ref_names, start=1):
            wf[str(3 + i)] = {"class_type": "LoadImage", "inputs": {"image": name}}
            # Autogrow 槽位在 API 提示词里是带父级前缀的键 images.image_N
            # （执行时由 build_nested_inputs 重组进 execute(images={...})）
            te_inputs[f"images.image_{i}"] = [str(3 + i), 0]
        wf["20"] = {"class_type": "TextEncodeQwenImage21", "inputs": te_inputs}
        if width is not None:
            # custom_size 路径：显式尺寸（需与 image_1 接近，否则画面漂移）
            wf["21"] = {"class_type": "EmptyLatentImage",
                        "inputs": {"width": width, "height": height, "batch_size": 1}}
            latent_src = ["21", 0]
        else:
            latent_src = ["20", 2]          # TextEncode 的 latent 输出
    else:
        wf["20"] = {"class_type": "TextEncodeQwenImage21", "inputs": te_inputs}
        w = width if width is not None else resolution
        h = height if height is not None else resolution
        wf["21"] = {"class_type": "EmptyLatentImage",
                    "inputs": {"width": w, "height": h, "batch_size": 1}}
        latent_src = ["21", 0]

    wf.update({
        "22": {"class_type": "ManualSigmas", "inputs": {"sigmas": sigmas}},
        "23": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "euler"}},
        "24": {"class_type": "RandomNoise", "inputs": {"noise_seed": seed}},
        "25": {"class_type": "CFGGuider",
               "inputs": {"model": ["1", 0], "positive": ["20", 0],
                          "negative": ["20", 1], "cfg": cfg}},
        "26": {"class_type": "SamplerCustomAdvanced",
               "inputs": {"noise": ["24", 0], "guider": ["25", 0], "sampler": ["23", 0],
                          "sigmas": ["22", 0], "latent_image": latent_src}},
        "27": {"class_type": "VAEDecode", "inputs": {"samples": ["26", 0], "vae": ["3", 0]}},
        "28": {"class_type": "SaveImage",
               "inputs": {"images": ["27", 0], "filename_prefix": prefix}},
    })
    return wf


# ───────────────────────────── 参考图处理 ─────────────────────────────

_MAGIC = ((b"\x89PNG\r\n\x1a\n", ".png"), (b"\xff\xd8\xff", ".jpg"),
          (b"GIF87a", ".gif"), (b"GIF89a", ".gif"))


def sniff_ext(blob):
    if blob[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if blob[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if blob[:4] in (b"GIF8",):
        return ".gif"
    if blob[:4] == b"RIFF" and blob[8:12] == b"WEBP":
        return ".webp"
    return None


def decode_b64(text, where):
    if "base64," in text[:100]:
        text = text.split("base64,", 1)[1]
    text = re.sub(r"\s+", "", text)
    try:
        return base64.b64decode(text, validate=True)
    except Exception:
        raise ApiError(400, "invalid_param", f"{where} 的 base64 解码失败")


def image_bytes(item, idx):
    """规范化 images 列表项 → (bytes, 来源描述)；bytes 直接来自 multipart 上传"""
    if isinstance(item, (bytes, bytearray)):
        return bytes(item), f"<upload#{idx}>"
    if isinstance(item, str):
        item = {"path": item}
    if not isinstance(item, dict):
        raise ApiError(400, "invalid_param", f"images[{idx - 1}] 格式错误，应为字符串或对象")
    if "path" in item:
        p = str(item["path"])
        if not os.path.isfile(p):
            raise ApiError(400, "image_not_found", f"images[{idx - 1}] 文件不存在: {p}")
        with open(p, "rb") as f:
            return f.read(), p
    for key in ("b64", "data_url"):
        if key in item:
            return decode_b64(str(item[key]), f"images[{idx}].{key}"), f"<{key}>"
    raise ApiError(400, "invalid_param", f"images[{idx}] 需含 path 或 b64/data_url 字段")


def stage_images(items):
    """参考图落到 ComfyUI input 目录，返回 LoadImage 用的文件名列表（调用方负责清理）"""
    if len(items) > MAX_IMAGES:
        raise ApiError(400, "too_many_images",
                       f"参考图最多 {MAX_IMAGES} 张，收到 {len(items)} 张",
                       {"hint": "官方模板惯例为 ≤10 张；张数越多序列越长，越慢越吃显存"})
    staged = []
    for i, item in enumerate(items, start=1):
        blob, src = image_bytes(item, i)
        ext = sniff_ext(blob)
        if ext is None:
            raise ApiError(400, "unsupported_image_type",
                           f"images[{i - 1}] 不是支持的图片格式（png/jpg/webp/gif）: {src}")
        name = f"genapi_{uuid.uuid4().hex[:10]}{ext}"
        with open(os.path.join(COMFY_INPUT, name), "wb") as f:
            f.write(blob)
        staged.append(name)
    return staged


def cleanup_staged(names):
    for n in names:
        try:
            os.unlink(os.path.join(COMFY_INPUT, n))
        except OSError:
            pass


def image_size(path):
    """读 PNG/JPEG 尺寸；解析失败返回 (None, None)"""
    try:
        with open(path, "rb") as f:
            head = f.read(26)
            if head[:8] == b"\x89PNG\r\n\x1a\n":
                w, h = struct.unpack(">II", head[16:24])
                return w, h
            if head[:2] == b"\xff\xd8":
                f.seek(2)
                while True:
                    b = f.read(1)
                    if not b:
                        break
                    if b != b"\xff":
                        continue
                    marker = f.read(1)
                    while marker == b"\xff":
                        marker = f.read(1)
                    m = marker[0]
                    if m in (0xD8, 0x01) or 0xD0 <= m <= 0xD7:
                        continue
                    ln = struct.unpack(">H", f.read(2))[0]
                    if m in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
                             0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                        data = f.read(ln - 2)
                        h, w = struct.unpack(">HH", data[1:5])
                        return w, h
                    f.seek(ln - 2, 1)
    except Exception:
        pass
    return None, None


# ───────────────────────────── 参数校验 ─────────────────────────────

def _as_int(value, name, lo, hi):
    try:
        v = int(value)
    except (TypeError, ValueError):
        raise ApiError(400, "invalid_param", f"{name} 必须是整数: {value!r}")
    if not (lo <= v <= hi):
        raise ApiError(400, "invalid_param", f"{name} 超出范围 [{lo}, {hi}]: {v}")
    return v


def parse_params(p):
    """p: 已经是 dict（JSON 或 multipart 字段，值可能仍是 str）"""
    prompt = str(p.get("prompt") or "").strip()
    if not prompt:
        raise ApiError(400, "missing_prompt", "prompt 为必填且不能为空")

    resolution = _as_int(p.get("resolution", 1024), "resolution", MIN_SIZE, MAX_SIZE)
    if resolution % SIZE_STEP:
        raise ApiError(400, "invalid_param",
                       f"resolution 必须是 {SIZE_STEP} 的倍数: {resolution}")

    width = height = None
    if p.get("width") not in (None, "") or p.get("height") not in (None, ""):
        if p.get("width") in (None, "") or p.get("height") in (None, ""):
            raise ApiError(400, "invalid_param", "width 与 height 必须成对提供")
        width = _as_int(p["width"], "width", MIN_SIZE, MAX_SIZE)
        height = _as_int(p["height"], "height", MIN_SIZE, MAX_SIZE)
        for name, v in (("width", width), ("height", height)):
            if v % SIZE_STEP:
                raise ApiError(400, "invalid_param",
                               f"{name} 必须是 {SIZE_STEP} 的倍数: {v}")

    seed = p.get("seed")
    if seed in (None, ""):
        seed = int.from_bytes(uuid.uuid4().bytes[:4], "big") % 2**31
    else:
        seed = _as_int(seed, "seed", 0, 2**63 - 1)

    try:
        cfg = float(p.get("cfg", 1.0))
    except (TypeError, ValueError):
        raise ApiError(400, "invalid_param", f"cfg 必须是数字: {p.get('cfg')!r}")
    if not (0.0 < cfg <= 100.0):
        raise ApiError(400, "invalid_param", f"cfg 超出范围 (0, 100]: {cfg}")

    tag = str(p.get("tag") or "").strip()
    tag = re.sub(r"[^A-Za-z0-9_-]", "", tag)[:40]

    rb = p.get("return_b64", False)
    return_b64 = rb if isinstance(rb, bool) else str(rb).lower() in ("1", "true", "yes")

    timeout_s = _as_int(p.get("timeout_s", 600), "timeout_s", 10, 3600)

    return {
        "prompt": prompt,
        "negative_prompt": str(p.get("negative_prompt") or ""),
        "resolution": resolution, "width": width, "height": height,
        "seed": seed, "cfg": cfg, "tag": tag,
        "return_b64": return_b64, "timeout_s": timeout_s,
    }


# ───────────────────────────── 生成主流程 ─────────────────────────────

def generate(params, image_items, comfy_host):
    staged = []
    try:
        staged = stage_images(image_items)
        has_refs = bool(staged)
        tag = params["tag"] or ("i2i" if has_refs else "t2i")
        prefix = f"{OUTPUT_SUBDIR}/{tag}-{params['seed']}"
        wf = build_workflow(params["prompt"], params["negative_prompt"],
                            params["resolution"], params["seed"], params["cfg"],
                            TURBO_SIGMAS, prefix, TE_DEFAULT, staged,
                            params["width"], params["height"])

        watcher = PeakWatcher()
        watcher.start()
        t0 = time.time()
        try:
            try:
                resp = post_json(comfy_host, "/prompt",
                                 {"prompt": wf, "client_id": str(uuid.uuid4())}, timeout=60)
            except urllib.error.HTTPError as e:
                try:
                    body = json.load(e)
                except Exception:
                    body = {"raw": e.read().decode(errors="replace")[:500]}
                raise ApiError(502, "comfy_rejected", "ComfyUI 拒绝了工作流", body)
            except (urllib.error.URLError, OSError, TimeoutError) as e:
                raise ApiError(503, "comfy_unreachable",
                               f"ComfyUI 不可达（{comfy_host}）: {e}")
            prompt_id = resp["prompt_id"]

            entry = None
            while time.time() - t0 < params["timeout_s"]:
                try:
                    hist = get_json(comfy_host, f"/history/{prompt_id}", timeout=30)
                except (urllib.error.URLError, OSError, TimeoutError) as e:
                    raise ApiError(503, "comfy_unreachable", f"轮询 ComfyUI 失败: {e}")
                if prompt_id in hist:
                    entry = hist[prompt_id]
                    break
                time.sleep(0.5)
            elapsed = time.time() - t0
        finally:
            watcher.stop_flag = True

        if entry is None:
            raise ApiError(504, "generation_timeout",
                           f"生成超时（{params['timeout_s']}s）", {"prompt_id": prompt_id})

        status = entry.get("status", {})
        if not status.get("completed"):
            detail = None
            for m in status.get("messages", []):
                if isinstance(m, list) and m and m[0] == "execution_error":
                    detail = m[1]
            raise ApiError(502, "generation_failed",
                           (detail or {}).get("exception_message", "生成失败"),
                           {"prompt_id": prompt_id, "execution_error": detail})

        images = []
        for _nid, out in entry.get("outputs", {}).items():
            for img in out.get("images", []):
                sub = img.get("subfolder", "")
                fname = img["filename"]
                abs_path = os.path.join(OUTPUT_DIR, sub, fname)
                w, h = image_size(abs_path)
                item = {
                    "path": abs_path, "filename": fname, "subfolder": sub,
                    "width": w, "height": h,
                    "url": comfy_url("/view?" + urllib.parse.urlencode(
                        {"filename": fname, "subfolder": sub, "type": "output"}), comfy_host),
                }
                if params["return_b64"]:
                    try:
                        with open(abs_path, "rb") as f:
                            item["b64"] = base64.b64encode(f.read()).decode()
                    except OSError:
                        item["b64"] = None
                images.append(item)
        if not images:
            raise ApiError(502, "generation_failed", "任务完成但没有输出图片",
                           {"prompt_id": prompt_id})

        return {
            "ok": True, "prompt_id": prompt_id, "seed": params["seed"],
            "resolution": params["resolution"],
            "width": images[0].get("width"), "height": images[0].get("height"),
            "elapsed_s": round(elapsed, 1), "peak_vram_mib": watcher.peak,
            "images": images,
        }
    finally:
        cleanup_staged(staged)


# ───────────────────────────── HTTP 服务 ─────────────────────────────

def parse_multipart(body, content_type):
    m = re.search(r'boundary="?([^";]+)"?', content_type)
    if not m:
        raise ApiError(400, "invalid_param", "multipart 缺少 boundary")
    boundary = b"--" + m.group(1).encode()
    fields, files = {}, []
    for part in body.split(boundary):
        if part in (b"", b"\r\n", b"--", b"--\r\n"):
            continue
        if part.startswith(b"--"):
            break
        if part.startswith(b"\r\n"):
            part = part[2:]
        head, sep, rest = part.partition(b"\r\n\r\n")
        if not sep:
            continue
        if rest.endswith(b"\r\n"):
            rest = rest[:-2]
        headers = head.decode("utf-8", "replace")
        cd = re.search(r'name="([^"]*)"(?:;\s*filename="([^"]*)")?',
                       headers, re.I | re.S)
        if not cd:
            continue
        field, filename = cd.group(1), cd.group(2)
        if filename:
            files.append((field, filename, rest))
        else:
            fields[field] = rest.decode("utf-8", "replace")
    return fields, files


class Handler(BaseHTTPRequestHandler):
    server_version = "GenImageAPI/1.0"
    comfy_host = DEFAULT_COMFY
    _key_entry = None           # 本次请求凭哪个 key 通过鉴权（None=回环豁免/鉴权未开）
    _key_id = None              # 上述 key 的 id（写进请求日志）

    def log_message(self, fmt, *args):        # 安静默认日志，改走 _log
        pass

    def _path(self):
        return self.path.split("?")[0].rstrip("/")

    def _v1_path(self):
        """是否 /v1/* 路径（这些端点的错误体用 OpenAI 风格）"""
        return self.path.split("?")[0].startswith("/v1/")

    def _log(self, status, extra=""):
        shown = re.sub(r"api_key=[^&\s]*", "api_key=***", self.path)   # URL 里的 key 脱敏
        if self._key_id is not None:
            extra = (extra + f" key_id={self._key_id}").strip()        # 鉴权通过带 key id
        print(f"[{time.strftime('%H:%M:%S')}] {self.command} {shown} "
              f"→ {status} {extra}", flush=True)

    def _send(self, status, payload, headers=None):
        body = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _error(self, err):
        if isinstance(err, ApiError):
            status, code, message, details = err.status, err.code, err.message, err.details
        else:
            status, code, message, details = 500, "internal_error", str(err), None
        if self._v1_path():
            payload = {"error": {"message": message,
                                 "type": "invalid_request_error", "code": code}}
        else:
            payload = {"ok": False, "error": {"code": code, "message": message}}
            if details is not None:
                payload["error"]["details"] = details
        self._send(status, payload)
        self._log(status, code)
        if not isinstance(err, ApiError):
            import traceback
            traceback.print_exc()

    # ───────────────────────── API Key 鉴权 ─────────────────────────

    def _extract_key(self):
        """取 key：Authorization: Bearer → X-API-Key → 查询参数 api_key"""
        auth = self.headers.get("Authorization") or ""
        if auth[:7].lower() == "bearer ":
            k = auth[7:].strip()
            if k:
                return k
        k = (self.headers.get("X-API-Key") or "").strip()
        if k:
            return k
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        return (qs.get("api_key") or [""])[0] or None

    def _check_auth(self, method):
        """每请求先 stat key 文件（变了就重读），受保护端点校验 key。

        返回 True 放行；False 表示已回 401。回环豁免或鉴权未开启时直接放行。
        """
        self._key_entry = None
        self._key_id = None
        KEY_STORE.refresh()
        p = self._path()
        if p not in (PROTECTED_POST if method == "POST" else PROTECTED_GET):
            return True
        if TRUST_LOOPBACK and self.client_address and self.client_address[0] in LOOPBACK:
            return True
        entry, code = KEY_STORE.authenticate(self._extract_key())
        if code is None:
            if entry is not None:
                self._key_entry, self._key_id = entry, entry.get("id")
            return True
        msg = ("缺少或无效的 API key" if code == "invalid_api_key"
               else "API key 已禁用（enabled=false）")
        if self._v1_path():
            payload = {"error": {"message": msg,
                                 "type": "invalid_request_error", "code": code}}
        else:
            payload = {"ok": False, "error": {"code": code, "message": msg}}
        self._send(401, payload, {"WWW-Authenticate": "Bearer"})
        self._log(401, code)
        return False

    def _record_usage(self):
        """凭 key 通过鉴权且生成成功 → 给该 key 计一次调用（失败只记日志）"""
        if self._key_entry is not None:
            record_usage(self._key_entry.get("id"))

    def do_GET(self):
        if not self._check_auth("GET"):
            return
        path = self._path()
        if path in ("", "/health", "/v1/health"):
            reachable = True
            try:
                get_json(self.comfy_host, "/system_stats", timeout=5)
            except Exception:
                reachable = False
            self._send(200, {"ok": True, "service": "qwen-image-gen-api",
                             "comfy": self.comfy_host,
                             "comfy_reachable": reachable,
                             "auth_required": KEY_STORE.enabled,
                             "endpoints": {"generate": "POST /generate",
                                           "health": "GET /health",
                                           "result": "GET /result?filename=&subfolder="}})
            self._log(200)
        elif path == "/v1/models":
            self._send(200, {"object": "list", "data": [
                {"id": "qwen-image-2.1-turbo", "object": "model",
                 "owned_by": "local"}]})
            self._log(200)
        elif path == "/result":
            self._serve_result()
        else:
            self._error(ApiError(404, "not_found", f"未知路径: {self.path}"))

    def _serve_result(self):
        """把产物 PNG 直接发给远端调用方（不必再暴露 ComfyUI 的 /view）"""
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        fname = (qs.get("filename") or [""])[0]
        sub = (qs.get("subfolder") or [""])[0]
        if not fname or "/" in fname or "\\" in fname:
            self._error(ApiError(400, "invalid_param", "filename 必填且不能含路径分隔符"))
            return
        base = os.path.realpath(OUTPUT_DIR)
        target = os.path.realpath(os.path.join(base, sub, fname))
        if not target.startswith(base + os.sep) or not os.path.isfile(target):
            self._error(ApiError(404, "not_found", f"产物不存在: {sub}/{fname}"))
            return
        with open(target, "rb") as f:
            body = f.read()
        ctype = "image/png" if fname.lower().endswith(".png") else "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        self._log(200, f"result {fname} {len(body)}B")

    def do_POST(self):
        if not self._check_auth("POST"):
            return
        path = self._path()
        if path == "/generate":
            self._do_generate()
        elif path == "/v1/images/generations":
            self._do_v1_generations()
        else:
            self._error(ApiError(404, "not_found", f"未知路径: {self.path}"))

    def _do_generate(self):
        """原生 POST /generate（JSON 或 multipart/form-data）"""
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0:
                raise ApiError(400, "invalid_param", "请求体为空")
            if length > MAX_BODY:
                raise ApiError(413, "payload_too_large",
                               f"请求体超过 {MAX_BODY // (1024 * 1024)} MB")
            body = self.rfile.read(length)
            ctype = self.headers.get("Content-Type", "")

            if ctype.startswith("multipart/form-data"):
                fields, files = parse_multipart(body, ctype)
                params = parse_params(fields)
                image_items = [blob for _f, _name, blob in files]   # 原始字节
            elif "json" in ctype:
                try:
                    payload = json.loads(body)
                except json.JSONDecodeError as e:
                    raise ApiError(400, "invalid_param", f"JSON 解析失败: {e}")
                if not isinstance(payload, dict):
                    raise ApiError(400, "invalid_param", "请求体必须是 JSON 对象")
                params = parse_params(payload)
                image_items = payload.get("images") or []
                if not isinstance(image_items, list):
                    raise ApiError(400, "invalid_param", "images 必须是数组")
            else:
                raise ApiError(415, "invalid_param",
                               "Content-Type 需为 application/json 或 multipart/form-data")

            result = generate(params, image_items, self.comfy_host)
            self._send(200, result)
            self._log(200, f"seed={result['seed']} "
                           f"{result['width']}x{result['height']} "
                           f"{result['elapsed_s']}s")
            self._record_usage()
        except ApiError as e:
            self._error(e)
        except Exception as e:                 # noqa: BLE001
            self._error(e)

    def _do_v1_generations(self):
        """OpenAI 兼容 POST /v1/images/generations（错误体为 OpenAI 风格）"""
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0:
                raise ApiError(400, "invalid_param", "请求体为空")
            if length > MAX_BODY:
                raise ApiError(413, "payload_too_large",
                               f"请求体超过 {MAX_BODY // (1024 * 1024)} MB")
            body = self.rfile.read(length)
            ctype = self.headers.get("Content-Type", "")
            if "json" not in ctype:
                raise ApiError(415, "invalid_param", "Content-Type 需为 application/json")
            try:
                payload = json.loads(body)
            except json.JSONDecodeError as e:
                raise ApiError(400, "invalid_param", f"JSON 解析失败: {e}")
            if not isinstance(payload, dict):
                raise ApiError(400, "invalid_param", "请求体必须是 JSON 对象")

            # 只认约定字段，其余杂参忽略（兼容客户端多传）
            model = payload.get("model") or DEFAULT_MODEL
            if model not in MODELS:
                raise ApiError(404, "model_not_found",
                               f"模型不存在: {model}；可用模型: {', '.join(MODELS)}")
            prompt = str(payload.get("prompt") or "").strip()
            if not prompt:
                raise ApiError(400, "missing_prompt", "prompt 为必填且不能为空")
            n = payload.get("n")
            if n not in (None, "", 1, "1"):
                raise ApiError(400, "invalid_param",
                               f"n 必须为 1：本服务一次只能生成一张图（收到 n={n!r}）")
            width, height = self._parse_size(payload.get("size"))
            rf = payload.get("response_format") or "url"
            if rf not in ("url", "b64_json"):
                raise ApiError(400, "invalid_param",
                               f"response_format 仅支持 url / b64_json，收到: {rf!r}")

            raw = {"prompt": prompt, "seed": payload.get("seed"),
                   "return_b64": rf == "b64_json"}
            if width is not None:
                raw["width"], raw["height"] = width, height
            params = parse_params(raw)
            result = generate(params, [], self.comfy_host)
            img = result["images"][0]
            if rf == "url":
                item = {"url": self._result_url(img)}
            else:
                b64 = img.get("b64")
                if not b64:
                    raise ApiError(500, "internal_error", "读取产物 base64 失败")
                item = {"b64_json": b64}
            self._send(200, {"created": int(time.time()), "data": [item]})
            self._log(200, f"v1 seed={result['seed']} "
                           f"{result['width']}x{result['height']} "
                           f"{result['elapsed_s']}s format={rf}")
            self._record_usage()
        except ApiError as e:
            self._error(e)
        except Exception as e:                 # noqa: BLE001
            self._error(e)

    @staticmethod
    def _parse_size(size):
        """size 缺省或 "auto" → 不传宽高（服务端默认 1024 方图）；
        "宽x高" → 64–2048 且 32 倍数，否则 400（不静默改值）"""
        if size in (None, "", "auto"):
            return None, None
        m = re.fullmatch(r"(\d+)\s*[xX]\s*(\d+)", str(size).strip())
        if not m:
            raise ApiError(400, "invalid_param",
                           f"size 应为 \"宽x高\"（如 1024x1024）或 \"auto\"，收到: {size!r}")
        w, h = int(m.group(1)), int(m.group(2))
        for name, v in (("width", w), ("height", h)):
            if not (MIN_SIZE <= v <= MAX_SIZE):
                raise ApiError(400, "invalid_param",
                               f"size {name} 需在 {MIN_SIZE}–{MAX_SIZE} 之间: {v}")
            if v % SIZE_STEP:
                raise ApiError(400, "invalid_param",
                               f"size {name} 必须是 {SIZE_STEP} 的倍数（{SIZE_STEP} 网格）: "
                               f"{v}；服务端不静默改值，请自行取整后重试")
        return w, h

    def _result_url(self, img):
        """产物绝对地址 http://<Host>/result?...；凭 key 通过鉴权时附 api_key，
        否则后续裸下载 /result 会被 401 拦下"""
        host = (self.headers.get("Host") or "").strip()
        if not host:
            addr = self.server.server_address
            host = f"{addr[0]}:{addr[1]}"
        url = f"http://{host}/result?" + urllib.parse.urlencode(
            {"filename": img["filename"], "subfolder": img["subfolder"]})
        if self._key_entry is not None:
            key = str(self._key_entry.get("key") or "")
            if key:
                url += "&" + urllib.parse.urlencode({"api_key": key})
        return url


def main():
    ap = argparse.ArgumentParser(description="Qwen-Image-2.1-Turbo HTTP 生图服务")
    ap.add_argument("--host", default=os.environ.get("GEN_STUDIO_IMAGE_API_HOST", "127.0.0.1"),
                    help="监听地址（默认 127.0.0.1；跨机调用设 GEN_STUDIO_IMAGE_API_HOST=0.0.0.0）")
    ap.add_argument("--port", type=int,
                    default=int(os.environ.get("GEN_STUDIO_IMAGE_API_PORT", 30021)))
    ap.add_argument("--comfy", default=DEFAULT_COMFY,
                    help="后端 ComfyUI 地址（默认 127.0.0.1:30020）")
    a = ap.parse_args()

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    Handler.comfy_host = a.comfy
    srv = ThreadingHTTPServer((a.host, a.port), Handler)
    print(f"[listen] http://{a.host}:{a.port}  (comfy={a.comfy}) "
          f"POST /generate  POST /v1/images/generations  GET /health", flush=True)
    print(f"[auth] key 文件 {KEYS_FILE} → 鉴权{'开启' if KEY_STORE.enabled else '关闭'}"
          f"；回环豁免{'开' if TRUST_LOOPBACK else '关'}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n[stop]", flush=True)


if __name__ == "__main__":
    main()
