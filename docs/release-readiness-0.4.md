# WhisperDesk 0.4 — release readiness

Feature freeze starts after v0.3.2-beta.15. No new 0.4 features are added unless a release-blocking defect is found.

## Automated release gate
- Full pytest suite on Windows.
- Build embedded Windows payload.
- Compile Launcher, Update and Setup installers.
- Verify non-empty build outputs.
- Updater semver/channel/integrity/fallback tests.
- Queue persistence/restart tests.
- Journal/checkpoint/partial-write recovery tests.
- Crash marker + missing queue recovery tests.
- History persistence/cleanup/rerun tests.
- Preflight/hardware/recommendation tests.
- Pause/Resume and Stop/Close confirmation tests.
- 0.4 cross-feature regression tests.

## Manual release gate
1. Update an existing Test installation to the latest candidate through the built-in updater.
2. Preflight: verify file count, audio duration, ETA, selected model/device and blocker behavior.
3. Recommendation: Reject leaves settings untouched; Apply updates runnable tasks.
4. History: complete one transcription, open result/original, rerun it, delete a record.
5. Crash recovery: terminate WhisperDesk after a checkpoint and verify resume after restart.
6. Pause/Resume: pause after at least one checkpoint, wait, resume, verify it continues from the checkpoint.
7. Stop File: Reject confirmation once, then confirm; current file stops safely and remaining queue continues.
8. Stop Queue: Reject confirmation once, then confirm; remaining queue is preserved.
9. Close while active: Reject once, then confirm; restart must offer safe recovery.
10. ETA GPU validation: run turbo + Auto on a known file and record preflight range and actual wall time.
11. Repeat the same turbo + Auto run once so local calibration can be checked.
12. Update channel: Test sees prerelease; Stable must not offer prerelease.

## ETA acceptance
For the real hardware validation:
- First-run Auto estimate may be wide and is not a blocker if actual time is inside the displayed interval.
- After one compatible completed local sample, the second estimate must be materially narrower.
- The second actual runtime should fall inside the locally calibrated interval.
- If Auto falls back from GPU to CPU, record that fact separately; do not treat a CPU fallback as proof of GPU ETA accuracy.

## Release decision
RC can be created when:
- automated release gate is green;
- manual control-flow tests pass;
- turbo + Auto/GPU ETA validation passes or a concrete ETA defect is fixed;
- no open P0 regression remains.

Stable 0.4 can be released after RC installs/updates cleanly and the final smoke test is green.
