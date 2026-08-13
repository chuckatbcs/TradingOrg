$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

function Start-LocalLlmHostAgent {
    $agentScript = Join-Path $PSScriptRoot "local-llm-host-agent.ps1"
    $port = 18787
    try {
        $health = Invoke-RestMethod -Uri "http://127.0.0.1:$port/health" -TimeoutSec 2
        if ($health.ok -and $health.agent -eq "tradingorg-local-llm-host-agent") {
            Write-Host "Local LLM host agent already running on port $port."
            return
        }
    } catch {
        # not running — start it
    }
    Write-Host "Starting local LLM host agent (lms bridge for Docker) on port $port..."
    Start-Process -FilePath "powershell.exe" -ArgumentList @(
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-File", $agentScript,
        "-Port", "$port"
    ) -WindowStyle Hidden
    $deadline = (Get-Date).AddSeconds(20)
    while ((Get-Date) -lt $deadline) {
        try {
            $health = Invoke-RestMethod -Uri "http://127.0.0.1:$port/health" -TimeoutSec 2
            if ($health.ok -and $health.agent -eq "tradingorg-local-llm-host-agent") {
                Write-Host "Local LLM host agent is ready."
                return
            }
        } catch {
            Start-Sleep -Milliseconds 400
        }
    }
    Write-Warning "Host agent did not become ready in time; hybrid local verify may fail until it is running."
}

Start-LocalLlmHostAgent

# Proactively start/load LM Studio so the first Verify does not wait cold.
try {
    Write-Host "Ensuring LM Studio server is up via host agent..."
    $started = Invoke-RestMethod -Method POST -Uri "http://127.0.0.1:18787/start" -ContentType "application/json" -Body '{"model":"qwen/qwen3-4b-2507"}' -TimeoutSec 180
    Write-Host "LM Studio ready on port $($started.port) ($($started.backend_url))"
} catch {
    Write-Warning "Could not pre-start LM Studio via host agent: $_"
}

Write-Host "Building images (if needed)..."
docker compose build
Write-Host "Starting web stack (UI + firm scheduler) on http://localhost:8000 ..."
docker compose up -d web
docker compose ps
Write-Host "Health:" (Invoke-RestMethod -Uri "http://localhost:8000/api/health" -TimeoutSec 10 | ConvertTo-Json -Compress)
