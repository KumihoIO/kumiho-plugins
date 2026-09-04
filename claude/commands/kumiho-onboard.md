---
description: Run the Kumiho Memory onboarding wizard — venv, SDK auth verification, backend config, skill ingestion
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
On macOS/Linux, derive the home directory from the OS account database, prefer
the shared `~/.kumiho/venv/bin/python`, then probe only fixed absolute Python
locations. Never trust host/project `HOME`, `PATH`, or a shell shim.

The MCP server itself starts through the persistent
`${CLAUDE_PLUGIN_DATA}/venv` compatibility alias. The wizard also records a
known-good absolute `KUMIHO_PYTHON` for older external integrations. Resolve
the bootstrap interpreter here, in this shell:

```bash
KUMIHO_UID=$(/usr/bin/id -u) || exit 1
KUMIHO_ACCOUNT_HOME=""
KUMIHO_PASSWD=""
if [ -x /usr/bin/getent ]; then
  KUMIHO_PASSWD=$(/usr/bin/getent passwd "$KUMIHO_UID")
elif [ -x /bin/getent ]; then
  KUMIHO_PASSWD=$(/bin/getent passwd "$KUMIHO_UID")
fi
if [ -n "$KUMIHO_PASSWD" ]; then
  IFS=: read -r _ _ _ _ _ KUMIHO_ACCOUNT_HOME _ <<EOF
$KUMIHO_PASSWD
EOF
elif [ -x /usr/bin/dscacheutil ]; then
  KUMIHO_DS=$(/usr/bin/dscacheutil -q user -a uid "$KUMIHO_UID")
  while IFS= read -r KUMIHO_LINE; do
    case "$KUMIHO_LINE" in
      "dir: "*) KUMIHO_ACCOUNT_HOME=${KUMIHO_LINE#dir: } ;;
    esac
  done <<EOF
$KUMIHO_DS
EOF
fi
case "$KUMIHO_ACCOUNT_HOME" in
  /*) ;;
  *) echo "Kumiho: OS account home could not be resolved safely" >&2; exit 1 ;;
esac

KUMIHO_SHARED_ROOT="$KUMIHO_ACCOUNT_HOME/.kumiho/venv"
KUMIHO_PY="$KUMIHO_SHARED_ROOT/bin/python"
if ! { [ -x "$KUMIHO_PY" ] && [ -f "$KUMIHO_SHARED_ROOT/pyvenv.cfg" ] &&
       "$KUMIHO_PY" -I -S -c 'import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 3)' 2>/dev/null &&
       "$KUMIHO_PY" -I -c 'import os,sys; raise SystemExit(0 if os.path.realpath(sys.prefix) == os.path.realpath(sys.argv[1]) else 3)' "$KUMIHO_SHARED_ROOT" 2>/dev/null; }; then
  KUMIHO_PY=""
  for KUMIHO_CANDIDATE in /usr/bin/python3 /usr/local/bin/python3 /opt/homebrew/bin/python3; do
    if [ -x "$KUMIHO_CANDIDATE" ] &&
       "$KUMIHO_CANDIDATE" -I -S -c 'import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 3)' 2>/dev/null; then
      KUMIHO_PY="$KUMIHO_CANDIDATE"
      break
    fi
  done
fi
[ -n "$KUMIHO_PY" ] || { echo "Kumiho: Python 3.10+ not found" >&2; exit 1; }
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

Use the resolved command in every step below and always add Python isolated
mode. On Windows, invoke it as `$KumihoPrefix = @($KumihoCommand |
Select-Object -Skip 1); $env:KUMIHO_CLAUDE_HOST="claude"; &
$KumihoCommand[0] @KumihoPrefix -I <arguments>`.
If resolution failed, stop rather than trying another executable alias.

## Steps

1. **Resolve the backend without asking.** The plugin can use either Kumiho
   Cloud (managed; explicit API token or SDK login) or a self-hosted
   `kumiho-server` Community Edition (CE, no Cloud token).

   - Never accept a JWT/API token as this command's argument or ask the user to
     paste one into chat. Credentials must be configured outside the plugin.
   - If the argument is `ce` (or `self-hosted` / `community`), go straight to
     the **CE** path.
   - If the argument is `cloud`, go straight to the **Cloud** path.
   - With no argument, reuse a valid explicit backend already present in the
     trusted host/user configuration. If none is present, select **Cloud**.
     Never pause to ask which backend to use.

2. **Cloud path** — runtime authentication, token refresh, discovery, and
   regional routing belong to the Python SDK. The plugin pins discovery to
   `https://control.kumiho.cloud`; never accept a custom control-plane or
   regional endpoint.

   Use credentials in this order:

   1. Prefer an explicit `KUMIHO_AUTH_TOKEN` configured persistently in the
      OS/user or trusted host environment **before Claude starts**. The plugin
      only passes the inherited value to the SDK; it never saves it.
   2. Otherwise authenticate the shared SDK credential store from a local
      terminal with `kumiho-auth login` or `kumiho-cli login`.

   If neither credential is currently available, do not ask for a secret.
   Report the two actions above and that Claude must be fully restarted after
   either one. Setup may still provision the runtime now and report Cloud as
   unauthenticated. Run setup without a token, using the resolved interpreter
   and platform-specific invocation form above:

   ```bash
   KUMIHO_CLAUDE_HOST=claude "$KUMIHO_PY" -I \
     "${CLAUDE_PLUGIN_ROOT}/scripts/setup.py" --yes
   ```

   `setup.py` provisions the shared venv, asks the SDK to verify authentication,
   and ingests skills; it does not own or persist Cloud credentials. Its legacy
   `--token` and `--token-stdin` flags are retained only for compatibility
   scripts that need one-process verification. Those values are not saved and
   do not configure the next Claude restart. The runtime keeps discovery in
   `~/.kumiho/official-cloud/discovery-cache.json`, isolated from legacy or
   custom-origin cache entries.

3. **CE path** — no token is needed. Run:

   ```bash
   KUMIHO_CLAUDE_HOST=claude "$KUMIHO_PY" -I \
     "${CLAUDE_PLUGIN_ROOT}/scripts/setup.py" --ce --yes
   ```

   If the user runs CE on a non-default endpoint, pass it through:
   `--ce-endpoint HOST:PORT` (default `127.0.0.1:9190`). Optional:
   `--ce-redis-url URL`, `--ce-llm-base-url URL` for a local LLM. The CE
   endpoint, Redis URL, and CE LLM URL must all use a loopback host, even when
   their schemes provide TLS.

   The CE server must be running first — see
   [kumiho-server-community](https://github.com/KumihoIO/kumiho-server-community).

   In both paths, if `CLAUDE_PLUGIN_ROOT` is not set, fall back to the plugin
   directory relative to this file (the `claude/` directory containing
   `scripts/`). `--yes` auto-confirms all yes/no prompts.

4. The wizard handles five steps automatically:
   - **Python venv** — creates or reuses the Kumiho Desktop/Claude/Codex
     runtime at `~/.kumiho/venv`; `${CLAUDE_PLUGIN_DATA}/venv` remains a hook
     compatibility link. Existing compatible packages are not reinstalled.
   - **Backend** — Cloud: verifies SDK-owned authentication and pins official
     discovery without persisting credentials.
     CE: writes `KUMIHO_CLAUDE_MODE=ce` (+ loopback endpoint) and probes the
     configured server.
   - **MCP config** — keeps Cloud credentials untouched; for CE, writes the
     validated backend selection to persistent Claude/OS config surfaces
   - **Skill ingestion** — populates `CognitiveMemory/Skills` in the graph
     from SKILL.md and reference docs
   - **Verification** — Cloud: SDK-owned discovery through the official control
     plane. CE: loopback `/api/_live` probe.

5. After the wizard completes, report the outcome concisely:
   - If setup succeeded (Cloud): "Onboarding complete. Start a new session —
     memory connects on first message."
   - If setup succeeded (CE): "CE onboarding complete. Ensure your
     kumiho-server CE is running, then start a new session."
   - If auth was skipped: "Onboarding complete but unauthenticated. Configure
     persistent `KUMIHO_AUTH_TOKEN` before starting Claude, or run
     `kumiho-auth login` / `kumiho-cli login` locally; restart Claude, then
     re-run `/kumiho-onboard`."
   - If the script failed: relay the error and suggest running it manually
     from a terminal using the resolved interpreter:
     `KUMIHO_CLAUDE_HOST=claude "$KUMIHO_PY" -I scripts/setup.py`

## Guardrails

- **Never** request, echo, or forward auth tokens through chat or argv.
- The wizard is designed to be re-runnable (idempotent) — re-running it
  upgrades packages, verifies SDK authentication or rewrites CE config, and
  re-ingests skills (stacking revisions, not duplicating).
- If the user just needs to re-authenticate, configure persistent
  `KUMIHO_AUTH_TOKEN` before host startup or run `kumiho-auth login` /
  `kumiho-cli login` in a local terminal. Never implement token storage,
  refresh, discovery, or regional routing in this command.
