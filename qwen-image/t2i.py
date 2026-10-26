#!/usr/bin/env python3
"""向本机 ComfyUI (127.0.0.1:30020) 提交一次 Qwen-Image-2.1-Turbo 文生图，
轮询显存峰值与耗时，打印结果文件路径。

用法:
  ./t2i.py --width 1024 --height 1024 --prompt "..." [--seed 42] [--tag 1024]
"""
import argparse
import json
import subprocess
import threading
import time
import urllib.request
import uuid

HOST = "127.0.0.1:30020"
GPU_INDEX = "0"

TURBO_SIGMAS = "1.0, 0.978453, 0.95418, 0.926626, 0.89508, 0.845148, 0.704534, 0.414568, 0"

DIT = "qwen_image_2.1_turbo_int8_convrot.safetensors"
TE_DEFAULT = "qwen3vl_8b_w4a8.safetensors"
VAE = "qwen_image_2.1_vae_bf16.safetensors"


def post_json(path, payload):
    data = json.dumps(payload).encode()
    req = urllib.request.Request(f"http://{HOST}{path}", data=data,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def get_json(path):
    with urllib.request.urlopen(f"http://{HOST}{path}", timeout=60) as r:
        return json.load(r)


def build_prompt(prompt, width, height, seed, cfg, sigmas, prefix, te):
    return {
        "1": {"class_type": "UNETLoader",
              "inputs": {"unet_name": DIT, "weight_dtype": "default"}},
        "2": {"class_type": "CLIPLoader",
              "inputs": {"clip_name": te, "type": "qwen_image", "device": "default"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": VAE}},
        "4": {"class_type": "TextEncodeQwenImage21",
              "inputs": {"clip": ["2", 0], "prompt": prompt, "negative_prompt": "",
                         "vae": ["3", 0], "resolution": 1024}},
        "5": {"class_type": "EmptyLatentImage",
              "inputs": {"width": width, "height": height, "batch_size": 1}},
        "6": {"class_type": "ManualSigmas", "inputs": {"sigmas": sigmas}},
        "7": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "euler"}},
        "8": {"class_type": "RandomNoise", "inputs": {"noise_seed": seed}},
        "9": {"class_type": "CFGGuider",
              "inputs": {"model": ["1", 0], "positive": ["4", 0],
                         "negative": ["4", 1], "cfg": cfg}},
        "10": {"class_type": "SamplerCustomAdvanced",
               "inputs": {"noise": ["8", 0], "guider": ["9", 0], "sampler": ["7", 0],
                          "sigmas": ["6", 0], "latent_image": ["5", 0]}},
        "11": {"class_type": "VAEDecode", "inputs": {"samples": ["10", 0], "vae": ["3", 0]}},
        "12": {"class_type": "SaveImage",
               "inputs": {"images": ["11", 0], "filename_prefix": prefix}},
    }


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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--prompt", required=True)
    ap.add_argument("--width", type=int, default=1024)
    ap.add_argument("--height", type=int, default=1024)
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--cfg", type=float, default=1.0)
    ap.add_argument("--sigmas", default=TURBO_SIGMAS)
    ap.add_argument("--tag", default="t2i")
    ap.add_argument("--te", default=TE_DEFAULT, help="text encoder filename")
    ap.add_argument("--timeout", type=int, default=1800)
    a = ap.parse_args()

    seed = a.seed if a.seed is not None else int.from_bytes(uuid.uuid4().bytes[:4], "big") % 2**31
    prefix = f"qwen-image-2.1-turbo/{a.tag}-{a.width}x{a.height}-{seed}"
    wf = build_prompt(a.prompt, a.width, a.height, seed, a.cfg, a.sigmas, prefix, a.te)
    cfg = {"prompt": wf, "client_id": str(uuid.uuid4())}

    w = PeakWatcher()
    w.start()
    t0 = time.time()
    resp = post_json("/prompt", cfg)
    pid = resp["prompt_id"]
    print(f"[submit] prompt_id={pid} seed={seed} size={a.width}x{a.height} cfg={a.cfg}", flush=True)

    entry = None
    while time.time() - t0 < a.timeout:
        hist = get_json(f"/history/{pid}")
        if pid in hist:
            entry = hist[pid]
            break
        time.sleep(1.0)
    status = (entry or {}).get("status", {})
    elapsed = time.time() - t0
    halt = time.time()

    files = []
    if status:
        for nid, out in (entry or {}).get("outputs", {}).items():
            for img in out.get("images", []):
                files.append(f"{img.get('subfolder','')}/{img['filename']}".lstrip("/"))

    base = w.base
    print(f"[result] elapsed={elapsed:.1f}s peak_vram={w.peak}MiB "
          f"(baseline {base}MiB, delta {w.peak - base}MiB)", flush=True)
    print(f"[status] {'ok' if status and status.get('completed') else status}", flush=True)
    for f in files:
        print(f"[file] output/{f}", flush=True)
    w.stop_flag = True
    _ = halt


if __name__ == "__main__":
    main()
