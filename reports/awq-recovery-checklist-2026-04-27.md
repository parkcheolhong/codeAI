# AWQ Recovery Checklist (2026-04-27)

- Generated at: 2026-04-27 00:04:13
- Target service: custom-llm-server
- Endpoint: <http://127.0.0.1:8002/health>

## Checklist Status

- [x] 컨테이너 현재 상태 확인
  - Evidence: custom-llm-server Up, port 8002->8000 published

- [x] transformers 버전 복원(현행 운영 버전 확인)
  - Evidence: transformers==5.6.2

- [x] pypcre 메타데이터 수정 및 설치 검증
  - Evidence: PyPcre==0.3.2

- [x] gptqmodel 설치 검증
  - Evidence: gptqmodel==6.0.3+cu130torch2.11

- [x] 모델 로딩 실검증 2회
  - Evidence #1: status=healthy, model_loaded=true, model_load_error=null
  - Evidence #2: status=healthy, model_loaded=true, model_load_error=null

- [x] Dockerfile 영구 반영 확인
  - Evidence: custom-server/Dockerfile 기반 이미지 gpu-llm-server-custom-llm-server 실행 중

## Package Snapshot

- transformers==5.6.2
- PyPcre==0.3.2
- gptqmodel==6.0.3+cu130torch2.11
- Defuser==0.0.8
- logbar==0.1.5
- tokenicer==0.0.7
- device-smi==0.4.1

## Runtime Snapshot

- status: healthy
- model_loaded: true
- tokenizer_loaded: true
- model_load_error: null
- gpu.device_name: NVIDIA GeForce RTX 5090
- gpu.memory_allocated_gb: 19.434925056
- gpu.memory_reserved_gb: 19.599982592
- gpu.memory_total_gb: 34.19045888

## Final Deployment Revalidation (Post-Restart)

- Revalidation window: 2026-04-27 00:05 ~ 00:07 (KST)
- Operation: `docker compose restart custom-llm-server`
- Container status after restart: `Up` (port `8002->8000`)
- Loader completion evidence:
  - `Model loaded successfully via AWQ loader!`
  - `Application startup complete.`
  - `Uvicorn running on http://0.0.0.0:8000`
- Health check revalidation evidence (same endpoint rule):
  - `/health` 200 OK (1)
  - `/health` 200 OK (2)
- Note:
  - Host-side probing during startup warmup returned transient connection-closed errors.
  - After startup completion, runtime log confirmed `/health` 200 twice and service remained operational.
