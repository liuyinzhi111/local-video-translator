# Validation and limitations

Development observations were made on Windows with an RTX 5060 8 GB and 32 GB RAM on 2026-10-26. They describe this prototype, not a performance guarantee.

## Completed during development

- Both model runtimes loaded on the NVIDIA GPU. Model download hashes matched official metadata.
- Real Chrome tab audio passed through the extension, backend and subtitle overlay using public speech samples and a public video page.
- English, French, Spanish and Italian samples were exercised. Other configured languages were not individually validated.
- Sixteen focused Python checks passed for segmentation, queue handling, cancellation, spoken numbers and word preservation. Audio-worklet checks covered 44.1 kHz and 48 kHz input.
- Invalid connection codes and untrusted WebSocket origins were rejected in service checks.

## Timing observations

Eight public FLEURS clips were sent through the production WebSocket at normal playback speed with the same translation model:

- Old Qwen pipeline: per-clip maximum subtitle-update gaps of roughly 3.3–10.7 seconds.
- Streaming pipeline: per-clip maximum update gaps of roughly 1.2–2.4 seconds.
- Streaming output first met a simple length threshold roughly 1.3–2.7 seconds after estimated speech onset. It was not faster on every clip.

The threshold was four Chinese characters, at least three source words, and completion of that translation generation. It did not establish semantic correctness. Speech onset was estimated from energy, not exact word alignment.

A subsequent context-joining change was not followed by another complete timing run. Development recordings and private local benchmark files are not included.

## Still outstanding

- A clean-machine installation of the source package.
- A 15-minute stability run of the latest streaming version.
- Complete long-pause, seek, video-switching and fullscreen regression after the latest changes.
- Accuracy evaluation across every configured language and diverse real-world audio.

A 15-minute run completed on an earlier Qwen version; it does not establish stability of the current streaming version.

## Known behavior and errors

Subtitles may be rewritten as more speech arrives. Acoustic endpointing can split a sentence at a rhetorical pause. Bounded context joining reduces some errors but cannot guarantee meaning; a continuation can replace earlier subtitles.

Observed errors include mistaken proper nouns, incorrectly recognized French words, and altered or omitted meanings in incomplete sentences. Deterministic normalization helps some large spoken numbers but cannot repair audio that was already transcribed incorrectly.

No additional translation-model benchmarks or long playback tests were run while preparing the GitHub package. Publication checks inspect source files, exclusions and syntax only.
