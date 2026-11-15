# Third-party notices

License review snapshot: 2026-11-05. Upstream terms can change; the linked
upstream license or model card controls at the time an operator obtains a
component.

The Civenthra source license does not relicense third-party software, models,
books, voices, portraits, fonts or generated assets. Users obtain those items
separately and must follow their original terms.

| Component | Role | Upstream terms | Distribution rule |
| --- | --- | --- | --- |
| [SoulX-FlashHead source](https://github.com/Soul-AILab/SoulX-FlashHead/blob/main/LICENSE) | Optional avatar inference | Apache-2.0 | Preserve upstream copyright and license notices. |
| [SoulX-FlashHead Lite DiT](https://huggingface.co/Soul-AILab/SoulX-FlashHead-1_3B) | Optional avatar model | Model-card terms (currently Apache-2.0) | Download separately; do not commit weights; re-check the model card before use. |
| [LTX-Video 0.9 VAE](https://huggingface.co/Lightricks/LTX-Video/blob/main/ltx-video-2b-v0.9.license.txt) in FlashHead Lite | Optional avatar VAE | OpenRAIL-M | Preserve OpenRAIL-M; never relicense it as the Civenthra source license. |
| [wav2vec2-base-960h](https://huggingface.co/facebook/wav2vec2-base-960h) | Avatar audio encoder | Apache-2.0 | Download separately; preserve notices. |
| [Hugging Face speech-to-speech](https://github.com/huggingface/speech-to-speech/blob/main/LICENSE)-derived UI files | Optional voice UI | Apache-2.0 | The copied snapshot retains `civenthra/companion/LICENSE`; preserve attribution when redistributing derived UI code. |
| [WanGP](https://github.com/deepbeepmeep/WanGP/blob/main/LICENSE.txt) | Optional MiniMax H3 runner | WanGP Community License 2.0 | Interoperate as an external install. Do not vendor or redistribute WanGP here. Its terms restrict selling, white-labeling, embedding in a paid product, or offering paid API/SaaS/hosted/OEM access without a separate license. |
| [MiniMax H3 and supporting weights](https://github.com/MiniMax-AI/MiniMax-H3) | Optional restricted scene generator | MiniMax H3 Community License Agreement | Not part of the open-source distribution. The upstream license excludes the EU, UK, Republic of Korea and USA; restricts use outside its Applicable Territory, redistribution, commercial products over its stated revenue threshold, and use of outputs to improve another AI model. Operators must read the current official license before use. |
| [coqui-tts code](https://github.com/idiap/coqui-ai-TTS) | Optional XTTS runtime | MPL-2.0 | Install separately; its code license does not change the model license. |
| [XTTS-v2 model and outputs](https://huggingface.co/coqui/XTTS-v2/blob/main/LICENSE.txt) | Optional voice cloning | Coqui Public Model License 1.0.0 | Non-commercial use only, including outputs. Civenthra does not bundle the model or accept its terms for the operator; use also requires documented voice authorization. |
| [faster-whisper](https://github.com/SYSTRAN/faster-whisper) and [Whisper](https://github.com/openai/whisper) | Optional local speech recognition | Upstream code/model terms | Install or download separately; preserve notices and verify the selected model card. |
| [llama.cpp](https://github.com/ggml-org/llama.cpp) | Optional local inference runtime | MIT | Install separately; model weights retain their own terms. |
| Qwen-family models | Optional local language and embedding models | Model-specific terms | Do not bundle weights; record the exact model revision and license before redistribution or deployment. |

## Public catalog sources

`civenthra/public_sources.json` links to third-party catalog and policy
endpoints. Those links do not make the catalog contents, books, scans,
transcriptions, metadata, or trademarks Apache-2.0. Project Gutenberg, DOAB,
OpenEdition, NCBI Bookshelf, Internet Archive, and Wikisource each retain their
own terms. Catalog metadata rights, work rights, transcription rights, and
download access are separate questions. Operators must review each item and
territory; downloaded content stays outside this source repository.

The public source distribution contains adapters only. `models/`, private media,
book corpora and generated output are excluded by `.gitignore` and the release
audit. Generated outputs may still contain third-party or personality rights;
the operator is responsible for reviewing each publication.

An adapter's presence is not a representation that the corresponding backend is
"open source" or lawful in every territory or deployment. H3 remains disabled by
default. Do not enable it, distribute its weights, or expose it to users until a
territory, use-case and current-license review has been completed.

In particular, an Apache-2.0 integration repository does not make MiniMax H3,
WanGP, XTTS-v2, reference voices, or generated outputs Apache-2.0 assets.
