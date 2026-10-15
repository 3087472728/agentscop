# Build and push the agentscop-platform server image to the Aliyun ACR
# (personal edition) docker registry, optionally re-tagging the upstream
# images (redis / qdrant / TEI) through the same namespace so the target
# server only needs ACR reachability.
#
# Registry: public endpoint (the "-vpc" endpoint only resolves inside
# the Aliyun VPC — useless for the office LAN and the intranet server):
#   crpi-d92le52k5f114023.cn-hangzhou.personal.cr.aliyuncs.com/<ns>/<image>:<tag>
#
# Two build paths:
#   .\deploy\build-push.ps1                # local docker build (needs
#                                          # working daemon egress)
#   .\deploy\build-push.ps1 -PushUpstream  # also push redis/qdrant/tei
#
# Legacy Nexus path (-ViaServer, build on the Telecom cloud host) is
# retired for ACR — the guard below rejects it.
#
# Requires: docker login <Registry> done once (user + fixed password
# from the ACR console "Access Credentials" page).

param(
    [string]$Registry = "crpi-d92le52k5f114023.cn-hangzhou.personal.cr.aliyuncs.com",
    [string]$Namespace = "agentrunner",
    [string]$Tag = "",
    [switch]$PushUpstream,
    [switch]$UpstreamOnly,
    [switch]$ViaServer
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot

if ($ViaServer) {
    throw "-ViaServer is the legacy Nexus path (build on the Telecom cloud host). ACR is public HTTPS - build and push directly from this machine."
}

if (-not $Tag) {
    $eap = $ErrorActionPreference; $ErrorActionPreference = "Continue"
    $sha = git -C $Root rev-parse --short HEAD 2>$null
    $ErrorActionPreference = $eap
    if ($LASTEXITCODE -ne 0 -or -not $sha) { $sha = $null }
    $Tag = if ($sha) { "{0}-{1}" -f (Get-Date -Format "yyyyMMdd"), $sha }
           else       { Get-Date -Format "yyyyMMdd-HHmmss" }  # no git repo: timestamp-only tag
}

function Invoke-Step([string]$Desc, [scriptblock]$Action) {
    Write-Host "[$Desc]" -ForegroundColor Cyan
    & $Action
    if ($LASTEXITCODE -ne 0) { throw "$Desc failed (exit $LASTEXITCODE)" }
}

# ---------- 0. Load deploy/.env and login if needed ----------
$EnvFile = Join-Path $PSScriptRoot ".env"
$CredUser = $null; $CredPass = $null
if (Test-Path $EnvFile) {
    Get-Content $EnvFile | ForEach-Object {
        if ($_ -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$') {
            switch ($Matches[1]) {
                'REGISTRY_USER'     { $CredUser = $Matches[2] }
                'REGISTRY_PASSWORD' { $CredPass = $Matches[2] }
                'REGISTRY'          { if (-not $PSBoundParameters.ContainsKey('Registry'))   { $script:Registry = $Matches[2] } }
                'NAMESPACE'         { if (-not $PSBoundParameters.ContainsKey('Namespace')) { $script:Namespace = $Matches[2] } }
            }
        }
    }
    if ($CredUser -and $CredPass) {
        # Prefer an existing working login (docker login / credential store);
        # only use deploy/.env credentials when not authenticated yet.
        $eap = $ErrorActionPreference; $ErrorActionPreference = "Continue"
        $stored = docker-credential-desktop list 2>$null | Out-String
        $ErrorActionPreference = $eap
        if ($stored -match [regex]::Escape($Registry)) {
            Write-Host "[login $Registry] already authenticated (stored credential)" -ForegroundColor DarkGray
        }
        else {
            Write-Host "[login $Registry] (credentials from deploy/.env)" -ForegroundColor Cyan
            Write-Output $CredPass | docker login $Registry --username $CredUser --password-stdin
            if ($LASTEXITCODE -ne 0) { throw "docker login $Registry failed (check deploy/.env or run docker login manually)" }
        }
    }
}

# ---------- 1. App image ----------
$AppImage = "${Registry}/${Namespace}/agentscop-platform:${Tag}"

if ($UpstreamOnly) {
    Write-Host "[-UpstreamOnly] skipping app build/push (latest: $AppImage)" -ForegroundColor DarkGray
}
else {
    Invoke-Step "build $AppImage" {
        # ACR personal edition rejects OCI attestation manifests
        # (vnd.oci.empty.v1+json) emitted by default on the containerd image
        # store - build without provenance/sbom attestations.
        docker build --provenance=false --sbom=false -f (Join-Path $PSScriptRoot "Dockerfile") -t $AppImage $Root
    }
    Invoke-Step "push $AppImage" { docker push $AppImage }
}

# ---------- 2. Optional: upstream images through ACR ----------
if ($PushUpstream) {
    # Each image lists candidate sources: direct first, NJU mirror
    # fallback (docker hub / ghcr.io are flaky without a proxy on CN
    # networks). A local image short-circuits the pull.
    $upstream = @(
        @{ Candidates = @("redis:7", "docker.nju.edu.cn/library/redis:7"); Dst = "${Registry}/${Namespace}/redis:7" },
        @{ Candidates = @("qdrant/qdrant:v1.19.1", "docker.nju.edu.cn/qdrant/qdrant:v1.19.1"); Dst = "${Registry}/${Namespace}/qdrant:v1.19.1" },
        @{ Candidates = @("ghcr.nju.edu.cn/huggingface/text-embeddings-inference:cpu-1.8", "ghcr.io/huggingface/text-embeddings-inference:cpu-1.8"); Dst = "${Registry}/${Namespace}/tei:cpu-1.8" }
    )
    foreach ($u in $upstream) {
        $src = $null
        foreach ($cand in $u.Candidates) {
            if (docker images -q $cand) { $src = $cand; break }
            Write-Host "[pull $cand]" -ForegroundColor Cyan
            docker pull $cand
            if ($LASTEXITCODE -eq 0) { $src = $cand; break }
            Write-Host "[pull $cand failed, trying next source]" -ForegroundColor Yellow
        }
        if (-not $src) { throw "all sources failed for $($u.Dst): $($u.Candidates -join ', ')" }
        Invoke-Step "tag $src -> $($u.Dst)" { docker tag $src $u.Dst }
        Invoke-Step "push $($u.Dst)" { docker push $u.Dst }
    }
}

Write-Host ""
Write-Host "[done] app image: $AppImage" -ForegroundColor Green
Write-Host "Set AGENTS_IMAGE=$AppImage in the server .env, then:"
Write-Host "  docker compose pull && docker compose up -d"
