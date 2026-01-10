# Fetch an Azure AD token and invoke the Bedrock AgentCore runtime.
[CmdletBinding()]
param(
    # If not provided, a random FX-related prompt is chosen each run.
    [string]$Prompt,
    # Optional custom pool of prompts; defaults to built-in FX prompts.
    [string[]]$PromptPool,
    [string]$LogPath
)

$ErrorActionPreference = "Stop"

# Resolve script directory for logging even if PSScriptRoot is empty (e.g., PowerShell 5.x oddities).
$scriptRoot = if ($PSScriptRoot) {
    $PSScriptRoot
} elseif ($PSCommandPath) {
    Split-Path -Parent $PSCommandPath
} else {
    Get-Location
}

if (-not $LogPath) {
    $LogPath = Join-Path $scriptRoot "agentcore_invocation.log"
}

$defaultPromptPool = @(
    "What is the exchange rate from USD to EUR today?",
    "How many Japanese Yen do I get for one US dollar right now?",
    "Give me the current GBP to USD exchange rate.",
    "What is today's USD to CAD rate?",
    "What is the spot rate for USD to CHF?",
    "Convert 100 USD to EUR using the latest rate.",
    "What is the EUR to USD exchange rate at the moment?",
    "How many AUD for 1 USD currently?"
)

$poolToUse = if ($PromptPool -and $PromptPool.Count -gt 0) { $PromptPool } else { $defaultPromptPool }
if (-not $Prompt) {
    $Prompt = Get-Random -InputObject $poolToUse
}

$azureAiScope = "https://ai.azure.com/.default"
$invokeUrl = "https://bedrock-agentcore.us-west-2.amazonaws.com/runtimes/arn%3Aaws%3Abedrock-agentcore%3Aus-west-2%3A025211824558%3Aruntime%2Fagentcore_langgraph_agent-1EC4Au3NoU/invocations?qualifier=DEFAULT"

function Write-Log {
    param([string]$Message)
    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $line = "[$timestamp] $Message"
    Write-Host $line
    if ($LogPath) {
        Add-Content -Path $LogPath -Value $line
    }
}

function Decode-JwtPayload {
    param([Parameter(Mandatory)][string]$Token)

    $parts = $Token.Split(".")
    if ($parts.Count -lt 2) {
        return $null
    }

    $payload = $parts[1].Replace("-", "+").Replace("_", "/")
    switch ($payload.Length % 4) {
        2 { $payload += "==" }
        3 { $payload += "=" }
        0 { }
        default { return $null }
    }

    try {
        $json = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($payload))
        return $json | ConvertFrom-Json
    }
    catch {
        return $null
    }
}

function Get-BearerToken {
    param([string]$Scope)

    if (-not (Get-Command az -ErrorAction SilentlyContinue)) {
        throw "Azure CLI (az) is not available on PATH."
    }

    $token = az account get-access-token --scope $Scope --query accessToken --output tsv 2>$null
    if (-not $token) {
        throw "Failed to retrieve access token; make sure you're logged in with 'az login'."
    }

    return $token.Trim()
}

function Invoke-BedrockAgentCore {
    param(
        [string]$Token,
        [string]$PromptText
    )

    $headers = @{
        authorization = "Bearer $Token"
        "content-type" = "application/json"
    }

    $body = @{ prompt = $PromptText } | ConvertTo-Json -Depth 4
    return Invoke-RestMethod -Method Post -Uri $invokeUrl -Headers $headers -Body $body
}

try {
    $token = Get-BearerToken -Scope $azureAiScope
    $claims = Decode-JwtPayload -Token $token
    if ($claims) {
        Write-Log "Retrieved bearer token. iss=$($claims.iss) aud=$($claims.aud) Prompt: $Prompt"
    }
    else {
        Write-Log "Retrieved bearer token. Prompt: $Prompt"
    }

    $response = Invoke-BedrockAgentCore -Token $token -PromptText $Prompt
    Write-Log "Invocation succeeded; response below."

    $jsonResponse = $response | ConvertTo-Json -Depth 10
    Write-Host $jsonResponse
    if ($LogPath) {
        Add-Content -Path $LogPath -Value $jsonResponse
    }
}
catch {
    Write-Error $_
    if ($LogPath) {
        Add-Content -Path $LogPath -Value ("Error: " + $_.ToString())
    }
    exit 1
}
