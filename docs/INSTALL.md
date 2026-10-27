# Installation / 安装说明

This is a manual Windows x64 source installation. Models and CUDA runtimes are separate downloads. The setup removes developer-specific paths, but a clean-machine installation has not yet been verified.

这是一份 Windows 源码安装说明。先准备 Python 环境、两个模型和两个运行程序，再加载浏览器插件。无需复用开发者的电脑目录。

## 1. Create a Python environment / 创建 Python 环境

Install Python 3.12 for Windows x64. Open PowerShell in the repository folder:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item config.example.json config.json
```

Do not overwrite an existing `config.json` containing your own settings. Streaming mode uses the native executables below and does not require PyTorch.

已有配置时，不要覆盖 `config.json`。流式模式使用原生运行程序，不需要先安装 PyTorch。

## 2. Download the two model files / 下载模型

Create a `models` directory, or keep models elsewhere and use absolute paths in `config.json`.

| Model | Download | Example destination |
| --- | --- | --- |
| Nemotron 3.5 ASR 0.6B Q8_0 | [Official files](https://huggingface.co/nvidia/nemotron-3.5-asr-streaming-0.6b/tree/main) | `models/nemotron-3.5-asr-streaming-0.6b.q8_0.gguf` |
| Hy-MT2 1.8B Q6_K | [Official files](https://huggingface.co/tencent/Hy-MT2-1.8B-GGUF/tree/main) | `models/Hy-MT2-1.8B-Q6_K.gguf` |

Files verified during development:

| File | Bytes | SHA-256 |
| --- | ---: | --- |
| Nemotron Q8_0 | 742,090,464 | `3fc991d3badad7277c11030a7519832cddaf2057aafed6d4b25147e953a070b1` |
| Hy-MT2 Q6_K | 1,474,785,120 | `d98fe604dec1f28f58f80d7d560f7177e584d3b8e5835862687660e5ff97cb40` |

Use PowerShell's `Get-FileHash -Algorithm SHA256` to inspect downloaded files. If upstream changes a file, check its official metadata before trusting a different hash. Follow the model cards' licenses.

模型也可放在你自己的管理目录，只需在配置中填写实际路径。

## 3. Download CUDA runtimes / 下载运行程序

Use the builds exercised during development:

1. **ASR:** [NeMo-Speech.cpp v0.2.0 Windows CUDA ZIP](https://github.com/NVIDIA/NeMo-Speech.cpp/releases/download/v0.2.0/nemo-speech-0.2.0-windows-x86_64-cuda.zip). Extract it, find the directory containing `nemo-speech.exe`, and copy that directory's contents, including DLLs, into `runtime/nemo`.
2. **Translation:** [llama.cpp b11669 CUDA 12.4 ZIP](https://github.com/ggml-org/llama.cpp/releases/download/b11669/llama-b11669-bin-win-cuda-12.4-x64.zip) and its [CUDA runtime DLL ZIP](https://github.com/ggml-org/llama.cpp/releases/download/b11669/cudart-llama-bin-win-cuda-12.4-x64.zip). Extract the executable and runtime DLL contents into the same `runtime/llama` directory.

Preserve the accompanying libraries; copying only executables will not work. An NVIDIA driver compatible with these builds is required. This project does not install or modify GPU drivers.

最终请确认下面两个路径存在，相关 DLL 也在相应目录：

```text
runtime/nemo/nemo-speech.exe
runtime/llama/llama-server.exe
```

Alternatively, set `streaming_asr_runtime` and `translation_runtime` to the actual extracted executable paths.

## 4. Check configuration / 核对配置

`config.example.json` uses project-relative paths. Copy it to `config.json` and edit these fields when necessary:

| Field | Meaning |
| --- | --- |
| `asr_backend` | `nemotron` for streaming; `qwen` for compatibility |
| `streaming_asr_model` | Nemotron GGUF path |
| `streaming_asr_runtime` | `nemo-speech.exe` path |
| `translation_model` | Hy-MT2 GGUF path |
| `translation_runtime` | `llama-server.exe` path |
| `asr_model` | Optional Qwen model directory |

Use forward slashes in JSON paths, such as `E:/AI_Models/example.gguf`. Keep the host at `127.0.0.1`. The extension expects backend port `18765`; changing it requires corresponding extension changes. Internal translation and ASR ports default to `18766` and `18767`.

配置路径可使用 `/`，避免反斜杠的 JSON 转义问题。普通使用不需要改端口。修改配置后退出本地服务并重新启动。

## 5. Start and pair the extension / 启动并连接插件

From the repository folder:

```powershell
.\.venv\Scripts\python.exe run.py
```

Or double-click `启动本地视频翻译.vbs`. It uses this repository's `.venv` and opens the local control panel. Wait for model loading to finish.

In Chrome:

1. Open `chrome://extensions` and enable Developer mode.
2. Choose **Load unpacked** and select the repository's `extension` directory.
3. Copy the local connection code from the control panel.
4. Open the extension, expand connection settings, paste the code and save.
5. On a normal video webpage, start translation and play the video.

Refresh the extension after updating its files, then refresh the video page. Restart the local service after backend updates. Deleting `runtime/access.json` generates a new connection code on the next start and requires pairing again.

## Optional: Qwen compatibility mode / 可选兼容模式

Streaming mode does not require this step. For Qwen, download the full [Qwen3-ASR-1.7B directory](https://huggingface.co/Qwen/Qwen3-ASR-1.7B) and install the optional dependencies.

The development environment used PyTorch 2.10.0 with CUDA 12.8:

```powershell
.\.venv\Scripts\python.exe -m pip install torch==2.10.0 --index-url https://download.pytorch.org/whl/cu128
.\.venv\Scripts\python.exe -m pip install -r requirements-qwen.txt
```

Point `asr_model` at the downloaded directory. Stop any active translation, then switch recognition mode in the control panel. Qwen mode generally needs more GPU memory and can produce longer subtitle-update gaps.

## Troubleshooting / 常见问题

- **Service not connected:** start the local service and confirm the connection code is saved in the extension.
- **Model load failure:** check filenames, driver compatibility and DLL locations. Read `logs/backend.log`, `logs/streaming-asr.log` and `logs/translation-runtime.log`.
- **Old subtitles after seeking:** stop and start translation again. Some embedded players do not expose playback events.
- **GPU memory remains occupied:** use the control panel's release or shutdown button. Closing its browser tab alone does not stop the backend.

## Developer checks

The included tests do not download models. Prepare `config.json` first. Node.js is needed only for the audio-worklet check.

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py"
node tests/test_pcm_worklet.mjs
```

Browser integration scripts tied to the original developer's browser profile, recordings and local benchmark outputs are not included.
