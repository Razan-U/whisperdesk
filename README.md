# WhisperDesk

Windows desktop app for local Whisper transcription: Ukrainian, English and
mixed Ukrainian/English/Russian, sequential queue, selected audio ranges,
checkpoint recovery, TXT export and retro light/dark themes.

Version **0.3.1** adds GitHub Releases based automatic updates with Stable/Test
channels, SHA-256 verification, background download, update-after-queue and rollback.
The existing NVIDIA preflight and automatic CPU retry remain available.

The 0.4 candidate adds preflight, local adaptive ETA, hardware recommendations,
independent transcription history, crash recovery and safe queue controls.
See the [release checklist](docs/release-readiness-0.4.md) for acceptance status.

- [Інструкція українською](README_UA.md)
- [Build Windows installers](installer/BUILD.md)
- [Validation and limitations](VALIDATION.md)

Source: Python 3.12, PySide6, faster-whisper / CTranslate2. License: MIT.
Model weights, recordings, transcripts and generated installers are not source
code and should not be committed. `.gitignore` excludes their usual paths.
