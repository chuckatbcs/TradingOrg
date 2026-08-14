# Host-side LM Studio launcher for TradingOrg Docker web.
# Listens on http://127.0.0.1:18787 so the container can POST /start via host.docker.internal.
param(
    [int]$Port = 18787,
    [string]$LmsPath = "",
    [int[]]$LmsPorts = @(1234, 1235, 1240, 8081),
    # Idle unload after this many seconds (lms --ttl).
    [int]$ModelTtlSec = 3600
)

$ErrorActionPreference = "Continue"
if (-not $LmsPath) {
    $LmsPath = Join-Path $env:USERPROFILE ".lmstudio\bin\lms.exe"
}
if (-not (Test-Path $LmsPath)) {
    throw "lms.exe not found at $LmsPath. Install LM Studio CLI or pass -LmsPath."
}

$prefix = "http://127.0.0.1:$Port/"
$listener = New-Object System.Net.HttpListener
$listener.Prefixes.Add($prefix)
try {
    $listener.Start()
} catch {
    Write-Host "Host agent already running on $prefix (or port in use): $_"
    exit 0
}

Write-Host "TradingOrg local-LLM host agent listening on $prefix"
Write-Host "Using lms: $LmsPath"

function Write-JsonResponse($ctx, $status, $obj) {
    $json = ($obj | ConvertTo-Json -Compress -Depth 8)
    $buffer = [System.Text.Encoding]::UTF8.GetBytes($json)
    $ctx.Response.StatusCode = $status
    $ctx.Response.ContentType = "application/json; charset=utf-8"
    $ctx.Response.Headers.Add("Access-Control-Allow-Origin", "*")
    $ctx.Response.ContentLength64 = $buffer.Length
    $ctx.Response.OutputStream.Write($buffer, 0, $buffer.Length)
    $ctx.Response.Close()
}

function Invoke-Lms {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$LmsArgs)
    $output = & $LmsPath @LmsArgs 2>&1 | ForEach-Object { "$_" }
    return ($output -join "`n").Trim()
}

function Get-LoadedModels {
    $raw = Invoke-Lms ps --json
    if (-not $raw) { return @() }
    try {
        $parsed = $raw | ConvertFrom-Json
        if ($null -eq $parsed) { return @() }
        if ($parsed -is [System.Array]) { return @($parsed) }
        return @($parsed)
    } catch {
        return @()
    }
}

function Test-ModelsEndpoint([int]$LmsPort) {
    try {
        $resp = Invoke-WebRequest -Uri "http://127.0.0.1:$LmsPort/v1/models" -UseBasicParsing -TimeoutSec 3
        return $resp.StatusCode -eq 200
    } catch {
        return $false
    }
}

function Test-ModelKeyMatch([string]$Wanted, $Entry) {
    if (-not $Wanted -or -not $Entry) { return $false }
    $keys = @(
        [string]$Entry.modelKey,
        [string]$Entry.identifier,
        [string]$Entry.indexedModelIdentifier,
        [string]$Entry.path
    ) | Where-Object { $_ }
    foreach ($k in $keys) {
        if ($k -eq $Wanted) { return $true }
        # Ignore LM Studio duplicate suffixes like model:2
        if ($k -like "$Wanted`:*") { return $true }
    }
    return $false
}

function Ensure-SingleModelLoaded([string]$Model) {
    <#
      Keep at most one instance of $Model loaded.
      Unload everything else so verify/smoke remaps do not leave VRAM packed.
    #>
    $notes = New-Object System.Collections.Generic.List[string]
    if (-not $Model) {
        $notes.Add("no model requested; leaving loaded set unchanged")
        return @{ notes = @($notes); identifier = $null }
    }

    $loaded = @(Get-LoadedModels)
    $keepers = @($loaded | Where-Object { Test-ModelKeyMatch $Model $_ })
    $extras = @($loaded | Where-Object { -not (Test-ModelKeyMatch $Model $_) })

    foreach ($extra in $extras) {
        $id = [string]$extra.identifier
        if (-not $id) { continue }
        $notes.Add("unloading extra model $id")
        $notes.Add((Invoke-Lms unload $id))
    }

    # Collapse duplicate instances of the same model (qwen/...:2, :3, ...).
    if ($keepers.Count -gt 1) {
        $primary = $keepers[0]
        foreach ($dup in $keepers[1..($keepers.Count - 1)]) {
            $id = [string]$dup.identifier
            if (-not $id) { continue }
            $notes.Add("unloading duplicate $id")
            $notes.Add((Invoke-Lms unload $id))
        }
        $keepers = @($primary)
    }

    if ($keepers.Count -eq 1) {
        $id = [string]$keepers[0].identifier
        $notes.Add("model already loaded as $id")
        return @{ notes = @($notes); identifier = $id }
    }

    $notes.Add("loading $Model (ttl=${ModelTtlSec}s)")
    $loadOut = Invoke-Lms load $Model -y --ttl $ModelTtlSec
    $notes.Add($loadOut)
    Start-Sleep -Seconds 1
    $after = @(Get-LoadedModels | Where-Object { Test-ModelKeyMatch $Model $_ })
    $id = if ($after.Count) { [string]$after[0].identifier } else { $Model }
    return @{ notes = @($notes); identifier = $id }
}

function Clear-LoadedModels {
    $notes = New-Object System.Collections.Generic.List[string]
    $before = @(Get-LoadedModels)
    if (-not $before.Count) {
        $notes.Add("no models loaded")
        return @{ ok = $true; unloaded = 0; notes = @($notes) }
    }
    $notes.Add((Invoke-Lms unload --all))
    $after = @(Get-LoadedModels)
    return @{
        ok       = ($after.Count -eq 0)
        unloaded = $before.Count
        notes    = @($notes)
        remaining = @($after | ForEach-Object { $_.identifier })
    }
}

function Start-LocalLlm([string]$Model) {
    $notes = New-Object System.Collections.Generic.List[string]
    # Prefer an already-healthy server on any candidate port.
    foreach ($p in $LmsPorts) {
        if (Test-ModelsEndpoint $p) {
            $ensured = Ensure-SingleModelLoaded -Model $Model
            foreach ($n in $ensured.notes) { $notes.Add($n) }
            return @{
                ok          = $true
                model       = $Model
                identifier  = $ensured.identifier
                port        = $p
                backend_url = "http://host.docker.internal:$p/v1"
                status      = (Invoke-Lms server status)
                notes       = @($notes + @("already running on port $p"))
            }
        }
    }

    $lastError = $null
    foreach ($p in $LmsPorts) {
        $notes.Add("trying lms server start --port $p")
        $startOut = Invoke-Lms server start --port $p --cors
        $notes.Add($startOut)
        $deadline = (Get-Date).AddSeconds(45)
        while ((Get-Date) -lt $deadline) {
            if (Test-ModelsEndpoint $p) {
                $ensured = Ensure-SingleModelLoaded -Model $Model
                foreach ($n in $ensured.notes) { $notes.Add($n) }
                return @{
                    ok          = $true
                    model       = $Model
                    identifier  = $ensured.identifier
                    port        = $p
                    backend_url = "http://host.docker.internal:$p/v1"
                    status      = (Invoke-Lms server status)
                    notes       = @($notes)
                }
            }
            if ($startOut -match "EACCES|EADDRINUSE|permission denied|address already in use") {
                $lastError = $startOut
                break
            }
            Start-Sleep -Seconds 2
            $notes.Add("waiting for LM Studio on port $p...")
        }
        $lastError = "port $p did not become ready"
        try { Invoke-Lms server stop | Out-Null } catch { }
    }

    return @{
        ok     = $false
        model  = $Model
        error  = $lastError
        notes  = @($notes)
    }
}

while ($listener.IsListening) {
    $ctx = $listener.GetContext()
    $path = $ctx.Request.Url.AbsolutePath.TrimEnd("/").ToLowerInvariant()
    if ($ctx.Request.HttpMethod -eq "OPTIONS") {
        $ctx.Response.StatusCode = 204
        $ctx.Response.Headers.Add("Access-Control-Allow-Origin", "*")
        $ctx.Response.Headers.Add("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        $ctx.Response.Headers.Add("Access-Control-Allow-Headers", "Content-Type")
        $ctx.Response.Close()
        continue
    }
    try {
        if ($ctx.Request.HttpMethod -eq "GET" -and ($path -eq "" -or $path -eq "/health" -or $path -eq "/status")) {
            $activePort = $null
            foreach ($p in $LmsPorts) {
                if (Test-ModelsEndpoint $p) { $activePort = $p; break }
            }
            $loaded = @(Get-LoadedModels)
            Write-JsonResponse $ctx 200 @{
                ok             = $true
                agent          = "tradingorg-local-llm-host-agent"
                lms            = $LmsPath
                server_running = [bool]$activePort
                port           = $activePort
                backend_url    = if ($activePort) { "http://host.docker.internal:$activePort/v1" } else { $null }
                server_status  = (Invoke-Lms server status)
                loaded_models  = @($loaded | ForEach-Object {
                        @{
                            identifier = $_.identifier
                            modelKey   = $_.modelKey
                            status     = $_.status
                        }
                    })
            }
            continue
        }
        if ($ctx.Request.HttpMethod -eq "POST" -and $path -eq "/start") {
            $reader = New-Object System.IO.StreamReader($ctx.Request.InputStream, $ctx.Request.ContentEncoding)
            $bodyText = $reader.ReadToEnd()
            $model = $null
            if ($bodyText) {
                try {
                    $body = $bodyText | ConvertFrom-Json
                    if ($body.model) { $model = [string]$body.model }
                } catch { }
            }
            $result = Start-LocalLlm -Model $model
            $code = if ($result.ok) { 200 } else { 503 }
            Write-JsonResponse $ctx $code $result
            continue
        }
        if ($ctx.Request.HttpMethod -eq "POST" -and ($path -eq "/cleanup" -or $path -eq "/unload")) {
            $reader = New-Object System.IO.StreamReader($ctx.Request.InputStream, $ctx.Request.ContentEncoding)
            $bodyText = $reader.ReadToEnd()
            $keep = $null
            if ($bodyText) {
                try {
                    $body = $bodyText | ConvertFrom-Json
                    if ($body.keep) { $keep = [string]$body.keep }
                    elseif ($body.model) { $keep = [string]$body.model }
                } catch { }
            }
            if ($keep) {
                $ensured = Ensure-SingleModelLoaded -Model $keep
                Write-JsonResponse $ctx 200 @{
                    ok         = $true
                    kept       = $keep
                    identifier = $ensured.identifier
                    notes      = $ensured.notes
                    loaded_models = @(Get-LoadedModels | ForEach-Object {
                            @{ identifier = $_.identifier; modelKey = $_.modelKey }
                        })
                }
            } else {
                Write-JsonResponse $ctx 200 (Clear-LoadedModels)
            }
            continue
        }
        Write-JsonResponse $ctx 404 @{ ok = $false; error = "not found"; agent = "tradingorg-local-llm-host-agent" }
    } catch {
        Write-JsonResponse $ctx 500 @{ ok = $false; error = "$_"; agent = "tradingorg-local-llm-host-agent" }
    }
}
