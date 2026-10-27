# Third-party components

The extension, local control panel and stream orchestration call the components below. This repository does not bundle their model weights or runtime executables.

| Component | Role | Official source |
| --- | --- | --- |
| NVIDIA Nemotron 3.5 ASR 0.6B | Streaming speech recognition | https://huggingface.co/nvidia/nemotron-3.5-asr-streaming-0.6b |
| NVIDIA NeMo-Speech.cpp 0.2.0 | Windows CUDA streaming ASR runtime | https://github.com/NVIDIA/NeMo-Speech.cpp/releases/tag/v0.2.0 |
| Hy-MT2 1.8B Q6_K | Translation model | https://huggingface.co/tencent/Hy-MT2-1.8B-GGUF |
| llama.cpp b11669 | Local translation runtime | https://github.com/ggml-org/llama.cpp/releases/tag/b11669 |
| Qwen3-ASR 1.7B | Optional compatibility ASR | https://github.com/QwenLM/Qwen3-ASR |
| Silero VAD | Compatibility-mode speech segmentation | https://github.com/snakers4/silero-vad |
| text2num 3.1.0 | Rule-based spoken-number normalization | https://pypi.org/project/text2num/ |
| FastAPI, Uvicorn, httpx, websockets, NumPy | Local service and audio transport | Official Python package distributions |
| PyTorch and Transformers | Optional Qwen inference | https://pytorch.org/ and https://github.com/huggingface/transformers |
| Google FLEURS | Public development speech samples, not bundled | https://huggingface.co/datasets/google/fleurs |

Consult the upstream repositories and model cards for licenses and attribution requirements. FLEURS samples used during development are distributed upstream under CC BY 4.0. The public samples and browser profiles used for development are not part of this source repository.

The project icon was generated with an AI image tool, then converted to PNG/ICO sizes.
