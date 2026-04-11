param(
    [Parameter(Mandatory = $false)]
    [string]$ContainerName = $(if ($env:CODEAI_BACKEND_CONTAINER) { $env:CODEAI_BACKEND_CONTAINER } else { "" }),

    [Parameter(Mandatory = $false)]
    [int]$PublishedPort = 18000,

    [Parameter(Mandatory = $false)]
    [switch]$SkipHttpCheck
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

function Test-HttpHealthTwice {
    param([int]$Port)

    $url = "http://127.0.0.1:$Port/health"
    for ($attempt = 1; $attempt -le 2; $attempt++) {
        $success = $false
        for ($retry = 1; $retry -le 30; $retry++) {
            try {
                $response = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 5
                if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 300) {
                    $success = $true
                    break
                }
            }
            catch {
                Start-Sleep -Seconds 2
            }
        }

        if (-not $success) {
            throw "헬스체크 실패: $url"
        }
    }
}

$resolvedContainerName = Resolve-BackendContainerName -RequestedName $ContainerName
for ($attempt = 1; $attempt -le 2; $attempt++) {
    $pythonVersion = (& docker exec $resolvedContainerName python --version 2>&1 | Out-String).Trim()
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }

    Write-Host $pythonVersion
    if ($pythonVersion -notmatch '^Python 3\.13\.') {
        throw "컨테이너 Python 버전이 3.13이 아닙니다: $pythonVersion"
    }
}

for ($attempt = 1; $attempt -le 2; $attempt++) {
    & docker exec $resolvedContainerName python -c "import fastapi, annotated_doc, uvicorn; print('ok')"
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
}

if (-not $SkipHttpCheck) {
    Test-HttpHealthTwice -Port $PublishedPort
}
