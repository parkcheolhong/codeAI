param(
    [Parameter(Mandatory = $false)]
    [string]$ContainerName = $(if ($env:CODEAI_BACKEND_CONTAINER) { $env:CODEAI_BACKEND_CONTAINER } else { "" })
)

$ErrorActionPreference = "Stop"

function Resolve-BackendContainerName {
    param([string]$RequestedName)

    if (-not [string]::IsNullOrWhiteSpace($RequestedName)) {
        return $RequestedName
    }

    if (-not [string]::IsNullOrWhiteSpace($env:CODEAI_BACKEND_CONTAINER)) {
        return $env:CODEAI_BACKEND_CONTAINER
    }

    $runningNames = @(docker ps --format "{{.Names}}")
    if ($runningNames -contains "devanalysis114-backend") {
        return "devanalysis114-backend"
    }

    $backendCandidate = $runningNames | Where-Object { $_ -match "backend" } | Select-Object -First 1
    if (-not [string]::IsNullOrWhiteSpace($backendCandidate)) {
        return $backendCandidate
    }

    throw "컨테이너 이름이 필요합니다. -ContainerName <name> 또는 CODEAI_BACKEND_CONTAINER 환경 변수를 지정하세요."
}

$resolvedContainerName = Resolve-BackendContainerName -RequestedName $ContainerName
$pythonScript = @'
import os
import subprocess
import sys
import time

candidates = [
    "/app/run_profiler_backend.py",
    "/workspace/run_profiler_backend.py",
    "/src/run_profiler_backend.py",
    "/code/run_profiler_backend.py",
    "/workspace/codeAI/run_profiler_backend.py",
]
entrypoint = next((p for p in candidates if os.path.isfile(p)), None)
assert entrypoint, "run_profiler_backend.py not found in known container paths"
workdir = os.path.dirname(entrypoint)
log_file = "/tmp/run_profiler_backend.log"
open(log_file, "w", encoding="utf-8").close()
handle = open(log_file, "a", encoding="utf-8")
process = subprocess.Popen(
    [sys.executable, "-u", entrypoint],
    cwd=workdir,
    stdout=handle,
    stderr=subprocess.STDOUT,
)
try:
    deadline = time.time() + 20
    while time.time() < deadline:
        time.sleep(1)
        text = open(log_file, "r", encoding="utf-8").read()
        if "Uvicorn running on" in text:
            break
        if process.poll() is not None:
            break

    time.sleep(3)
finally:
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
    handle.close()

text = open(log_file, "r", encoding="utf-8").read()
print(text, end="")
if "Redis queue unavailable" in text:
    print("[FAIL] Redis queue unavailable detected", file=sys.stderr)
    sys.exit(2)
if "ad order runtime recovery bootstrap disabled" not in text:
    print("[FAIL] runtime recovery disable evidence missing", file=sys.stderr)
    sys.exit(3)
print("[OK] runtime recovery disabled evidence detected")
print("[OK] Redis queue warning not detected")
'@
$encodedScript = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($pythonScript))
$launcher = "import base64; exec(base64.b64decode('$encodedScript').decode('utf-8'))"

& docker exec $resolvedContainerName python -c $launcher
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}
