param(
    [Parameter(Mandatory = $false)]
    [string]$ContainerName = $(if ($env:CODEAI_BACKEND_CONTAINER) { $env:CODEAI_BACKEND_CONTAINER } else { "devanalysis114-backend" }),

    [Parameter(Mandatory = $false)]
    [string]$ImageName = "devanalysis114-backend",

    [Parameter(Mandatory = $false)]
    [int]$PublishedPort = 18000,

    [Parameter(Mandatory = $false)]
    [switch]$SkipHttpCheck
)

$ErrorActionPreference = "Stop"

function Resolve-BackendNetworkName {
    $candidateContainers = @(
        "devanalysis114-redis",
        "devanalysis114-postgres",
        "devanalysis114-nginx",
        "devanalysis114-frontend-admin",
        "devanalysis114-qdrant",
        "devanalysis114-minio"
    )

    foreach ($candidate in $candidateContainers) {
        $networkName = (& docker inspect -f "{{range `$k, `$v := .NetworkSettings.Networks}}{{println `$k}}{{end}}" $candidate 2>$null | Select-Object -First 1)
        if (-not [string]::IsNullOrWhiteSpace($networkName)) {
            return $networkName.Trim()
        }
    }

    $networkCandidate = (& docker network ls --format "{{.Name}}" | Where-Object { $_ -match "devanalysis114|default|backend" -and $_ -notin @("bridge", "host", "none") } | Select-Object -First 1)
    if (-not [string]::IsNullOrWhiteSpace($networkCandidate)) {
        return $networkCandidate.Trim()
    }

    return ""
}

function Resolve-EnvFilePath {
    $candidates = @(
        ".env",
        ".env.backend",
        "backend/.env",
        "backend/.env.backend"
    )

    foreach ($candidate in $candidates) {
        $fullPath = Join-Path $PSScriptRoot $candidate
        if (Test-Path $fullPath) {
            return $fullPath
        }
    }

    return ""
}

function Remove-ContainerIfExists {
    param([string]$Name)

    $containerId = (& docker ps -aq -f "name=^${Name}$" | Select-Object -First 1)
    if (-not [string]::IsNullOrWhiteSpace($containerId)) {
        & docker rm -f $Name | Out-Null
        if ($LASTEXITCODE -ne 0) {
            exit $LASTEXITCODE
        }
    }
}

function Wait-ContainerRunning {
    param([string]$Name)

    for ($attempt = 1; $attempt -le 30; $attempt++) {
        $state = (& docker inspect -f "{{.State.Running}}" $Name 2>$null | Out-String).Trim()
        if ($state -eq "true") {
            return
        }
        Start-Sleep -Seconds 2
    }

    & docker logs $Name
    throw "컨테이너가 실행 상태로 전환되지 않았습니다: $Name"
}

$networkName = Resolve-BackendNetworkName
$envFilePath = Resolve-EnvFilePath

Write-Host "[INFO] building image: $ImageName"
& docker build -t $ImageName $PSScriptRoot
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

Write-Host "[INFO] recreating container: $ContainerName"
Remove-ContainerIfExists -Name $ContainerName

$dockerRunArgs = @(
    "run",
    "-d",
    "--name", $ContainerName,
    "-p", "${PublishedPort}:8000"
)

if (-not [string]::IsNullOrWhiteSpace($networkName)) {
    $dockerRunArgs += @("--network", $networkName)
}

if (-not [string]::IsNullOrWhiteSpace($envFilePath)) {
    $dockerRunArgs += @("--env-file", $envFilePath)
}

$dockerRunArgs += $ImageName

& docker @dockerRunArgs
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

Wait-ContainerRunning -Name $ContainerName

$verifyArgs = @(
    "-File", (Join-Path $PSScriptRoot "verify_python313_container.ps1"),
    "-ContainerName", $ContainerName,
    "-PublishedPort", $PublishedPort
)
if ($SkipHttpCheck) {
    $verifyArgs += "-SkipHttpCheck"
}

& powershell @verifyArgs
if ($LASTEXITCODE -ne 0) {
    & docker logs $ContainerName
    exit $LASTEXITCODE
}

Write-Host "[OK] backend container rebuilt and verified: $ContainerName"
