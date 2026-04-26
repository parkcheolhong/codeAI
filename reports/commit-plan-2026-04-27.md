# Commit Unit Plan (2026-04-27)

## Current Limitation

- This directory is **not** a Git repository (`.git` not found).
- Direct commit execution is blocked until Git is initialized or this folder is moved under an existing Git repo.

## Recommended Commit Units

### Commit 1: Runtime AWQ Loader Stability

- Files:
  - `custom-server/server.py`
- Purpose:
  - Add AWQ dedicated loader path (`AutoAWQForCausalLM.from_quantized`) before generic loader fallback.
  - Keep generic loader path as fallback while avoiding meta tensor failure path.
- Suggested message:
  - `fix(runtime): add AWQ dedicated loader path and keep safe fallback`

### Commit 2: Image Dependency Hardening for GPTQ/AWQ Runtime

- Files:
  - `custom-server/Dockerfile`
  - `custom-server/patch_pypcre.py`
- Purpose:
  - Persist metadata-workaround installation flow for pypcre/defuser/logbar/tokenicer ecosystem issues.
  - Ensure gptqmodel runtime dependencies are installed in deterministic order.
- Suggested message:
  - `fix(image): harden dependency install path for gptqmodel and broken package metadata`

### Commit 3: Deployment Verification Evidence Sync

- Files:
  - `reports/awq-recovery-checklist-2026-04-27.md`
- Purpose:
  - Record restart and post-restart health revalidation evidence.
  - Preserve operational proof (`model_loaded=true`, `/health` 200 evidence).
- Suggested message:
  - `docs(ops): sync AWQ recovery checklist with post-restart validation evidence`

## Commands to Run After Git Becomes Available

```bash
git add custom-server/server.py
git commit -m "fix(runtime): add AWQ dedicated loader path and keep safe fallback"

git add custom-server/Dockerfile custom-server/patch_pypcre.py
git commit -m "fix(image): harden dependency install path for gptqmodel and broken package metadata"

git add reports/awq-recovery-checklist-2026-04-27.md
git commit -m "docs(ops): sync AWQ recovery checklist with post-restart validation evidence"
```

## Optional Bootstrap (only if you want this folder itself to become a new repo)

```bash
git init
git checkout -b main
```

Then run the 3 commit blocks above.
