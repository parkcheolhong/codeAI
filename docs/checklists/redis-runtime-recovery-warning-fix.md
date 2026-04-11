# Redis Runtime Recovery Warning Fix Checklist

- [x] `run_profiler_backend.py` 1차 기동 시 `Redis queue unavailable` 경고 미출력 확인
- [x] `run_profiler_backend.py` 2차 기동 시 `Redis queue unavailable` 경고 미출력 확인
- [x] `runtime_recovery` 단계가 환경 변수 비활성화로 skip 처리되는지 1차 확인
- [x] `runtime_recovery` 단계가 환경 변수 비활성화로 skip 처리되는지 2차 확인

## 반영 내용
- `backend/main.py`에 `ENABLE_AD_ORDER_RUNTIME_RECOVERY_BOOTSTRAP` 환경 변수 가드를 추가함.
- `run_profiler_backend.py`에서 프로파일러 기동 시 `ENABLE_AD_ORDER_RUNTIME_RECOVERY_BOOTSTRAP=false`를 기본 설정함.
- `run_profiler_backend.py`의 profiler 전용 환경 변수 적용 방식을 `setdefault(...)`에서 강제 할당으로 변경함.
- `run_profiler_backend.py`가 컨테이너 내부에서는 기본적으로 `0.0.0.0`에 바인딩하도록 수정함.
- `run_profiler_backend_container.ps1`가 짧게 기동 후 로그를 출력하고, `runtime_recovery` skip 및 Redis 경고 부재를 `[OK]/[FAIL]`로 명시 출력하도록 수정함.
- `run_profiler_backend_container.ps1`의 컨테이너 내부 검증 실행은 base64 인코딩된 Python 스크립트 방식으로 전환해 인용 오류를 제거함.

## 현재 상태
- 코드 수정은 반영됨.
- 실증 로그 기준으로 1차/2차 모두 `Redis queue unavailable` 경고가 출력되지 않았음.
- 실증 로그 기준으로 1차/2차 모두 `ad order runtime recovery bootstrap disabled` 로그가 확인되어 skip 처리 근거가 확보됨.
- 따라서 Redis runtime recovery 경고 수정은 코드 반영과 2회 실증 검증까지 완료됨.
