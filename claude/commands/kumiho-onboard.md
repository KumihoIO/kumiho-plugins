---
description: Run the Kumiho Memory onboarding wizard — venv, auth, MCP config, skill ingestion
argument-hint: "cloud | ce"
---

# Kumiho Onboarding Wizard

Run the onboarding wizard that configures the kumiho-memory plugin end-to-end:
Python venv, backend selection, MCP server config, and skill ingestion into
the graph.

## Before any step: resolve the interpreter

No single interpreter name exists on every platform. Resolve by OS first.
On Windows, **never execute `python`, `python3`, or a PATH-resolved `py` as a
probe**: App Execution Aliases and shims can open Store/legacy application
windows. Derive the home directory from the OS account record, then use the
existing `~/.kumiho/venv/Scripts/python.exe`; if it is absent, use only a
PE-validated Python Launcher at its OS-managed path. If neither works, stop and
report Python 3.10+ missing.
On macOS/Linux, prefer the shared `~/.kumiho/venv/bin/python`, then probe
`python3`, then `python`.

The MCP server itself starts through the persistent
`${CLAUDE_PLUGIN_DATA}/venv` compatibility alias. The wizard also records a
known-good absolute `KUMIHO_PYTHON` for older external integrations. Resolve
the bootstrap interpreter here, in this shell:

```bash
KUMIHO_PY="$HOME/.kumiho/venv/bin/python"
if ! "$KUMIHO_PY" -c 'import sys; raise SystemExit(sys.version_info < (3,10))' 2>/dev/null; then
  KUMIHO_PY=""
  for c in python3 python; do
    if "$c" -c 'import sys; raise SystemExit(sys.version_info < (3,10))' 2>/dev/null; then
      KUMIHO_PY="$c"; break
    fi
  done
fi
```

```powershell
$KumihoAccountHome = [Environment]::GetFolderPath(
  [Environment+SpecialFolder]::UserProfile
)
if (-not [IO.Path]::IsPathFullyQualified($KumihoAccountHome)) {
  throw "Windows account home could not be resolved safely"
}
$KumihoSharedPython = Join-Path $KumihoAccountHome ".kumiho\venv\Scripts\python.exe"
function Test-KumihoPe([string] $Path) {
  if (-not [IO.Path]::IsPathFullyQualified($Path) -or
      -not $Path.EndsWith(".exe", [StringComparison]::OrdinalIgnoreCase) -or
      $Path.Replace("/", "\").ToLowerInvariant().Contains("\windowsapps\") -or
      -not (Test-Path -LiteralPath $Path -PathType Leaf)) { return $false }
  $Stream = $null
  $Reader = $null
  try {
    $Stream = [System.IO.File]::OpenRead($Path)
    if ($Stream.Length -lt 68) { return $false }
    $Reader = [System.IO.BinaryReader]::new($Stream)
    if ($Reader.ReadUInt16() -ne 0x5A4D) { return $false }
    $Stream.Position = 0x3C
    $PeOffset = $Reader.ReadUInt32()
    if ($PeOffset -lt 64 -or $PeOffset -gt [Math]::Min($Stream.Length - 4, 4MB)) {
      return $false
    }
    $Stream.Position = $PeOffset
    return $Reader.ReadUInt32() -eq 0x00004550
  } catch {
    return $false
  } finally {
    if ($null -ne $Reader) { $Reader.Dispose() }
    elseif ($null -ne $Stream) { $Stream.Dispose() }
  }
}
$KumihoSharedOk = $false
if (Test-KumihoPe $KumihoSharedPython) {
  & $KumihoSharedPython -I -c "import os,sys; expected=os.path.normcase(os.path.realpath(sys.argv[1])); prefix=os.path.normcase(os.path.realpath(sys.prefix)); raise SystemExit(0 if sys.version_info >= (3,10) and prefix == expected and prefix != os.path.normcase(os.path.realpath(sys.base_prefix)) else 3)" (Split-Path -Parent (Split-Path -Parent $KumihoSharedPython))
  $KumihoSharedOk = $LASTEXITCODE -eq 0
}
if ($KumihoSharedOk) {
  $KumihoCommand = @($KumihoSharedPython)
} else {
  $KumihoWindows = [Environment]::GetFolderPath(
    [Environment+SpecialFolder]::Windows
  )
  $KumihoLaunchers = @(
    (Join-Path $KumihoWindows "py.exe"),
    (Join-Path $KumihoAccountHome "AppData\Local\Programs\Python\Launcher\py.exe")
  )
  $KumihoLauncher = $KumihoLaunchers |
    Where-Object { Test-KumihoPe $_ } |
    Select-Object -First 1
  if ($null -eq $KumihoLauncher) {
    throw "Python 3.10+ not found — install Kumiho Desktop or Python and retry"
  }
  & $KumihoLauncher -3 -I -S -c "import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 3)"
  if ($LASTEXITCODE -ne 0) {
    throw "The OS Python Launcher has no Python 3.10+ interpreter"
  }
  $KumihoCommand = @($KumihoLauncher, "-3")
}
```

Use the resolved command in every step below. On Windows, invoke it as
`$env:KUMIHO_CLAUDE_HOST="claude"; & $KumihoCommand[0] @($KumihoCommand | Select-Object -Skip 1) <arguments>`.
If resolution failed, stop rather than trying another executable alias.

## Steps

1. **Pick the backend.** The plugin can use either Kumiho Cloud (managed,
   API-token) or a self-hosted `kumiho-server` Community Edition (CE, no token).

   - Never accept a JWT/API token as this command's argument or ask the user to
     paste one into chat. Credentials belong only in a local terminal prompt.
   - If the argument is `ce` (or `self-hosted` / `community`), go straight to
     the **CE** path.
   - Otherwise ask:
     > Which backend? **1) Kumiho Cloud** (API token) or **2) Self-hosted CE**
     > (local kumiho-server, no token)?

     Wait for their reply before proceeding.

2. **Cloud path** — never collect a credential in chat or put one in process
   arguments. Give the user the resolved local-terminal command below. It uses
   a masked prompt on a TTY and reads a single line from stdin in automation:

   ```bash
   KUMIHO_CLAUDE_HOST=claude "$KUMIHO_PY" \
     "${CLAUDE_PLUGIN_ROOT}/scripts/setup.py" --token-stdin --yes
   ```

3. **CE path** — no token is needed. Run:

   ```bash
   KUMIHO_CLAUDE_HOST=claude "$KUMIHO_PY" \
     "${CLAUDE_PLUGIN_ROOT}/scripts/setup.py" --ce --yes
   ```

   If the user runs CE on a non-default endpoint, pass it through:
   `--ce-endpoint HOST:PORT` (default `127.0.0.1:9190`). Optional:
   `--ce-redis-url URL`, `--ce-llm-base-url URL` for a local LLM. Plaintext
   `redis://` is loopback-only; remote Redis must use `rediss://`.

   The CE server must be running first — see
   [kumiho-server-community](https://github.com/KumihoIO/kumiho-server-community).

   In both paths, if `CLAUDE_PLUGIN_ROOT` is not set, fall back to the plugin
   directory relative to this file (the `claude/` directory containing
   `scripts/`). `--yes` auto-confirms all yes/no prompts.

4. The wizard handles five steps automatically:
   - **Python venv** — creates or reuses the Kumiho Desktop/Claude/Codex
     runtime at `~/.kumiho/venv`; `${CLAUDE_PLUGIN_DATA}/venv` remains a hook
     compatibility link. Existing compatible packages are not reinstalled.
   - **Backend** — Cloud: validates and caches the token. CE: writes
     `KUMIHO_CLAUDE_MODE=ce` (+ endpoint) and probes the configured server.
   - **MCP config** — writes credentials/CE config to OS env, Claude Desktop
     config, and `.env.local` so the MCP server restarts configured
   - **Skill ingestion** — populates `CognitiveMemory/Skills` in the graph
     from SKILL.md and reference docs
   - **Verification** — Cloud: control-plane discovery. CE: `/api/_live` probe.

5. After the wizard completes, report the outcome concisely:
   - If setup succeeded (Cloud): "Onboarding complete. Start a new session —
     memory connects on first message."
   - If setup succeeded (CE): "CE onboarding complete. Ensure your
     kumiho-server CE is running, then start a new session."
   - If auth was skipped: "Onboarding complete but unauthenticated. Re-run
     `/kumiho-onboard` when you have a token."
   - If the script failed: relay the error and suggest running it manually
     from a terminal: `KUMIHO_CLAUDE_HOST=claude python scripts/setup.py`

## Guardrails

- **Never** request, echo, or forward auth tokens through chat or argv.
- The wizard is designed to be re-runnable (idempotent) — re-running it
  upgrades packages, re-authenticates / re-writes CE config, and re-ingests
  skills (stacking revisions, not duplicating).
- If the user just needs to re-authenticate, `/kumiho-onboard` handles it —
  the wizard validates and caches the new token without repeating other steps.
