#!/usr/bin/env python3
"""Contract and smoke tests for the native Codex plugin.

The tests are intentionally stdlib-only and can run either under pytest or as
python codex/scripts/test_codex_plugin.py.  The optional Codex CLI smoke uses a
temporary CODEX_HOME and a local marketplace source, so it needs no network
access, account, or Kumiho token.
"""

import json
import importlib.util
import io
import os
import re
import shutil
import subprocess
import sys
import tempfile
import types
from pathlib import Path
from contextlib import redirect_stderr, redirect_stdout
from unittest import SkipTest

_HERE = Path(__file__).resolve().parent
_PLUGIN = _HERE.parent
_REPO = _PLUGIN.parent

NATIVE_MARKETPLACE = _REPO / ".agents" / "plugins" / "marketplace.json"
LEGACY_CODEX_MARKETPLACE = _REPO / ".codex-plugin" / "marketplace.json"
CLAUDE_MARKETPLACE = _REPO / ".claude-plugin" / "marketplace.json"
PLUGIN_MANIFEST = _PLUGIN / ".codex-plugin" / "plugin.json"
MCP_CONFIG = _PLUGIN / ".mcp.json"
NODE_LAUNCHER = _HERE / "run_kumiho_mcp.mjs"
THREAD_ID_BRIDGE = _HERE / "thread_id_bridge.mjs"
THREAD_CONTEXT_SHIM = _HERE / "codex_thread_context.py"
PYTHON_LAUNCHER = _HERE / "run_kumiho_mcp.py"
ONBOARD_SCRIPT = _HERE / "onboard_kumiho.py"
INGEST_SCRIPT = _HERE / "ingest_skills.py"
VERIFY_SCRIPT = _HERE / "verify_backend.py"
CE_RUNNER = _HERE / "run_kumiho_ce.py"
CLOUD_RUNNER = _HERE / "run_kumiho_cloud.py"
LEGACY_SETUP = _HERE / "setup_codex.py"
MEMORY_SKILL = _PLUGIN / "skills" / "kumiho-memory" / "SKILL.md"
ONBOARD_SKILL = _PLUGIN / "skills" / "kumiho-onboard" / "SKILL.md"
AGENTS_PROTOCOL = _PLUGIN / "AGENTS.md"
IDENTITY_ONBOARDING = (
    _PLUGIN / "skills" / "kumiho-memory" / "references" / "onboarding.md"
)
EXPECTED_MCP_ARG = "scripts/run_kumiho_mcp.mjs"


def _load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _repository_only():
    if not CLAUDE_MARKETPLACE.exists():
        raise SkipTest("repository marketplace is not included in plugin snapshots")


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, child in value.items():
            yield from _strings(key)
            yield from _strings(child)
    elif isinstance(value, list):
        for child in value:
            yield from _strings(child)


def _run_checked(command, *, cwd, env=None, timeout=30):
    result = subprocess.run(
        command,
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )
    assert result.returncode == 0, (
        f"command failed ({result.returncode}): {command!r}\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    return result


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_native_marketplace_schema():
    _repository_only()
    assert NATIVE_MARKETPLACE.exists(), (
        "native Codex marketplace missing: .agents/plugins/marketplace.json"
    )
    assert not LEGACY_CODEX_MARKETPLACE.exists(), (
        "legacy .codex-plugin/marketplace.json masks the native Codex layout"
    )

    body = _load_json(NATIVE_MARKETPLACE)
    assert body.get("name") == "kumiho-plugins"
    assert isinstance(body.get("interface", {}).get("displayName"), str)
    assert body["interface"]["displayName"].strip()

    entries = [p for p in body.get("plugins", [])
               if p.get("name") == "kumiho-memory"]
    assert len(entries) == 1, (
        "native marketplace must contain exactly one kumiho-memory entry"
    )
    entry = entries[0]
    assert entry.get("source") == {"source": "local", "path": "./codex"}
    assert entry.get("policy") == {
        "installation": "AVAILABLE",
        "authentication": "ON_INSTALL",
    }
    assert entry.get("category") == "Productivity"
    assert "version" not in entry, (
        "native Codex marketplace entries are unversioned; version belongs "
        "in codex/.codex-plugin/plugin.json"
    )


def test_native_plugin_manifest_contract():
    body = _load_json(PLUGIN_MANIFEST)
    assert body.get("name") == "kumiho-memory"
    assert re.fullmatch(r"\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.+-]+)?",
                        str(body.get("version", "")))
    assert body.get("skills") == "./skills/"
    assert body.get("mcpServers") == "./.mcp.json"
    assert (_PLUGIN / body["skills"]).is_dir()
    assert (_PLUGIN / body["mcpServers"]).is_file()

    leftovers = [value for value in _strings(body) if "[TODO:" in value]
    assert not leftovers, f"placeholder(s) remain in plugin manifest: {leftovers}"
    prompts = body.get("interface", {}).get("defaultPrompt")
    assert isinstance(prompts, list) and 1 <= len(prompts) <= 3
    assert all(isinstance(prompt, str) and 0 < len(prompt) <= 128 for prompt in prompts)

    launcher = _load_module(PYTHON_LAUNCHER, "kumiho_codex_user_agent_test")
    assert launcher._codex_user_agent() == f"kumiho-codex/{body['version']}"


def test_native_mcp_uses_node_launcher_without_placeholders():
    body = _load_json(MCP_CONFIG)
    server = body.get("mcpServers", {}).get("kumiho-memory")
    assert isinstance(server, dict), "codex/.mcp.json has no kumiho-memory server"
    assert server.get("command") == "node", (
        "Codex MCP must enter through Node so Python discovery works on Windows"
    )
    assert server.get("args") == [EXPECTED_MCP_ARG]
    assert server.get("cwd") == ".", (
        "Codex must resolve the relative launcher from the plugin root"
    )

    environment = server.get("env")
    assert isinstance(environment, dict)
    required_env = {
        "KUMIHO_CLAUDE_HOST": "codex",
        "KUMIHO_MEMORY_DECISIONS": "1",
        "KUMIHO_AUTO_ASSESS": "1",
    }
    for key, expected in required_env.items():
        assert environment.get(key) == expected
    assert "KUMIHO_MEMORY_CODE" not in environment, (
        "deprecated KUMIHO_MEMORY_CODE emits a runtime warning; use "
        "KUMIHO_MEMORY_DECISIONS"
    )
    assert environment.get("KUMIHO_CLAUDE_PACKAGE_SPEC")

    if CLAUDE_MARKETPLACE.exists():
        claude_mcp = _load_json(_REPO / "claude" / ".mcp.json")
        claude_spec = (
            claude_mcp["mcpServers"]["kumiho-memory"]["env"]
            ["KUMIHO_CLAUDE_PACKAGE_SPEC"]
        )
        match = re.fullmatch(
            r"\$\{KUMIHO_CLAUDE_PACKAGE_SPEC:-(.+)\}", claude_spec
        )
        assert match, f"unexpected Claude package spec: {claude_spec!r}"
        assert environment["KUMIHO_CLAUDE_PACKAGE_SPEC"] == match.group(1)

    launcher = (_PLUGIN / server["args"][0]).resolve()
    assert launcher == NODE_LAUNCHER.resolve()
    assert launcher.is_file(), f"Node launcher missing: {launcher}"
    assert THREAD_ID_BRIDGE.is_file(), "Codex thread-id bridge is missing"

    forbidden = ("${", "CLAUDE_PLUGIN_ROOT", "[TODO:")
    leftovers = [value for value in _strings(body)
                 if any(marker in value for marker in forbidden)]
    assert not leftovers, (
        "Codex does not shell-expand MCP manifest placeholders: "
        f"{leftovers}"
    )


def test_node_launcher_source_contract():
    """Pin bootstrap/session behavior without starting Node or Python."""
    source = NODE_LAUNCHER.read_text(encoding="utf-8")

    ordered_markers = (
        "const override = unquote(process.env.KUMIHO_PYTHON);",
        "const sharedVenvPython = process.platform",
        'source: "~/.kumiho/venv"',
        'const defaults = process.platform === "win32"',
        'source: "PATH"',
    )
    for marker in ordered_markers:
        assert marker in source, f"launcher bootstrap contract missing {marker!r}"
    positions = [source.index(marker) for marker in ordered_markers]
    assert positions == sorted(positions), (
        "Python discovery must prefer KUMIHO_PYTHON, then the shared "
        "~/.kumiho/venv interpreter, then PATH"
    )
    for marker in (
        'join(accountHome, ".kumiho", "venv", "Scripts", "python.exe")',
        'join(accountHome, ".kumiho", "venv", "bin", "python")',
        "if (existsSync(sharedVenvPython))",
        "function trustedHome()",
        "userInfo().homedir",
        "function verifiedWindowsExecutable(path)",
        "if (!isAbsolute(path))",
        'if (!path.toLowerCase().endsWith(".exe"))',
        "if (isWindowsAppExecutionAlias(path))",
        "isWindowsAppExecutionAlias(resolved)",
        'if (candidate.preflightError)',
        'function windowsPathExecutables(names, env = process.env)',
        'realpathSync.native(path)',
        '.includes("\\\\windowsapps\\\\")',
    ):
        assert marker in source, f"shared venv discovery missing {marker!r}"

    windows_defaults = re.search(
        r'const defaults = process\.platform === "win32"'
        r'(?P<windows>.*?)\n\s*: \[\["python3"',
        source,
        flags=re.DOTALL,
    )
    assert windows_defaults, "could not identify the Windows candidate branch"
    windows_branch = windows_defaults.group("windows")
    assert 'windowsPathExecutables(["py.exe", "python3.exe", "python.exe"])' in windows_branch
    assert not re.search(
        r'\[\s*"python(?:3)?"\s*,', windows_branch
    ), "Windows automatic discovery must not execute bare python/python3 aliases"
    assert '[["py", ["-3"]]]' not in windows_branch

    probe_start = source.index("function probePython")
    probe_end = source.index("function findPython", probe_start)
    probe_source = source[probe_start:probe_end]
    assert '"-I"' in probe_source
    assert '"-S"' not in probe_source, (
        "-S prevents site.py from applying pyvenv.cfg and makes the shared "
        "venv look like base Python"
    )

    start = source.index("function mcpEnvironment")
    end_marker = "function terminateWindowsTree"
    assert end_marker in source[start:]
    environment_source = source[start:source.index(end_marker, start)]
    for marker in (
        "const codexId =",
        '(childEnv.CODEX_THREAD_ID ?? "").trim()',
        '(childEnv.CODEX_SESSION_ID ?? "").trim()',
        "if (codexId) childEnv.KUMIHO_SESSION_ID = codexId;",
        "else delete childEnv.KUMIHO_SESSION_ID;",
    ):
        assert marker in environment_source, (
            f"Codex session isolation contract missing {marker!r}"
        )
    for key in (
        "KUMIHO_AUTH_TOKEN",
        "KUMIHO_CONFIG_DIR",
        "KUMIHO_CLAUDE_HOME",
        "KUMIHO_UPSTASH_REDIS_URL",
        "KUMIHO_MEMORY_PROXY_URL",
        "KUMIHO_MCP_HOSTED",
        "KUMIHO_HOSTED_LOCAL_REDIS",
        "KUMIHO_LOCAL_REDIS_URL",
        "UPSTASH_REDIS_REST_URL",
        "UPSTASH_REDIS_REST_TOKEN",
    ):
        assert f'"{key}"' in environment_source
    assert "if (!(childEnv.KUMIHO_SESSION_ID" not in environment_source
    assert '(childEnv.KUMIHO_SESSION_ID ?? "").trim()' not in environment_source
    assert "childEnv.HOME = accountHome;" in environment_source
    assert "childEnv.USERPROFILE = accountHome;" in environment_source

    for marker in (
        'import { CodexThreadIdBridge } from "./thread_id_bridge.mjs";',
        'stdio: bridgeThreadId ? ["pipe", "pipe", "inherit"] : "inherit"',
        "process.stdin.pipe(bridge).pipe(child.stdin);",
        "child.stdout.pipe(process.stdout);",
        'startPython(SCRIPT_PATH, args, "MCP launcher", { bridgeThreadId: true })',
        "function hasWindowsPeHeader(path)",
        "header.readUInt32LE(0x3c)",
        "signature.equals(Buffer.from([0x50, 0x45, 0x00, 0x00]))",
        "not a verified native Windows PE executable",
    ):
        assert marker in source, f"per-call Codex session bridge missing {marker!r}"


def test_codex_thread_context_contract():
    """The wire carrier must become request context, never global environment."""
    source = THREAD_CONTEXT_SHIM.read_text(encoding="utf-8")
    bridge_source = THREAD_ID_BRIDGE.read_text(encoding="utf-8")
    js_carrier = re.search(
        r'^export const THREAD_CONTEXT_ARGUMENT = "([^"]+)";',
        bridge_source,
        flags=re.MULTILINE,
    )
    py_carrier = re.search(
        r'^THREAD_CONTEXT_ARGUMENT = "([^"]+)"$',
        source,
        flags=re.MULTILINE,
    )
    assert js_carrier and py_carrier
    assert js_carrier.group(1) == py_carrier.group(1), (
        "Node and Python must agree on the private thread-id wire carrier"
    )
    for marker in (
        'THREAD_CONTEXT_ARGUMENT = "__kumiho_codex_thread_id"',
        "contextvars.ContextVar(",
        "clean_arguments.pop(THREAD_CONTEXT_ARGUMENT, None)",
        "_thread_id.reset(token)",
        "memory_manager._host_session_env = host_session_for_call",
        "mcp_tools._recall_scope = recall_scope_for_call",
        'result["session_id_source"] = "codex-thread-meta"',
        'os.getenv("KUMIHO_CLAUDE_HOST") != "codex"',
    ):
        assert marker in source, f"Codex request-context shim missing {marker!r}"
    assert 'os.environ["KUMIHO_SESSION_ID"]' not in source

    for adapter in (CE_RUNNER, CLOUD_RUNNER):
        adapter_source = adapter.read_text(encoding="utf-8")
        assert "install_codex_thread_context" in adapter_source, (
            f"{adapter.name} does not install Codex request context"
        )

    canonical = (_REPO / "claude" / "scripts" / "run_kumiho_mcp.py")
    if canonical.exists():
        launcher_source = canonical.read_text(encoding="utf-8")
        publish_start = launcher_source.index("def _publish_reflex_config")
        publish_end = launcher_source.index("def _llm_base_url_is_safe", publish_start)
        assert 'os.getenv("KUMIHO_CLAUDE_HOST") == "codex"' in (
            launcher_source[publish_start:publish_end]
        ), "Codex startup can overwrite Claude's reflex hook snapshot"


def test_node_launcher_doctor():
    node = shutil.which("node")
    if node is None:
        raise SkipTest("node is not installed")
    assert NODE_LAUNCHER.exists(), f"Node launcher missing: {NODE_LAUNCHER}"

    task_env = os.environ.copy()
    task_env.pop("KUMIHO_PYTHON", None)
    task_env.pop("KUMIHO_SESSION_ID", None)
    task_env.pop("CODEX_SESSION_ID", None)
    session_sentinel = "doctor-must-not-print-this-session-id"
    task_env["CODEX_THREAD_ID"] = session_sentinel
    result = _run_checked(
        [node, str(NODE_LAUNCHER), "--doctor"],
        cwd=_PLUGIN,
        env=task_env,
        timeout=20,
    )
    output = result.stdout
    for marker in (
        "Kumiho Memory MCP doctor",
        "Node:",
        "Python launcher:",
        "MCP script:",
        "MCP script exists: yes",
        "Onboarding script exists: yes",
        "Session id bridge: MCP _meta thread id -> request context (per call)",
        "Environment fallback: CODEX_THREAD_ID",
        "Candidates:",
    ):
        assert marker in output, f"doctor output missing {marker!r}:\n{output}"
    assert "Python launcher: not found" not in output
    assert session_sentinel not in output, "doctor leaked the session id value"
    source = NODE_LAUNCHER.read_text(encoding="utf-8")
    assert "findPython({ scanAll: true })" not in source


def test_python_probe_rejects_stdout_noise():
    node = shutil.which("node")
    if node is None:
        raise SkipTest("node is not installed")
    with tempfile.TemporaryDirectory(prefix="kumiho-fake-python-") as temp:
        if os.name == "nt":
            fake = Path(temp) / "fake-python.exe"
            fake.write_text("not a Windows PE executable\r\n", encoding="utf-8")
        else:
            fake = Path(temp) / "fake-python"
            fake.write_text("#!/bin/sh\nprintf 'injected startup banner\\n'\n", encoding="utf-8")
            fake.chmod(0o755)
        task_env = os.environ.copy()
        task_env["KUMIHO_PYTHON"] = str(fake)
        result = _run_checked(
            [node, str(NODE_LAUNCHER), "--doctor"],
            cwd=_PLUGIN,
            env=task_env,
            timeout=20,
        )
        output = result.stdout
        assert f"Python launcher: {fake}" not in output
        fake_lines = [line for line in output.splitlines() if str(fake) in line]
        assert fake_lines and "unavailable" in fake_lines[0]


def test_windows_process_tree_shutdown_is_pinned():
    source = NODE_LAUNCHER.read_text(encoding="utf-8")
    for marker in (
        "terminateWindowsTree",
        '["/PID", String(pid), "/T"]',
        'args.push("/F")',
        "result.status === 0",
        'const windowsRoot = "C:\\\\Windows"',
        "if (!existsSync(taskkill)) return false",
        "env: { SystemRoot: windowsRoot, WINDIR: windowsRoot, PATH: system32 }",
        'child.kill("SIGKILL")',
        "child.killed means only that a signal was sent",
    ):
        assert marker in source, f"launcher process-tree guard missing {marker!r}"
    assert "process.env.SystemRoot" not in source
    assert "process.env.windir" not in source


def test_onboarding_skill_and_secret_contract():
    assert ONBOARD_SKILL.is_file(), "native Codex onboarding skill is missing"
    assert ONBOARD_SCRIPT.is_file(), "native Codex onboarding helper is missing"
    assert INGEST_SCRIPT.is_file(), "native Codex skill-ingestion helper is missing"
    assert VERIFY_SCRIPT.is_file(), "native Codex backend verifier is missing"
    assert CE_RUNNER.is_file(), "explicit CE runtime adapter is missing"
    assert CLOUD_RUNNER.is_file(), "explicit Codex Cloud runtime adapter is missing"
    ce_runner = CE_RUNNER.read_text(encoding="utf-8")
    assert 'token=""' in ce_runner
    assert "use_discovery=False" in ce_runner
    assert '"KUMIHO_AUTO_CONFIGURE"' in ce_runner
    for mode in ("--module", "--script", "--code"):
        assert mode in ce_runner

    cloud_runner = CLOUD_RUNNER.read_text(encoding="utf-8")
    for marker in (
        "https://control.kumiho.cloud",
        'CODEX_AUTH_DIRNAME = "codex-cloud"',
        '"discovery-cache.json"',
        "force_refresh=True",
        '"KUMIHO_AUTO_CONFIGURE"',
        '"KUMIHO_AUTH_TOKEN"',
    ):
        assert marker in cloud_runner

    onboarding = ONBOARD_SKILL.read_text(encoding="utf-8")
    assert re.search(r"(?m)^name:\s*kumiho-onboard\s*$", onboarding)
    for marker in (
        "--onboard cloud --non-interactive",
        "--onboard ce --non-interactive",
        "Never ask the user to paste",
        "must not edit Claude",
    ):
        assert marker in onboarding, f"onboarding skill is missing {marker!r}"

    memory = MEMORY_SKILL.read_text(encoding="utf-8")
    for marker in (
        "references/bootstrap.md",
        "references/onboarding.md",
        "Absolute secret exclusion",
        "This overrides",
        "every capture rule",
    ):
        assert marker in memory, f"memory skill is missing {marker!r}"
    refs = MEMORY_SKILL.parent / "references"
    assert (refs / "bootstrap.md").is_file()
    assert (refs / "onboarding.md").is_file()

    helper = ONBOARD_SCRIPT.read_text(encoding="utf-8")
    assert not re.search(
        r"add_argument\(\s*[\"']--token[\"']", helper
    ), "Codex onboarding must never accept a credential in argv"
    assert re.search(r"bounded_proc\.run\(\s*command\s*,", helper), (
        "onboarding children need a process-tree-bounded timeout"
    )
    assert 'str(CLOUD_RUNNER), "--auth-check"' in helper
    assert "def _run_interactive(" in helper, (
        "secure Cloud login must retain the user's terminal"
    )
    interactive = helper[helper.index("def _run_interactive("):]
    interactive = interactive[:interactive.index("\n\ndef ", 1)]
    assert "bounded_proc.run(" in interactive
    assert "stdout=None" in interactive and "stderr=None" in interactive

    onboarding_skill = ONBOARD_SKILL.read_text(encoding="utf-8")
    assert "without framing it as a\n   question" in onboarding_skill
    assert "ask the user to run it" not in onboarding_skill.casefold()


def test_identity_onboarding_is_automatic_and_non_blocking():
    """Keep first identity creation automatic and in the original turn."""
    documents = {
        "AGENTS protocol": AGENTS_PROTOCOL.read_text(encoding="utf-8"),
        "identity onboarding reference": IDENTITY_ONBOARDING.read_text(
            encoding="utf-8"
        ),
    }
    required_meanings = (
        "automatic",
        "infer the response language",
        "Kumiho",
        "balanced",
        "~/.kumiho/artifacts/",
        "original request",
        "same turn",
    )
    forbidden_question_instructions = (
        "AskUserQuestion",
        "ask in the user's language",
        "ask together",
        "then stop and wait",
        "stop-and-wait",
        "## Round 1",
        "## Round 2",
        "What should I call you",
        "Would you like to name me",
        "Should answers be concise",
        "Are there any specific behavior rules",
    )

    for label, body in documents.items():
        folded = body.casefold()
        for marker in required_meanings:
            assert marker.casefold() in folded, (
                f"{label} is missing automatic onboarding meaning {marker!r}"
            )
        for marker in forbidden_question_instructions:
            assert marker.casefold() not in folded, (
                f"{label} still contains interactive onboarding instruction "
                f"{marker!r}"
            )
        assert "?" not in body, (
            f"{label} contains a question; first identity onboarding must not "
            "pause the user's original request"
        )


def test_codex_backend_config_is_host_isolated():
    launcher = _load_module(PYTHON_LAUNCHER, "kumiho_codex_launcher_test")
    controlled = (
        "KUMIHO_CLAUDE_MODE",
        "KUMIHO_CLAUDE_SERVER_ENDPOINT",
        "KUMIHO_LOCAL_SERVER_ENDPOINT",
        "KUMIHO_SERVER_ENDPOINT",
        "KUMIHO_SERVER_ADDRESS",
        "KUMIHO_AUTH_TOKEN",
        "UPSTASH_REDIS_URL",
        "KUMIHO_UPSTASH_REDIS_URL",
        "KUMIHO_MEMORY_PROXY_URL",
        "KUMIHO_MCP_HOSTED",
        "KUMIHO_HOSTED_LOCAL_REDIS",
        "KUMIHO_LOCAL_REDIS_URL",
        "UPSTASH_REDIS_REST_URL",
        "UPSTASH_REDIS_REST_TOKEN",
        "KUMIHO_LLM_BASE_URL",
        "KUMIHO_CLAUDE_HOST",
        "KUMIHO_CONTROL_PLANE_URL",
        "KUMIHO_CONTROL_PLANE_API_URL",
        "KUMIHO_TENANT_HINT",
        "KUMIHO_FIREBASE_API_KEY",
        "KUMIHO_FIREBASE_ID_TOKEN",
        "KUMIHO_FIREBASE_PROJECT_ID",
        "KUMIHO_USE_CONTROL_PLANE_TOKEN",
        "KUMIHO_WORKSPACE_ROOT",
        "KUMIHO_ENV_FILE",
        "KUMIHO_CODEX_BACKEND",
        "KUMIHO_CODEX_CE_ENDPOINT",
        "KUMIHO_CODEX_CE_REDIS_URL",
        "KUMIHO_CODEX_CE_LLM_BASE_URL",
        "KUMIHO_SERVER_USE_TLS",
        "KUMIHO_SERVER_AUTHORITY",
        "KUMIHO_SSL_TARGET_OVERRIDE",
        "KUMIHO_SERVER_CA_FILE",
        "KUMIHO_REQUIRE_TLS",
    )
    original = {key: os.environ.get(key) for key in controlled}
    present = {key for key in controlled if key in os.environ}
    try:
        with tempfile.TemporaryDirectory(prefix="kumiho-codex-config-") as temp:
            path = Path(temp) / "codex.json"
            path.write_text(
                json.dumps({"schema_version": 1, "backend": "cloud"}),
                encoding="utf-8",
            )
            os.environ["KUMIHO_CLAUDE_MODE"] = "ce"
            os.environ["KUMIHO_CLAUDE_SERVER_ENDPOINT"] = "claude-only:9190"
            os.environ["KUMIHO_LOCAL_SERVER_ENDPOINT"] = "claude-only:9190"
            os.environ["UPSTASH_REDIS_URL"] = "redis://claude-only:6379"
            os.environ["KUMIHO_UPSTASH_REDIS_URL"] = "redis://alias-only:6379"
            os.environ["KUMIHO_MEMORY_PROXY_URL"] = "https://proxy.example.test"
            os.environ["KUMIHO_MCP_HOSTED"] = "1"
            os.environ["KUMIHO_HOSTED_LOCAL_REDIS"] = "1"
            os.environ["KUMIHO_LOCAL_REDIS_URL"] = "redis://local-alias:6379"
            os.environ["UPSTASH_REDIS_REST_URL"] = "https://redis-rest.example.test"
            os.environ["UPSTASH_REDIS_REST_TOKEN"] = "must-be-scrubbed"
            os.environ["KUMIHO_AUTH_TOKEN"] = "claude-custom-control-plane-token"
            os.environ["KUMIHO_LLM_BASE_URL"] = "http://claude-only:11434/v1"
            os.environ["KUMIHO_CONTROL_PLANE_URL"] = "https://untrusted.example.test"
            os.environ["KUMIHO_CONTROL_PLANE_API_URL"] = "https://untrusted.example.test"
            os.environ["KUMIHO_TENANT_HINT"] = "claude-only-tenant"
            os.environ["KUMIHO_SERVER_USE_TLS"] = "false"
            os.environ["KUMIHO_SERVER_AUTHORITY"] = "claude-only-authority"
            os.environ["KUMIHO_SSL_TARGET_OVERRIDE"] = "claude-only-target"
            os.environ["KUMIHO_SERVER_CA_FILE"] = "claude-only-ca.pem"
            os.environ["KUMIHO_REQUIRE_TLS"] = "0"
            launcher._apply_codex_config(path)
            for key in (
                "KUMIHO_CLAUDE_MODE",
                "KUMIHO_CLAUDE_SERVER_ENDPOINT",
                "KUMIHO_LOCAL_SERVER_ENDPOINT",
                "KUMIHO_SERVER_ENDPOINT",
                "KUMIHO_SERVER_ADDRESS",
                "UPSTASH_REDIS_URL",
                "KUMIHO_UPSTASH_REDIS_URL",
                "KUMIHO_MEMORY_PROXY_URL",
                "KUMIHO_MCP_HOSTED",
                "KUMIHO_HOSTED_LOCAL_REDIS",
                "KUMIHO_LOCAL_REDIS_URL",
                "UPSTASH_REDIS_REST_URL",
                "UPSTASH_REDIS_REST_TOKEN",
                "KUMIHO_LLM_BASE_URL",
                "KUMIHO_SERVER_USE_TLS",
                "KUMIHO_SERVER_AUTHORITY",
                "KUMIHO_SSL_TARGET_OVERRIDE",
                "KUMIHO_SERVER_CA_FILE",
                "KUMIHO_REQUIRE_TLS",
            ):
                assert key not in os.environ, f"Cloud did not neutralize {key}"
            assert os.environ["KUMIHO_AUTH_TOKEN"] == ""
            shared = _load_module(
                _HERE / "_vendored_launcher.py",
                "kumiho_shared_hydration_test",
            )
            diagnostic = io.StringIO()
            shared._CodexPrefixStream(diagnostic).write(
                "[kumiho-claude] shared diagnostic"
            )
            assert diagnostic.getvalue() == (
                "[kumiho-codex] shared diagnostic"
            )
            os.environ["KUMIHO_CLAUDE_HOST"] = "codex"
            shared._hydrate_env_from_dotenv = lambda: (
                shared._set_env_if_absent(
                    "KUMIHO_CLAUDE_MODE", "ce", "simulated Claude dotenv"
                ),
                shared._set_env_if_absent(
                    "KUMIHO_CLAUDE_SERVER_ENDPOINT",
                    "claude-only:9190",
                    "simulated Claude dotenv",
                ),
            )
            shared._hydrate_env_from_claude_settings = lambda: None
            shared._hydrate_env_from_plugin_mcp = lambda: None
            shared._load_bearer_token = lambda: None
            shared._hydrate_env_from_local_config()
            assert not shared._ce_mode_enabled(), (
                "shared hydration overrode Codex's explicit Cloud backend"
            )
            assert "KUMIHO_CLAUDE_SERVER_ENDPOINT" not in os.environ
            assert "KUMIHO_CONTROL_PLANE_URL" not in os.environ
            assert "KUMIHO_CONTROL_PLANE_API_URL" not in os.environ
            assert "KUMIHO_TENANT_HINT" not in os.environ

            path.write_text(
                json.dumps({
                    "schema_version": 1,
                    "backend": "ce",
                    "endpoint": "127.0.0.1:9190",
                    "redis_url": "redis://127.0.0.1:6379",
                    "llm_base_url": "http://127.0.0.1:11434/v1",
                }),
                encoding="utf-8",
            )
            os.environ["KUMIHO_AUTH_TOKEN"] = "must-be-cleared"
            launcher._apply_codex_config(path)
            shared._hydrate_env_from_dotenv = lambda: shared._set_env_if_absent(
                "KUMIHO_CLAUDE_MODE", "cloud", "simulated Claude dotenv"
            )
            shared._hydrate_env_from_local_config()
            assert os.environ["KUMIHO_CLAUDE_MODE"] == "ce"
            assert os.environ["KUMIHO_CLAUDE_SERVER_ENDPOINT"] == "127.0.0.1:9190"
            assert os.environ["KUMIHO_AUTH_TOKEN"] == ""
            assert os.environ["UPSTASH_REDIS_URL"] == "redis://127.0.0.1:6379"
            assert os.environ["KUMIHO_LLM_BASE_URL"] == "http://127.0.0.1:11434/v1"
            assert shared._ce_mode_enabled()
            shared._bootstrap_server_endpoint()
            assert os.environ["KUMIHO_SERVER_ENDPOINT"] == "127.0.0.1:9190"
            assert "KUMIHO_LOCAL_SERVER_ENDPOINT" not in os.environ

            path.write_text(
                json.dumps({
                    "schema_version": 1,
                    "backend": "ce",
                    "endpoint": "grpcs://ce.example.test:7443",
                }),
                encoding="utf-8",
            )
            os.environ["UPSTASH_REDIS_URL"] = "redis://claude-only:6379"
            os.environ["KUMIHO_LLM_BASE_URL"] = "http://claude-only:11434/v1"
            launcher._apply_codex_config(path)
            assert os.environ["KUMIHO_CLAUDE_SERVER_ENDPOINT"] == (
                "grpcs://ce.example.test:7443"
            )
            assert os.environ["UPSTASH_REDIS_URL"] == "redis://127.0.0.1:6379"
            assert "KUMIHO_LLM_BASE_URL" not in os.environ

            for invalid in (
                {"schema_version": 1, "backend": "ce"},
                {"schema_version": 1, "backend": "unknown"},
                {"schema_version": 999, "backend": "ce", "endpoint": "bad:9190"},
                {
                    "schema_version": 1,
                    "backend": "ce",
                    "endpoint": "https://user:secret@ce.example.test:443",
                },
                {
                    "schema_version": 1,
                    "backend": "ce",
                    "endpoint": "ce.example.test:9190",
                    "redis_url": "redis://user:secret@cache.example.test:6379",
                },
                {
                    "schema_version": 1,
                    "backend": "ce",
                    "endpoint": "grpc://ce.example.test:9190",
                },
                {
                    "schema_version": 1,
                    "backend": "ce",
                    "endpoint": "grpcs://ce.example.test:9190",
                    "llm_base_url": "http://llm.example.test/v1",
                },
            ):
                path.write_text(json.dumps(invalid), encoding="utf-8")
                os.environ["KUMIHO_CLAUDE_MODE"] = "ce"
                os.environ["KUMIHO_CLAUDE_SERVER_ENDPOINT"] = "claude-only:9190"
                os.environ["KUMIHO_AUTH_TOKEN"] = "must-not-survive"
                try:
                    launcher._apply_codex_config(path)
                except SystemExit as exc:
                    assert exc.code == 2
                else:
                    raise AssertionError(
                        "invalid explicit Codex config silently fell back to Cloud"
                    )
                assert os.environ["KUMIHO_AUTH_TOKEN"] == ""
                for key in (
                    "KUMIHO_CODEX_BACKEND",
                    "KUMIHO_CLAUDE_MODE",
                    "KUMIHO_CLAUDE_SERVER_ENDPOINT",
                    "KUMIHO_CONTROL_PLANE_URL",
                ):
                    assert key not in os.environ
    finally:
        for key in controlled:
            if key in present:
                os.environ[key] = original[key]
            else:
                os.environ.pop(key, None)


def test_onboarding_help_is_non_mutating_and_redacted():
    node = shutil.which("node")
    if node is None:
        raise SkipTest("node is not installed")
    with tempfile.TemporaryDirectory(prefix="kumiho-codex-onboard-") as temp:
        config_dir = Path(temp) / "config"
        task_env = os.environ.copy()
        task_env["KUMIHO_CONFIG_DIR"] = str(config_dir)
        secret = "onboarding-must-not-print-this-token"
        task_env["KUMIHO_AUTH_TOKEN"] = secret
        result = _run_checked(
            [node, str(NODE_LAUNCHER), "--onboard", "--help"],
            cwd=_PLUGIN,
            env=task_env,
            timeout=20,
        )
        output = result.stdout + result.stderr
        assert "{auto,cloud,ce}" in output
        assert "--non-interactive" in output
        assert "--token" not in output
        assert secret not in output
        assert not (config_dir / "codex.json").exists()

    helper = ONBOARD_SCRIPT.read_text(encoding="utf-8")
    assert "input(" not in helper, "onboarding must not ask backend questions"
    onboard = _load_module(ONBOARD_SCRIPT, "kumiho_codex_auto_backend_test")
    parsed = onboard._parse_args([])
    assert parsed.backend == "auto"


def test_onboarding_config_helpers_are_atomic_and_secret_free():
    onboard = _load_module(ONBOARD_SCRIPT, "kumiho_codex_onboard_test")
    scrubbed = (
        "CLAUDE_PLUGIN_DATA",
        "KUMIHO_CONTROL_PLANE_API_URL",
        "KUMIHO_LLM_BASE_URL",
        "KUMIHO_UPSTASH_REDIS_URL",
        "KUMIHO_LOCAL_REDIS_URL",
        "KUMIHO_MEMORY_PROXY_URL",
        "KUMIHO_MCP_HOSTED",
        "KUMIHO_HOSTED_LOCAL_REDIS",
        "UPSTASH_REDIS_REST_URL",
        "UPSTASH_REDIS_REST_TOKEN",
        "KUMIHO_CODEX_BACKEND",
        "KUMIHO_CODEX_CE_ENDPOINT",
        "KUMIHO_CODEX_CE_REDIS_URL",
        "KUMIHO_CODEX_CE_LLM_BASE_URL",
    )
    controlled = (*scrubbed, "KUMIHO_CONFIG_DIR", "KUMIHO_AUTH_TOKEN")
    prior_env = {key: os.environ.get(key) for key in controlled}
    present_env = {key for key in controlled if key in os.environ}
    try:
        for key in scrubbed:
            os.environ[key] = "must-not-reach-onboarding-child"
        os.environ["CLAUDE_PLUGIN_DATA"] = "C:/claude-only/plugin-data"
        assert onboard._plugin_data_dir() is None, (
            "Codex onboarding reused Claude's host-owned plugin data"
        )
        os.environ["KUMIHO_CONTROL_PLANE_API_URL"] = "https://untrusted.example.test"
        os.environ["KUMIHO_LLM_BASE_URL"] = "http://claude-only:11434/v1"
        os.environ["KUMIHO_AUTH_TOKEN"] = "explicit-codex-token"
        child_env = onboard._child_env()
        for key in scrubbed:
            assert key not in child_env, f"onboarding child inherited {key}"
        assert "KUMIHO_AUTH_TOKEN" not in child_env
        assert "KUMIHO_AUTH_TOKEN" not in onboard._child_env(
            drop_auth_token=True
        )
        with tempfile.TemporaryDirectory(prefix="kumiho-shared-runtime-") as temp:
            os.environ["KUMIHO_CONFIG_DIR"] = temp
            expected = Path(temp) / "venv"
            expected /= "Scripts/python.exe" if os.name == "nt" else "bin/python"
            assert onboard._venv_python() == expected
    finally:
        for key in controlled:
            if key in present_env:
                os.environ[key] = prior_env[key]
            else:
                os.environ.pop(key, None)
    assert onboard._normalize_endpoint("http://127.0.0.1:9190/") == (
        "http://127.0.0.1:9190"
    )
    assert onboard._normalize_endpoint("grpcs://ce.example.test:7443") == (
        "grpcs://ce.example.test:7443"
    )
    try:
        onboard._normalize_endpoint("grpc://ce.example.test:9190")
    except ValueError:
        pass
    else:
        raise AssertionError("remote plaintext CE endpoint was accepted")
    try:
        onboard._normalize_endpoint("https://user:secret@example.test:9190")
    except ValueError:
        pass
    else:
        raise AssertionError("credential-bearing CE endpoint was accepted")
    for value in (
        "https://ce.example.test:9190?api_key=secret",
        "https://ce.example.test:9190#secret",
    ):
        try:
            onboard._normalize_endpoint(value)
        except ValueError:
            pass
        else:
            raise AssertionError("secret-bearing CE endpoint suffix was accepted")
    for value, schemes, label in (
        ("https://host.test/v1?api_key=secret", {"http", "https"}, "LLM URL"),
        ("redis://host.test:6379/0#secret", {"redis", "rediss"}, "Redis URL"),
    ):
        try:
            onboard._validate_url(value, schemes=schemes, label=label)
        except ValueError:
            pass
        else:
            raise AssertionError(f"secret-bearing {label} was accepted")
    assert onboard._validate_url(
        "http://127.0.0.1:11434/v1",
        schemes={"http", "https"},
        label="LLM URL",
        require_tls_for_remote=True,
    ) == "http://127.0.0.1:11434/v1"
    try:
        onboard._validate_url(
            "http://llm.example.test/v1",
            schemes={"http", "https"},
            label="LLM URL",
            require_tls_for_remote=True,
        )
    except ValueError:
        pass
    else:
        raise AssertionError("remote plaintext LLM URL was accepted")

    with tempfile.TemporaryDirectory(prefix="kumiho-codex-write-") as temp:
        path = Path(temp) / "nested" / "codex.json"
        payload = {
            "schema_version": 1,
            "backend": "ce",
            "endpoint": "127.0.0.1:9190",
            "redis_url": "redis://127.0.0.1:6379",
        }
        written = onboard._write_config(payload, path)
        assert written == path
        assert _load_json(path) == payload
        assert not list(path.parent.glob(f".{path.name}.*.tmp"))


def test_onboarding_preserves_existing_ce_configuration():
    onboard = _load_module(ONBOARD_SCRIPT, "kumiho_codex_ce_rerun_test")
    previous_config_dir = os.environ.get("KUMIHO_CONFIG_DIR")
    try:
        with tempfile.TemporaryDirectory(prefix="kumiho-codex-ce-rerun-") as temp:
            os.environ["KUMIHO_CONFIG_DIR"] = temp
            original = {
                "schema_version": 1,
                "backend": "ce",
                "endpoint": "127.0.0.1:9292",
                "redis_url": "rediss://cache.example.test:6380/0",
                "llm_base_url": "https://llm.example.test/v1",
            }
            onboard._write_config(original)
            existing = onboard._existing_config()
            assert existing == original
            args = onboard._parse_args([])
            onboard._probe_ce = lambda endpoint: endpoint == original["endpoint"]
            payload, live = onboard._configure_ce(args, existing)
            assert live
            assert payload == original
            assert _load_json(Path(temp) / "codex.json") == original
    finally:
        if previous_config_dir is None:
            os.environ.pop("KUMIHO_CONFIG_DIR", None)
        else:
            os.environ["KUMIHO_CONFIG_DIR"] = previous_config_dir


def test_automatic_onboarding_refuses_an_invalid_existing_config():
    """A damaged explicit CE choice must never silently become Cloud."""
    onboard = _load_module(ONBOARD_SCRIPT, "kumiho_codex_invalid_auto_test")
    previous_config_dir = os.environ.get("KUMIHO_CONFIG_DIR")
    try:
        with tempfile.TemporaryDirectory(prefix="kumiho-codex-invalid-") as temp:
            os.environ["KUMIHO_CONFIG_DIR"] = temp
            config = Path(temp) / "codex.json"
            original = "{ definitely-not-json\n"
            config.write_text(original, encoding="utf-8")

            onboard._provision = lambda: Path(sys.executable)

            def unexpected_backend(*args, **kwargs):
                raise AssertionError(
                    "automatic onboarding continued after invalid config"
                )

            onboard._resolve_backend = unexpected_backend
            output = io.StringIO()
            errors = io.StringIO()
            with redirect_stdout(output), redirect_stderr(errors):
                result = onboard.main(["auto", "--non-interactive"])

            assert result == 2
            assert config.read_text(encoding="utf-8") == original
            diagnostic = output.getvalue() + errors.getvalue()
            assert "explicit `cloud` or `ce` backend" in diagnostic
            assert "silently become Cloud" in diagnostic
    finally:
        if previous_config_dir is None:
            os.environ.pop("KUMIHO_CONFIG_DIR", None)
        else:
            os.environ["KUMIHO_CONFIG_DIR"] = previous_config_dir


def test_ingestion_configures_an_explicit_backend_client():
    ingest = _load_module(INGEST_SCRIPT, "kumiho_codex_ingest_client_test")
    fake_kumiho = types.ModuleType("kumiho")
    fake_kumiho.__path__ = []
    fake_auth = types.ModuleType("kumiho.auth_cli")
    cloud_client = object()
    ce_client = object()
    calls = {}

    def ensure_token(interactive=False):
        assert interactive is False
        for key in (
            "KUMIHO_CONTROL_PLANE_API_URL",
            "KUMIHO_FIREBASE_API_KEY",
            "KUMIHO_FIREBASE_ID_TOKEN",
            "KUMIHO_FIREBASE_PROJECT_ID",
            "KUMIHO_USE_CONTROL_PLANE_TOKEN",
            "KUMIHO_WORKSPACE_ROOT",
            "KUMIHO_ENV_FILE",
        ):
            assert key not in os.environ, f"auth routing override leaked: {key}"
        return "public-token", "cache"

    fake_auth.ensure_token = ensure_token

    def cloud_factory(**kwargs):
        calls["cloud"] = kwargs
        return cloud_client

    def ce_factory(**kwargs):
        calls["ce"] = kwargs
        return ce_client

    configured = []
    fake_kumiho.client_from_discovery = cloud_factory
    fake_kumiho.connect = ce_factory
    fake_kumiho.configure_default_client = configured.append

    module_names = ("kumiho", "kumiho.auth_cli", "kumiho._token_loader")
    prior_modules = {name: sys.modules.get(name) for name in module_names}
    controlled_env = (
        "KUMIHO_CONFIG_DIR",
        "KUMIHO_CODEX_CONFIG_ROOT",
        "KUMIHO_AUTH_TOKEN",
        "KUMIHO_CLAUDE_MODE",
        "KUMIHO_CLAUDE_SERVER_ENDPOINT",
        "KUMIHO_LOCAL_SERVER_ENDPOINT",
        "KUMIHO_SERVER_ENDPOINT",
        "KUMIHO_SERVER_ADDRESS",
        "UPSTASH_REDIS_URL",
        "KUMIHO_LLM_BASE_URL",
        "KUMIHO_CONTROL_PLANE_API_URL",
        "KUMIHO_FIREBASE_API_KEY",
        "KUMIHO_FIREBASE_ID_TOKEN",
        "KUMIHO_FIREBASE_PROJECT_ID",
        "KUMIHO_USE_CONTROL_PLANE_TOKEN",
        "KUMIHO_WORKSPACE_ROOT",
        "KUMIHO_ENV_FILE",
        "KUMIHO_SERVER_USE_TLS",
        "KUMIHO_SERVER_AUTHORITY",
        "KUMIHO_SSL_TARGET_OVERRIDE",
        "KUMIHO_SERVER_CA_FILE",
        "KUMIHO_REQUIRE_TLS",
    )
    prior_env = {key: os.environ.get(key) for key in controlled_env}
    present_env = {key for key in controlled_env if key in os.environ}
    temp_config = tempfile.TemporaryDirectory(prefix="kumiho-ingest-config-")
    try:
        config_root = Path(temp_config.name)
        auth_dir = config_root / "codex-cloud"
        auth_dir.mkdir()
        (auth_dir / "kumiho_authentication.json").write_text(
            json.dumps({"api_token": "public-token"}),
            encoding="utf-8",
        )
        os.environ["KUMIHO_CONFIG_DIR"] = str(config_root)
        sys.modules["kumiho"] = fake_kumiho
        sys.modules["kumiho.auth_cli"] = fake_auth
        sys.modules.pop("kumiho._token_loader", None)
        os.environ["KUMIHO_CLAUDE_MODE"] = "ce"
        os.environ["KUMIHO_LOCAL_SERVER_ENDPOINT"] = "wrong-host:9190"
        for key in (
            "KUMIHO_CONTROL_PLANE_API_URL",
            "KUMIHO_FIREBASE_API_KEY",
            "KUMIHO_FIREBASE_ID_TOKEN",
            "KUMIHO_FIREBASE_PROJECT_ID",
            "KUMIHO_USE_CONTROL_PLANE_TOKEN",
            "KUMIHO_WORKSPACE_ROOT",
            "KUMIHO_ENV_FILE",
        ):
            os.environ[key] = "untrusted"
        os.environ["KUMIHO_SERVER_USE_TLS"] = "false"
        os.environ["KUMIHO_SERVER_AUTHORITY"] = "wrong-authority"
        os.environ["KUMIHO_SSL_TARGET_OVERRIDE"] = "wrong-target"
        os.environ["KUMIHO_SERVER_CA_FILE"] = "wrong-ca.pem"
        ingest._configure_backend("cloud", {"backend": "cloud"})
        assert calls["cloud"] == {
            "id_token": "public-token",
            "control_plane_url": "https://control.kumiho.cloud",
            "cache_path": str(auth_dir / "discovery-cache.json"),
            "force_refresh": True,
        }
        assert configured[-1] is cloud_client
        assert "KUMIHO_CLAUDE_MODE" not in os.environ
        assert "KUMIHO_LOCAL_SERVER_ENDPOINT" not in os.environ
        assert os.environ["KUMIHO_REQUIRE_TLS"] == "1"
        for key in (
            "KUMIHO_SERVER_USE_TLS",
            "KUMIHO_SERVER_AUTHORITY",
            "KUMIHO_SSL_TARGET_OVERRIDE",
            "KUMIHO_SERVER_CA_FILE",
        ):
            assert key not in os.environ

        ingest._configure_backend(
            "ce",
            {
                "backend": "ce",
                "endpoint": "grpcs://127.0.0.1:9292",
                "redis_url": "redis://127.0.0.1:6380",
            },
        )
        assert calls["ce"] == {
            "endpoint": "grpcs://127.0.0.1:9292",
            "token": "",
            "enable_auto_login": False,
            "use_discovery": False,
        }
        assert configured[-1] is ce_client
        assert os.environ["KUMIHO_CLAUDE_MODE"] == "ce"
        assert "KUMIHO_LOCAL_SERVER_ENDPOINT" not in os.environ
        assert os.environ["KUMIHO_SERVER_ENDPOINT"] == "grpcs://127.0.0.1:9292"
        assert os.environ["KUMIHO_SERVER_USE_TLS"] == "true"
        assert os.environ["KUMIHO_REQUIRE_TLS"] == "1"

        class EmptyBackend:
            project = None
            created = []

            @classmethod
            def get_project(cls, name):
                assert name == "CognitiveMemory"
                return cls.project

            @classmethod
            def create_project(cls, name, description):
                cls.created.append((name, description))
                cls.project = object()
                return cls.project

        ingest._ensure_ingest_project(EmptyBackend)
        ingest._ensure_ingest_project(EmptyBackend)
        assert len(EmptyBackend.created) == 1

        ingested_documents = []

        def ingest_file(path, **kwargs):
            ingested_documents.append((Path(path), kwargs))
            return types.SimpleNamespace(item_name=kwargs["item_name"])

        fake_skill_ingest = types.SimpleNamespace(
            DEFAULT_AGENT_COMPAT=["claude", "openclaw"],
            ingest_file=ingest_file,
        )
        ingest._enable_codex_agent_compat(fake_skill_ingest)
        ingest._enable_codex_agent_compat(fake_skill_ingest)
        assert fake_skill_ingest.DEFAULT_AGENT_COMPAT == ["codex"]
        results = ingest._ingest_documents(fake_skill_ingest, dry_run=True)
        assert len(results) == len(ingested_documents) >= 3
        names = [kwargs["item_name"] for _path, kwargs in ingested_documents]
        assert len(names) == len(set(names))
        assert all(name.startswith("codex-kumiho-memory") for name in names)
        for _path, kwargs in ingested_documents:
            assert kwargs["project"] == "CognitiveMemory"
            assert kwargs["space_name"] == "Skills"
            assert kwargs["tags"] == ["codex", "kumiho-memory"]
            assert kwargs["dry_run"] is True
    finally:
        for name, previous in prior_modules.items():
            if previous is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous
        for key in controlled_env:
            if key in present_env:
                os.environ[key] = prior_env[key]
            else:
                os.environ.pop(key, None)
        temp_config.cleanup()


def test_codex_ce_adapter_pins_scheme_tls_and_empty_token():
    adapter = _load_module(CE_RUNNER, "kumiho_codex_ce_adapter_test")
    assert adapter._validated_endpoint("127.0.0.1:9190") == "127.0.0.1:9190"
    assert adapter._validated_endpoint("grpcs://ce.example.test:7443") == (
        "grpcs://ce.example.test:7443"
    )
    try:
        adapter._validated_endpoint("grpc://ce.example.test:9190")
    except ValueError:
        pass
    else:
        raise AssertionError("CE adapter accepted a remote plaintext endpoint")
    fake_kumiho = types.ModuleType("kumiho")
    calls = {"configured": []}
    client = object()

    def connect(**kwargs):
        calls["connect"] = kwargs
        calls["tls_at_connect"] = os.environ.get("KUMIHO_SERVER_USE_TLS")
        calls["token_at_connect"] = os.environ.get("KUMIHO_AUTH_TOKEN")
        return client

    fake_kumiho.connect = connect
    fake_kumiho.configure_default_client = calls["configured"].append
    fake_kumiho.auto_configure_from_discovery = lambda: None
    prior_module = sys.modules.get("kumiho")
    prior_argv = sys.argv[:]
    controlled = (
        "KUMIHO_SERVER_ENDPOINT",
        "KUMIHO_SERVER_USE_TLS",
        "KUMIHO_AUTH_TOKEN",
        "KUMIHO_AUTO_CONFIGURE",
        "KUMIHO_CONTROL_PLANE_URL",
        "KUMIHO_CONTROL_PLANE_API_URL",
        "KUMIHO_DISCOVERY_CACHE_FILE",
        "KUMIHO_TENANT_HINT",
        "KUMIHO_FIREBASE_API_KEY",
        "KUMIHO_FIREBASE_ID_TOKEN",
        "KUMIHO_FIREBASE_PROJECT_ID",
        "KUMIHO_USE_CONTROL_PLANE_TOKEN",
        "KUMIHO_WORKSPACE_ROOT",
        "KUMIHO_ENV_FILE",
        "KUMIHO_SERVER_AUTHORITY",
        "KUMIHO_SSL_TARGET_OVERRIDE",
        "KUMIHO_SERVER_CA_FILE",
        "KUMIHO_REQUIRE_TLS",
    )
    prior_env = {key: os.environ.get(key) for key in controlled}
    present_env = {key for key in controlled if key in os.environ}
    try:
        sys.modules["kumiho"] = fake_kumiho
        sys.argv = [str(CE_RUNNER), "--code", "pass"]
        os.environ.update({
            "KUMIHO_SERVER_ENDPOINT": "grpcs://ce.example.test:7443",
            "KUMIHO_SERVER_USE_TLS": "false",
            "KUMIHO_SERVER_AUTHORITY": "stale-authority",
            "KUMIHO_SSL_TARGET_OVERRIDE": "stale-target",
            "KUMIHO_SERVER_CA_FILE": "stale-ca.pem",
            "KUMIHO_REQUIRE_TLS": "0",
            "KUMIHO_AUTH_TOKEN": "must-not-leak",
            "KUMIHO_AUTO_CONFIGURE": "1",
            "KUMIHO_CONTROL_PLANE_URL": "https://untrusted.example.test",
            "KUMIHO_CONTROL_PLANE_API_URL": "https://untrusted.example.test",
            "KUMIHO_DISCOVERY_CACHE_FILE": "untrusted-cache.json",
            "KUMIHO_TENANT_HINT": "untrusted-tenant",
            "KUMIHO_FIREBASE_API_KEY": "untrusted-api-key",
            "KUMIHO_FIREBASE_ID_TOKEN": "untrusted-id-token",
            "KUMIHO_FIREBASE_PROJECT_ID": "untrusted-project",
            "KUMIHO_USE_CONTROL_PLANE_TOKEN": "1",
            "KUMIHO_WORKSPACE_ROOT": "untrusted-workspace",
            "KUMIHO_ENV_FILE": "untrusted.env",
        })
        adapter.main()
        assert calls["connect"] == {
            "endpoint": "grpcs://ce.example.test:7443",
            "token": "",
            "enable_auto_login": False,
            "use_discovery": False,
        }
        assert calls["tls_at_connect"] == "true"
        assert calls["token_at_connect"] == ""
        assert os.environ["KUMIHO_REQUIRE_TLS"] == "1"
        for key in (
            "KUMIHO_SERVER_AUTHORITY",
            "KUMIHO_SSL_TARGET_OVERRIDE",
            "KUMIHO_SERVER_CA_FILE",
        ):
            assert key not in os.environ
        assert calls["configured"][-1] is client
        assert "KUMIHO_AUTO_CONFIGURE" not in os.environ
        for key in (
            "KUMIHO_CONTROL_PLANE_URL",
            "KUMIHO_CONTROL_PLANE_API_URL",
            "KUMIHO_DISCOVERY_CACHE_FILE",
            "KUMIHO_TENANT_HINT",
            "KUMIHO_FIREBASE_API_KEY",
            "KUMIHO_FIREBASE_ID_TOKEN",
            "KUMIHO_FIREBASE_PROJECT_ID",
            "KUMIHO_USE_CONTROL_PLANE_TOKEN",
            "KUMIHO_WORKSPACE_ROOT",
            "KUMIHO_ENV_FILE",
        ):
            assert key not in os.environ
        assert fake_kumiho.auto_configure_from_discovery() is client
    finally:
        sys.argv = prior_argv
        if prior_module is None:
            sys.modules.pop("kumiho", None)
        else:
            sys.modules["kumiho"] = prior_module
        for key in controlled:
            if key in present_env:
                os.environ[key] = prior_env[key]
            else:
                os.environ.pop(key, None)


def test_codex_cloud_adapter_pins_official_discovery_and_env_tokens():
    adapter = _load_module(CLOUD_RUNNER, "kumiho_codex_cloud_adapter_test")
    controlled = (
        "KUMIHO_CONFIG_DIR",
        "KUMIHO_CODEX_CONFIG_ROOT",
        "KUMIHO_AUTH_TOKEN",
        "KUMIHO_AUTO_CONFIGURE",
        "KUMIHO_CONTROL_PLANE_URL",
        "KUMIHO_CONTROL_PLANE_API_URL",
        "KUMIHO_DISCOVERY_CACHE_FILE",
        "KUMIHO_CLAUDE_DISCOVERY_USER_AGENT",
        "KUMIHO_TENANT_HINT",
        "KUMIHO_SERVER_ENDPOINT",
        "KUMIHO_SERVER_ADDRESS",
        "KUMIHO_CLAUDE_MODE",
        "KUMIHO_CLAUDE_SERVER_ENDPOINT",
        "KUMIHO_SERVER_USE_TLS",
        "KUMIHO_SERVER_AUTHORITY",
        "KUMIHO_SSL_TARGET_OVERRIDE",
        "KUMIHO_SERVER_CA_FILE",
        "KUMIHO_REQUIRE_TLS",
        "KUMIHO_LLM_BASE_URL",
        "KUMIHO_UPSTASH_REDIS_URL",
        "KUMIHO_MEMORY_PROXY_URL",
        "KUMIHO_MCP_HOSTED",
        "KUMIHO_HOSTED_LOCAL_REDIS",
        "KUMIHO_LOCAL_REDIS_URL",
        "UPSTASH_REDIS_REST_URL",
        "UPSTASH_REDIS_REST_TOKEN",
    )
    prior_env = {key: os.environ.get(key) for key in controlled}
    present_env = {key for key in controlled if key in os.environ}
    module_names = (
        "kumiho",
        "kumiho.auth_cli",
        "kumiho.discovery",
    )
    prior_modules = {name: sys.modules.get(name) for name in module_names}
    try:
        with tempfile.TemporaryDirectory(prefix="kumiho-cloud-adapter-") as temp:
            os.environ.update({
                "KUMIHO_CONFIG_DIR": temp,
                "KUMIHO_AUTH_TOKEN": "Bearer environment-token",
                "KUMIHO_AUTO_CONFIGURE": "1",
                "KUMIHO_CONTROL_PLANE_URL": "https://untrusted.example.test",
                "KUMIHO_CONTROL_PLANE_API_URL": "https://untrusted.example.test",
                "KUMIHO_DISCOVERY_CACHE_FILE": str(Path(temp) / "claude-cache.json"),
                "KUMIHO_CLAUDE_DISCOVERY_USER_AGENT": "kumiho-codex/0.21.0",
                "KUMIHO_TENANT_HINT": "claude-tenant",
                "KUMIHO_SERVER_ENDPOINT": "untrusted.example.test:443",
                "KUMIHO_CLAUDE_MODE": "ce",
                "KUMIHO_SERVER_USE_TLS": "false",
                "KUMIHO_SERVER_AUTHORITY": "wrong-authority",
                "KUMIHO_SSL_TARGET_OVERRIDE": "wrong-target",
                "KUMIHO_SERVER_CA_FILE": "wrong-ca.pem",
                "KUMIHO_LLM_BASE_URL": "http://127.0.0.1:9/v1",
                "KUMIHO_UPSTASH_REDIS_URL": "redis://wrong.example.test:6379",
                "KUMIHO_MEMORY_PROXY_URL": "https://wrong.example.test",
                "KUMIHO_MCP_HOSTED": "1",
                "KUMIHO_HOSTED_LOCAL_REDIS": "1",
                "KUMIHO_LOCAL_REDIS_URL": "redis://wrong.example.test:6379",
                "UPSTASH_REDIS_REST_URL": "https://wrong.example.test",
                "UPSTASH_REDIS_REST_TOKEN": "must-not-leak",
            })
            cache_path = adapter._prepare_environment()
            auth_dir = Path(temp) / "codex-cloud"
            assert cache_path == auth_dir / "discovery-cache.json"
            assert os.environ["KUMIHO_CODEX_CONFIG_ROOT"] == temp
            assert os.environ["KUMIHO_CONFIG_DIR"] == str(auth_dir)
            assert os.environ["KUMIHO_CONTROL_PLANE_URL"] == (
                "https://control.kumiho.cloud"
            )
            assert os.environ["KUMIHO_DISCOVERY_CACHE_FILE"] == str(cache_path)
            for key in (
                "KUMIHO_AUTO_CONFIGURE",
                "KUMIHO_CONTROL_PLANE_API_URL",
                "KUMIHO_TENANT_HINT",
                "KUMIHO_SERVER_ENDPOINT",
                "KUMIHO_CLAUDE_MODE",
                "KUMIHO_SERVER_USE_TLS",
                "KUMIHO_SERVER_AUTHORITY",
                "KUMIHO_SSL_TARGET_OVERRIDE",
                "KUMIHO_SERVER_CA_FILE",
                "KUMIHO_AUTH_TOKEN",
                "KUMIHO_UPSTASH_REDIS_URL",
                "KUMIHO_MEMORY_PROXY_URL",
                "KUMIHO_MCP_HOSTED",
                "KUMIHO_HOSTED_LOCAL_REDIS",
                "KUMIHO_LOCAL_REDIS_URL",
                "UPSTASH_REDIS_REST_URL",
                "UPSTASH_REDIS_REST_TOKEN",
            ):
                assert key not in os.environ
            assert os.environ["KUMIHO_REQUIRE_TLS"] == "1"
            assert os.environ["KUMIHO_LLM_BASE_URL"] == "http://127.0.0.1:9/v1"

            fake_kumiho = types.ModuleType("kumiho")
            fake_kumiho.__path__ = []
            fake_auth = types.ModuleType("kumiho.auth_cli")
            fake_discovery = types.ModuleType("kumiho.discovery")
            calls = {
                "ensure": 0,
                "ensure_value": "cached-token",
                "configured": [],
                "discovery_attempts": [],
                "reject_tokens": set(),
            }

            def ensure_token(*, interactive=False):
                calls["ensure"] += 1
                assert interactive is False
                if calls.get("ensure_error"):
                    raise RuntimeError("simulated unusable auth-cli cache")
                return calls["ensure_value"], "cache"

            fake_auth.ensure_token = ensure_token

            cloud_client = object()

            def raw_post(_url, *args, **kwargs):
                calls["http_headers"] = kwargs.get("headers")
                return object()

            fake_discovery.requests = types.SimpleNamespace(post=raw_post)
            fake_kumiho.discovery = fake_discovery

            def client_from_discovery(**kwargs):
                calls["discovery"] = kwargs
                calls["discovery_attempts"].append(dict(kwargs))
                if kwargs["id_token"] in calls["reject_tokens"]:
                    raise RuntimeError("simulated rejected credential")
                discovery = getattr(fake_kumiho, "discovery", None)
                if discovery is not None:
                    discovery.requests.post(
                        "https://control.kumiho.cloud/api/discovery/tenant",
                        headers={"User-Agent": "kumiho-python/0.13.0"},
                    )
                return cloud_client

            fake_kumiho.client_from_discovery = client_from_discovery
            fake_kumiho.connect = lambda **kwargs: calls.setdefault(
                "fallback", kwargs
            )
            fake_kumiho.configure_default_client = calls["configured"].append
            fake_kumiho.auto_configure_from_discovery = lambda: None
            sys.modules["kumiho"] = fake_kumiho
            sys.modules["kumiho.auth_cli"] = fake_auth
            sys.modules.pop("kumiho._token_loader", None)
            sys.modules["kumiho.discovery"] = fake_discovery

            auth_path = auth_dir / "kumiho_authentication.json"
            auth_path.write_text(
                json.dumps({"api_token": "isolated-api-token"}),
                encoding="utf-8",
            )
            client, authenticated = adapter._configure_client(cache_path)
            assert authenticated and client is cloud_client
            assert calls["ensure"] == 1
            assert calls["discovery"] == {
                "id_token": "cached-token",
                "control_plane_url": "https://control.kumiho.cloud",
                "cache_path": str(cache_path),
                "force_refresh": True,
            }
            assert calls["configured"][-1] is cloud_client
            assert calls["http_headers"]["User-Agent"] == "kumiho-codex/0.21.0"
            assert fake_kumiho.auto_configure_from_discovery() is cloud_client

            # A future SDK may stop exporting its private discovery module.
            # The adapter must retain official routing through the public API,
            # even if the optional User-Agent hook can no longer be installed.
            del fake_kumiho.discovery
            sys.modules.pop("kumiho.discovery", None)
            calls.pop("discovery", None)
            assert adapter._client_from_official_discovery(
                fake_kumiho, "fallback-token", cache_path
            ) is cloud_client
            assert calls["discovery"] == {
                "id_token": "fallback-token",
                "control_plane_url": "https://control.kumiho.cloud",
                "cache_path": str(cache_path),
                "force_refresh": True,
            }

            # Ambient tokens are ignored even if introduced after environment
            # preparation; only the Codex-owned credential directory is read.
            os.environ["KUMIHO_AUTH_TOKEN"] = "stale-environment-token"
            calls["discovery_attempts"].clear()
            calls["reject_tokens"] = {"stale-environment-token"}
            client, authenticated = adapter._configure_client(cache_path)
            assert authenticated and client is cloud_client
            assert calls["discovery"]["id_token"] == "cached-token"
            assert all(
                attempt["id_token"] != "stale-environment-token"
                for attempt in calls["discovery_attempts"]
            ), "ambient auth crossed the Codex Cloud credential boundary"

            # A token in the shared root may belong to Claude's custom control
            # plane and must never be offered to the official Codex endpoint.
            auth_path.unlink()
            shared_secret = "claude-custom-control-plane-token"
            (Path(temp) / "kumiho_authentication.json").write_text(
                json.dumps({"api_token": shared_secret}),
                encoding="utf-8",
            )
            calls["discovery_attempts"].clear()
            _client, authenticated = adapter._configure_client(
                cache_path,
                allow_cached_route=False,
            )
            assert not authenticated
            assert all(
                attempt["id_token"] != shared_secret
                for attempt in calls["discovery_attempts"]
            )

            # A Codex-owned dashboard cache may contain only api_token, which
            # auth_cli.ensure_token does not understand; that host-local legacy
            # form remains supported.
            auth_path.write_text(
                json.dumps({"api_token": "api-cache-token"}),
                encoding="utf-8",
            )
            calls["ensure_error"] = True
            calls["reject_tokens"] = set()
            calls["discovery_attempts"].clear()
            client, authenticated = adapter._configure_client(cache_path)
            assert authenticated and client is cloud_client
            assert calls["discovery"]["id_token"] == "api-cache-token"

            # Onboarding auth-check never calls an unverified token valid via
            # an offline discovery record.
            calls["reject_tokens"] = {
                "api-cache-token",
            }
            _client, authenticated = adapter._configure_client(
                cache_path,
                allow_cached_route=False,
            )
            assert not authenticated
            assert calls["fallback"] == {
                "endpoint": "needs-auth.kumiho.invalid:443",
                "token": "",
                "enable_auto_login": False,
                "use_discovery": False,
            }
    finally:
        for name, previous in prior_modules.items():
            if previous is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous
        for key in controlled:
            if key in present_env:
                os.environ[key] = prior_env[key]
            else:
                os.environ.pop(key, None)


def test_legacy_setup_rejects_control_character_injection():
    setup = _load_module(LEGACY_SETUP, "kumiho_codex_legacy_setup_test")
    malicious = "127.0.0.1:9190\n[mcp_servers.injected]\ncommand=evil"
    try:
        setup._checked_value(malicious)
    except ValueError:
        pass
    else:
        raise AssertionError("legacy setup accepted a multiline CLI value")

    old_home = os.environ.get("CODEX_HOME")
    try:
        os.environ["CODEX_HOME"] = ""
        assert "CODEX_HOME" not in setup._child_env()
    finally:
        if old_home is None:
            os.environ.pop("CODEX_HOME", None)
        else:
            os.environ["CODEX_HOME"] = old_home


def test_legacy_setup_delegates_toml_semantics_to_codex_cli():
    codex = shutil.which("codex")
    if codex is None:
        if os.getenv("KUMIHO_REQUIRE_CODEX_CLI") == "1":
            raise AssertionError("Codex CLI is required for the legacy setup contract")
        raise SkipTest("Codex CLI is not installed")

    shim = LEGACY_SETUP.parent / "run_kumiho_mcp.mjs"
    with tempfile.TemporaryDirectory(prefix="kumiho-legacy-setup-") as temp:
        root = Path(temp)

        # A commented example is not an existing server. Raw substring scans
        # used to treat this as a successful registration and write nothing.
        commented_home = root / "commented"
        commented_home.mkdir()
        commented_config = commented_home / "config.toml"
        commented_config.write_text(
            "# [mcp_servers.kumiho-memory]\n"
            "# command = \"node\"\n"
            "# args = [\"run_kumiho_mcp.mjs\"]\n",
            encoding="utf-8",
        )
        task_env = os.environ.copy()
        task_env.update({
            "CODEX_HOME": str(commented_home),
            "CODEX_CLI_PATH": codex,
            "NO_COLOR": "1",
        })
        _run_checked(
            [sys.executable, str(LEGACY_SETUP)],
            cwd=_REPO,
            env=task_env,
        )
        listed = json.loads(_run_checked(
            [codex, "mcp", "list", "--json"],
            cwd=_REPO,
            env=task_env,
        ).stdout)
        assert [entry["name"] for entry in listed] == ["kumiho-memory"]

        # Quoted and dotted TOML table spellings are semantically identical.
        # The setup must recognize the quoted form instead of appending a
        # duplicate table that makes Codex's entire config invalid.
        quoted_home = root / "quoted"
        quoted_home.mkdir()
        quoted_config = quoted_home / "config.toml"
        quoted_config.write_text(
            '[mcp_servers."kumiho-memory"]\n'
            'command = "node"\n'
            f"args = [{json.dumps(shim.as_posix())}]\n",
            encoding="utf-8",
        )
        quoted_env = {**task_env, "CODEX_HOME": str(quoted_home)}
        _run_checked(
            [sys.executable, str(LEGACY_SETUP)],
            cwd=_REPO,
            env=quoted_env,
        )
        quoted_list = json.loads(_run_checked(
            [codex, "mcp", "list", "--json"],
            cwd=_REPO,
            env=quoted_env,
        ).stdout)
        assert [entry["name"] for entry in quoted_list] == ["kumiho-memory"]


def test_onboarding_never_claims_success_after_failed_verification():
    onboard = _load_module(ONBOARD_SCRIPT, "kumiho_codex_completion_test")
    onboard._provision = lambda: Path(sys.executable)
    onboard._configure_ce = lambda _args, _existing=None: ({"backend": "ce"}, False)
    onboard._ingest_skills = lambda _python, _backend: True
    onboard._verify_runtime = lambda _python: True
    onboard._verify_backend = lambda _python, _backend: False
    output = io.StringIO()
    with redirect_stdout(output), redirect_stderr(output):
        result = onboard.main(["ce", "--non-interactive"])
    assert result != 0
    assert "Onboarding complete" not in output.getvalue()
    assert "Onboarding is incomplete" in output.getvalue()


def test_isolated_codex_plugin_add_and_mcp_get():
    """Exercise Codex's own marketplace resolver when its CLI is available."""
    _repository_only()
    codex = shutil.which("codex")
    if codex is None:
        if os.getenv("KUMIHO_REQUIRE_CODEX_CLI") == "1":
            raise AssertionError("Codex CLI is required for the native CI contract")
        raise SkipTest("Codex CLI is not installed")

    probe = subprocess.run(
        [codex, "plugin", "marketplace", "add", "--help"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if probe.returncode != 0:
        if os.getenv("KUMIHO_REQUIRE_CODEX_CLI") == "1":
            raise AssertionError(
                "pinned Codex CLI has no plugin marketplace command:\n"
                f"{probe.stdout}\n{probe.stderr}"
            )
        raise SkipTest("installed Codex CLI has no plugin marketplace command")

    with tempfile.TemporaryDirectory(prefix="kumiho-codex-plugin-") as temp_home:
        task_env = os.environ.copy()
        task_env["CODEX_HOME"] = temp_home
        task_env["NO_COLOR"] = "1"

        _run_checked(
            [codex, "plugin", "marketplace", "add", str(_REPO), "--json"],
            cwd=_REPO,
            env=task_env,
        )
        _run_checked(
            [codex, "plugin", "add", "kumiho-memory@kumiho-plugins", "--json"],
            cwd=_REPO,
            env=task_env,
        )
        cache_root = (
            Path(temp_home) / "plugins" / "cache" / "kumiho-plugins" /
            "kumiho-memory"
        )
        installed = [path for path in cache_root.iterdir() if path.is_dir()]
        assert len(installed) == 1, f"unexpected installed snapshots: {installed}"
        installed_root = installed[0]
        for relative in (
            ".codex-plugin/plugin.json",
            ".mcp.json",
            "skills/kumiho-onboard/SKILL.md",
            "skills/kumiho-memory/SKILL.md",
            "skills/kumiho-memory/references/bootstrap.md",
            "skills/kumiho-memory/references/onboarding.md",
            "scripts/run_kumiho_mcp.mjs",
            "scripts/run_kumiho_mcp.py",
            "scripts/_vendored_launcher.py",
            "scripts/bounded_proc.py",
            "scripts/onboard_kumiho.py",
            "scripts/ingest_skills.py",
            "scripts/verify_backend.py",
            "scripts/run_kumiho_ce.py",
            "scripts/run_kumiho_cloud.py",
            "scripts/codex_thread_context.py",
            "scripts/thread_id_bridge.mjs",
        ):
            installed_file = installed_root / relative
            source_file = _PLUGIN / relative
            assert installed_file.is_file(), (
                f"native Codex snapshot omitted {relative}"
            )
            assert installed_file.read_bytes() == source_file.read_bytes(), (
                f"native Codex snapshot changed {relative} during install"
            )
        installed_root = installed_root.resolve()
        registered = _run_checked(
            [codex, "mcp", "get", "kumiho-memory", "--json"],
            cwd=_REPO,
            env=task_env,
        ).stdout

    resolved = json.loads(registered)
    transport = resolved.get("transport", {})
    assert transport.get("type") == "stdio"
    assert transport.get("command") == "node"
    assert transport.get("args") == [EXPECTED_MCP_ARG]
    resolved_cwd = transport.get("cwd")
    assert isinstance(resolved_cwd, str) and resolved_cwd
    assert Path(resolved_cwd).is_absolute(), (
        "Codex must resolve cwd='.' to an absolute installed plugin root"
    )
    assert Path(resolved_cwd).resolve() == installed_root, (
        "Codex registered the MCP cwd outside the installed plugin snapshot: "
        f"{resolved_cwd!r} != {str(installed_root)!r}"
    )
    leftovers = [value for value in _strings(transport)
                 if "${" in value or "CLAUDE_PLUGIN_ROOT" in value]
    assert not leftovers, f"Codex resolved stale MCP placeholders: {leftovers}"


TESTS = (
    test_native_marketplace_schema,
    test_native_plugin_manifest_contract,
    test_native_mcp_uses_node_launcher_without_placeholders,
    test_node_launcher_source_contract,
    test_codex_thread_context_contract,
    test_node_launcher_doctor,
    test_python_probe_rejects_stdout_noise,
    test_windows_process_tree_shutdown_is_pinned,
    test_onboarding_skill_and_secret_contract,
    test_identity_onboarding_is_automatic_and_non_blocking,
    test_codex_backend_config_is_host_isolated,
    test_onboarding_help_is_non_mutating_and_redacted,
    test_onboarding_config_helpers_are_atomic_and_secret_free,
    test_onboarding_preserves_existing_ce_configuration,
    test_automatic_onboarding_refuses_an_invalid_existing_config,
    test_ingestion_configures_an_explicit_backend_client,
    test_codex_ce_adapter_pins_scheme_tls_and_empty_token,
    test_codex_cloud_adapter_pins_official_discovery_and_env_tokens,
    test_legacy_setup_rejects_control_character_injection,
    test_legacy_setup_delegates_toml_semantics_to_codex_cli,
    test_onboarding_never_claims_success_after_failed_verification,
    test_isolated_codex_plugin_add_and_mcp_get,
)


def main() -> int:
    failures = 0
    for test in TESTS:
        try:
            test()
            print(f"PASS: {test.__name__}")
        except SkipTest as exc:
            print(f"SKIP: {test.__name__}: {exc}")
        except (AssertionError, json.JSONDecodeError, OSError,
                subprocess.SubprocessError) as exc:
            print(f"FAIL: {test.__name__}: {exc}")
            failures += 1
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
