#!/usr/bin/env python3
"""
doc_parser 依赖自动安装脚本。

自动检测 GPU 并选择匹配的 torch 构建：
  - 有 NVIDIA GPU  → 安装 CUDA 版 torch（默认 cu124，可用 --cuda 覆盖）
  - 无 GPU        → 安装 CPU 版 torch（省 ~2GB 下载）

同时安装项目本体 + 可选后端依赖（docling / mineru）。

用法:
    python scripts/install_deps.py                 # 全量（dev+docling+mineru）
    python scripts/install_deps.py --no-docling    # 跳过 docling
    python scripts/install_deps.py --no-mineru     # 跳过 mineru
    python scripts/install_deps.py --cuda 121      # 指定 CUDA 版本
    python scripts/install_deps.py --venv .venv    # 指定虚拟环境路径

依赖: 需要本机已安装 uv（https://docs.astral.sh/uv/）。
"""

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TORCH_INDEX = "https://download.pytorch.org/whl"
PYPI_INDEX = "https://pypi.org/simple"


def log(msg: str) -> None:
    print(f"[install] {msg}", flush=True)


def find_uv() -> str:
    uv = shutil.which("uv")
    if uv:
        return uv
    # Windows 常见安装位置（uv 默认装到用户目录）
    for cand in (
        Path.home() / ".local" / "bin" / "uv.exe",
        Path.home() / ".local" / "bin" / "uv",
        Path.home() / "AppData" / "Roaming" / "uv" / "uv.exe",
    ):
        if cand.exists():
            return str(cand)
    sys.exit("未找到 uv，请先安装: https://docs.astral.sh/uv/ (winget install astral-sh.uv)")


def detect_gpu() -> bool:
    """检测是否有可用 NVIDIA GPU（优先 nvidia-smi，失败回退 torch）。"""
    if shutil.which("nvidia-smi"):
        r = subprocess.run(
            ["nvidia-smi", "-L"], capture_output=True, text=True, timeout=30
        )
        if r.returncode == 0:
            return "GPU" in r.stdout
    try:
        import torch  # noqa: PLC0415

        return torch.cuda.is_available()
    except Exception:
        return False


def run(cmd: list[str]) -> None:
    log("$ " + " ".join(str(c) for c in cmd))
    subprocess.run(cmd, check=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="安装 doc_parser 依赖")
    parser.add_argument("--venv", default=".venv", help="虚拟环境目录（默认 .venv）")
    parser.add_argument("--cuda", default="124", help="CUDA 版本号（默认 124，仅 GPU 机生效）")
    parser.add_argument("--no-docling", action="store_true", help="跳过 docling 后端")
    parser.add_argument("--no-mineru", action="store_true", help="跳过 mineru 后端")
    parser.add_argument("--no-dev", action="store_true", help="跳过 dev 依赖（pytest）")
    args = parser.parse_args()

    uv = find_uv()
    venv = ROOT / args.venv
    gpu = detect_gpu()

    log(f"检测到 GPU: {gpu}")
    if gpu:
        log(f"将安装 CUDA {args.cuda} 版 torch")
        torch_index = f"{TORCH_INDEX}/cu{args.cuda}"
    else:
        log("将安装 CPU 版 torch")
        torch_index = f"{TORCH_INDEX}/cpu"

    # ── 1. 创建虚拟环境 ──
    if not venv.exists():
        run([uv, "venv", str(venv)])

    py = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    log(f"虚拟环境解释器: {py}")

    # ── 2. 先装 torch（正确构建），再装项目 ──
    run(
        [
            uv, "pip", "install", "--python", str(py),
            "--index-url", torch_index,
            "--extra-index-url", PYPI_INDEX,
            "torch", "torchvision", "torchaudio",
        ]
    )

    # ── 3. 安装项目本体 + 可选依赖 ──
    extras = []
    if not args.no_dev:
        extras.append("dev")
    if not args.no_docling:
        extras.append("docling")
    if not args.no_mineru:
        extras.append("mineru")

    target = f"{ROOT}[{','.join(extras)}]" if extras else str(ROOT)
    run(
        [
            uv, "pip", "install", "--python", str(py),
            "--extra-index-url", torch_index,
            "-e", target,
        ]
    )

    log("安装完成。")
    log(f"  GPU: {gpu}  torch 构建: {'CUDA' if gpu else 'CPU'}")
    log("验证: 运行 `python -c \"import torch; print(torch.cuda.is_available())\"`")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
