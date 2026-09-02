<#
.SYNOPSIS
    One-time AWS bootstrap for the Kumiho hosted MCP resource server.

.DESCRIPTION
    Creates (or updates) the ECR repository, the two IAM roles App Runner needs,
    an autoscaling configuration, and the App Runner service itself, then prints
    the service ARN for the GitHub Actions secret.

    Adapted from kumiho-control/scripts/aws/bootstrap-apprunner.ps1. The
    differences that matter:

      * health check is GET /healthz (this service has no /api/ready);
      * the only runtime secret is KUMIHO_CONTROL_PLANE_INTERNAL_KEY, used for
        service-token introspection — everything else the RS needs is public
        configuration;
      * KUMIHO_MCP_HOSTED=1 is set here, not just in the Dockerfile: several SDK
        guards read the process flag between requests, and a missing flag would
        silently put a shared server on the single-tenant code path;
      * KUMIHO_MEMORY_DECISIONS is deliberately never set (Decision Memory
        assumes a local git checkout, which a hosted box does not have);
      * KUMIHO_STACK_MIDDLE_BAND=0 — hosted runs strong-only revision stacking
        until per-tenant score telemetry says the middle band is safe here.

    Idempotent: re-running updates the existing service in place.

.EXAMPLE
    pwsh ./deploy/bootstrap-apprunner.ps1 -UpdateGitHubSecret
#>

param(
    [string]$AwsAccountId = "901635709806",
    [string]$AwsRegion = "us-east-1",
    [string]$ServiceName = "kumiho-cloud-mcp",
    [string]$EcrRepository = "kumiho-cloud-mcp",
    [string]$ImageTag = "latest",
    [string]$GitHubRepo = "KumihoIO/kumiho-plugins",
    [string]$PublicUrl = "https://mcp.kumiho.cloud/mcp",
    [string]$ControlPlaneUrl = "https://control.kumiho.cloud",
    [switch]$UpdateGitHubSecret
)

$ErrorActionPreference = "Stop"

function Invoke-AwsText {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)

    $output = & aws @Arguments 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw ($output -join [Environment]::NewLine)
    }
    return ($output | Out-String).Trim()
}

function Invoke-Gh {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)

    & gh @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "gh command failed: gh $($Arguments -join ' ')"
    }
}

function Write-JsonFile {
    param(
        [Parameter(Mandatory = $true)][object]$InputObject,
        [Parameter(Mandatory = $true)][string]$Path
    )
    $InputObject | ConvertTo-Json -Depth 10 | Set-Content -Path $Path -Encoding ascii
}

function Get-RoleArn {
    param([Parameter(Mandatory = $true)][string]$RoleName)

    return Invoke-AwsText -Arguments @(
        "iam", "get-role", "--role-name", $RoleName, "--query", "Role.Arn", "--output", "text"
    )
}

function Ensure-Role {
    param(
        [Parameter(Mandatory = $true)][string]$RoleName,
        [Parameter(Mandatory = $true)][string]$TrustPolicyPath
    )

    $existingArn = (& aws iam get-role --role-name $RoleName --query "Role.Arn" --output text 2>$null | Out-String).Trim()
    if ($LASTEXITCODE -eq 0 -and $existingArn) {
        return $existingArn
    }

    Invoke-AwsText -Arguments @(
        "iam", "create-role",
        "--role-name", $RoleName,
        "--assume-role-policy-document", "file://$TrustPolicyPath",
        "--description", "Bootstrap role for $RoleName"
    ) | Out-Null

    return Get-RoleArn -RoleName $RoleName
}

function Ensure-InlinePolicy {
    param(
        [Parameter(Mandatory = $true)][string]$RoleName,
        [Parameter(Mandatory = $true)][string]$PolicyName,
        [Parameter(Mandatory = $true)][string]$PolicyPath
    )

    Invoke-AwsText -Arguments @(
        "iam", "put-role-policy",
        "--role-name", $RoleName,
        "--policy-name", $PolicyName,
        "--policy-document", "file://$PolicyPath"
    ) | Out-Null
}

function Ensure-ManagedPolicyAttachment {
    param(
        [Parameter(Mandatory = $true)][string]$RoleName,
        [Parameter(Mandatory = $true)][string]$PolicyArn
    )

    $attached = Invoke-AwsText -Arguments @(
        "iam", "list-attached-role-policies",
        "--role-name", $RoleName,
        "--query", "AttachedPolicies[?PolicyArn=='$PolicyArn'] | length(@)",
        "--output", "text"
    )

    if ($attached -eq "0") {
        Invoke-AwsText -Arguments @(
            "iam", "attach-role-policy", "--role-name", $RoleName, "--policy-arn", $PolicyArn
        ) | Out-Null
    }
}

function Ensure-EcrRepository {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$Region
    )

    $existing = (& aws ecr describe-repositories --region $Region --repository-names $Name --query "repositories[0].repositoryUri" --output text 2>$null | Out-String).Trim()
    if ($LASTEXITCODE -eq 0 -and $existing -and $existing -ne "None") {
        return $existing
    }

    return Invoke-AwsText -Arguments @(
        "ecr", "create-repository",
        "--region", $Region,
        "--repository-name", $Name,
        "--image-scanning-configuration", "scanOnPush=true",
        "--query", "repository.repositoryUri",
        "--output", "text"
    )
}

function Ensure-AutoScalingConfiguration {
    param(
        [Parameter(Mandatory = $true)][string]$ConfigName,
        [Parameter(Mandatory = $true)][string]$Region
    )

    $existingArn = Invoke-AwsText -Arguments @(
        "apprunner", "list-auto-scaling-configurations",
        "--region", $Region,
        "--query", "AutoScalingConfigurationSummaryList[?AutoScalingConfigurationName=='$ConfigName' && Status=='ACTIVE'] | [0].AutoScalingConfigurationArn",
        "--output", "text"
    )

    if ($existingArn -and $existingArn -ne "None") {
        return $existingArn
    }

    return Invoke-AwsText -Arguments @(
        "apprunner", "create-auto-scaling-configuration",
        "--region", $Region,
        "--auto-scaling-configuration-name", $ConfigName,
        "--max-concurrency", "60",
        "--min-size", "1",
        "--max-size", "10",
        "--query", "AutoScalingConfiguration.AutoScalingConfigurationArn",
        "--output", "text"
    )
}

function Get-SecretArn {
    param([Parameter(Mandatory = $true)][string]$SecretName)

    return Invoke-AwsText -Arguments @(
        "secretsmanager", "describe-secret",
        "--region", $AwsRegion,
        "--secret-id", $SecretName,
        "--query", "ARN",
        "--output", "text"
    )
}

function Wait-AppRunnerService {
    param(
        [Parameter(Mandatory = $true)][string]$ServiceArn,
        [Parameter(Mandatory = $true)][string]$Region
    )

    for ($attempt = 0; $attempt -lt 90; $attempt++) {
        $status = Invoke-AwsText -Arguments @(
            "apprunner", "describe-service",
            "--region", $Region,
            "--service-arn", $ServiceArn,
            "--query", "Service.Status",
            "--output", "text"
        )

        if ($status -eq "RUNNING") { return }
        if ($status -in @("CREATE_FAILED", "DELETE_FAILED", "PAUSED")) {
            throw "App Runner service entered unexpected status: $status"
        }
        Start-Sleep -Seconds 10
    }

    throw "Timed out waiting for App Runner service to reach RUNNING"
}

$tempRoot = Join-Path $env:TEMP "$ServiceName-apprunner-bootstrap"
New-Item -ItemType Directory -Path $tempRoot -Force | Out-Null

$instanceTrustPath = Join-Path $tempRoot "instance-trust.json"
$ecrTrustPath = Join-Path $tempRoot "ecr-trust.json"
$instancePolicyPath = Join-Path $tempRoot "instance-policy.json"
$serviceConfigPath = Join-Path $tempRoot "service-config.json"

$repositoryUri = Ensure-EcrRepository -Name $EcrRepository -Region $AwsRegion
$imageIdentifier = "${AwsAccountId}.dkr.ecr.${AwsRegion}.amazonaws.com/${EcrRepository}:${ImageTag}"
Write-Host "ECR repository: $repositoryUri"

# The only real secret this service holds. Everything else is public config.
$runtimeSecrets = @{
    KUMIHO_CONTROL_PLANE_INTERNAL_KEY = Get-SecretArn -SecretName "kumiho/CONTROL_PLANE_INTERNAL_KEY"
}

$runtimeEnvVars = @{
    KUMIHO_MCP_HOSTED      = "1"
    KUMIHO_MCP_PUBLIC_URL  = $PublicUrl
    KUMIHO_AS_ISSUER       = $ControlPlaneUrl
    KUMIHO_JWKS_URL        = "$ControlPlaneUrl/.well-known/kumiho-jwks.json"
    KUMIHO_CONTROL_PLANE_URL = $ControlPlaneUrl
    KUMIHO_MCP_DOCS_URL    = "https://kumiho.io/docs/connect/claude"
    KUMIHO_MCP_LOG_LEVEL   = "INFO"
    # Off by default in code too; pinned here so a future default flip cannot
    # quietly widen the authenticated surface of a running service. The legacy
    # SSE transport adds an /sse stream plus a /messages/ POST whose only
    # tenant binding is an in-process session map.
    KUMIHO_MCP_ENABLE_SSE  = "0"
    # Revision stacking runs strong-only for hosted tenants: a capture stacks
    # onto an existing item only at similarity >= 0.75 and above the lexical
    # overlap floor; the 0.55 type-match band is withheld. The SDK's own
    # default is the two-band gate, calibrated on one corpus, and its false
    # positives move the "published" tag onto an unrelated item -- they hide a
    # memory rather than merely duplicating one. Pinned here as well as in the
    # image and in code, so the mode survives a base-image change and is
    # visible in `aws apprunner describe-service`. Flip to "1" only for a
    # deployment whose own stack_mode / stack_score telemetry justifies it.
    KUMIHO_STACK_MIDDLE_BAND = "0"
    PORT                   = "8080"
    # Deliberately absent: KUMIHO_MCP_DEV_MODE, KUMIHO_MCP_ALLOW_SHIM,
    # KUMIHO_AUTH_TOKEN, KUMIHO_SERVICE_TOKEN, KUMIHO_MEMORY_DECISIONS,
    # UPSTASH_REDIS_URL, KUMIHO_HOSTED_LOCAL_REDIS, KUMIHO_LOCAL_REDIS_URL.
    # Any of those would give every tenant one ambient identity, or would let
    # the service start on a dependency set that cannot enforce the reviewed
    # tool profile.
}

Write-JsonFile -Path $instanceTrustPath -InputObject @{
    Version = "2012-10-17"
    Statement = @(
        @{
            Effect = "Allow"
            Principal = @{ Service = "tasks.apprunner.amazonaws.com" }
            Action = "sts:AssumeRole"
        }
    )
}

Write-JsonFile -Path $ecrTrustPath -InputObject @{
    Version = "2012-10-17"
    Statement = @(
        @{
            Effect = "Allow"
            Principal = @{ Service = "build.apprunner.amazonaws.com" }
            Action = "sts:AssumeRole"
        }
    )
}

Write-JsonFile -Path $instancePolicyPath -InputObject @{
    Version = "2012-10-17"
    Statement = @(
        @{
            Effect = "Allow"
            Action = @("secretsmanager:GetSecretValue")
            Resource = @($runtimeSecrets.KUMIHO_CONTROL_PLANE_INTERNAL_KEY)
        }
    )
}

$instanceRoleName = "$ServiceName-instance-role"
$ecrRoleName = "$ServiceName-ecr-access-role"

$instanceRoleArn = Ensure-Role -RoleName $instanceRoleName -TrustPolicyPath $instanceTrustPath
Ensure-InlinePolicy -RoleName $instanceRoleName -PolicyName "$ServiceName-instance-policy" -PolicyPath $instancePolicyPath

$ecrRoleArn = Ensure-Role -RoleName $ecrRoleName -TrustPolicyPath $ecrTrustPath
Ensure-ManagedPolicyAttachment -RoleName $ecrRoleName -PolicyArn "arn:aws:iam::aws:policy/service-role/AWSAppRunnerServicePolicyForECRAccess"

# IAM is eventually consistent; App Runner rejects a role it cannot see yet.
Start-Sleep -Seconds 10

$autoScalingArn = Ensure-AutoScalingConfiguration -ConfigName "$ServiceName-scaling" -Region $AwsRegion

$sourceConfiguration = @{
    AuthenticationConfiguration = @{ AccessRoleArn = $ecrRoleArn }
    AutoDeploymentsEnabled = $false
    ImageRepository = @{
        ImageIdentifier = $imageIdentifier
        ImageRepositoryType = "ECR"
        ImageConfiguration = @{
            Port = "8080"
            RuntimeEnvironmentVariables = $runtimeEnvVars
            RuntimeEnvironmentSecrets = $runtimeSecrets
        }
    }
}

Write-JsonFile -Path $serviceConfigPath -InputObject $sourceConfiguration

$existingServiceArn = Invoke-AwsText -Arguments @(
    "apprunner", "list-services",
    "--region", $AwsRegion,
    "--query", "ServiceSummaryList[?ServiceName=='$ServiceName'] | [0].ServiceArn",
    "--output", "text"
)

if (-not $existingServiceArn -or $existingServiceArn -eq "None") {
    Write-Host "Creating App Runner service $ServiceName..."
    $serviceArn = Invoke-AwsText -Arguments @(
        "apprunner", "create-service",
        "--region", $AwsRegion,
        "--service-name", $ServiceName,
        "--source-configuration", "file://$serviceConfigPath",
        "--instance-configuration", "Cpu=1 vCPU,Memory=2 GB,InstanceRoleArn=$instanceRoleArn",
        "--health-check-configuration", "Protocol=HTTP,Path=/healthz,Interval=10,Timeout=5,HealthyThreshold=1,UnhealthyThreshold=5",
        "--auto-scaling-configuration-arn", $autoScalingArn,
        "--query", "Service.ServiceArn",
        "--output", "text"
    )
} else {
    Write-Host "Updating existing App Runner service $ServiceName..."
    $serviceArn = $existingServiceArn
    Invoke-AwsText -Arguments @(
        "apprunner", "update-service",
        "--region", $AwsRegion,
        "--service-arn", $serviceArn,
        "--source-configuration", "file://$serviceConfigPath",
        "--instance-configuration", "Cpu=1 vCPU,Memory=2 GB,InstanceRoleArn=$instanceRoleArn",
        "--health-check-configuration", "Protocol=HTTP,Path=/healthz,Interval=10,Timeout=5,HealthyThreshold=1,UnhealthyThreshold=5",
        "--auto-scaling-configuration-arn", $autoScalingArn
    ) | Out-Null
}

Write-Host "Waiting for App Runner service to become RUNNING..."
Wait-AppRunnerService -Region $AwsRegion -ServiceArn $serviceArn

$serviceUrl = Invoke-AwsText -Arguments @(
    "apprunner", "describe-service",
    "--region", $AwsRegion,
    "--service-arn", $serviceArn,
    "--query", "Service.ServiceUrl",
    "--output", "text"
)

if (-not $serviceArn) {
    throw "App Runner service ARN is empty after bootstrap"
}

Write-Host ""
Write-Host "App Runner bootstrap complete."
Write-Host "  Service ARN: $serviceArn"
Write-Host "  Service URL: https://$serviceUrl"
Write-Host ""
Write-Host "Next:"
Write-Host "  1. gh secret set MCP_APP_RUNNER_SERVICE_ARN --repo $GitHubRepo --body `"$serviceArn`""
Write-Host "  2. Set ORIGIN_URL in cloud-mcp/worker/wrangler.toml (the workflow does this on deploy)."
Write-Host "  3. Point mcp.kumiho.cloud at the worker and exempt 160.79.104.0/21 from the WAF UA challenge."

if ($UpdateGitHubSecret) {
    Invoke-Gh -Arguments @("secret", "set", "MCP_APP_RUNNER_SERVICE_ARN", "--repo", $GitHubRepo, "--body", $serviceArn)
    Write-Host "Updated MCP_APP_RUNNER_SERVICE_ARN on $GitHubRepo"
}
