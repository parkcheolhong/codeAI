# Release Title
AWQ Recovery Baseline - 2026-04-27

## Release Tag Strategy
Primary milestone tags shipped in this release:
- awq-runtime-loader-v1 (5eb1b53)
- awq-image-hardening-v1 (896c7c1)
- awq-ops-evidence-v1 (62579fe)

## Highlights
- AWQ runtime loading stabilized with dedicated AWQ-first path.
- Image build resiliency improved for gptqmodel and metadata-broken dependencies.
- Ops evidence synchronized with restart/health verification records.
- Baseline repository assets added: compose profiles, monitoring, nginx, lightweight web UI.

## Included Commits
- 5eb1b53 fix(runtime): add AWQ dedicated loader path and keep safe fallback
- 896c7c1 fix(image): harden dependency install path for gptqmodel and broken package metadata
- 62579fe docs(ops): sync AWQ recovery checklist with post-restart validation evidence
- 3b35d42 chore(repo): add baseline configs, compose profiles, and operation docs
- 30d3bac feat(observability): add monitoring stack and nginx gateway configs
- 785a734 feat(web-ui): add lightweight model server dashboard
- c314966 docs(release): add 2026-04-27 AWQ recovery release notes

## Verification Notes
- Runtime AWQ load success observed in container logs.
- Health endpoint returned successful responses after restart stabilization.
- Recovery checklist and release notes aligned to the same evidence set.

## Breaking Changes
- None explicitly introduced.

## Upgrade Notes
- Pull branch gpu-llm-server-awq-20260427.
- Rebuild custom-llm-server image to apply dependency hardening.
- Validate health endpoint after container startup.

## Rollback
- Rollback by reverting from milestone tags in reverse order:
  1) awq-ops-evidence-v1 (docs)
  2) awq-image-hardening-v1 (image/deps)
  3) awq-runtime-loader-v1 (runtime loader)
