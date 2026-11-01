# Third-party software and models

O-jarvis's own code is under the [MIT license](./LICENSE). The packages it ships and the models it downloads keep their own licenses.

## Bundled in the `.deb` and the AppImage

| Component | License | Use in O-jarvis |
|---|---|---|
| [piper-tts](https://github.com/OHF-Voice/piper1-gpl) | GPL-3.0-or-later | Local voice (the "Automatic" profile) |
| [faster-whisper](https://github.com/SYSTRAN/faster-whisper), [CTranslate2](https://github.com/OpenNMT/CTranslate2) | MIT | Local speech to text (the "Automatic" profile) |
| [openWakeWord](https://github.com/dscripka/openWakeWord) | Apache-2.0 | Wake word (the "Automatic" profile) |
| [ONNX Runtime](https://github.com/microsoft/onnxruntime) | MIT | Runs Meta's MMS voices on the CPU |
| [Playwright](https://github.com/microsoft/playwright-python) | Apache-2.0 | Jarvis's own browser |
| [PyAV](https://github.com/PyAV-Org/PyAV) | BSD-3-Clause | Audio decoding |
| [PyInstaller](https://github.com/pyinstaller/pyinstaller) bootloader | GPL-2.0-or-later with the PyInstaller exception | Starts the bundled engine |

Because the packages bundle piper-tts, distributing them follows the GPL-3.0; the MIT license is compatible with it, and the complete source code is public in this repository. The other Python dependencies are listed with their versions in [`engine/uv.lock`](./engine/uv.lock).

## Models downloaded at first use

| Model | License |
|---|---|
| Meta MMS TTS, French and English ([`facebook/mms-tts-fra`](https://huggingface.co/facebook/mms-tts-fra), [`facebook/mms-tts-eng`](https://huggingface.co/facebook/mms-tts-eng), ONNX versions by Xenova) | CC-BY-NC-4.0 (non-commercial use) |

Every other model downloaded from Réglages › Modèles locaux keeps the license shown on its own page. Muse Spark, Muse Spark Open, Muse Wonder, Muse Voice Transcribe and SAM 3.1 are called through APIs and follow their providers' terms.
