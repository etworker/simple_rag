#!/usr/bin/env python3
"""
simple_rag 全系统依赖安装脚本（uv workspace 根级入口）。

脚本自动检测 NVIDIA GPU，并通过 uv 锁文件选择互斥的运行时依赖：
  - CPU：fastembed + onnxruntime + faiss-cpu + PyTorch CPU
  - NVIDIA GPU：fastembed-gpu + onnxruntime-gpu + PyTorch cu126；Linux 使用
    faiss-gpu，Windows 使用 faiss-cpu（Faiss GPU wheel 不支持 Windows）

用法（在 simple_rag 仓库根目录）：
    python setup_env.py
    ./setup_env.sh                    # Linux / macOS
    setup_env.bat                     # Windows
    python setup_env.py --device cpu
    python setup_env.py --device gpu
    python setup_env.py --pypi-index https://mirrors.aliyun.com/pypi/simple

依赖：本机需有 Python；如果找不到 uv，脚本会尝试使用当前 Python 的 pip --user 安装 uv。项目依赖统一由 uv 根据根目录 uv.lock 同步。脚本创建并同步 workspace 根目录的 .venv。
"""

import argparse
import os
import shutil
import subprocess
import sys
import sysconfig
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# 覆盖 PyPI 镜像源的环境变量名
ENV_PYPI_INDEX = "SIMPLE_RAG_PYPI_INDEX"
PYPI_INDEX = os.environ.get(ENV_PYPI_INDEX, "https://pypi.tuna.tsinghua.edu.cn/simple")


def log(msg: str) -> None:
    print(f"[setup] {msg}", flush=True)


def _uv_candidates() -> list[Path]:
    candidates = [
        Path.home() / ".local" / "bin" / "uv.exe",
        Path.home() / ".local" / "bin" / "uv",
        Path.home() / "AppData" / "Roaming" / "uv" / "uv.exe",
    ]
    for scheme in ("nt_user", "posix_user"):
        try:
            scripts_dir = sysconfig.get_path("scripts", scheme=scheme)
        except (KeyError, ValueError):
            continue
        if scripts_dir:
            base = Path(scripts_dir)
            candidates.extend((base / "uv.exe", base / "uv"))
    return candidates


def find_uv() -> str:
    uv = shutil.which("uv")
    if uv:
        return uv

    for cand in _uv_candidates():
        if cand.exists():
            return str(cand)

    log("未找到 uv，尝试使用当前 Python 的 pip --user 安装 uv")
    result = subprocess.run(
        [sys.executable, "-m", "pip", "install", "--user", "uv"],
        check=False,
    )
    if result.returncode == 0:
        uv = shutil.which("uv")
        if uv:
            return uv
        for cand in _uv_candidates():
            if cand.exists():
                return str(cand)

    sys.exit(
        "未找到 uv，且自动安装失败。请手动安装 uv："
        "https://docs.astral.sh/uv/（Windows 可用 winget install astral-sh.uv）"
    )


def detect_gpu() -> bool:
    """检测是否存在可用的 NVIDIA GPU。"""
    nvidia_smi = shutil.which("nvidia-smi")
    if nvidia_smi:
        result = subprocess.run(
            [nvidia_smi, "-L"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if result.returncode == 0 and "GPU" in result.stdout:
            return True
    try:
        import torch

        return bool(torch.cuda.is_available())
    except Exception:  # noqa: BLE001 - 探测失败即按 CPU 环境处理
        return False


def run(cmd: list[str], env=None) -> None:
    log("$ " + " ".join(str(c) for c in cmd))
    subprocess.run(cmd, check=True, env=env)


def read_torch_cuda(py: Path) -> str:
    result = subprocess.run(
        [str(py), "-c", "import torch; print(torch.version.cuda or '')"],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    return result.stdout.strip()


def verify_runtime(py: Path, *, expect_gpu: bool) -> None:
    """验证最终生效的向量运行时；GPU extra 不得静默回退到 CPU ORT。"""
    code = (
        "import importlib.metadata as md; import faiss, onnxruntime as ort; "
        "installed={d.metadata['Name'].lower() for d in md.distributions() if d.metadata.get('Name')}; "
        "print('faiss_gpus=' + str(faiss.get_num_gpus())); "
        "print('onnx_providers=' + ','.join(ort.get_available_providers())); "
        "print('onnxruntime_dist=' + str('onnxruntime' in installed)); "
        "print('onnxruntime_gpu_dist=' + str('onnxruntime-gpu' in installed)); "
        "print('faiss_cpu_dist=' + str('faiss-cpu' in installed)); "
        "print('faiss_gpu_dist=' + str('faiss-gpu' in installed))"
    )
    result = subprocess.run(
        [str(py), "-c", code],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"向量运行时验证失败: {result.stderr.strip()}")

    values = {}
    for line in result.stdout.splitlines():
        log(line)
        key, separator, value = line.partition("=")
        if separator:
            values[key] = value

    if expect_gpu:
        if values.get("onnxruntime_gpu_dist") != "True":
            raise RuntimeError("GPU 模式未安装 onnxruntime-gpu")
        providers = values.get("onnx_providers", "").split(",")
        if "CUDAExecutionProvider" not in providers:
            raise RuntimeError(
                "GPU 模式未启用 CUDAExecutionProvider；onnxruntime 与 onnxruntime-gpu "
                "可能因 MinerU 依赖发生文件覆盖，请重新运行安装脚本"
            )
        if sys.platform.startswith("linux") and values.get("faiss_gpu_dist") != "True":
            raise RuntimeError("Linux GPU 模式未安装 faiss-gpu")
        if os.name == "nt" and values.get("faiss_cpu_dist") != "True":
            raise RuntimeError("Windows GPU 模式应安装 faiss-cpu")
        if values.get("onnxruntime_dist") == "True":
            log("说明：MinerU/Magika 依赖声明同时引入 onnxruntime；已确认当前导入实际支持 CUDA")
    elif values.get("onnxruntime_dist") != "True":
        raise RuntimeError("CPU 模式未安装 onnxruntime")


def main() -> int:
    parser = argparse.ArgumentParser(description="配置 simple_rag 全系统环境")
    parser.add_argument(
        "--device",
        choices=("auto", "cpu", "gpu"),
        default="auto",
        help="运行设备；auto 自动检测 NVIDIA GPU（默认）",
    )
    parser.add_argument("--python", default="3.12", help="Python 版本（默认 3.12）")
    parser.add_argument(
        "--pypi-index",
        default=PYPI_INDEX,
        help="uv sync 使用的 PyPI 镜像",
    )
    args = parser.parse_args()

    uv = find_uv()
    detected_gpu = detect_gpu()
    use_gpu = detected_gpu if args.device == "auto" else args.device == "gpu"
    if args.device == "gpu" and not detected_gpu:
        log("警告：强制 GPU 模式，但 nvidia-smi/torch 未检测到可用 NVIDIA GPU")

    runtime_extra = "gpu" if use_gpu else "cpu"
    log(f"设备检测: detected_gpu={detected_gpu}, selected={runtime_extra}, platform={sys.platform}")
    if use_gpu and os.name == "nt":
        log("Windows GPU 模式：embedding 使用 CUDA，Faiss 使用 CPU（Windows 无 Faiss GPU wheel）")

    venv = ROOT / ".venv"
    if not venv.exists():
        run([uv, "venv", "--python", args.python, str(venv)])
    py = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")

    sync_env = dict(os.environ)
    sync_env["UV_DEFAULT_INDEX"] = args.pypi_index
    excluded_extra = "cpu" if use_gpu else "gpu"
    run(
        [
            uv,
            "sync",
            "--project",
            str(ROOT),
            "--all-packages",
            "--all-extras",
            "--no-extra",
            excluded_extra,
            "--python",
            str(py),
            "--frozen",
        ],
        env=sync_env,
    )

    cuda = read_torch_cuda(py)
    if use_gpu and not cuda:
        raise RuntimeError("选择了 GPU 模式，但锁定的 PyTorch 未报告 CUDA")
    if not use_gpu and cuda:
        raise RuntimeError(f"选择了 CPU 模式，但实际安装的是 CUDA PyTorch ({cuda})")
    log(f"torch CUDA: {cuda}" if cuda else "torch 为 CPU 构建")
    verify_runtime(py, expect_gpu=use_gpu)
    log(f"安装完成：{runtime_extra} 运行时")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
