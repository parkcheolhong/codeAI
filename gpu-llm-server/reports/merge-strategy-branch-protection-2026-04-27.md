# Branch Protection Aware Merge Strategy

## Current Status
- **Branch Protection Not Currently Enabled on main**: Verified via `gh api repos/parkcheolhong/codeAI/branches/main/protection` → HTTP 404 "Branch not protected"
- All repository branches checked; none have active protection rules configured.
- This PR can proceed with standard merge practices without protection-rule constraints.

## Policy Snapshot (Not Currently Enabled)
- **required_status_checks**: none (no protection rule active)
- **required_pull_request_reviews**: none (no protection rule active)
- **required_conversation_resolution**: none (no protection rule active)
- **enforce_admins**: none (no protection rule active)
- **allow_force_pushes**: unrestricted (no protection rule active)
- **allow_deletions**: unrestricted (no protection rule active)

## Future Protection Rules
If branch protection rules are added to main in the future, run the following to update this strategy:

1. Fetch live protection rules
   - `gh auth status` (verify authentication)
   - `gh api repos/parkcheolhong/codeAI/branches/main/protection > reports/main-branch-protection-live.json`

2. Update this document with enforced policy values
   - Replace "Not Currently Enabled" snapshot with actual rule values
   - Adjust merge strategy accordingly (e.g., if linear-history required, use rebase-only)

## Safe Default Strategy
Use a protection-compatible path that succeeds under typical main branch policies.

1. Open PR
- Base: main
- Head: gpu-llm-server-awq-20260427
- Use the prepared PR body from reports/pr-body-2026-04-27.md

2. Require green checks before merge
- Run CI/workflow checks required by repository rules.
- Re-run failed checks after fixes; do not bypass.

3. Require review approvals
- At least one code-owner/reviewer approval recommended.
- Resolve all review threads before merge.

4. Keep branch up to date
- Rebase or merge latest main into head branch if required by policy.
- Re-run checks after update.

5. Merge method recommendation
- Prefer Squash and merge for linear history in this feature bundle.
- Suggested squash commit title:
  - feat: AWQ runtime recovery hardening and release baseline

6. Post-merge actions
- Create GitHub Release using reports/github-release-draft-awq-2026-04-27.md
- Keep milestone tags awq-runtime-loader-v1, awq-image-hardening-v1, awq-ops-evidence-v1
- Optionally mark pre-release first, then promote after production verification.

## If Strict Rules Exist
If repository enforces any of the following, apply before merge:
- Linear history: use rebase/squash only.
- Signed commits: sign final merge commit.
- Conversation resolution required: close all threads.
- Required deployment gate: complete environment deployment check.

## Command Snippets
- Check branch diff:
  - git fetch origin
  - git log --oneline origin/main..origin/gpu-llm-server-awq-20260427
- Update head branch with latest main (rebase path):
  - git checkout gpu-llm-server-awq-20260427
  - git fetch origin
  - git rebase origin/main
  - git push --force-with-lease
