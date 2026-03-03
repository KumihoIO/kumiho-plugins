param(
    [switch]$NoStart,
    [switch]$StartOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Add-PathIfMissing([string]$dir) {
    if ([string]::IsNullOrWhiteSpace($dir)) { return }
    if (-not (Test-Path $dir)) { return }
    $parts = $env:Path -split ';'
    if ($parts -contains $dir) { return }
    $env:Path = "$dir;$env:Path"
}

function Ensure-NpmOnPath {
    $npmCmd = Get-Command npm -ErrorAction SilentlyContinue
    if ($npmCmd) { return }

    # Common locations for npm on Windows (MSI install + global npm bin).
    Add-PathIfMissing (Join-Path $env:ProgramFiles 'nodejs')
    Add-PathIfMissing (Join-Path $env:APPDATA 'npm')

    $npmCmd = Get-Command npm -ErrorAction SilentlyContinue
    if (-not $npmCmd) {
        throw "npm was not found on PATH. n8n community package install requires npm (fixes: install Node.js v18/v20 LTS, or add 'C:\\Program Files\\nodejs' and '%APPDATA%\\npm' to PATH)."
    }
}

$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot

if ($StartOnly) {
    Write-Host "StartOnly enabled: skipping build/pack/install; launching n8n only." -ForegroundColor Yellow
}

if (-not $StartOnly) {
  Write-Host "Cleaning dist folder..." -ForegroundColor Cyan
  if (Test-Path "dist") {
      Remove-Item -Recurse -Force "dist"
  }

  Write-Host "Building nodes..." -ForegroundColor Cyan
  npm run build
  if ($LASTEXITCODE -ne 0) {
    throw "Build failed."
  }

  Write-Host "Ensuring node icon assets are present in dist..." -ForegroundColor Cyan

  $nodesRoot = Join-Path $projectRoot "nodes"
  $distRoot = Join-Path $projectRoot "dist"
  $distNodesRoot = Join-Path $distRoot "nodes"

  if (-not (Test-Path $distNodesRoot)) {
      throw "Build output missing: $distNodesRoot"
  }

  $nodeTsFiles = Get-ChildItem -Path $nodesRoot -Recurse -File -Filter "*.node.ts"
  if (-not $nodeTsFiles -or $nodeTsFiles.Count -eq 0) {
      Write-Warning "No *.node.ts files found under $nodesRoot"
  }

  $iconRegexSingle = [regex]"icon\s*:\s*'file:([^']+)'"
  $iconRegexDouble = [regex]'icon\s*:\s*"file:([^"]+)"'

  $copied = 0
  $missing = @()

  foreach ($nodeTs in $nodeTsFiles) {
      $nodeDir = Split-Path -Parent $nodeTs.FullName
      $nodeRelDir = $nodeDir.Substring($nodesRoot.Length).TrimStart('\\')
      $distNodeDir = Join-Path $distNodesRoot $nodeRelDir

      $content = Get-Content -LiteralPath $nodeTs.FullName -Raw
      $iconMatchResults = @()
      $iconMatchResults += $iconRegexSingle.Matches($content)
      $iconMatchResults += $iconRegexDouble.Matches($content)

      foreach ($m in $iconMatchResults) {
          $iconRel = $m.Groups[1].Value
          if ([string]::IsNullOrWhiteSpace($iconRel)) {
              continue
          }

          $iconExt = [System.IO.Path]::GetExtension($iconRel)
          if (-not $iconExt -or $iconExt.ToLowerInvariant() -ne '.svg') {
              throw "Non-SVG node icon detected in $($nodeTs.FullName): file:$iconRel. For n8n Cloud compatibility, icons must be SVG."
          }

          $sourceCandidate = Join-Path $nodeDir $iconRel
          $destCandidate = Join-Path $distNodeDir $iconRel

          try {
              $sourceFull = (Resolve-Path -LiteralPath $sourceCandidate -ErrorAction Stop).Path
          } catch {
              $missing += "$($nodeTs.FullName):$iconRel (expected at $sourceCandidate)"
              continue
          }

          $destFull = [System.IO.Path]::GetFullPath($destCandidate)
          $destDir = Split-Path -Parent $destFull
          if (-not (Test-Path $destDir)) {
              New-Item -ItemType Directory -Force -Path $destDir | Out-Null
          }

          Copy-Item -LiteralPath $sourceFull -Destination $destFull -Force
          $copied++
          Write-Host "Copied icon asset: $iconRel" -ForegroundColor Green
      }
  }

  if ($missing.Count -gt 0) {
      Write-Warning "One or more node icon assets were referenced but not found:"
      $missing | ForEach-Object { Write-Warning "  $_" }
      throw "Missing icon assets. Fix the paths/files above or update the node icon declarations."
  }

  Write-Host "Copied $copied SVG icon(s) into dist." -ForegroundColor Green

  $customDir = Join-Path $env:USERPROFILE ".n8n\\custom"

  if (-not (Test-Path $customDir)) {
    New-Item -ItemType Directory -Force -Path $customDir | Out-Null
  }

  Set-Location $customDir

  if (-not (Test-Path "package.json")) {
    Write-Host "Initializing custom directory..." -ForegroundColor Cyan
    npm init -y
  }

  # Remove any existing link/install
  if (Test-Path "node_modules/n8n-nodes-kumiho") {
      Write-Host "Removing existing package from custom directory..." -ForegroundColor Cyan
      npm uninstall n8n-nodes-kumiho
  }

  # Also clean up the official nodes directory where UI-installed nodes go
  $officialNodesDir = Join-Path $env:USERPROFILE ".n8n\\nodes"
  if (Test-Path "$officialNodesDir\\node_modules\\n8n-nodes-kumiho") {
      Write-Host "Removing duplicate from official nodes directory..." -ForegroundColor Cyan
      Remove-Item -Path "$officialNodesDir\\node_modules\\n8n-nodes-kumiho" -Recurse -Force
  }

  Write-Host "Packing and installing..." -ForegroundColor Cyan
  # 1. Create a tarball of the project (avoids symlink issues)
  $tarball = npm pack "$projectRoot"

  if (-not (Test-Path $tarball)) {
      throw "Failed to create tarball: $tarball"
  }

  # 2. Install from the tarball
  npm install $tarball --omit=peer

  # 3. Clean up tarball
  Remove-Item $tarball

  # 4. Remove n8n-workflow to avoid instance conflicts (peer dependency issue)
  if (Test-Path "node_modules/n8n-workflow") {
      Write-Host "Removing local n8n-workflow to prevent conflicts..." -ForegroundColor Cyan
      Remove-Item -Recurse -Force "node_modules/n8n-workflow"
  }

  # Verify installation
  $installedDist = Join-Path "node_modules/n8n-nodes-kumiho" "dist"
  if (-not (Test-Path $installedDist)) {
      Write-Warning "INSTALLED PACKAGE IS MISSING DIST FOLDER!"
      Get-ChildItem "node_modules/n8n-nodes-kumiho"
  } else {
      Write-Host "Verified dist folder exists in installed package." -ForegroundColor Green
  }
} else {
  # Start-only still runs from the n8n custom dir so N8N_CUSTOM_EXTENSIONS can resolve.
  $customDir = Join-Path $env:USERPROFILE ".n8n\\custom"
  if (-not (Test-Path $customDir)) {
    New-Item -ItemType Directory -Force -Path $customDir | Out-Null
  }
  Set-Location $customDir
}

if (-not $NoStart) {
  Write-Host "Starting n8n..." -ForegroundColor Cyan
  Write-Host "IMPORTANT: If nodes don't show up, please HARD REFRESH your browser (Ctrl+F5)" -ForegroundColor Yellow
    Ensure-NpmOnPath
  $env:N8N_LOG_LEVEL = 'debug'
  $env:N8N_LOG_OUTPUT = 'console'

  # Match docker-compose style: NODES_EXCLUDE=["n8n-nodes-base.localFileTrigger"]
  $env:NODES_EXCLUDE = '["n8n-nodes-base.localFileTrigger"]'
  Write-Host "NODES_EXCLUDE: $env:NODES_EXCLUDE" -ForegroundColor Cyan
  
  # Try letting n8n discover it naturally in the custom folder first
  # If that fails, uncomment the line below to force it
  $env:N8N_CUSTOM_EXTENSIONS = Join-Path $customDir "node_modules"
  Write-Host "N8N_CUSTOM_EXTENSIONS: $env:N8N_CUSTOM_EXTENSIONS" -ForegroundColor Cyan
  
  # Allow access to ComfyUI output and local n8n files
  $env:N8N_RESTRICT_FILE_ACCESS_TO = "$env:USERPROFILE\.n8n-files;C:\ComfyUI\output;C:\Users\isake\AppData\Local\com.kumiho.ingest.studio"
  Write-Host "N8N_RESTRICT_FILE_ACCESS_TO: $env:N8N_RESTRICT_FILE_ACCESS_TO" -ForegroundColor Cyan
  
  npx -y n8n@latest
}
