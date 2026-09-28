# WhisperDesk

Windows desktop app for local Whisper transcription: Ukrainian, English and
mixed Ukrainian/English/Russian, sequential queue, selected audio ranges,
checkpoint recovery, TXT export and retro light/dark themes.

Version **0.3.0** adds a private NVIDIA component download, GPU preflight,
automatic CPU retry after GPU failure, persistent diagnostics and a small updater.

- [Інструкція українською](README_UA.md)
- [Build Windows installers](installer/BUILD.md)
- [Validation and limitations](VALIDATION.md)

Source: Python 3.12, PySide6, faster-whisper / CTranslate2. License: MIT.
Model weights, recordings, transcripts and generated installers are not source
code and should not be committed. `.gitignore` excludes their usual paths.
