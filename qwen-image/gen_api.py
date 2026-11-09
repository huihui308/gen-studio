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
GET  /health      存活与后端可达性
GET  /result      下载产物：/result?filename=xx.png&subfolder=qwen-image-2.1-turbo
POST /generate    生成图片（JSON 或 multipart/form-data 上传）

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
unsupported_image_type / payload_too_large（4xx）；comfy_unreachable（503）/
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

(TBD：接口鉴权 —— 当前无鉴权，仅建议内网/回环调用；是否加 token 由接入方定)
(TBD：端口对外暴露策略 —— 默认绑 127.0.0.1，如需跨机调用用 GEN_STUDIO_IMAGE_API_HOST=0.0.0.0)
"""
import argparse
import base64
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

    def log_message(self, fmt, *args):        # 安静默认日志，改走 _log
        pass

    def _log(self, status, extra=""):
        print(f"[{time.strftime('%H:%M:%S')}] {self.command} {self.path} "
              f"→ {status} {extra}", flush=True)

    def _send(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _error(self, err):
        if isinstance(err, ApiError):
            payload = {"ok": False, "error": {"code": err.code,
                                              "message": err.message}}
            if err.details is not None:
                payload["error"]["details"] = err.details
            self._send(err.status, payload)
            self._log(err.status, err.code)
        else:
            self._send(500, {"ok": False, "error": {
                "code": "internal_error", "message": str(err)}})
            self._log(500, "internal_error")
            import traceback
            traceback.print_exc()

    def do_GET(self):
        path = self.path.split("?")[0].rstrip("/")
        if path in ("", "/health", "/v1/health"):
            reachable = True
            try:
                get_json(self.comfy_host, "/system_stats", timeout=5)
            except Exception:
                reachable = False
            self._send(200, {"ok": True, "service": "qwen-image-gen-api",
                             "comfy": self.comfy_host,
                             "comfy_reachable": reachable,
                             "endpoints": {"generate": "POST /generate",
                                           "health": "GET /health",
                                           "result": "GET /result?filename=&subfolder="}})
            self._log(200)
        elif path == "/result":
            self._serve_result()
        else:
            self._send(404, {"ok": False, "error": {
                "code": "not_found", "message": f"未知路径: {self.path}"}})
            self._log(404)

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
        if self.path.split("?")[0].rstrip("/") != "/generate":
            self._send(404, {"ok": False, "error": {
                "code": "not_found", "message": f"未知路径: {self.path}"}})
            self._log(404)
            return
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

            t0 = time.time()
            result = generate(params, image_items, self.comfy_host)
            self._send(200, result)
            self._log(200, f"seed={result['seed']} "
                           f"{result['width']}x{result['height']} "
                           f"{result['elapsed_s']}s")
        except ApiError as e:
            self._error(e)
        except Exception as e:                 # noqa: BLE001
            self._error(e)


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
          f"POST /generate  GET /health", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n[stop]", flush=True)


if __name__ == "__main__":
    main()
