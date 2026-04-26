# Release Notes - 2026-04-27

## Scope
- AWQ runtime recovery and loader stabilization
- Container dependency hardening for reproducible build
- Operational evidence and restart health verification docs sync
- Baseline repository composition (compose/docs/monitoring/web-ui)

## Tagged Milestones
- `awq-runtime-loader-v1` -> `5eb1b53` : AWQ 전용 로더 경로 우선 적용 및 안전 폴백 유지
- `awq-image-hardening-v1` -> `896c7c1` : gptqmodel + 메타데이터 깨진 패키지 설치 경로 고정
- `awq-ops-evidence-v1` -> `62579fe` : 재기동 이후 health 검증 포함 체크리스트/커밋플랜 문서 동기화

## Additional Commits In This Push
- `3b35d42` chore(repo): baseline configs, compose profiles, docs
- `30d3bac` feat(observability): monitoring + nginx gateway
- `785a734` feat(web-ui): lightweight dashboard

## Validation Snapshot
- 모델 로딩 로그에서 AWQ 로더 성공 메시지 확인
- `/health` 엔드포인트 200 응답 다회 확인
- Docker build/install 경로에서 핵심 의존성 설치 가능성 점검
