# Controlled testing (initial stage)

This workflow is a first step toward user-approved remote testing.

## Current scope
- Manual trigger only: **Actions → Controlled Windows tests → Run workflow**.
- Choices: `smoke` (pytest stops at first failure) or `full` (complete pytest suite).
- Runs on GitHub-hosted Windows, **not** the user's personal laptop.
- Isolates WhisperDesk test data in the runner's temporary directory.
- Publishes JUnit XML as a downloadable workflow artifact (retained 14 days).
- Does not publish transcripts, private recordings, models or credentials.
- No automatic updates or deployments.

## Launch permissions and limitations
Only authorize a run after an explicit user request. This connector currently
supports reading workflow results and re-running existing jobs but does not
expose the arbitrary workflow_dispatch action. Until an authorized dispatch
integration exists, the repository owner must click **Run workflow** on GitHub.

GitHub only exposes manual workflow dispatch in the Actions UI once the workflow
file is present on the default branch.

## Local hardware benchmarking: NOT YET ENABLED
Do **not** attach a self-hosted runner with access to a personal Windows session
to the public repository. Untrusted code/PR workflows, unsafe workflow edits,
or runner persistence can create a workstation compromise.

Before introducing real CPU/CUDA benchmarks:
1. Use a separate private repository or other isolated orchestrator.
2. Install a dedicated ephemeral runner under a non-administrator Windows account
   in an isolated VM or separate test PC; a personal daily-use laptop is not an
   appropriate unattended general-purpose runner.
3. Define fixed test commands, input audio hashes, limits and filesystem paths;
   do not allow arbitrary command inputs from a workflow.
4. Ensure runs are explicitly approved and clean up the runtime after each run.
5. Export redacted results without uploading private audio or transcripts.
6. Validate CPU, CUDA, ETA and power-mode measurements on the target hardware.

The initial workflow is suitable for automated software regression tests only,
not proof of GPU operation or transcription quality on local equipment.
