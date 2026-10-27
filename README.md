<p align="center"><img src="extension/icons/icon-128.png" width="96" alt="Local Video Translator icon"></p>

# Local Video Translator

A local browser extension for real-time video transcription and subtitle translation, powered by on-device AI. No cloud APIs required.

[中文说明](README.zh-CN.md) · [Installation](docs/INSTALL.md) · [Validation and limitations](docs/VALIDATION.md)

Capture the audio of a Chrome tab, transcribe it on your NVIDIA GPU, and display translated subtitles over the page. The video does not need an existing subtitle track. The current interface and subtitle output are in Simplified Chinese; selecting another output language is not implemented in this version.

**Status: experimental Windows prototype.** This repository contains source code, not a ready-to-run installer. Models and inference runtimes must be downloaded separately. Fresh-machine installation has not yet been validated.

## Features

- Real tab-audio capture through a Manifest V3 extension; no microphone required.
- Local, cached streaming speech recognition with NVIDIA Nemotron 3.5 ASR.
- Local translation with Hy-MT2 1.8B through llama.cpp.
- Automatic source-language recognition, or a manually selected source language.
- Draggable subtitles, adjustable font size, optional original-language text and fullscreen support where the player permits it.
- Pause/resume and seek/source-change handling; one active tab at a time.
- Optional Qwen3-ASR compatibility mode for additional source languages.
- A local control panel for loading/unloading models and switching recognition modes.

Recognition and translation run locally. Playback websites still make their normal network requests, and the initial downloads need internet access.

## Requirements

- Windows x64 and Python 3.12.
- Chrome 116 or newer, with unpacked extensions enabled.
- An NVIDIA GPU and a compatible driver for the selected CUDA runtimes.
- Development hardware: RTX 5060 8 GB, 32 GB system RAM. Other hardware has not been systematically tested.

See the [installation guide](docs/INSTALL.md) for Python setup, exact model files, runtime downloads and configuration.

Once installed:

1. Run `启动本地视频翻译.vbs`, or `.\.venv\Scripts\python.exe run.py` from PowerShell in the repository folder.
2. Wait for the local control panel to report that the models are ready.
3. Open `chrome://extensions`, enable Developer mode, choose **Load unpacked**, and select this repository's `extension` folder.
4. Copy the connection code from the local control panel and save it in the extension's connection settings.
5. Open a video page, start translation in the extension, and play the video.

`Alt + Shift + T` starts or stops translation for the active tab. Stop translation before changing the source language or recognition mode. Closing the control-panel tab does not stop the backend; use its shutdown button to release the models.

## Source languages

| Mode | Source-language selection |
| --- | --- |
| Streaming / Nemotron | English, Spanish, Italian, French, German, Portuguese, Japanese, Korean, Russian, Arabic, Hindi, Vietnamese, Turkish, Dutch, Polish, Czech, Chinese |
| Compatibility / Qwen | All of the above, plus Thai, Indonesian, Malay, Filipino, Persian and Cantonese |

These selections reflect the configured model combination, not measured quality across every language. Initial hands-on work covered English, French, Spanish and Italian. Automatic language selection is bounded by the selected recognition mode.

## Architecture

```text
Chrome tab audio
    -> tabCapture + AudioWorklet (16 kHz PCM)
    -> authenticated loopback WebSocket
    -> Nemotron streaming ASR / optional Qwen ASR
    -> Hy-MT2 translation through llama.cpp
    -> subtitle overlay
```

The backend, translation runtime and streaming ASR runtime listen on loopback ports `18765`, `18766` and `18767`. Keep these ports available; the extension currently expects the backend at port `18765`.

## Privacy and local files

- Only the tab you start translating is captured. Its original audio remains audible.
- Audio stays in memory and is sent to local loopback services. Recording and subtitle persistence are not enabled by default.
- The local connection code is generated at runtime and saved in `runtime/access.json`, then stored locally in the paired extension.
- Models, runtimes, connection codes, local configuration, browser profiles, logs and development recordings are excluded from this repository.
- `.gitignore` is for Git tracking. It does not sanitize files manually uploaded through a browser.

## Current limitations

Subtitles can be revised as new speech arrives, including earlier text when an unfinished phrase continues. Names, numbers, accents, overlapping speech and background music can cause transcription or translation errors. Streaming mode has not been shown to be more accurate than Qwen in every scenario.

Protected media, browser-internal pages and picture-in-picture overlays are not supported. Playback events inside some cross-origin or closed players may not be detected; stopping and starting again clears the local context.

See [validation notes](docs/VALIDATION.md) for the work completed and the checks still outstanding. No accuracy or fixed-latency guarantee is made.

## Repository layout

```text
backend/             Local service, model adapters and control panel
extension/           Unpacked Chrome extension
assets/              Project icon and generation notes
config.example.json  Portable configuration template
requirements.txt     Python dependencies for streaming mode
requirements-qwen.txt Optional compatibility-mode dependencies
tests/               Focused unit and audio-worklet checks
docs/                Setup and validation notes
```

## Third-party components and licensing

Model weights and third-party runtime binaries are not redistributed here. Download them from their official sources and follow their respective licenses. See [THIRD_PARTY.md](THIRD_PARTY.md).

A license for this project's own source has not yet been selected.
