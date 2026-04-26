# AWQ Recovery Baseline (2026-04-27)

AWQ runtime loading is stabilized with an AWQ-first loader path, container dependency installation is hardened for reproducible builds, and operational recovery evidence is synchronized across checklist and release docs.

## Milestone Tags

- awq-runtime-loader-v1 (5eb1b53)
- awq-image-hardening-v1 (896c7c1)
- awq-ops-evidence-v1 (62579fe)

## Included Changes

- Runtime: AWQ-first load path with safe fallback.
- Image: deterministic install path for gptqmodel and metadata-broken packages.
- Docs/Ops: restart + health validation evidence and release traceability.
- Baseline assets: compose profiles, monitoring/nginx, lightweight web UI.

## Verification

- AWQ load success observed in runtime logs.
- Health endpoint returned successful responses after restart stabilization.

## Breaking Changes

- None.

## Rollback

- Revert by milestone tag order: awq-ops-evidence-v1 -> awq-image-hardening-v1 -> awq-runtime-loader-v1.
