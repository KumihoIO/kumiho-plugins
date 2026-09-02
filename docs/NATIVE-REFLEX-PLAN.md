# FINAL BUILD PLAN — Kumiho Memory host reflex (claude plugin)

**Repo:** `H:/KumihoIO/kumiho-plugins/claude` (kumiho-memory v0.17.2)
**Target:** recall arrives without the model asking; capture stops being an adjective.
**Status of the three candidate designs:** all three had the same correct spine and all three had load-bearing legs that the judges killed in the binary. This plan keeps the spine, deletes every killed leg, and rebuilds the survivors on primitives that at least two independent judges verified.

---

## 0. Architecture decision

**Winner: Design 2's skeleton — "per-turn host injection served from a local cache, observation before enforcement, shipped as independently revertible tiers" — rebuilt on corrected primitives.**

Three sentences why: (1) Every one of the nine adversarial verdicts endorsed exactly one mechanism — a blocking `UserPromptSubmit` **command** hook emitting `hookSpecificOutput.additionalContext` — and Design 2 is the only candidate whose *architecture* survives intact, because its fatal flaws were located coding errors (`seek(-N, SEEK_END)`, `os.replace` under Windows sharing, `_spawn([sid, cwd])`, an off-by-one floor, dead env knobs) rather than dependencies on capabilities that do not exist. (2) Design 1's per-turn detached prefetch and Design 3's `mcp_tool` capture legs are both structurally dead — `mcp_tool` hooks have no `async` field at all (`McpToolHookSchema` = `type, server, tool, input, if, timeout, statusMessage, once`; the backgrounding branch lives only in the command-spawn path), and a per-turn worker costs ~6 process launches plus 2 control-plane HTTPS round trips because `_bootstrap_server_endpoint()` has no cache and `_ensure_runtime()` spawns a probe even warm. (3) Design 2's tiering is the only packaging where step 1 is a real fix on its own and every later tier reverts by deleting one JSON key — which matters more than usual in a repo with no CI, no linter, no hook tests, and no hot reload.

### Grafts, named

| Taken from | What |
|---|---|
| **D1 (Reflex Loop)** | The injection **contract**: exactly one top-level `hookSpecificOutput` key, `hookEventName` matching the event, memory block formatted entirely by the producer so the blocking hook does zero formatting; absolute `created_at` + computed age string per memory instead of the deleted TEMPORAL AWARENESS prose; `<state>/reflex.log` as the first observability either reflex has ever had; per-eviction logging as the explicit anti-pattern to the silent 50-cap. |
| **D1's judges** | Cross-turn **content-hash dedup** of the injected block and a hard per-session emission budget (L3's finding that `hook_additional_context` is pushed into `k.messages` and therefore *persists* in history, so per-turn injection accumulates); neutral `statusMessage` on every hook (L3: omitting it surfaces the raw command path via `W5`/`lSe`); `PostToolUse` matcher `mcp__.*kumiho-memory.*__kumiho_memory_(engage\|reflect)` as the observation channel instead of transcript parsing (L1). |
| **D2 (Kumiho Reflex)** | The tiered, per-step-revertible packaging; the **file-based kill switch** (`<state>/reflex.off`) rather than env-only, because hooks never see `.mcp.json` env; observation-first sequencing (measure the reflex for a week before enforcing anything); `reflex_state.py` as a stdlib-only shared module that must never import `run_kumiho_mcp.py`; the queue spill + eviction log + `count` subcommand. |
| **D2's judges** | Discriminate on `hook_event_name`, **never** on `agent_id` (it is a base field from `Kf` and a false branch silently zeroes the whole feature); emit the **resolved absolute path** of the drain command, since `CLAUDE_PLUGIN_ROOT` is empirically empty in the agent's Bash environment and SKILL.md:211's documented drain has therefore always been broken. |
| **D3 (Reflex Parity Port)** | `Stop.last_assistant_message` as the capture input instead of parsing the transcript (schema description: *"Avoids the need to read and parse the transcript file"*); the two deliberate deviations from the canonical worker prologue — never call `_ensure_runtime()` inside a session, and cache the endpoint instead of a discovery POST per invocation; the README parity-row correction; heading-slug preservation in SKILL.md. |
| **D3's judges** | `Kf` guarantees `session_id`/`transcript_path`/`cwd`/`prompt_id` on **every** event, so four of D1's and D2's "assumptions" are dead weight; `prompt_id` ("UUID correlating a user prompt with all subsequent events until the next prompt") as the exact turn key; async **command** hooks on `pL`-dispatched events are genuinely backgrounded and emit **no attachment** (`if(Te.backgrounded){yield{outcome:"success"};return}`) — that is the free capture channel. |
| **openclaw** (`src/hooks.ts`) | `buildRecallQuery` (`:310-347`) ported verbatim; `formatRecalledMemories` (`:223-282`) ported verbatim; the `(Tool-only turn: …)` placeholder; the `engageUnsupported` latch and the `deduplicated` fall-through at `:110-117` so a dedup hit never overwrites a good cache with an empty one. |

---

## 1. Hard discards — do not build these

| Discarded | Why (verified, ≥2 judges unless noted) |
|---|---|
| **Every `mcp_tool` hook** | `McpToolHookSchema` has no `async` field; `Bqs` is a bare `await` inline in both dispatchers → a Stop `mcp_tool` blocks turn-end for up to its `timeout`. Its result is validated as hook output and surfaces as `hook_success`/`hook_non_blocking_error` → user-visible every turn. It reads no env → **no kill switch**. The `server` name is a host-composed string with no schema contract. And no judge established that an `mcp_tool` return value reaches model context. |
| **`PreCompact` and `PostCompact` registrations** | `BEe`/`Hpt` push `"PreCompact [<command>] completed successfully"` **unconditionally** for every succeeded non-cancelled hook — even with empty stdout — and that string is joined into `userDisplayMessage` and displayed. `async:true` does not save you: `TL` (the dispatcher these events use) does not check `backgrounded` and maps `status===0 → succeeded:true`. Three independent judges confirmed. Registering these narrates the plumbing, full stop. |
| **Per-turn detached prefetch worker** | Each run = `_sanitize` + `_hydrate` + `_bootstrap_server_endpoint()` (live `urlopen(discovery_url, timeout=8)` POST, **no cache**, clears presets) + `_ensure_runtime()` (spawns a probe subprocess even warm; can run `venv.create` + `pip install`) + a fresh gRPC connect + a venv import measured at 0.7–2.0 s. ≈6 process launches and 2 cloud round trips per user turn. |
| **20-turn auto-`consolidate`** | `consolidate_session` treats the LLM summary as critical (`_summary_or_exc` → `raise`, else `{"success": False}`), and keyless pins the base URL to `http://127.0.0.1:9/v1`. In the default posture it is a **guaranteed failure every 20 turns**, and a `.consolidated.<n>` marker would make it never retry when a key appears. |
| **Host-executed raw-text buffering, default ON** | `add_assistant_response` → `RedisMemoryBuffer.add_message`, and that buffer is **Upstash cloud** (control-plane discovery / `KUMIHO_UPSTASH_REDIS_URL` / proxy). Default-ON means verbatim assistant text egresses on every turn while `README:75` still says "Raw transcripts stay local". Also fires `_background_assess` per turn for keyed users, whose `_auto_store_cursors` cooldown is *instance* state and therefore defeated by fresh processes. → moved to Step 8, default **OFF**. |
| **`Stop` + `{"decision":"block"}`** | Model-mediated (the exact dependency being removed), burns a turn, user-visible, and `CLAUDE_CODE_STOP_HOOK_BLOCK_CAP` (8) makes it unreliable by construction. Unanimous. |
| **`asyncRewake`** | `rewakeSummary` is user-visible; waking the model *is* narration. |
| **`type:"prompt"` / `type:"agent"` hooks** | Verified: *"Only available for tool events: PreToolUse, PostToolUse, PermissionRequest"*. |
| **`once: true`** | Removes the hook — process-wide state that is unreasonable across resume/fork/clear. Use a marker file. |
| **Transcript tail-parsing for reflex detection** | `seek(-N, SEEK_END)` raises in text mode and `OSError 22` on files < window; measured single JSONL lines up to 1.7 MB and files to 122 MB, so a 256 KB window holds 0–1 complete records. Replaced by `PostToolUse` matcher observation. |
| **`_spawn(script, [sid, cwd])`** | `code-capture-hook.py:53` does `repo_dir = args[0] if args else os.getcwd()` then passes it as `cwd=` — passing a session UUID first makes Popen raise, silently, forever. Any spawn passes a **real directory** as `args[0]`. |
| **`.mcp.json` as a config path for hooks** | Empirically `KUMIHO_MEMORY_CODE` is **empty** in the agent/tool environment despite being declared `${KUMIHO_MEMORY_CODE:-1}`. Hooks inherit the CLI process env. Hook-read knobs get hardcoded defaults + a file kill switch; `.mcp.json` + the sanitize tuple are for the MCP server and for workers that hydrate. |
| **`prompt` in argv** | Process command lines are captured by Windows 4688 / Sysmon 1 and routinely forwarded off-machine by EDR. Query text goes in a state **file**; argv carries only a directory and a session id. |
| **`suppressOutput: true` on the injection hook** | Described as "Hide stdout from transcript" — whether it also suppresses `hookSpecificOutput` parsing is unverified, and the injection hook's entire value is in its parsed stdout. Do not set it there. |

---

## 2. Empirical verification gates (Step 0)

None of these need a marketplace release. Register the probes as **user-level hooks** in `~/.claude/settings.json` pointing at absolute paths in the working tree — that is also how every later step gets tested before it is published.

```jsonc
// ~/.claude/settings.json  — TEMPORARY, delete after Step 0
{
  "hooks": {
    "UserPromptSubmit": [ { "hooks": [ {
      "type": "command",
      "command": "python \"H:/KumihoIO/kumiho-plugins/claude/scripts/_probe.py\" ups",
      "timeout": 5,
      "statusMessage": "probe"
    } ] } ],
    "Stop": [ { "hooks": [ {
      "type": "command",
      "command": "python \"H:/KumihoIO/kumiho-plugins/claude/scripts/_probe.py\" stop",
      "timeout": 15,
      "async": true,
      "statusMessage": "probe"
    } ] } ]
  }
}
```

`scripts/_probe.py` (throwaway, deleted at the end of Step 0): dumps stdin verbatim + a monotonic timestamp to `<state>/probe.jsonl`, and for `ups` prints a nonce block.

| Gate | Question | Method | Gates which step |
|---|---|---|---|
| **V1** | What does a blocking per-turn hook actually cost **on this machine**? | Two sources. (a) Extract `durationMs` from every hook attachment record in `~/.claude/projects/**/*.jsonl` — the method that produced `SessionStart:startup n=137 med=868 p95=2757 max=5247 ms`. (b) The `ups` probe's own monotonic delta over 30 real prompts. Note the host spawns command hooks **through Git Bash** (`spawn(D,[],{shell:$e})`), so the chain is host → bash → python. | **Step 6** (hard gate + human decision) |
| **V2** | Does `additionalContext` reach the model **this** turn, and does it **persist** into later turns? | `ups` probe injects `NONCE=<uuid>`. Same turn: "repeat the nonce you just received." Next turn: "what nonce did you receive two turns ago?" Persistence is the token-cost question — if it persists (expected: `k.messages.push(qa({type:"hook_additional_context",…}))`), the dedup + session budget in Step 6 are mandatory, not optional. | **Step 6** |
| **V3** | Is `async:true` on a **command** Stop hook truly non-blocking and attachment-free? | `stop` probe sleeps 8 s then touches a file. Confirm the turn ends immediately, the transcript shows no `hook_success` attachment for it, and no `running stop hooks… 1/1` text lingers. | **Step 4** |
| **V4** | Does a `PostToolUse` matcher fire on the full MCP tool name? | Probe hook with matcher `mcp__.*kumiho-memory.*__kumiho_memory_(engage\|reflect)`; call `kumiho_memory_engage` once; assert a probe line appeared. (`hooks.json:48` already proves MCP-name regex matching on `PermissionRequest`.) | **Step 4** |
| **V5** | Does `tool_memory_engage` work **keyless against this user's real backend**, and how long does it take? | In the plugin venv: `python -c "import json,sys; from kumiho_memory.mcp_tools import tool_memory_engage; print(json.dumps(tool_memory_engage({'query':'kumiho memory reflex','limit':5,'recall_mode':'summarized'})))"` with no LLM key, `PYTHONIOENCODING=utf-8`. Record RC, memory count, wall time. One judge got RC 0 with real memories in 6,448 ms — but against a **local CE** backend. Also assert `_bootstrap_server_endpoint()` did not leave `needs-auth.kumiho.invalid:443`. | **Step 5** (hard gate) |
| **V6** | Does `SubagentStart` `additionalContext` actually reach the subagent? | Nonce block via a `SubagentStart` probe; spawn a trivial Task subagent; ask it to echo the nonce. Schema union membership is verified; delivery is not. | **Step 6b** |
| **V7** | Does an interpreter-fallback command string parse under the real hook shell on **both** Windows paths? | Register a probe whose command is `python "…/_probe.py" x \|\| py -3 "…/_probe.py" x`. Confirm it runs with Git Bash present, then rename Git Bash out of PATH and confirm the PowerShell path does not parse-error. | **Step 3** (interpreter half only) |
| **V8** | Does the Windows `os.replace` retry loop hold under the real access pattern? | Two processes: one open-reads `<sid>.recall.json` in a tight loop, the other `write_json_atomic`s it 200×. Assert zero unhandled `PermissionError` (WinError 32/5 are both reproducible) and that every failed replace produced a log line. | **Step 5** |
| **V9** | Korean/UTF-8 round-trip across the subprocess boundary | Piped children report `sys.stdout.encoding == 'cp949'` on this machine and `json.dumps(ensure_ascii=False)` mojibake'd Korean. Assert a Korean query survives hook → state file → worker → cache → injected block byte-for-byte, with `PYTHONIOENCODING=utf-8`, `ensure_ascii=True` on every dump, and `encoding="utf-8"` on every open. | **Steps 4, 5, 6** |

**Exit criterion for Step 0:** a table of the nine answers written into `docs/HOOK-FACTS.md` in the plugin repo, with the extraction method for each, so the next person does not need a five-agent mapping pass.

---

## 3. PART A — Stop the bleeding (no new architecture; ship first)

These three steps fix measured, ongoing damage. They touch no hook events, add no latency, add no tokens, and each reverts with a single `git revert`.

---

### Step 1 — Queue: stop silently discarding commits

**Measured:** `%LOCALAPPDATA%\kumiho-claude\pending-code-captures.jsonl` is at **exactly 50 lines = `_MAX_QUEUE`** (`code_capture_pending.py:26`), `_write(entries[-_MAX_QUEUE:])` at `:92` drops the oldest with **no log line**, oldest surviving entry `2026-07-18`, fresh enqueues logged `2026-07-30 10:50:23 / 11:39:17 / 12:01:59`, 31 of 50 slots are one hot repo starving 12 others. Enqueue rate ≈6/day.

**Files edited**
- `H:/KumihoIO/kumiho-plugins/claude/scripts/code_capture_pending.py`

**Files created**
- `H:/KumihoIO/kumiho-plugins/claude/scripts/test_code_capture_pending_cap.py`

**Changes**
1. `_MAX_QUEUE = 50` → `200`. **Not 500** — at ~6/day, 500 is ~80 days of debt and 500 diffs for the model to read; the cap raise buys headroom, the spill buys durability.
2. Extract a pure function so the logic is testable without a git repo:
   ```python
   def _apply_cap(entries, cap=_MAX_QUEUE):
       """Return (keep, spilled). Oldest entries spill."""
       if len(entries) <= cap:
           return entries, []
       return entries[-cap:], entries[:-cap]
   ```
3. Replace `_write(entries[-_MAX_QUEUE:])` at `:92` with:
   ```python
   keep, spilled = _apply_cap(entries)
   if spilled:
       _append_overflow(spilled)      # <state>/pending-code-captures.overflow.jsonl
       for e in spilled:
           _log("queue overflow: evicted %s %s" % (e.get("commit","?")[:12],
                                                   (e.get("subject") or "")[:80]))
   _write(keep)
   ```
   `_log` appends to the **existing** `<state>/code-ingest.log` (do not invent a new log file for this).
4. New `count` subcommand:
   ```json
   {"pending": 50, "overflow": 0,
    "queue_path": "C:\\Users\\...\\pending-code-captures.jsonl",
    "drain_cmd": "python \"H:\\...\\claude\\scripts\\code_capture_pending.py\" list"}
   ```
   `drain_cmd` is built from `sys.executable` + `os.path.abspath(__file__)`. This fixes a real, separate bug: `CLAUDE_PLUGIN_ROOT` is **empirically empty** in the agent's Bash environment, so `SKILL.md:211`'s documented drain command has never worked as written.
5. All `open()` calls in this file get `encoding="utf-8"`; all `json.dumps` get `ensure_ascii=True`.

**Fixes:** silent commit discard (active); an undrainable queue; an unmeasurable backlog.

**Revert:** `git revert <sha>`. `pending-code-captures.overflow.jsonl` is append-only and additive — leaving it behind is harmless.

**Test** (`test_code_capture_pending_cap.py`, pytest-native — plain `assert`, zero-arg functions, **not** added to `conftest.py collect_ignore`, since that list is an opt-OUT and the house return-bool style therefore never actually runs):

```python
import importlib.util, json, subprocess, sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent

def _load(name):
    # underscore-named module, but load by path anyway so the test is
    # location-independent and matches the hyphen-named-script pattern
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), SCRIPTS / name)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

def test_apply_cap_spills_oldest():
    ccp = _load("code_capture_pending.py")
    entries = [{"commit": f"c{i:04d}"} for i in range(205)]
    keep, spilled = ccp._apply_cap(entries, cap=200)
    assert len(keep) == 200 and len(spilled) == 5
    assert spilled[0]["commit"] == "c0000"
    assert keep[0]["commit"] == "c0005"

def test_enqueue_logs_eviction(tmp_path, monkeypatch):
    monkeypatch.setenv("KUMIHO_CLAUDE_HOME", str(tmp_path))
    ccp = _load("code_capture_pending.py")
    # enqueue() early-returns when `git rev-parse HEAD` is empty, so a real
    # repo is required for the integration path.
    repo = tmp_path / "repo"; repo.mkdir()
    for cmd in (["git","init","-q"], ["git","config","user.email","t@t"],
                ["git","config","user.name","t"]):
        subprocess.run(cmd, cwd=repo, check=True)
    (repo / "a.txt").write_text("1", encoding="utf-8")
    subprocess.run(["git","add","-A"], cwd=repo, check=True)
    subprocess.run(["git","commit","-qm","seed"], cwd=repo, check=True)
    ccp._write([{"commit": f"c{i:04d}", "subject": "old"} for i in range(200)])
    ccp.enqueue(str(repo))
    state = Path(ccp._state_dir())
    assert (state / "pending-code-captures.overflow.jsonl").exists()
    assert "queue overflow: evicted" in (state / "code-ingest.log").read_text(encoding="utf-8")

def test_count_reports_absolute_drain_cmd(tmp_path, monkeypatch):
    monkeypatch.setenv("KUMIHO_CLAUDE_HOME", str(tmp_path))
    out = subprocess.run([sys.executable, str(SCRIPTS / "code_capture_pending.py"), "count"],
                         capture_output=True, text=True, encoding="utf-8",
                         env={**__import__("os").environ,
                              "KUMIHO_CLAUDE_HOME": str(tmp_path),
                              "PYTHONIOENCODING": "utf-8"})
    d = json.loads(out.stdout)
    assert Path(d["drain_cmd"].split('"')[1]).is_absolute()
```

Verify: `python -m pytest claude/scripts/ -q` → **13 passed → 16 passed**.

---

### Step 2 — Artifacts: make 5,362 countable; remove the SessionEnd stall risk

**Measured:** 5,362–5,388 `.md` artifacts in `~/.kumiho/artifacts/` that nothing ingests. `save-session-artifact.py:_parse_transcript` does `path.read_text()` on the **whole** transcript inside a blocking SessionEnd hook; measured transcript sizes median ~880 KB, p95 ~34 MB, max ~122 MB. Worse, `Ncn()` computes **one shared SessionEnd budget** = `max(1500ms, min(largest_declared_timeout*1000, 60000))`, so both SessionEnd hooks split the same 10 s.

**Files edited**
- `scripts/save-session-artifact.py`

**Files created**
- `scripts/test_save_session_artifact_guard.py`

**Changes**
1. Size early-out at the top of `_parse_transcript`:
   ```python
   cap = int(os.getenv("KUMIHO_ARTIFACT_MAX_BYTES", str(32 * 1024 * 1024)))
   try:
       if path.stat().st_size > cap:
           _log("skip: transcript %d bytes exceeds KUMIHO_ARTIFACT_MAX_BYTES=%d" % (path.stat().st_size, cap))
           return []
   except OSError:
       return []
   ```
2. `path.read_text(encoding="utf-8", errors="replace")` — currently unqualified, which is a cp949 mojibake vector on this machine (V9).
3. After `output_path.write_text(...)` (~`:272`), append one line to `<state>/artifact-index.jsonl`:
   `{"session_id","path","date","exchanges","transcript_path","ts"}`. This is the first time the 5,362-file store becomes countable. It is **not** an ingester.
4. Add `if __name__ == "__main__":` around the module-level entry. Currently the file (and `session-bootstrap.py`) executes `print(...)` + `sys.exit(0)` at import time, which makes `spec.loader.exec_module()` print and raise `SystemExit` — a latent trap for every future hook test.

**Fixes:** a real pre-existing stall (a 122 MB `read_text` inside a shared 10 s budget); an uncountable artifact store; a cp949 corruption path; untestability.

**Revert:** `git revert <sha>`; `artifact-index.jsonl` is additive.

**Test:** importlib-load, point `_parse_transcript` at a file grown past the cap with `os.truncate`, assert `[]` and a log line; run the real entrypoint via `subprocess` with a 3-exchange fake transcript on stdin and assert one `artifact-index.jsonl` line with `exchanges == 3`.

---

### Step 3 — Interpreter, encoding, and the line that forbids repair

**Files edited**
- `scripts/session-bootstrap.py`
- `hooks/hooks.json` (labels; interpreter fallback only if **V7** passes)
- `scripts/session_mine_worker.py` (one-line gate move)
- `README.md`

**Changes**

1. **`session-bootstrap.py` reads stdin.** It currently imports only `json, sys` and discards `session_id`, `source`, `transcript_path`, `cwd` — all four of which `Kf` guarantees on every event and which its own siblings already consume. Add the `_read_hook_input()` idiom from `save-session-artifact.py:34-42`, plus `sys.stdout.reconfigure(encoding="utf-8")`, plus the `__main__` guard. Persist `<state>/reflex/<sid>.session.json` = `{session_id, source, cwd, transcript_path, ts}`.

2. **Delete the recovery ban.** Line 24's `Do NOT invoke the kumiho-memory skill.` is what makes the diagnosed failure *unrecoverable by construction* — it forbids the only natural repair. Replace with: `You MAY consult the kumiho-memory skill if the protocol is unclear. Do not re-run bootstrap.` (One string. Nothing else in the block changes in this step — the rewrite is Step 7.)

3. **Neutral `statusMessage` on every existing hook.** With `statusMessage` absent, the progress payload's label falls back to `lSe(e)` = the **raw command string**, so a user watching hook progress sees `python "C:\Users\...\scripts\session-bootstrap.py"`. Add `"statusMessage": "kumiho"` to all four existing entries. This is the cheapest invisibility fix in the plan and the one that three designs got backwards by *omitting* the field for invisibility.

4. **AUTOMINE ordering bug (does not turn it on).** `session_mine_worker.py:73` evaluates `if not _automine_enabled(): return 0` **before** `_sanitize_placeholder_env_vars()` (`:98`) and `_hydrate_env_from_local_config()` (`:99`). So declaring `KUMIHO_MEMORY_CODE_AUTOMINE` in `.mcp.json` is inert and the flag is currently impossible to enable except as a real process env var — which is why `session-mine.log` last ran `2026-07-11`. Move the gate to after `:99`. Default stays **OFF** (constraint 3). Add the name to `.mcp.json` and the sanitize tuple in Step 5.

5. **Interpreter fallback — gated on V7 only.** All hooks use bare `python`, and `python3` does not resolve on this machine. If V7 shows `python "$X" || py -3 "$X"` parses cleanly under both the Git-Bash and the no-Git-Bash Windows shell, ship it. **If V7 fails or is inconclusive, ship nothing here** and instead document in README: "if `python` does not resolve on your PATH, override the hook commands in `~/.claude/settings.json`". Changing the command string is the higher-risk half of an otherwise zero-risk step, and it is a human decision (§8, D8).

**Fixes:** discarded hook payload; the recovery ban; raw script paths surfacing as progress labels; an unreachable AUTOMINE flag; cp949 in the bootstrap path.

**Revert:** `git revert <sha>`.

**Test** (`test_session_bootstrap.py`) — this is the **first hook test in the repository**, so the stdin-feeding pattern matters. Two patterns, used deliberately:

```python
import importlib.util, io, json, os, subprocess, sys
from pathlib import Path
SCRIPTS = Path(__file__).resolve().parent

# PATTERN A — real entrypoint via subprocess. Use this for ANY assertion about
# exit code or exact stdout, because that is the thing that breaks silently.
def _run_hook(script, payload, env_extra=None):
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", **(env_extra or {})}
    return subprocess.run(
        [sys.executable, str(SCRIPTS / script)],
        input=json.dumps(payload, ensure_ascii=True),
        capture_output=True, text=True, encoding="utf-8", env=env, timeout=30)

# PATTERN B — importlib + monkeypatched stdin. Use this for pure functions and
# for monkeypatching _spawn. Hyphen-named scripts REQUIRE spec_from_file_location.
def _load(script):
    spec = importlib.util.spec_from_file_location(script.replace("-", "_").removesuffix(".py"),
                                                 SCRIPTS / script)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)          # safe only because of the __main__ guard
    return mod

def test_bootstrap_survives_empty_stdin(tmp_path):
    r = _run_hook("session-bootstrap.py", {}, {"KUMIHO_CLAUDE_HOME": str(tmp_path)})
    assert r.returncode == 0
    assert "Traceback" not in r.stderr

def test_bootstrap_persists_session_json(tmp_path):
    sid = "11111111-2222-3333-4444-555555555555"
    r = _run_hook("session-bootstrap.py",
                  {"session_id": sid, "source": "startup",
                   "cwd": str(tmp_path), "transcript_path": str(tmp_path / "t.jsonl")},
                  {"KUMIHO_CLAUDE_HOME": str(tmp_path)})
    assert r.returncode == 0
    d = json.loads((tmp_path / "reflex" / f"{sid}.session.json").read_text(encoding="utf-8"))
    assert d["source"] == "startup"

def test_bootstrap_no_longer_bans_the_skill(tmp_path):
    r = _run_hook("session-bootstrap.py", {"session_id": "s", "source": "startup"},
                  {"KUMIHO_CLAUDE_HOME": str(tmp_path)})
    assert "Do NOT invoke the kumiho-memory skill" not in r.stdout
```

---

## 4. PART B — Architecture

### Step 4 — Observation only. Zero critical path, zero context, zero cloud, zero raw text.

Gated on **V3** and **V4**. This is the cheapest step with real value and it produces the data that decides whether Step 6's floor should exist at all. Today *no code anywhere calls, counts, observes, retries, or logs either reflex.*

**Files created**
- `scripts/reflex_state.py` — stdlib-only shared module. **Must not import `run_kumiho_mcp.py`.**
- `scripts/reflex-observe.py` — hyphen-named entrypoint for `Stop` and `PostToolUse`.
- `scripts/test_reflex_observe.py`

**`reflex_state.py` — the corrected state layer (~90 lines)**

```python
# state_dir(): standalone mirror of run_kumiho_mcp._state_dir, duplicated for the
# reason code_capture_pending.py:29-39 already documents. KUMIHO_CLAUDE_HOME ->
# LOCALAPPDATA/kumiho-claude on nt -> XDG_CACHE_HOME or ~/.cache.
# reflex_dir() = state_dir()/"reflex", mkdir(parents=True, exist_ok=True)
# off()      -> (state_dir()/"reflex.off").exists()          <- universal kill switch
# gate(name, default_true) -> code-capture-hook.py:42-45 falsy tuple idiom

def tail_lines(path, max_lines=200, max_bytes=131072):
    """Absolute-seek tail. NEVER seek(-n, SEEK_END): text mode raises
    io.UnsupportedOperation and binary mode raises OSError 22 on short files."""
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            if size > max_bytes:
                f.seek(size - max_bytes)   # absolute -> always legal
                f.readline()               # discard the partial first line
            data = f.read()
    except OSError:
        return []
    return data.decode("utf-8", "replace").splitlines()[-max_lines:]

def write_json_atomic(path, obj, attempts=4):
    """os.replace on Windows raises PermissionError (WinError 32/5) when either
    file is open in another process. Retry, then give up and LOG."""
    tmp = str(path) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=True)
    for i in range(attempts):
        try:
            os.replace(tmp, path); return True
        except PermissionError:
            time.sleep(0.05 * (i + 1))
    log("replace failed after %d attempts: %s" % (attempts, path))
    try: os.unlink(tmp)
    except OSError: pass
    return False

# append_jsonl(path, obj): single open(mode="a", encoding="utf-8") + one write of
#   json.dumps(obj, ensure_ascii=True) + "\n". NO rotation-by-rename (same
#   rename-while-open bug, and rotation would destroy the newest ledger anchor);
#   instead cap by BYTES and refuse to append past 2 MiB, logging once.
# log(msg): append to state_dir()/"reflex.log", encoding="utf-8".
# prune(max_sessions=40, max_age_days=7): delete whole per-session file sets and
#   LOG EVERY DELETION. Called only from detached workers, never from a hook.
```

Single-writer discipline, so `os.replace` only ever contends with a *reader*:

| file | sole writer | readers |
|---|---|---|
| `<sid>.session.json` | `session-bootstrap.py` | worker |
| `<sid>.turn.json` | `memory-reflex.py` (Step 6) | worker |
| `<sid>.recall.json` | `reflex_prefetch_worker.py` (Step 5) | `memory-reflex.py` |
| `<sid>.turns.jsonl` | `reflex-observe.py` (append-only) | `memory-reflex.py` (tail) |

**`reflex-observe.py`** — dispatches on `hook_event_name`, **never** on `agent_id` (`agent_id` is a base field from `Kf(e,t,r) → agent_id: r?.agentId`; branching on it can silently zero the whole feature, and subagent stops fire `SubagentStop` anyway — `let l = o ? "SubagentStop" : "Stop"`).

- `hook_event_name == "Stop"`: if `stop_hook_active` is truthy → exit 0. Take `last_assistant_message` **straight from stdin** — the schema says it exists precisely to avoid parsing the transcript. Append one ledger line:
  ```json
  {"prompt_id":"...","session_id":"...","kind":"stop","ts":1753...,
   "resp_len":1834,"resp_sha12":"9f2a1c...","tool_only":false}
  ```
  When `last_assistant_message` is missing/whitespace → `tool_only: true`, `resp_len: 0` (openclaw's empty-response guard, `hooks.ts:443-446`; `add_assistant_response` has **no** empty guard, `memory_manager.py:709-725`). **Store a sha256 prefix and a length — never the text.** That is what makes this step privacy-free.
- `hook_event_name == "PostToolUse"`: read the tool name from stdin, append `{"kind":"tool","tool":"…engage|…reflect","prompt_id":…,"ts":…}`. Zero transcript I/O; immune to the 1.7 MB-line problem.
- Prints nothing. Exit 0 on every path including bare `except BaseException`.

**Hook JSON to add** (mirror the exact key names used by the existing `PostToolUse:Bash` entry at `hooks.json:16-27`):

```json
"Stop": [
  {
    "hooks": [
      {
        "type": "command",
        "command": "python \"${CLAUDE_PLUGIN_ROOT}/scripts/reflex-observe.py\"",
        "timeout": 10,
        "async": true,
        "statusMessage": "kumiho"
      }
    ]
  }
]
```
```json
"PostToolUse": [
  { "matcher": "Bash", "hooks": [ /* EXISTING — byte-unchanged */ ] },
  {
    "matcher": "mcp__.*kumiho-memory.*__kumiho_memory_(engage|reflect)",
    "hooks": [
      {
        "type": "command",
        "command": "python \"${CLAUDE_PLUGIN_ROOT}/scripts/reflex-observe.py\"",
        "timeout": 10,
        "async": true,
        "statusMessage": "kumiho"
      }
    ]
  }
]
```

**Fixes:** reflect/engage become countable for the first time. `reflex.log` + `<sid>.turns.jsonl` are what make the next diagnosis cost minutes instead of five agents.

**Revert:** delete the `"Stop"` key and the second `PostToolUse` matcher group. Nothing else references the ledger until Step 6.

**Test:** Pattern A for both event shapes; assert `stop_hook_active: true` writes nothing; assert the ledger contains **no substring of the response text** (feed a distinctive Korean sentence and grep for it — closes V9 and the privacy assertion in one test); assert a `PostToolUse` payload with the long MCP name produces `{"kind":"tool"}`.

---

### Step 5 — Recall cache producer: debounced, single-flight, off the critical path

Gated on **V5** (hard — if keyless `engage` does not return memories against this user's real backend, stop here and Step 6 has nothing to inject) and **V8**.

**Key structural choice, and the main departure from all three designs:** the producer runs from the **async `Stop`** hook, not from `UserPromptSubmit`. The turn is over, so a 3–6 s worker gets the user's whole thinking-time window before the next prompt — the same stale-while-revalidate contract, but the revalidate happens during idle time, and the blocking hook in Step 6 spawns **nothing at all**.

**Files created**
- `scripts/reflex_prefetch_worker.py`

**Files edited**
- `scripts/reflex-observe.py` (spawn at the end of the `Stop` branch)
- `scripts/session-bootstrap.py` (warm spawn on `source in {startup, resume, clear, compact, fork}`)
- `.mcp.json`, `scripts/run_kumiho_mcp.py` (sanitize tuple)

**Spawn call — the corrected idiom.** `_spawn` uses `args[0]` as the child's `cwd`, so `args[0]` must be a **real directory**:

```python
_spawn("reflex_prefetch_worker.py", [cwd_from_payload_or_getcwd, session_id])
# sys.executable, DEVNULL x3, creationflags=0x8|0x200 on nt else start_new_session=True
```
The query text is **never** in argv — the worker reads it from `<sid>.turn.json` / `<sid>.session.json` (process command lines land in Windows 4688 / Sysmon 1 and get forwarded off-machine by EDR).

**Worker logic**

1. Canonical prologue, minus two deliberate omissions and one addition:
   - `_load_launcher()` via `importlib.util.spec_from_file_location` (`code_ingest_worker.py:34-39`), then `_sanitize_placeholder_env_vars()`, `_hydrate_env_from_local_config()`, `_validate_auth_token()` unless `_ce_mode_enabled()`, `_configure_llm_fallback()`.
   - **Omit `_ensure_runtime()`.** It can run `venv.create` + `pip install --upgrade` (`run_kumiho_mcp.py:103-107`) and spawns a probe subprocess even warm. Instead probe: `_venv_python(state_dir()/"venv")` exists **and** the marker file exists. If not → `log("skip: venv not provisioned")`, exit 0. Never provision inside a session.
   - **Omit the per-invocation `_bootstrap_server_endpoint()`.** It clears presets and does a live `urlopen(discovery_url, timeout=8)` POST every time. Instead read `<state>/reflex/endpoint.json`; on a hit newer than **900 s** set `KUMIHO_SERVER_ENDPOINT` directly; on a miss call `_bootstrap_server_endpoint()` once and persist. **Invalidate (unlink) on any transport error.** CE mode always goes through `_bootstrap_ce_endpoint`.
   - **Add the auth-sentinel bail.** With no auth token and not CE mode, `_bootstrap_server_endpoint()` does **not raise** — it sets `KUMIHO_SERVER_ENDPOINT='needs-auth.kumiho.invalid:443'` and returns 0. If the endpoint matches that sentinel → `log("skip: no auth token")`, exit 0. Without this, every turn fires a doomed gRPC call at an RFC-6761 never-resolving host, forever, on any tokenless install.
   - Do **not** copy `code_ingest_worker.py:91-103`'s keyless bail. Commit *mining* needs an LLM; `engage` does not. Keyless ≠ auth-less: this worker skips the LLM bail and keeps the auth bail.
2. **Global** single-flight lock `<state>/reflex/prefetch.lock`, stale window **300 s** — global not per-session, because `_ensure_runtime()`/the venv is global state and a second live session must not fork a concurrent install; and 300 s not 120 s because a 120 s stale window plus a slow run means turn N+1 forks a *second* worker. A would-be second writer logs `superseded` and exits; the next `Stop` retries. Latest-wins by retry, not by lock starvation.
3. **Debounce.** Skip and log when the last successful prefetch is younger than `KUMIHO_REFLEX_MIN_INTERVAL_S` (default 45) **and** the new query is ≥80 % word-overlap with the cached query. Bounds cloud cost to ~1 engage per 45 s of active conversation instead of one per turn.
4. **Query:** verbatim port of openclaw `buildRecallQuery` (`hooks.ts:310-347`) — current prompt always; prior user prompt appended when the current is ≤6 words; first 20 words of the last assistant text appended; word-level dedup on `[^\w]`-stripped lowercase with `len > 2`; 200-char cap. Plus git branch + `basename(cwd)` on the cold/SessionStart path where there is no prompt yet.
5. **The engage call.** One venv subprocess, args on **stdin** (not argv), `PYTHONIOENCODING=utf-8`, `timeout=45`:
   ```
   import json,sys
   from kumiho_memory.mcp_tools import tool_memory_engage
   a=json.load(sys.stdin)
   sys.stdout.write(json.dumps(tool_memory_engage(a), ensure_ascii=True))
   ```
   Args: `{"query": q, "limit": 5, "recall_mode": "summarized"}` — **`limit`, not `top_k`** (`tool_memory_engage` reads `limit`, `space_paths`, `memory_types`; `top_k` is silently ignored). Do **not** pass `session_id` — the word appears zero times in that function. `recall_mode="summarized"` is title+summary only (`memory_manager.py:2370`), no LLM. `graph_augmented` stays false.
6. **Formatting happens here, not in the blocking hook.** Verbatim port of `formatRecalledMemories` (`hooks.ts:223-282`), including the ≥2-segment / not-in-`{personal,users,session,work}` project heuristic. Each memory line carries `[type] title: summary`, absolute `created_at`, a computed age string, and `Kref:` — computation replacing the deleted TEMPORAL AWARENESS prose. Hard-truncate on a memory boundary at `KUMIHO_REFLEX_MAX_CHARS`.
7. Port the `engageUnsupported` latch and the `deduplicated` fall-through (`hooks.ts:110-117`) into `<sid>.state` so a server-side dedup hit never overwrites a good cache with an empty one.
8. Write `<sid>.recall.json` = `{generated_at, query, block, content_sha12, count, krefs[]}` via `write_json_atomic`. Log every attempt, skip, and failure. Call `prune()`. Return 0 on every path.

**Env vars — both places (constraint 7), with an honest note about which reach whom**

`.mcp.json` → kumiho-memory server `env` block:
```jsonc
"KUMIHO_REFLEX":                 "${KUMIHO_REFLEX:-1}",
"KUMIHO_REFLEX_PREFETCH":        "${KUMIHO_REFLEX_PREFETCH:-1}",
"KUMIHO_REFLEX_LIMIT":           "${KUMIHO_REFLEX_LIMIT:-5}",
"KUMIHO_REFLEX_MIN_INTERVAL_S":  "${KUMIHO_REFLEX_MIN_INTERVAL_S:-45}",
"KUMIHO_REFLEX_MAX_CHARS":       "${KUMIHO_REFLEX_MAX_CHARS:-1600}",
"KUMIHO_REFLEX_TTL_S":           "${KUMIHO_REFLEX_TTL_S:-900}",
"KUMIHO_REFLEX_FLOOR":           "${KUMIHO_REFLEX_FLOOR:-3}",
"KUMIHO_REFLEX_SESSION_BUDGET_CHARS": "${KUMIHO_REFLEX_SESSION_BUDGET_CHARS:-6000}",
"KUMIHO_REFLEX_BUFFER":          "${KUMIHO_REFLEX_BUFFER:-0}",
"KUMIHO_ARTIFACT_MAX_BYTES":     "${KUMIHO_ARTIFACT_MAX_BYTES:-33554432}",
"KUMIHO_MEMORY_CODE_AUTOMINE":   "${KUMIHO_MEMORY_CODE_AUTOMINE:-}"
```
`run_kumiho_mcp.py:_sanitize_placeholder_env_vars()` (~`:885-896`, currently ending at `KUMIHO_MEMORY_CODE`): add **all eleven** names. Sanitize runs before hydrate and the workers call it first, so this is what makes the declared defaults real on the Desktop path, where `${VAR:-default}` arrives literally.

**Do not repeat the designs' mistake:** `.mcp.json` env reaches the **MCP server process and the hydrating workers only**. Hooks inherit the CLI process env — empirically `KUMIHO_MEMORY_CODE` is *empty* in the agent environment despite being declared. Therefore:

| knob | reached by |
|---|---|
| `KUMIHO_REFLEX_PREFETCH`, `_LIMIT`, `_MIN_INTERVAL_S`, `_BUFFER`, `AUTOMINE` | worker (post-hydrate) → `.mcp.json` + `.env.local` + process env **all work** |
| `KUMIHO_REFLEX`, `_TTL_S`, `_MAX_CHARS`, `_FLOOR`, `_SESSION_BUDGET_CHARS`, `KUMIHO_ARTIFACT_MAX_BYTES` | stdlib hooks → **only** real process env (`~/.claude/settings.json` `env`, or the shell). Hardcode the default in `os.getenv(name, default)` and document `~/.claude/settings.json` as the tuning path. |
| **universal off** | `touch <state-dir>/reflex.off` — checked first by every hook, works on every platform including Desktop |

**Fixes:** the first host-side, keyless recall path that does not cost a cloud round trip per turn.

**Revert:** `KUMIHO_REFLEX_PREFETCH=0`, or delete the `_spawn` call in `reflex-observe.py`. Step 6 then injects nothing and is a ~2-file-read no-op.

**Test:** Pattern B — monkeypatch the venv subprocess call to return a canned engage payload; assert `<sid>.recall.json` written with a stable `content_sha12`; assert the lock blocks a second run and logs `superseded`; assert the debounce skip logs; assert the `needs-auth.kumiho.invalid` sentinel produces `skip: no auth token` and **zero** subprocess calls; assert a Korean query survives to `block` byte-identically; assert `write_json_atomic` returns `False` + logs (not raises) when `os.replace` is forced to fail.

---

### Step 6 — Recall injection: the one blocking hook

**Gated on V1 (latency), V2 (same-turn arrival + persistence), and a human go/no-go (§8, D1).**

**Files created**
- `scripts/memory-reflex.py`
- `scripts/test_memory_reflex.py`

**Critical path, exactly:** interpreter start → `json.loads(stdin)` → 1 read of `<sid>.recall.json` → 1 read of `<sid>.turn.json` → 1 byte-capped tail of `<sid>.turns.jsonl` → 1 `write_json_atomic` of `<sid>.turn.json` → 1 `print`. **No network. No venv. No launcher import. No `Popen`.** Print happens **before** any other side effect — one design put a broken spawn before the print inside a single blanket `except`, which silently nulled the entire feature on every turn.

**Logic**

1. `if reflex_state.off() or not gate("KUMIHO_REFLEX", True): sys.exit(0)`.
2. `_read_hook_input()`. Dispatch on `hook_event_name`.
3. Read `<sid>.recall.json`. Serve when `now - generated_at < KUMIHO_REFLEX_TTL_S` (900).
4. **Cross-turn dedup — mandatory, not an optimisation.** `additionalContext` is pushed as a `hook_additional_context` attachment into `k.messages` and therefore **persists** in history and is re-sent on every later request. So emit the memory block **only when `content_sha12` differs from the last injected hash**, tracked in `<sid>.turn.json`. Also enforce `KUMIHO_REFLEX_SESSION_BUDGET_CHARS` (default 6000) as a hard cumulative ceiling per session; past it, emit nothing but the floor line. Without both, a 40-turn session accrues ~16 k tokens of near-duplicate blocks and *accelerates* the compaction it exists to survive.
5. **No cold-start poll loop.** D1/D2 both spent 1200–1500 ms polling for a file that a debounced producer may legitimately never write. Turn 1 is warmed by the SessionStart spawn; if the cache is absent, emit nothing. Cheaper and honest.
6. **The floor line** — a counted fact, not an adjective, and corrected for D2's off-by-one (which made it fire on literally every turn at the shipped default):
   - Compute `n_since` from the ledger **before** appending anything for the current turn.
   - Emit only when `n_since >= KUMIHO_REFLEX_FLOOR` (default **3**, not 1) **and** at most once per 5 turns.
   - Text: `Turns since your last kumiho_memory_reflect: 4. session_id=<uuid>. If a decision, preference, fact or correction landed, call kumiho_memory_reflect with typed captures and pass these Kref values as source_krefs. Otherwise ignore this line.`
   - Never `decision`/`reason`; never any narration.
7. **Pending-queue line**, from Step 1's `count`, emitted only when `pending >= 10` and at most once per 20 turns, carrying the **resolved absolute** `drain_cmd`.
8. Emit exactly one JSON object whose **only** top-level key is `hookSpecificOutput` (unrecognised top-level keys are stripped with a `"Did you mean hookSpecificOutput.additionalContext (with a hookEventName)?"` warning). Bare `except BaseException` → print nothing, exit 0.

**Hook JSON**

```json
"UserPromptSubmit": [
  {
    "hooks": [
      {
        "type": "command",
        "command": "python \"${CLAUDE_PLUGIN_ROOT}/scripts/memory-reflex.py\"",
        "timeout": 5,
        "statusMessage": "kumiho"
      }
    ]
  }
]
```
No `async` (the injection must be awaited). No `suppressOutput` (unverified whether it also suppresses `hookSpecificOutput` parsing). No matcher.

**Step 6b — `SubagentStart`, gated on V6.** Subagents inherit all 66 memory tools and zero protocol. One ~400-char card: the two reflex names, "recall is host-injected in the parent, not here", the reflect duty, "do not narrate", and the parent `session_id`. Same entrypoint, `--subagent`, dispatching on `hook_event_name`.

```json
"SubagentStart": [
  {
    "hooks": [
      {
        "type": "command",
        "command": "python \"${CLAUDE_PLUGIN_ROOT}/scripts/memory-reflex.py\" --subagent",
        "timeout": 5,
        "statusMessage": "kumiho"
      }
    ]
  }
]
```

**Revert:** delete the `"UserPromptSubmit"` / `"SubagentStart"` keys, or — instantly, with no reinstall — `touch <state-dir>/reflex.off`.

**Test** (`test_memory_reflex.py`, the file that pins the fragile parts):

```python
def test_envelope_is_exact(tmp_path):
    """The single most important test in the plan: a drifted envelope is
    SILENTLY IGNORED WITH A WARNING, so pin it."""
    sid = "aaaa"; seed_cache(tmp_path, sid, block="<kumiho_memory>MARK-7</kumiho_memory>")
    r = _run_hook("memory-reflex.py",
                  {"hook_event_name": "UserPromptSubmit", "session_id": sid,
                   "prompt": "why did we pick nano_banana_2?", "cwd": str(tmp_path),
                   "prompt_id": "p1", "transcript_path": str(tmp_path / "t.jsonl")},
                  {"KUMIHO_CLAUDE_HOME": str(tmp_path)})
    assert r.returncode == 0
    d = json.loads(r.stdout)
    assert set(d.keys()) == {"hookSpecificOutput"}
    assert d["hookSpecificOutput"]["hookEventName"] == "UserPromptSubmit"
    assert "MARK-7" in d["hookSpecificOutput"]["additionalContext"]
```

Plus: empty stdin → exit 0, **empty** stdout, no traceback; malformed JSON → same; missing cache → exit 0 in well under 1 s (assert wall time, to pin the no-poll decision); **second identical turn emits no memory block** (the dedup regression test); budget exhausted → floor only; `reflex.off` → empty stdout; `n_since == 2` with `FLOOR=3` → no floor line (the off-by-one regression test); `hook_event_name == "SubagentStart"` → `hookEventName: "SubagentStart"` and **no** parent memory block; Korean memory text survives to stdout byte-identically.

---

### Step 7 — Text surfaces: bootstrap, SKILL.md, README

**Files edited:** `scripts/session-bootstrap.py`, `skills/kumiho-memory/SKILL.md`, `README.md`.

**`session-bootstrap.py`** — shrink `CONTEXT` from 3,117 chars to ~1,900.
- **Delete** (host now does it, or it actively harms): the `TWO REFLEXES` / `ENGAGE` / `REFLECT` bullet stack (`:30-40`); `Call kumiho_memory_engage ONCE with a broad query` (`:56`); `Invoke the kumiho-memory:kumiho-memory skill` (`:55`); the recovery ban (already gone in Step 3); the TEMPORAL AWARENESS prose (`:63-70`) — the producer now writes absolute `created_at` + a computed age per memory, i.e. code instead of judgement.
- **Replace** the ceiling with a fact: `Relevant memories are injected by the host as <kumiho_memory> when they change. Treat them as recalled context. Call kumiho_memory_engage only to go deeper than the injected block.`
- **Keep verbatim:** the explicit-remember rule (`:41-44`), no-narration (`:45`), no-repeat / no-self-play (`:46-51`), Skill Discovery (`:77-83`).
- **Keep `STORE COMPACT SUMMARIES` (`:72-75`)** — see §7; the host is *not* capturing the compact summary in this plan, so deleting the only rule that asks for it would be a straight regression. Add a copy in SKILL.md so it survives compaction.

**`SKILL.md`** — body text only. Every `#`/`##`/`###` heading stays **byte-identical**: heading slugs are graph identity, `ingest-skills.py:56` ingests only `not s.inline`, and the non-inline (graph-identity) headings are exactly `## Memory Discipline` (`:177`), `## Session End` (`:187`), `## Tools Quick Reference` (`:195`).
- `## Hard Constraints` rule 1 (`:15`): "One engage per turn — AT MOST one call" → "Recall is host-injected each time it changes. Call engage only to go deeper."
- `## Two Reflexes` (`:38`): recall documented as host-executed with the state-file paths; the model's residual duty is **typed captures + `source_krefs` from the injected Krefs** and honouring explicit "remember this".
- `### Engage` (`:42`): **delete the licence to skip** (`Skip when the answer is already visible`, `:52`) — the host does not skip, so the model's remaining job is escalation only.
- `## Skill Discovery Protocol` (`:116`): delete "Skill discovery consumes your one engage-per-turn" (`:152`) — no longer true.
- Add the compact-summary rule (copy of bootstrap `:72-75`) so it survives compaction even with no hook.
- Fix `:211`'s drain instruction to use Step 1's `count` output rather than `$CLAUDE_PLUGIN_ROOT`, which is empirically empty in the agent's Bash environment.

**`README.md`** — `:66`'s "host-side automation still differs by platform" is falsified by the shipped hook surface; the honest replacement is that the remaining difference is **transport** (in-process hook vs. hook subprocess + debounced worker), not behaviour. Rows `:73-74`:
- Recall: `Host-injected on UserPromptSubmit (debounced stale-while-revalidate; emitted on change)` | `Automatic before_prompt_build hook`.
- Capture: `Host-observed on Stop; the write itself is agent-side and keyless` | `Automatic agent_end buffering`.
- Keep the genuine asymmetries visible: idle-timer consolidation (no generic Claude Code event) and — until §8 D5 is decided — host-executed compact-summary capture.
- Add a `Reflex` section: env vars and which config path actually reaches them, `reflex.off`, the state files, and the queue-overflow file.
- **Do not touch `:75` "Privacy model | Raw transcripts stay local"** — it stays true because Step 8 ships OFF.

**Revert:** `git revert <sha>`. No heading renames means no graph orphaning either way.

---

### Step 8 — OPT-IN, DEFAULT OFF: host-side turn buffering

Only build this if the human says yes (§8, D3). `KUMIHO_REFLEX_BUFFER=0` by default.

When 1, the async `Stop` worker: writes the **user** turn first via `kumiho_chat_add({session_id, role:"user", message})` then the assistant turn via `kumiho_memory_reflect({session_id, response, captures: []})`. The ordering is not cosmetic — openclaw calls `recordUserTurn` inside `before_prompt_build` with the explicit comment that *"without this call every turn after the first would silently drop the user message from the session buffer"*, and all three designs omitted it, leaving an assistant-only monologue that any later consolidation would summarise as half a conversation. Apply the empty-response guard (skip tool-only turns, or substitute openclaw's `(Tool-only turn: …)` placeholder). Log `created_bucket` from `add_message` on every write — the SDK added that flag specifically to make drifted-session-id non-silent, and ignoring it would re-hide issue #3. **No `consolidate` call unless an LLM key is present**, because `consolidate_session` raises/`{"success": False}` without one and keyless pins the base URL to a dead port.

What must be stated in README **if and only if** this ever defaults ON: up to `KUMIHO_REFLEX_RESPONSE_CHARS` of verbatim assistant text egresses to Upstash cloud Redis on every turn (1 h TTL), and for keyed users each write fires `_background_assess` — one LLM call per turn, because the `_auto_store_cursors` cooldown is instance state and a fresh worker process always sees `last_cursor == 0`.

---

## 5. Resident-token delta — honest accounting

**Today:**
- `session-bootstrap.py` injects 3,117 chars ≈ **779 tok per SessionStart event** — and SessionStart matches `startup|resume|clear|compact|fork`, with 52 `SessionStart:compact` firings measured across 200 transcripts and 13 injections inside a single 14,301-record session. So today's real cost is 779 × (bootstraps in the session), not 779 once. The diagnosis undercounted this.
- `SKILL.md` = 13,198 chars ≈ 3,300 tok, all-or-nothing when the skill loads. **Unchanged by this plan** (heading slugs are identity; body edits are roughly length-neutral).

**After the plan:**

| item | tok | when |
|---|---|---|
| bootstrap block (3,117 → ~1,900 chars) | ~475 | per SessionStart event (−300 vs today) |
| memory block, `KUMIHO_REFLEX_MAX_CHARS=1600` | ≤400 | **only when the memory set changes** (content-hash dedup) |
| session ceiling `KUMIHO_REFLEX_SESSION_BUDGET_CHARS=6000` | **≤1,500** | hard cumulative cap on all memory blocks per session |
| floor line | ~35 | ≥3 turns since reflect, ≤1 per 5 turns |
| pending-queue line | ~40 | pending ≥10, ≤1 per 20 turns |
| SubagentStart card | ~110 | per subagent spawn |

Worked example — 40-turn session, 2 bootstraps, 5 memory-set changes, 4 floor emissions:

- **Today:** 779 × 2 = **1,558 tok**
- **Plan:** 475 × 2 + 400 × 5 + 35 × 4 = 950 + 2,000 + 140 = **3,090 tok**

**It roughly doubles. Say this out loud: +~1,530 tok on a busy 40-turn session, capped at ~+2,000 by the session budget.** Two mitigating facts and one uncomfortable one:
- The *instruction* share drops ~40 % (779 → 475 per bootstrap). What grows is content the model can actually use.
- Because `additionalContext` is pushed into `k.messages` and **persists**, every emission is permanently resident for the rest of the session — which is exactly why the dedup and the hard session budget are non-negotiable rather than nice-to-have. Without them the same top-5 memories re-inject every turn and a 40-turn session accrues ~16 k tokens of duplicates, triggering *earlier* compaction.
- `KUMIHO_REFLEX_SESSION_BUDGET_CHARS=0` reduces the plan to Steps 1–5 + the floor line, i.e. **net −300 tok/bootstrap and no growth at all**, if the token cost turns out to be unacceptable. That is a supported configuration, not a degraded one.

---

## 6. Composition with `feat/session-id-defaults`

**Current state:** `H:/KumihoIO/kumiho-memory` is at `04d3c42` with a **clean** working tree — the concurrent work has landed, so the plan's baseline of "uncommitted changes on `ddcdd22`" is stale. Server-side `resolve_session_id` tiers: (1) host-env `KUMIHO_SESSION_ID` / `CLAUDE_CODE_SESSION_ID`, (2) Redis active-session pointer (24 h TTL), (3) generated. Tools report `session_id` + `session_id_source`. Guidance: session_id is "OPTIONAL and best omitted."

**What the plugin passes, and why hooks are the best source**

| call | pass session_id? | why |
|---|---|---|
| `tool_memory_engage` (Step 5 worker) | **no** | the word `session_id` appears **zero** times in that function; passing it is a no-op that creates a false impression of session-scoped recall |
| `kumiho_memory_reflect` / `kumiho_chat_add` / `kumiho_memory_consolidate` (Step 8, opt-in) | **yes, explicitly from hook stdin** | `Kf` guarantees `session_id` on every event on every host path, **including Claude Desktop**, where the SDK's own docstring records `KUMIHO_SESSION_ID`/`CLAUDE_CODE_SESSION_ID` as measured **absent**. Also, the MCP server's env is fixed at *server* launch, so tier 1 goes stale across `/clear`, resume and fork while the hook payload never does. And the installed `reflect` still does `session_id = args["session_id"]` (required) — passing it works against both old and new SDK. |

**The real conflict, and it is not theoretical.** `_resolve_session` short-circuits on an explicit argument (`if explicit: return explicit, "argument"`) and **never registers the active-session pointer**; the host-env tier likewise "never touches the active-session pointer" (`memory_manager.py:3546`). So on Desktop: the plugin's hook-supplied writes land under the true CLI uuid via the `argument` tier, while the model's now-recommended id-omitting `reflect(captures)` falls through tier 1 (absent) → tier 2 (pointer never written by anyone) → tier 3 **generated**. Two buckets per session, silently — the exact fragmentation issue #3 exists to kill, re-entered from the other side. `consolidate({session_id})` would then consolidate half a conversation.

**SDK asks (in `kumiho-memory`, small and self-contained)**

1. **Publish the pointer on the `argument` tier.** When a caller supplies an explicit `session_id`, treat it as authoritative and *also* register/refresh the Redis active-session pointer. One host-side write per session then makes every subsequent id-omitting model call converge, on every platform including Desktop. This is the single change that makes hook-supplied identity the system's source of truth, and it is what turns the plugin's biggest structural advantage into an actual one.
2. **Keep the `argument` tier.** Do not deprecate or error on an explicit `session_id`. This plan depends on it, and it is the only identity channel that is correct on Desktop.
3. **Surface `created_bucket` in the tool result** for `reflect` / `chat_add`, not just internally. The flag exists to make drift non-silent; a host worker cannot log what it cannot see.
4. **Document that `engage` ignores `session_id`** (or make it honour it). Right now every caller who passes it believes something false.
5. **Pin `KUMIHO_CLAUDE_PACKAGE_SPEC`** more tightly than `kumiho-memory[all]>=0.17.3`. Step 5 imports `kumiho_memory.mcp_tools.tool_memory_engage` — an open lower bound across repos means a rename silently stops prefetch (it degrades to today's behaviour, and the worker logs the `ImportError`, but a floor beats a log line).

**Conflict risk summary:** low and one-directional. This plan makes **zero** cross-repo edits and lands on a pre-existing tier. The risk is that the reflex loop will not exercise or validate the *new* host-env tier at all, so a regression there stays invisible to these hooks — worth one deliberate Desktop test after ask 1 lands.

---

## 7. Not fixed, and what needs a release

**Explicitly NOT fixed**

| | |
|---|---|
| **The 5,362-artifact backlog** | Step 2 makes it countable and indexed. Turning transcripts into memories is a mining pass that needs an LLM (constraint 3), and `backfill_inventory.py:287-293` already mines `~/.claude/projects/*/*.jsonl` — the canonical source most artifacts were *derived from*. Belongs to `/kumiho-backfill`. The claim that ~1,000 artifacts are the only surviving copy of their session is **inferred from counts, not verified session-by-session**. |
| **Typed keyless graph captures** | `reflect(captures=[])` writes only the Redis buffer, not the graph. Typed decision/preference/fact/correction nodes still require either the model's `captures` or an LLM key. **No keyless typed-capture parity with openclaw is claimed.** |
| **Keyless consolidation** | `consolidate_session` hard-requires an LLM. Unfixable in-plugin. |
| **Host-executed compact-summary capture** | `PostCompact` carries `compact_summary` and would be the single best keyless graph write in the whole design — but registering the hook narrates `PostCompact [python …] completed successfully` to the user on every compaction, verified three times, with `async` providing no escape. The bootstrap rule stays, a copy goes in SKILL.md, and the host does not do it. See §8 D5. |
| **The ~0.5–0.9 s per-turn hook cost** | Irreducible in the process-per-hook model (host → Git Bash → python). Only a resident daemon removes it, and that is §8 D9. |
| **`kumiho_deprecate_item` always prompts** | `auto-approve-memory.py:25-28` falls through on any tool name containing `deprecate`, contradicting `SKILL.md:21`'s "respect *forget X* immediately". §8 D6. |
| **AUTOMINE stays OFF** | Step 3 makes it *possible* to enable (gate moved after hydration) and Step 5 declares it in both places. It does not turn on. `session-mine.log` stays silent until a human opts in. |
| **Recall relevance/quality** | Same `engage`, same keyword-ish query. This plan changes the determinism of *invocation*, not the quality of results. Inherent cost of stale-while-revalidate: a hard topic switch gets slightly-off memories for one interval — which is what the residual optional `engage` is for. |
| **Subagent in-flight guidance** | Step 6b gives a protocol card at spawn. Their turns are otherwise unobserved (`SubagentStop` observation is a one-line add once someone wants it). |
| **Idle-timer consolidation** | No generic idle event. `TeammateIdle` is teammate/team-scoped. Stays a documented asymmetry in the parity table rather than a faked port. |

**Needs a marketplace release (constraint 11 — nothing hot-reloads)**

Every step. The live install is the 0.17.0 marketplace cache; `hooks.json`, all scripts, `SKILL.md`, `.mcp.json` and `README.md` all take effect only after a version bump + marketplace update. **Verified byte-identical for the diagnosed files, so the diagnosis applies to what actually ran.**

Iterate without republishing by pointing `~/.claude/settings.json` hooks at absolute paths in the working tree — that is the Step 0 harness, and it is how V1–V9 and every step's manual smoke test get run. Suggested cadence: `0.18.0` = Steps 1–3 (bleeding only, zero behaviour change), `0.19.0` = Step 4 (observation), `0.20.0` = Steps 5–7 (recall).

---

## 8. Decisions that are the human's, not the engineer's

| # | Decision | What I'd default to | Why it isn't mine to make |
|---|---|---|---|
| **D1** | **Ship a blocking per-turn hook at all?** Measured in-the-wild telemetry on the identical mechanism: `SessionStart` med **868 ms**, p95 **2,757 ms**, max **5,247 ms** — and max > the 5 s timeout means the *existing* hook has already been killed by its own timeout at least once. Best-of-N microbenchmarks put a bare python hook at 178–250 ms direct and 301–508 ms through Git Bash. So the real per-prompt cost is somewhere in **0.35–0.9 s**, felt at the single most latency-visible moment. | Ship, gated on **V1** measured on this machine; hard-cap `timeout: 5`; `reflex.off` as an instant escape. | It is a taste call about the user's daily driver. If 0.5 s between Enter and first token is unacceptable, Steps 1–5 still deliver real value and Step 6 simply never lands. |
| **D2** | **Resident token growth** (~1,558 → ~3,090 tok on a busy 40-turn session; §5). | `KUMIHO_REFLEX_SESSION_BUDGET_CHARS=6000`. | Trading context budget for reliability is a product decision. `=0` is a supported configuration that keeps the −300 tok/bootstrap saving with zero growth. |
| **D3** | **Host-side turn buffering (Step 8) ON or OFF by default?** ON means up to `KUMIHO_REFLEX_RESPONSE_CHARS` of verbatim assistant text egressing to Upstash **cloud** Redis every turn, 1 h TTL, plus one `_background_assess` LLM call per turn for keyed users (cooldown defeated by fresh processes). `README:75` currently says "Raw transcripts stay local". | **OFF**, matching `KUMIHO_MEMORY_CODE_AUTOMINE`'s default-OFF posture and its stated reason. | A privacy-posture and billing decision. Flipping it requires correcting the README privacy row in the same commit. |
| **D4** | **Does the plugin want an auto-`consolidate` at all**, given it hard-fails keyless? | No auto-consolidate; SessionEnd + the manual tool only. | If the user always runs with a key, a threshold consolidate becomes reasonable and the calculus changes. |
| **D5** | **Accept one `PostCompact [python …] completed successfully` line per compaction** in exchange for host-executed `compact_summary` capture — the best keyless graph write available? | **No** for v1; keep the model-side rule in both bootstrap and SKILL.md. | It is a straight trade of one visible line per compaction against a guaranteed keyless typed memory. Only the user can price "never narrate the plumbing." |
| **D6** | **`kumiho_deprecate_item`**: auto-approve it (removing "deprecate" from `destructive_keywords`) so "forget X" is honoured immediately, or soften `SKILL.md:21`'s promise? | Soften the SKILL.md wording (zero-risk). | Auto-approving a destructive memory op without a prompt is the user's risk to accept. |
| **D7** | **`_MAX_QUEUE` target.** 200 (≈33 days of headroom at the measured ~6/day) vs 500 (≈80 days, 500 diffs to drain). | 200 + spill + per-eviction log. | Depends on how the user intends to drain — and whether the drain ever becomes automatic. |
| **D8** | **Interpreter fallback in the hook command string.** Fixes a total-silent-death mode, but changes a shell-parsed string whose non-Git-Bash Windows shell is unverified. | Ship only if **V7** passes on both shells; otherwise document the `~/.claude/settings.json` override. | Trading a rare total failure against a possible universal parse error. |
| **D9** | **Ever build the resident daemon?** It is the only shape that gets per-turn recall near 0 ms (it is what openclaw's plugin lifetime provides for free), and no judge called the daemon itself fatal — only its `mcp_tool` legs, its request-file drain, and the consent question ("nothing establishes the user consents to a background process holding a cloud connection for 900 s after they walk away"). | Not now. Revisit only if V1 shows the blocking hook is intolerable **and** Step 4's ledger shows recall is worth the cost. | A background resident process on the user's laptop maintaining a cloud connection is a consent decision, not an optimisation. |
| **D10** | **How much of the 779-token block to delete.** It also carries anti-self-play, anti-repetition and temporal rules that have nothing to do with the diagnosed failure. | The Step 7 list: delete what the host now does; keep everything else verbatim. | Some of those rules may be load-bearing for reasons outside this diagnosis; only the author knows which. |

---

## Appendix — verified facts an implementer must not re-derive

- `Kf(permissionMode, sessionId, ctx)` puts `session_id, transcript_path, cwd, prompt_id, permission_mode, agent_id, agent_type, effort` on **every** hook payload. Four "assumptions" in the candidate designs were dead weight; the `<sid>.session.json` fallback is belt-and-braces, not a necessity.
- `agent_id` is a **base** field (`agent_id: r?.agentId`). Discriminate on `hook_event_name`. Subagent stops fire `SubagentStop` (`let l = o ? "SubagentStop" : "Stop"`).
- `UserPromptSubmit` carries `prompt`. `additionalContext` lands in **that** turn: `if(N.additionalContexts?.length>0) k.messages.push(qa({type:"hook_additional_context",…}))`, synchronously inside the awaited hook loop before the query — and therefore **persists** in history.
- `Stop` carries `stop_hook_active` and `last_assistant_message` = `Jc(d.message.content,"\n").trim() || void 0` (undefined on a text-empty final message). Its schema description: *"Avoids the need to read and parse the transcript file."*
- `Stop`, `SubagentStop`, `SubagentStart` and `UserPromptSubmit` all appear in the `additionalContext` output union. `PostCompact` does **not**.
- Async **command** hooks on `pL`-dispatched events are truly backgrounded and emit no attachment (`if(Te.backgrounded){yield{outcome:"success"};return}`). `TL` (Pre/PostCompact) does **not** check `backgrounded`. `SessionStart` is dispatched with `forceSyncExecution`, so an async hook there runs synchronously and its stdout **is** parsed.
- Omitting `statusMessage` makes the progress label fall back to the **raw command string** (`W5` → `lSe`). Always set a neutral one.
- `mcp_tool.input` **does** interpolate `${dotted.path}` (`vF_`, `/\$\{([a-zA-Z_][a-zA-Z0-9_.]*)\}/g`, missing → `""`, recurses arrays/objects). Recorded for completeness — this plan uses no `mcp_tool` hooks, because they cannot be async, cannot be env-gated, and surface their result to the user.
- `type:"prompt"` and `type:"agent"` are *"Only available for tool events: PreToolUse, PostToolUse, PermissionRequest."*
- `CLAUDE_CODE_STOP_HOOK_BLOCK_CAP` defaults to 8. `CLAUDE_PLUGIN_ROOT` is **empty** in the agent's Bash environment.
- Transcript records have **no** top-level `tool_name`; the tool name is at `message.content[].name`. A hook grepping `'"tool_name": "mcp__..."'` silently never matches. This plan reads tool names from the `PostToolUse` payload and never parses transcripts for them.
- Windows: `os.replace` raises `PermissionError` (WinError 32/5) when either file is open elsewhere. `seek(-N, SEEK_END)` raises in text mode and `OSError 22` on files shorter than the window. Piped children report `cp949`; force `PYTHONIOENCODING=utf-8`, `ensure_ascii=True`, `encoding="utf-8"`.
- `code-capture-hook.py:53`: `repo_dir = args[0] if args else os.getcwd()` → `cwd=repo_dir`. `args[0]` **must** be a real directory.
- Keyless with no auth token: `_bootstrap_server_endpoint()` sets `KUMIHO_SERVER_ENDPOINT='needs-auth.kumiho.invalid:443'` and returns 0 without raising. Always check the sentinel before any gRPC call.
- `tool_memory_engage` reads `limit` / `space_paths` / `memory_types`; `top_k` is ignored and `session_id` is unused. `recall_mode="summarized"` is title+summary only, no LLM.
- `pytest claude/scripts/ -q` → 13 passed. `conftest.py collect_ignore` is an **opt-OUT** list; the house return-bool style is never actually enforced, so new tests are pytest-native and are **not** added to that list.

---

## Addendum — 2026-09-02: keyless consolidation

Decision D4 and the "20-turn auto-`consolidate`" discard (§1) rested on one fact:
`consolidate_session` hard-required an LLM, so keyless it was a guaranteed
failure. kumiho-memory now takes an agent-written `summary` (and
`implications`) on `kumiho_memory_consolidate` and skips its summarizer when it
is present — the in-loop model, or a subagent it delegates to (e.g. Sonnet via
the Agent tool), writes the summary, exactly as reflect / decompose /
code_capture already work.

What shipped in the plugin on top of that:

- `reflex-observe.py` ledgers `consolidate` calls with a best-effort `ok` flag
  read from the tool response; the PostToolUse matcher includes the tool.
- `memory-reflex.py` counts completed turns since the last *successful*
  consolidate and, at `KUMIHO_REFLEX_CONSOLIDATE_FLOOR` (default 20, `0`
  disables), injects one keyless consolidation instruction carrying the
  session id and the call shape. Five-turn cooldown, same discipline as the
  reflect floor.
- Still NOT host-executed: the host has no model to write the summary with,
  so the model (or its subagent) does, on the nudge. D4's "SessionEnd + manual
  tool only" is superseded by "counted floor + manual tool", not by an
  automatic write.
