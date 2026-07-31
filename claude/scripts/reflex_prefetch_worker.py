#!/usr/bin/env python3
"""Detached memory-prefetch worker -- the expensive half of the reflex.

Spawned from the async ``Stop`` hook AFTER a turn has ended, so it runs during
the user's thinking time and never on the critical path.  It calls
``kumiho_memory_engage`` ONCE, formats the result the way the model should see
it, and writes ``<reflex>/<sid>.recall.json``.  The fast blocking hook on the
NEXT turn only has to read that file -- classic stale-while-revalidate, ported
from openclaw's ``prefetchedRecall`` (openclaw/src/hooks.ts).

The query text is NEVER passed in argv: process command lines are captured by
EDR agents, so the prompt is read from ``<sid>.turn.json`` instead and only the
cwd + session id travel on the command line.

Deliberately does NOT reuse two launcher steps the sibling workers call:

* ``_ensure_runtime()`` -- it can run ``venv.create`` + ``pip install`` and
  spawns a probe subprocess even when warm.  A per-turn worker must never
  provision anything, so the venv is probed directly (interpreter + marker
  file) and a cold state dir is a skip, not an install.
* ``_bootstrap_server_endpoint()`` per invocation -- control-plane discovery is
  an HTTPS round trip.  The resolved endpoint is cached in
  ``<reflex>/endpoint.json`` for 15 minutes and the cache is dropped on any
  transport error so a moved region self-heals.

The auth sentinel is the load-bearing bail: with no token that bootstrap does
NOT raise, it pins ``needs-auth.kumiho.invalid:443`` (RFC 6761, never
resolves).  Without the explicit check every single turn would fire a doomed
gRPC call at a host that cannot exist, forever.

Run: python reflex_prefetch_worker.py <cwd> <session_id>  (detached; prints nothing)
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import reflex_state as rs  # noqa: E402

# Global, not per-session: the venv and the endpoint cache are global state, so
# two sessions prefetching at once would contend over the same files.
LOCK_STALE_S = 300
ENDPOINT_TTL_S = 900
ENGAGE_TIMEOUT_S = 45
MARKER_FILE = ".installed-packages.txt"
AUTH_SENTINEL = "needs-auth.kumiho.invalid"

DEFAULT_MIN_INTERVAL_S = 45
DEFAULT_LIMIT = 5
DEFAULT_MAX_CHARS = 1600
QUERY_MAX_CHARS = 200
DEBOUNCE_OVERLAP = 0.8
TRANSCRIPT_MAX_LINES = 120
TRANSCRIPT_MAX_BYTES = 262144

# Args go in on STDIN (never argv).  ``limit`` -- NOT ``top_k``, which the tool
# silently ignores.  No ``session_id``: tool_memory_engage ignores it entirely.
# ``recall_mode="summarized"`` is title+summary only and uses no LLM.
_ENGAGE_SNIPPET = (
    "import json,sys\n"
    "from kumiho_memory.mcp_tools import tool_memory_engage\n"
    "a=json.load(sys.stdin)\n"
    "sys.stdout.write(json.dumps(tool_memory_engage(a), ensure_ascii=True))\n"
)

# openclaw's isUnknownToolError (client.ts:135-144), minus the HTTP 404 arm:
# this transport is a subprocess, so there is no status code to inspect.
# Phrases that mean the BACKEND genuinely lacks the tool -- a permanent capability
# gap worth latching. Local import failures are deliberately NOT here: a
# ModuleNotFoundError is transient, and its most likely cause is the plugin
# upgrade itself. `pip install --upgrade` removes the old kumiho-memory
# distribution before writing the new one, and _venv_ready cannot see that window
# (interpreter and marker both still exist), so a Stop-spawned prefetch landing
# mid-reinstall would latch the reflex dark for the rest of the session.
_UNKNOWN_TOOL_PHRASES = (
    "unknown tool", "tool not found", "method not found", "unsupported tool",
)

# Transient: skip this round, never latch.
_TRANSIENT_PHRASES = (
    "no module named 'kumiho_memory'", "cannot import name 'tool_memory_engage'",
)

# Windows: a DETACHED_PROCESS parent has no console, so launching a
# console-subsystem child makes the OS allocate a NEW VISIBLE one. capture_output
# does not suppress it. Without this flag the user gets a black window on every
# turn, for as long as a cold interpreter start + gRPC round trip takes.
_NO_WINDOW = 0x08000000 if os.name == "nt" else 0

_OPEN_TAG = "<kumiho_memory>"
_CLOSE_TAG = "</kumiho_memory>"
_GUIDE = (
    "Auto-recalled long-term memories from previous conversations. Treat as "
    "authoritative facts -- use these to answer questions about the user's "
    "preferences, history, and prior decisions before relying on general knowledge."
)
_PROJECT_GUIDE = (
    "Creative project items relevant to this conversation. Pass their krefs as "
    "source_krefs when capturing new outputs derived from this work."
)
# openclaw's project heuristic (hooks.ts:238-241).
_NON_PROJECT_SEGMENTS = ("personal", "users", "session", "work")

_NUMBER_WORDS = {
    2: "two", 3: "three", 4: "four", 5: "five", 6: "six",
    7: "seven", 8: "eight", 9: "nine", 10: "ten", 11: "eleven",
}


def _load_launcher():
    path = Path(__file__).resolve().parent / "run_kumiho_mcp.py"
    spec = importlib.util.spec_from_file_location("kumiho_claude_launcher", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # main() is __main__-guarded
    return module


# --------------------------------------------------------------------- paths

def _recall_path(session_id: str) -> Path:
    return rs.reflex_dir() / ("%s.recall.json" % session_id)


def _turn_path(session_id: str) -> Path:
    return rs.reflex_dir() / ("%s.turn.json" % session_id)


def _session_path(session_id: str) -> Path:
    return rs.reflex_dir() / ("%s.session.json" % session_id)


def _state_path(session_id: str) -> Path:
    return rs.reflex_dir() / ("%s.state" % session_id)


def _endpoint_path() -> Path:
    return rs.reflex_dir() / "endpoint.json"


def _env_int(name: str, default: int) -> int:
    raw = (os.getenv(name, "") or "").strip()
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


# ------------------------------------------------------------------- runtime

def _venv_ready(launcher) -> Path | None:
    """Probe, never provision.

    ``_ensure_runtime()`` would happily run ``venv.create`` + ``pip install``
    mid-session, which is minutes of CPU for a prefetch nobody is waiting on.
    A missing runtime is a skip: onboarding provisions it, not this worker.
    """
    state = rs.state_dir()
    python_path = launcher._venv_python(state / "venv")
    if not python_path.exists() or not (state / MARKER_FILE).exists():
        return None
    return python_path


def _resolve_endpoint(launcher) -> str:
    """Endpoint with a 15-minute cache; CE always goes through the launcher."""
    if launcher._ce_mode_enabled():
        launcher._bootstrap_server_endpoint()  # local, no discovery round trip
        return (os.getenv("KUMIHO_LOCAL_SERVER_ENDPOINT", "") or "").strip()

    cached = rs.read_json(_endpoint_path(), None)
    if isinstance(cached, dict):
        endpoint = str(cached.get("endpoint") or "").strip()
        ts = cached.get("ts")
        if endpoint and isinstance(ts, (int, float)) and (time.time() - ts) < ENDPOINT_TTL_S:
            os.environ["KUMIHO_SERVER_ENDPOINT"] = endpoint
            os.environ.pop("KUMIHO_SERVER_ADDRESS", None)
            return endpoint

    launcher._bootstrap_server_endpoint()  # may raise RuntimeError
    endpoint = (os.getenv("KUMIHO_SERVER_ENDPOINT", "") or "").strip()
    # Never cache the auth sentinel: it costs nothing to re-derive (the
    # no-token path short-circuits before any network call) and caching it
    # would keep the reflex dark for 15 minutes after a token finally lands.
    if endpoint and AUTH_SENTINEL not in endpoint:
        rs.write_json_atomic(_endpoint_path(), {"endpoint": endpoint, "ts": int(time.time())})
    return endpoint


def _drop_endpoint_cache() -> None:
    try:
        _endpoint_path().unlink(missing_ok=True)
    except OSError:
        pass


# --------------------------------------------------------------------- query

def _norm_word(word: str) -> str:
    """openclaw's ``word.toLowerCase().replace(/[^\\w]/g, "")``.

    Python's ``\\w`` is Unicode-aware, JavaScript's is not.  That difference is
    load-bearing, not incidental: under the JS semantics every token of a
    Korean prompt normalizes to "" and is dropped, so an all-Korean message
    produces an EMPTY recall query.  Keeping Unicode semantics is the port's
    one deliberate behavioural change.
    """
    return re.sub(r"[^\w]", "", word, flags=re.UNICODE).lower()


def _is_cjk(ch: str) -> bool:
    o = ord(ch)
    return (
        0xAC00 <= o <= 0xD7A3      # Hangul syllables
        or 0x1100 <= o <= 0x11FF   # Hangul jamo
        or 0x3040 <= o <= 0x30FF   # Hiragana + Katakana
        or 0x4E00 <= o <= 0x9FFF   # CJK unified ideographs
        or 0x3400 <= o <= 0x4DBF   # CJK extension A
    )


def _is_significant(word: str) -> bool:
    """Is this normalized token worth keeping in a recall query?

    openclaw uses a flat ``len > 2``, calibrated for Latin script where 1-2
    character tokens are almost always stopwords ("a", "of", "is"). Applied to
    CJK that is destructive: a Hangul syllable carries roughly as much meaning as
    a whole English word, so ``가격`` (price), ``결제`` (payment) and ``오류``
    (error) -- precisely the terms a user searches on -- all fail the test, and a
    short all-Korean prompt reduces to an EMPTY query with no recall at all.

    Threshold is therefore script-aware: 2+ characters for CJK, 3+ otherwise.
    This is a deliberate divergence from openclaw, for the same reason the
    Unicode ``\\w`` in _norm_word is: faithful parity here means broken recall.
    """
    if not word:
        return False
    if len(word) > 2:
        return True
    return len(word) >= 2 and any(_is_cjk(c) for c in word)


def _words(text: str) -> set:
    return {w for w in (_norm_word(t) for t in (text or "").split()) if _is_significant(w)}


def _overlap(new: str, old: str) -> float:
    a, b = _words(new), _words(old)
    if not a or not b:
        return 1.0 if a == b else 0.0
    return len(a & b) / float(len(a))


def _build_recall_query(current: str, prev_user: str, last_assistant: str) -> str:
    """Port of openclaw's buildRecallQuery (hooks.ts:310-347).

    Token significance is script-aware rather than openclaw's flat ``len > 2``
    -- see _is_significant for why faithful parity would empty a Korean query.
    """
    current = (current or "").strip()
    parts = [current]

    # Short / ambiguous message -- pull in the previous user turn for topic.
    if len(current.split()) <= 6 and prev_user:
        parts.append(prev_user.strip())

    # Key terms from the last assistant turn (highest signal appears early).
    if last_assistant:
        parts.append(" ".join(last_assistant.strip().split()[:20]))

    seen: set = set()
    tokens: list = []
    for part in parts:
        for word in part.split():
            w = _norm_word(word)
            if _is_significant(w) and w not in seen:
                seen.add(w)
                tokens.append(word)
    return " ".join(tokens)[:QUERY_MAX_CHARS]


def _transcript_context(transcript_path: str) -> tuple:
    """(previous user text, last assistant text) from the transcript tail.

    The observation ledger deliberately stores only a hash of the assistant
    text, so the transcript is the only place the prior turns can come from.
    Tailed, never fully read: transcripts reach hundreds of MB.
    """
    if not transcript_path or not os.path.exists(transcript_path):
        return "", ""
    users: list = []
    assistants: list = []
    for line in rs.tail_lines(transcript_path, TRANSCRIPT_MAX_LINES, TRANSCRIPT_MAX_BYTES):
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(entry, dict):
            continue
        message = entry.get("message") or entry
        if not isinstance(message, dict):
            continue
        role = message.get("role", "")
        if role not in ("user", "assistant"):
            continue
        content = message.get("content", "")
        if isinstance(content, list):
            texts = [
                b.get("text", "") for b in content
                if isinstance(b, dict) and b.get("type") == "text"
            ] + [b for b in content if isinstance(b, str)]
            content = "\n".join(t for t in texts if t)
        if not isinstance(content, str):
            continue
        content = content.strip()
        if not content or content.startswith("<system-reminder>"):
            continue
        (users if role == "user" else assistants).append(content)
    # users[-1] is the turn we are prefetching FOR; the anchor is the one before.
    prev_user = users[-2] if len(users) > 1 else ""
    return prev_user, (assistants[-1] if assistants else "")


def _git_branch(cwd: str) -> str:
    try:
        proc = subprocess.run(
            ["git", "-C", cwd, "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=10, creationflags=_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    if proc.returncode != 0:
        return ""
    branch = (proc.stdout or "").strip()
    return "" if branch in ("", "HEAD") else branch


# ------------------------------------------------------------------ format

def _human_age(created_at: str, now: datetime | None = None) -> str:
    """Absolute timestamps are useless to a model that cannot subtract dates.

    SessionStart already instructs the model to express memory age naturally
    ("earlier today", "yesterday", "about two weeks ago"); computing it here
    means it cannot get the arithmetic wrong.
    """
    raw = (created_at or "").strip()
    if not raw:
        return ""
    text = raw.replace("Z", "+00:00").replace("z", "+00:00")
    if " " in text and "T" not in text:
        text = text.replace(" ", "T", 1)
    try:
        parsed = datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return ""
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    now = now or datetime.now(timezone.utc)
    days = (now - parsed).days
    if days < 0:
        return "just now"
    if days == 0:
        return "earlier today"
    if days == 1:
        return "yesterday"
    if days < 7:
        return "%d days ago" % days
    if days < 14:
        return "last week"
    if days < 31:
        return "about %s weeks ago" % _number_word(days // 7)
    if days < 365:
        months = days // 30
        return "about a month ago" if months == 1 else "about %s months ago" % _number_word(months)
    years = days // 365
    return "about a year ago" if years == 1 else "about %s years ago" % _number_word(years)


def _number_word(n: int) -> str:
    return _NUMBER_WORDS.get(n, str(n))


def _flat(value) -> str:
    if isinstance(value, (list, tuple)):
        value = ", ".join(str(v) for v in value if v)
    return " ".join(str(value or "").split())


def _is_project(mem: dict) -> bool:
    """openclaw's heuristic: 2+ space segments and a non-generic leaf."""
    segments = [s for s in str(mem.get("space") or "").split("/") if s]
    return len(segments) >= 2 and segments[-1].lower() not in _NON_PROJECT_SEGMENTS


def _render_memory(mem: dict) -> list:
    lines = ["- [%s] %s: %s" % (
        _flat(mem.get("type")) or "memory",
        _flat(mem.get("title")) or "(untitled)",
        _flat(mem.get("summary")),
    )]
    topics = _flat(mem.get("topics") or mem.get("tags"))
    if topics:
        lines.append("  Topics: %s" % topics)
    created = _flat(mem.get("created_at"))
    if created:
        age = _human_age(created)
        lines.append("  Recorded: %s%s" % (created, " (%s)" % age if age else ""))
    lines.append("  Kref: %s" % _flat(mem.get("kref")))
    return lines


def _format_recalled(memories: list, max_chars: int) -> tuple:
    """Port of formatRecalledMemories (hooks.ts:223-282).

    Deviation: openclaw emits two sibling tag blocks (<kumiho_memory> and
    <kumiho_project>); here the project items are a labelled section INSIDE the
    single <kumiho_memory> envelope, because the consumer contract is one
    block.  The split itself (and its heuristic) is unchanged.

    Truncation is on a MEMORY BOUNDARY -- a half-quoted memory reads as a
    corrupted fact, which is worse than a missing one.  At least one memory is
    always emitted, so an oversized single memory is not silently dropped.
    """
    ordered = [(False, m) for m in memories if not _is_project(m)]
    ordered += [(True, m) for m in memories if _is_project(m)]

    lines = [_OPEN_TAG, _GUIDE, ""]
    krefs: list = []
    kept = 0
    project_opened = False
    for is_project, mem in ordered:
        trial = list(lines)
        if is_project and not project_opened:
            trial += ["", _PROJECT_GUIDE, ""]
        trial += _render_memory(mem)
        if len("\n".join(trial + ["", _CLOSE_TAG])) > max_chars and kept:
            break
        lines = trial
        kept += 1
        project_opened = project_opened or is_project
        kref = _flat(mem.get("kref"))
        if kref:
            krefs.append(kref)
    if not kept:
        return "", [], 0
    return "\n".join(lines + ["", _CLOSE_TAG]), krefs, kept


# ------------------------------------------------------------------- engage

def _call_engage(python_path, args: dict) -> tuple:
    """One subprocess, args on stdin.  Returns ``(payload, error)``.

    ``PYTHONIOENCODING=utf-8`` is not optional: the default piped encoding on a
    Korean Windows install is cp949, and a memory containing a single character
    outside it would come back mojibake or raise in the child.
    """
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    try:
        proc = subprocess.run(
            [str(python_path), "-c", _ENGAGE_SNIPPET],
            input=json.dumps(args, ensure_ascii=True),
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            env=env, timeout=ENGAGE_TIMEOUT_S, creationflags=_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return None, "engage subprocess failed: %s" % exc
    if proc.returncode != 0:
        return None, "engage rc=%d: %s" % (proc.returncode, (proc.stderr or "").strip()[-400:])
    try:
        data = json.loads(proc.stdout or "")
    except (json.JSONDecodeError, ValueError):
        return None, "engage returned unparseable output: %s" % (proc.stdout or "")[:200]
    if not isinstance(data, dict):
        return None, "engage returned a non-object result"
    return data, ""


def _is_transient_error(message: str) -> bool:
    """A local failure that will resolve on its own -- skip, never latch."""
    low = (message or "").lower()
    return any(phrase in low for phrase in _TRANSIENT_PHRASES)


def _is_unknown_tool_error(message: str) -> bool:
    low = (message or "").lower()
    if _is_transient_error(low):
        return False
    return any(phrase in low for phrase in _UNKNOWN_TOOL_PHRASES)


# ---------------------------------------------------------------------- main

def _prefetch(session_id: str, cwd_arg: str) -> int:
    launcher = _load_launcher()

    # Same environment pipeline as the MCP server, minus provisioning.
    launcher._sanitize_placeholder_env_vars()
    launcher._hydrate_env_from_local_config()
    if not launcher._ce_mode_enabled():
        launcher._validate_auth_token()
    launcher._configure_llm_fallback()
    # NOTE: no keyless bail here, unlike code_ingest_worker.  Commit mining
    # needs an LLM; engage in summarized mode does not.  Keyless is fine.

    python_path = _venv_ready(launcher)
    if python_path is None:
        rs.log("skip: venv not provisioned")
        return 0

    try:
        endpoint = _resolve_endpoint(launcher)
    except RuntimeError as exc:
        _drop_endpoint_cache()
        rs.log("skip: endpoint bootstrap failed (%s)" % exc)
        return 0
    if AUTH_SENTINEL in endpoint:
        rs.log("skip: no auth token")
        return 0

    session = rs.read_json(_session_path(session_id), None) or {}
    turn = rs.read_json(_turn_path(session_id), None) or {}
    cwd = str(session.get("cwd") or "").strip() or cwd_arg
    if not os.path.isdir(cwd):
        cwd = cwd_arg if os.path.isdir(cwd_arg) else os.getcwd()

    prompt = str(turn.get("prompt") or "").strip()
    if prompt:
        prev_user, last_assistant = _transcript_context(
            str(session.get("transcript_path") or ""))
        query = _build_recall_query(prompt, prev_user, last_assistant)
    else:
        # Cold / SessionStart path: no prompt exists yet, so the working
        # context IS the query.
        query = _build_recall_query(
            "%s %s" % (_git_branch(cwd), os.path.basename(os.path.abspath(cwd))), "", "")
    if not query:
        rs.log("skip: empty query")
        return 0

    previous = rs.read_json(_recall_path(session_id), None) or {}
    min_interval = _env_int("KUMIHO_REFLEX_MIN_INTERVAL_S", DEFAULT_MIN_INTERVAL_S)
    age = time.time() - float(previous.get("generated_at") or 0)
    if age < min_interval:
        overlap = _overlap(query, str(previous.get("query") or ""))
        if overlap >= DEBOUNCE_OVERLAP:
            rs.log("skip: debounced (%.0fs < %ds, %.0f%% overlap)"
                   % (age, min_interval, overlap * 100))
            return 0

    state = rs.read_json(_state_path(session_id), None) or {}
    if state.get("engage_unsupported"):
        rs.log("skip: engage unsupported on this backend (latched)")
        return 0

    limit = _env_int("KUMIHO_REFLEX_LIMIT", DEFAULT_LIMIT)
    rs.log("prefetch start: session=%s limit=%d qlen=%d" % (session_id, limit, len(query)))
    data, error = _call_engage(python_path, {
        "query": query, "limit": limit, "recall_mode": "summarized",
    })
    if data is None:
        # Any failure invalidates the cached endpoint: a moved region or a dead
        # cache entry must not keep failing for the rest of its 15-minute TTL.
        _drop_endpoint_cache()
        if _is_unknown_tool_error(error):
            state["engage_unsupported"] = True
            rs.write_json_atomic(_state_path(session_id), state)
            rs.log("prefetch failed, latching engage_unsupported: %s" % error)
        else:
            rs.log("prefetch failed: %s" % error)
        return 0

    if data.get("deduplicated"):
        # openclaw hooks.ts:110-117 -- a dedup hit comes back EMPTY because an
        # identical recall already consumed the server's window.  Writing it
        # would replace a good cache with nothing.
        rs.log("skip: engage deduplicated (existing cache kept)")
        return 0
    if data.get("backend_error"):
        rs.log("engage reported backend_error: %s" % _flat(data.get("backend_error"))[:200])

    results = data.get("results")
    if not isinstance(results, list):
        results = []
    results = [m for m in results if isinstance(m, dict)]
    block, krefs, count = _format_recalled(
        results, _env_int("KUMIHO_REFLEX_MAX_CHARS", DEFAULT_MAX_CHARS))
    rs.write_json_atomic(_recall_path(session_id), {
        "generated_at": int(time.time()),
        "query": query,
        "block": block,
        "content_sha12": hashlib.sha256(block.encode("utf-8")).hexdigest()[:12],
        "count": count,
        "krefs": krefs,
    })
    rs.log("prefetch done: session=%s recalled=%d of %d, %d chars"
           % (session_id, count, len(results), len(block)))
    return 0


def main() -> int:
    try:
        if rs.off() or not rs.gate("KUMIHO_REFLEX_PREFETCH"):
            return 0
        session_id = rs.safe_id(sys.argv[2] if len(sys.argv) > 2 else "")
        if not session_id:
            return 0
        cwd_arg = sys.argv[1] if len(sys.argv) > 1 else os.getcwd()

        lock = rs.reflex_dir() / "prefetch.lock"
        try:
            if lock.exists() and (time.time() - lock.stat().st_mtime) < LOCK_STALE_S:
                rs.log("superseded: another prefetch is running (lock %s)" % lock)
                return 0
            lock.write_text(str(os.getpid()), encoding="utf-8")
        except OSError:
            pass  # lock is best-effort

        try:
            return _prefetch(session_id, cwd_arg)
        finally:
            try:
                lock.unlink(missing_ok=True)
            except OSError:
                pass
            # Only the run that held the lock prunes: it is a whole-directory
            # stat sweep, and the superseded/kill-switch paths must stay free.
            try:
                rs.prune()
            except Exception:  # noqa: BLE001
                pass
    except Exception as exc:  # noqa: BLE001 -- a prefetch must never fail a session
        rs.log("worker error: %s" % exc)
        return 0


if __name__ == "__main__":
    sys.exit(main())
