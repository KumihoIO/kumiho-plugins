#!/usr/bin/env python3
"""Tests for the detached prefetch worker.

Nothing here touches the network: the endpoint cache is pre-seeded and the one
venv subprocess is replaced.  The load-bearing assertions are the ones for
failures that are silent in production -- the auth sentinel (a doomed gRPC call
every turn, forever), the dedup fall-through (a good cache replaced by an empty
one), and the cp949 round trip (Korean surviving the pipe).

Run: python -m pytest claude/scripts/test_reflex_prefetch.py -q
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
KOREAN_Q = "결제 파이프라인 리팩터링 어떻게 결정했더라"
KOREAN_TITLE = "결제 파이프라인 재설계 결정"
KOREAN_SUMMARY = "멱등 키를 도입하고 재시도는 지수 백오프로 통일하기로 했다"


def _load():
    spec = importlib.util.spec_from_file_location(
        "reflex_prefetch_worker", SCRIPTS / "reflex_prefetch_worker.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _payload(count: int = 2, title: str = "Chose Postgres over DynamoDB",
             summary: str = "Relational joins dominated the access pattern.") -> dict:
    results = []
    for i in range(count):
        results.append({
            "kref": "kref://cognitive/mem-%d" % i,
            "type": "decision",
            "title": "%s #%d" % (title, i),
            "summary": summary,
            "space": "CognitiveMemory/personal",
            "created_at": time.strftime(
                "%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 86400)),
            "tags": ["storage", "adr"],
        })
    return {"context": "", "results": results,
            "source_krefs": [m["kref"] for m in results], "count": count}


class _Spy:
    """Stands in for the venv subprocess; counts calls so 'zero subprocess
    calls' is an assertion rather than a hope."""

    def __init__(self, result=None, error=""):
        self.calls = []
        self.result = result
        self.error = error

    def __call__(self, python_path, args):
        self.calls.append((str(python_path), args))
        return self.result, self.error


def _prepare(tmp_path, monkeypatch, *, sid="s1", prompt="why did we pick postgres",
             endpoint="grpc.kumiho.example:443", venv=True, spy=None, mod=None):
    """A warm, offline environment: state dir, fake venv, cached endpoint."""
    monkeypatch.setenv("KUMIHO_CLAUDE_HOME", str(tmp_path))
    monkeypatch.delenv("KUMIHO_REFLEX_PREFETCH", raising=False)
    monkeypatch.delenv("KUMIHO_CLAUDE_MODE", raising=False)
    monkeypatch.delenv("KUMIHO_CLAUDE_SERVER_ENDPOINT", raising=False)
    # Cleared unconditionally: a leaked host value points at a REAL venv, so
    # "no venv" would not be no venv at all.
    monkeypatch.delenv("CLAUDE_PLUGIN_DATA", raising=False)

    reflex = tmp_path / "reflex"
    reflex.mkdir(parents=True, exist_ok=True)
    if venv:
        # Build the fake venv where the worker ACTUALLY looks. This fixture used
        # to hardcode <state>/venv; when the real venv moved under the plugin
        # data dir the worker went dead in production while these tests stayed
        # green, because the fixture and the bug agreed with each other.
        monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path / "pdata"))
        (tmp_path / ".installed-packages.txt").write_text("kumiho", encoding="utf-8")
        for sub in ("Scripts", "bin"):
            d = tmp_path / "pdata" / "venv" / sub
            d.mkdir(parents=True, exist_ok=True)
            (d / "python.exe").write_text("", encoding="utf-8")
            (d / "python").write_text("", encoding="utf-8")
    (reflex / "endpoint.json").write_text(
        json.dumps({"endpoint": endpoint, "ts": int(time.time())}), encoding="utf-8")
    (reflex / ("%s.session.json" % sid)).write_text(
        json.dumps({"session_id": sid, "cwd": str(tmp_path), "transcript_path": ""}),
        encoding="utf-8")
    if prompt is not None:
        (reflex / ("%s.turn.json" % sid)).write_text(
            json.dumps({"prompt": prompt}, ensure_ascii=True), encoding="utf-8")

    mod = mod or _load()
    if spy is not None:
        monkeypatch.setattr(mod, "_call_engage", spy)
    monkeypatch.setattr(sys, "argv",
                        ["reflex_prefetch_worker.py", str(tmp_path), sid])
    return mod


def _recall(tmp_path, sid="s1"):
    p = tmp_path / "reflex" / ("%s.recall.json" % sid)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def _log(tmp_path) -> str:
    p = tmp_path / "reflex.log"
    return p.read_text(encoding="utf-8") if p.exists() else ""


# ------------------------------------------------------------ the happy path

def test_engage_payload_produces_the_exact_cache_contract(tmp_path, monkeypatch):
    spy = _Spy(_payload())
    mod = _prepare(tmp_path, monkeypatch, spy=spy)
    assert mod.main() == 0

    cache = _recall(tmp_path)
    assert set(cache) == {"generated_at", "query", "block", "content_sha12",
                          "count", "krefs"}
    assert isinstance(cache["generated_at"], int)
    assert cache["count"] == 2
    assert cache["krefs"] == ["kref://cognitive/mem-0", "kref://cognitive/mem-1"]
    assert cache["content_sha12"] == hashlib.sha256(
        cache["block"].encode("utf-8")).hexdigest()[:12]
    assert len(cache["content_sha12"]) == 12

    block = cache["block"]
    assert block.startswith("<kumiho_memory>")
    assert block.endswith("</kumiho_memory>")
    assert "- [decision] Chose Postgres over DynamoDB #0:" in block
    assert "Kref: kref://cognitive/mem-0" in block
    assert "(yesterday)" in block  # computed age, not left to the model
    assert len(spy.calls) == 1
    # limit, not top_k (top_k is silently ignored); no session_id.
    _, args = spy.calls[0]
    assert args["limit"] == 5
    assert args["recall_mode"] == "summarized"
    assert "top_k" not in args and "session_id" not in args
    assert "postgres" in args["query"].lower()


def test_content_sha12_is_stable_across_runs(tmp_path, monkeypatch):
    spy = _Spy(_payload())
    mod = _prepare(tmp_path, monkeypatch, spy=spy)
    assert mod.main() == 0
    first = _recall(tmp_path)["content_sha12"]

    # Age out the debounce window so the second run really re-fetches.
    monkeypatch.setenv("KUMIHO_REFLEX_MIN_INTERVAL_S", "1")
    time.sleep(1.1)
    assert mod.main() == 0
    assert _recall(tmp_path)["content_sha12"] == first
    assert len(spy.calls) == 2


def test_truncation_lands_on_a_memory_boundary(tmp_path, monkeypatch):
    monkeypatch.setenv("KUMIHO_REFLEX_MAX_CHARS", "600")
    spy = _Spy(_payload(count=8))
    mod = _prepare(tmp_path, monkeypatch, spy=spy)
    assert mod.main() == 0

    cache = _recall(tmp_path)
    assert 0 < cache["count"] < 8
    assert len(cache["block"]) <= 600
    assert cache["block"].endswith("</kumiho_memory>")
    # Every kept memory is whole: as many Kref lines as memories counted.
    assert cache["block"].count("  Kref: ") == cache["count"]
    assert len(cache["krefs"]) == cache["count"]


def test_project_memories_are_split_out_inside_one_envelope(tmp_path, monkeypatch):
    payload = _payload(count=1)
    payload["results"].append({
        "kref": "kref://project/art-1", "type": "artifact",
        "title": "Landing page draft", "summary": "Second revision.",
        "space": "CognitiveMemory/blog-post-jan25", "created_at": "",
    })
    mod = _prepare(tmp_path, monkeypatch, spy=_Spy(payload))
    assert mod.main() == 0

    block = _recall(tmp_path)["block"]
    assert block.count("<kumiho_memory>") == 1
    assert "Creative project items" in block
    assert block.index("kref://cognitive/mem-0") < block.index("kref://project/art-1")


# --------------------------------------------------------------- skip paths

def test_lock_supersedes_a_second_concurrent_run(tmp_path, monkeypatch):
    spy = _Spy(_payload())
    mod = _prepare(tmp_path, monkeypatch, spy=spy)
    (tmp_path / "reflex" / "prefetch.lock").write_text("999999", encoding="utf-8")

    assert mod.main() == 0
    assert "superseded" in _log(tmp_path)
    assert spy.calls == []
    assert _recall(tmp_path) is None
    # The loser must not delete the winner's lock on its way out.
    assert (tmp_path / "reflex" / "prefetch.lock").exists()


def test_debounce_skips_a_near_identical_query(tmp_path, monkeypatch):
    spy = _Spy(_payload())
    mod = _prepare(tmp_path, monkeypatch, spy=spy)
    (tmp_path / "reflex" / "s1.recall.json").write_text(json.dumps({
        "generated_at": int(time.time()), "query": "why did we pick postgres",
        "block": "<kumiho_memory>keep me</kumiho_memory>",
        "content_sha12": "abcdef123456", "count": 1, "krefs": ["kref://x"],
    }), encoding="utf-8")

    assert mod.main() == 0
    assert "debounced" in _log(tmp_path)
    assert spy.calls == []
    assert _recall(tmp_path)["block"] == "<kumiho_memory>keep me</kumiho_memory>"


def test_a_different_question_defeats_the_debounce(tmp_path, monkeypatch):
    spy = _Spy(_payload())
    mod = _prepare(tmp_path, monkeypatch, spy=spy)
    (tmp_path / "reflex" / "s1.recall.json").write_text(json.dumps({
        "generated_at": int(time.time()),
        "query": "unrelated invoicing rounding behaviour question",
        "block": "", "content_sha12": "x", "count": 0, "krefs": [],
    }), encoding="utf-8")

    assert mod.main() == 0
    assert len(spy.calls) == 1


def test_auth_sentinel_skips_before_any_subprocess(tmp_path, monkeypatch):
    spy = _Spy(_payload())
    mod = _prepare(tmp_path, monkeypatch, spy=spy,
                   endpoint="needs-auth.kumiho.invalid:443")
    assert mod.main() == 0
    assert "skip: no auth token" in _log(tmp_path)
    assert spy.calls == []
    assert _recall(tmp_path) is None


def test_missing_venv_skips_before_any_subprocess(tmp_path, monkeypatch):
    spy = _Spy(_payload())
    mod = _prepare(tmp_path, monkeypatch, spy=spy, venv=False)
    assert mod.main() == 0
    assert "skip: venv not provisioned" in _log(tmp_path)
    assert spy.calls == []
    assert _recall(tmp_path) is None


def test_a_venv_without_its_marker_is_logged_as_a_broken_install(tmp_path, monkeypatch):
    """A venv with no marker is not "onboarding has not run yet", it is a
    half-finished install -- and it is invisible everywhere else, because the
    MCP server decides by installed versions and starts fine (#65). This log
    line is the only evidence there is, so it has to say which of the two
    states it found."""
    spy = _Spy(_payload())
    mod = _prepare(tmp_path, monkeypatch, spy=spy)
    (tmp_path / ".installed-packages.txt").unlink()
    assert mod.main() == 0
    assert "install incomplete" in _log(tmp_path)
    assert spy.calls == []
    assert _recall(tmp_path) is None


def test_kill_switch_and_env_gate_stop_everything(tmp_path, monkeypatch):
    spy = _Spy(_payload())
    mod = _prepare(tmp_path, monkeypatch, spy=spy)
    (tmp_path / "reflex.off").write_text("", encoding="utf-8")
    assert mod.main() == 0
    (tmp_path / "reflex.off").unlink()

    monkeypatch.setenv("KUMIHO_REFLEX_PREFETCH", "0")
    assert mod.main() == 0
    assert spy.calls == []
    assert _recall(tmp_path) is None


def test_deduplicated_never_overwrites_a_good_cache(tmp_path, monkeypatch):
    good = {"generated_at": int(time.time()) - 3600,
            "query": "entirely different earlier topic about billing",
            "block": "<kumiho_memory>- [fact] real: content</kumiho_memory>",
            "content_sha12": "0123456789ab", "count": 1, "krefs": ["kref://good"]}
    spy = _Spy({"context": "", "results": [], "source_krefs": [], "count": 0,
                "deduplicated": True, "note": "Duplicate recall"})
    mod = _prepare(tmp_path, monkeypatch, spy=spy)
    (tmp_path / "reflex" / "s1.recall.json").write_text(json.dumps(good), encoding="utf-8")

    assert mod.main() == 0
    assert len(spy.calls) == 1  # it really did call engage
    assert _recall(tmp_path) == good  # ...and kept the old cache byte for byte
    assert "deduplicated" in _log(tmp_path)


def test_unknown_tool_error_latches_and_the_next_run_skips(tmp_path, monkeypatch):
    spy = _Spy(None, "engage rc=1: RuntimeError: unknown tool kumiho_memory_engage")
    mod = _prepare(tmp_path, monkeypatch, spy=spy)
    assert mod.main() == 0
    assert json.loads((tmp_path / "reflex" / "s1.state").read_text(
        encoding="utf-8"))["engage_unsupported"] is True
    # A transport failure also drops the endpoint cache so a moved region heals.
    assert not (tmp_path / "reflex" / "endpoint.json").exists()

    (tmp_path / "reflex" / "endpoint.json").write_text(
        json.dumps({"endpoint": "grpc.kumiho.example:443", "ts": int(time.time())}),
        encoding="utf-8")
    assert mod.main() == 0
    assert len(spy.calls) == 1  # latched: no second attempt
    assert "engage unsupported" in _log(tmp_path)


def test_engage_failure_writes_nothing_and_returns_zero(tmp_path, monkeypatch):
    spy = _Spy(None, "engage subprocess failed: timed out")
    mod = _prepare(tmp_path, monkeypatch, spy=spy)
    assert mod.main() == 0
    assert _recall(tmp_path) is None
    assert "prefetch failed" in _log(tmp_path)


def test_cold_session_start_path_uses_branch_and_directory(tmp_path, monkeypatch):
    spy = _Spy(_payload())
    mod = _prepare(tmp_path, monkeypatch, prompt=None, spy=spy)
    assert mod.main() == 0
    _, args = spy.calls[0]
    assert tmp_path.name in args["query"]


# ------------------------------------------------------------------ encoding

def test_korean_survives_the_real_subprocess_pipe(tmp_path, monkeypatch):
    """cp949 is the default piped encoding here, so the engage hop is the one
    place Korean silently turns into mojibake.  This runs a REAL subprocess
    (the local interpreter, no network) to exercise that pipe end to end."""
    mod = _load()
    original = mod._call_engage
    calls = []

    def through_a_real_pipe(python_path, args):
        calls.append(args)
        return original(sys.executable, args)

    # Echo the query back inside a memory, so the round trip covers both
    # directions: query in on stdin, Korean memory text out on stdout.
    monkeypatch.setattr(mod, "_ENGAGE_SNIPPET",
                        "import json,sys\n"
                        "a=json.load(sys.stdin)\n"
                        "sys.stdout.write(json.dumps({'results':[{"
                        "'kref':'kref://k/1','type':'decision',"
                        "'title':%r,'summary':a['query'],"
                        "'space':'CognitiveMemory/personal','created_at':''}],"
                        "'count':1}, ensure_ascii=True))\n" % KOREAN_TITLE)
    monkeypatch.setattr(mod, "_call_engage", through_a_real_pipe)

    _prepare(tmp_path, monkeypatch, prompt=KOREAN_Q, mod=mod)
    assert mod.main() == 0

    cache = _recall(tmp_path)
    # Python's \w is Unicode-aware, so the Korean query survives tokenization
    # (JavaScript's would have normalized every token to "" -> empty query).
    # Significance is script-aware too, so 2-syllable Korean words survive:
    # openclaw's flat `len > 2` would drop "결제" and empty a short prompt.
    assert "파이프라인" in calls[0]["query"]
    assert "결제" in calls[0]["query"]
    assert KOREAN_TITLE in cache["block"]
    assert cache["query"] in cache["block"]  # echoed back through the real pipe
    assert cache["count"] == 1
    # The stored JSON is pure ASCII (ensure_ascii) yet decodes back identically.
    raw = (tmp_path / "reflex" / "s1.recall.json").read_text(encoding="utf-8")
    assert raw.isascii()
    assert KOREAN_TITLE in json.loads(raw)["block"]


def test_korean_memory_text_round_trips_through_the_cache(tmp_path, monkeypatch):
    payload = {"results": [{
        "kref": "kref://k/2", "type": "preference", "title": KOREAN_TITLE,
        "summary": KOREAN_SUMMARY, "space": "CognitiveMemory/personal",
        "created_at": "", "tags": ["결제", "재시도"],
    }], "count": 1}
    mod = _prepare(tmp_path, monkeypatch, prompt=KOREAN_Q, spy=_Spy(payload))
    assert mod.main() == 0

    block = _recall(tmp_path)["block"]
    assert KOREAN_TITLE in block and KOREAN_SUMMARY in block
    assert "Topics: 결제, 재시도" in block


# --------------------------------------------------------------- unit pieces

def test_build_recall_query_ports_the_openclaw_rules():
    mod = _load()
    # Long message: neither the previous turn nor the assistant excerpt padding
    # changes the fact that the current message leads.
    q = mod._build_recall_query(
        "should we keep the retry budget at three attempts per request", "", "")
    assert q.startswith("should")
    assert "we" not in q.split()  # tokens of length <= 2 are dropped

    # Short message pulls in the previous user turn.
    short = mod._build_recall_query("what about that?", "the invoice rounding bug", "")
    assert "invoice" in short and "rounding" in short

    # ...and a long one does not.
    long_msg = mod._build_recall_query(
        "explain the invoice pipeline retry semantics in detail please",
        "completely unrelated kubernetes ingress", "")
    assert "kubernetes" not in long_msg

    # Assistant excerpt is capped at 20 words.
    assistant = " ".join("w%02d" % i for i in range(40))
    tail = mod._build_recall_query("hm", "", assistant)
    assert "w19" in tail and "w20" not in tail

    # Dedup keeps the original token, not the normalized one, and caps at 200.
    dup = mod._build_recall_query("Postgres postgres, POSTGRES!", "", "")
    assert dup == "Postgres"
    assert len(mod._build_recall_query("alpha " * 200, "", "")) <= 200


def test_human_age_reads_the_way_a_person_would():
    mod = _load()
    now = datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc)

    def age(days, hours=0):
        when = now - timedelta(days=days, hours=hours)
        return mod._human_age(when.isoformat().replace("+00:00", "Z"), now)

    assert age(0, 3) == "earlier today"
    assert age(1) == "yesterday"
    assert age(3) == "3 days ago"
    assert age(9) == "last week"
    assert age(15) == "about two weeks ago"
    assert age(40) == "about a month ago"
    assert age(200) == "about six months ago"
    assert age(800) == "about two years ago"
    assert mod._human_age("") == ""
    assert mod._human_age("not a date") == ""


def test_project_heuristic_matches_openclaw():
    mod = _load()
    assert mod._is_project({"space": "CognitiveMemory/blog-post-jan25"}) is True
    assert mod._is_project({"space": "CognitiveMemory/personal"}) is False
    assert mod._is_project({"space": "CognitiveMemory/work"}) is False
    assert mod._is_project({"space": "CognitiveMemory"}) is False
    assert mod._is_project({}) is False


def test_overlap_drives_the_debounce_the_way_it_reads():
    mod = _load()
    assert mod._overlap("alpha beta gamma", "alpha beta gamma") == 1.0
    assert mod._overlap("alpha beta gamma", "alpha beta delta") < 0.8
    assert mod._overlap("alpha beta gamma delta", "alpha beta gamma delta epsilon") >= 0.8
    assert mod._overlap("alpha", "") == 0.0


def test_short_korean_words_survive_the_significance_filter():
    """openclaw's flat `len > 2` empties a short Korean prompt entirely.
    가격/결제/오류 are exactly the terms a Korean user searches on."""
    w = _load()
    assert w._build_recall_query("결제 오류", "", "") == "결제 오류"
    assert "가격" in w._build_recall_query("가격 정책 변경", "", "")
    for tok in ("가격", "결제", "오류", "決済", "データ"):
        assert w._is_significant(tok), tok


def test_latin_stopwords_are_still_dropped():
    """The CJK relaxation must not weaken Latin filtering."""
    w = _load()
    assert w._build_recall_query("is it ok to do a b c", "", "") == ""
    for tok in ("a", "of", "is", "to"):
        assert not w._is_significant(tok), tok


def test_module_not_found_is_transient_and_never_latches():
    """C2 regression. `pip install --upgrade` removes the old kumiho-memory
    distribution before writing the new one, and _venv_ready cannot see that
    window. A prefetch landing mid-reinstall must not latch the reflex dark for
    the rest of the session -- and the upgrade path is how this ships."""
    w = _load()
    assert w._is_transient_error("ModuleNotFoundError: No module named 'kumiho_memory'")
    assert not w._is_unknown_tool_error("No module named 'kumiho_memory'")
    assert not w._is_unknown_tool_error("cannot import name 'tool_memory_engage'")
    # a genuine backend capability gap still latches
    assert w._is_unknown_tool_error("Unknown tool: kumiho_memory_engage")


def test_child_processes_are_spawned_without_a_console_window():
    """C1 regression. The worker runs DETACHED_PROCESS (no console), so a
    console-subsystem child makes Windows allocate a NEW VISIBLE one -- a black
    window on every turn. capture_output does not suppress it."""
    import os as _os
    w = _load()
    expected = 0x08000000 if _os.name == "nt" else 0
    assert w._NO_WINDOW == expected
    src = (SCRIPTS / "reflex_prefetch_worker.py").read_text(encoding="utf-8")
    # every subprocess.run in this worker must carry the flag
    assert src.count("subprocess.run(") == src.count("creationflags=_NO_WINDOW")


if __name__ == "__main__":
    sys.exit(subprocess.call([sys.executable, "-m", "pytest", __file__, "-q"]))
