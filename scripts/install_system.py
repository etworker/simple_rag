#!/usr/bin/env python3
"""
simple_rag 全系统依赖安装脚本（uv workspace 根级入口）。

在仓库根目录执行，一次性安装 4 个模块（doc_parser / llm_chat / version_diff / rag_demo）
的全部依赖与 extras（dev、docling、mineru）。

torch 策略（docling/mineru 解析后端都需要 torch）：
  - 有 NVIDIA GPU  → 安装 CUDA 版 torch（默认 cu124，可用 --cuda 覆盖）
  - 无 GPU        → 安装 CPU 版 torch（docling/mineru 可运行，仅推理较慢）
  - embedding 基于 fastembed/ONNX，不额外依赖 torch

用法（在 simple_rag 仓库根目录）:
    # 机器上有系统 Python 时
    python scripts/install_system.py                    # 全量安装

    # 机器上只有 uv、没有系统 Python 时（uv 会自动下载托管 Python）
    uv run --no-project python scripts/install_system.py

    python scripts/install_system.py --cuda 121         # 指定 CUDA 版本
    python scripts/install_system.py --sync-only        # 跳过 torch，仅 uv sync

依赖: 需要本机已安装 uv（https://docs.astral.sh/uv/）。
      --no-project 让 uv 在隔离环境运行本脚本；脚本内部自建 workspace 的 .venv。
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
            ["nvidia-smi", "-L"], capture_output=True, text=True, timeout=30, check=False
        )
        if r.returncode == 0:
            return "GPU" in r.stdout
    try:
        import torch

        return torch.cuda.is_available()
    except Exception:  # noqa: BLE001 - 检测失败即视为无 GPU
        return False


def run(cmd: list[str]) -> None:
    log("$ " + " ".join(str(c) for c in cmd))
    subprocess.run(cmd, check=True)


def read_torch_cuda(py: Path) -> str:
    """查询已装 torch 的 CUDA 版本（无 torch 或非 CUDA 构建返回空串）。"""
    r = subprocess.run(
        [str(py), "-c", "import torch; print(torch.version.cuda or '')"],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    return r.stdout.strip()


def main() -> int:
    parser = argparse.ArgumentParser(description="安装 simple_rag 全系统依赖")
    parser.add_argument("--cuda", default="124", help="CUDA 版本号（默认 124，仅 GPU 机生效）")
    parser.add_argument("--python", default="3.12", help="Python 版本（默认 3.12）")
    parser.add_argument("--sync-only", action="store_true", help="跳过 torch 步骤，仅 uv sync")
    args = parser.parse_args()

    uv = find_uv()
    gpu = detect_gpu()
    log(f"检测到 GPU: {gpu}")

    # ── 1. 创建 workspace 虚拟环境（指定版本，uv 自动下载托管 Python）──
    venv = ROOT / ".venv"
    if not venv.exists():
        run([uv, "venv", "--python", args.python, str(venv)])

    py = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")

    # ── 2. torch 策略：无论有无 GPU 都安装 torch（docling/mineru 均需要）──
    #       有 GPU → CUDA 版（默认 cu124，可用 --cuda 覆盖）
    #       无 GPU → CPU 版（mineru 用 pipeline 模式可运行，仅速度较慢）
    if not args.sync_only:
        if gpu:
            torch_index = f"{TORCH_INDEX}/cu{args.cuda}"
            log(f"检测到 GPU：安装 CUDA {args.cuda} 版 torch (index={torch_index})")
        else:
            torch_index = f"{TORCH_INDEX}/cpu"
            log("未检测到 GPU：安装 CPU 版 torch（docling/mineru 均可用，仅推理较慢）")
        run(
            [
                uv, "pip", "install", "--python", str(py),
                "--index-url", torch_index,
                "--extra-index-url", PYPI_INDEX,
                "torch", "torchvision", "torchaudio",
            ]
        )

    # ── 3. workspace 全量同步（4 模块 + 全部 extras：dev、docling、mineru）──
    run(
        [
            uv, "sync", "--project", str(ROOT),
            "--all-extras", "--python", str(py),
        ]
    )

    # ── 4. 校验状态：torch 构建与 GPU 匹配 ──
    cuda = read_torch_cuda(py)
    if cuda:
        log(f"安装完成。torch CUDA: {cuda}")
        if gpu and not cuda:
            log("警告: 检测到 GPU 但 torch 为 CPU 构建，请检查 --cuda 版本与 CUDA 驱动兼容性")
    else:
        log("安装完成。torch 为 CPU 构建（无 CUDA）；docling/mineru 可运行，GPU 加速不可用")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())