#!/usr/bin/env python3
"""Offline unit checks for backfill/ingest_runner.py (History Backfill stage 2).

kumiho_memory is faked in sys.modules — no venv, no network, no real backend.
Covers the review-mandated behaviors: keyless flags on every call, event_date
passthrough, per-capture screening and resume, newest->oldest replay,
decompose anchoring + ontology-disabled skip, feature gate, consent refusal.

Usage (from claude/scripts/):
    python test_backfill_ingest.py
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import re
import sys
import tempfile
import types
from pathlib import Path


def _check(name: str, cond: bool) -> bool:
    print(f"  {'ok' if cond else 'FAIL'}: {name}")
    return bool(cond)


# --- fake kumiho_memory ----------------------------------------------------

class FakeCredentialError(ValueError):
    pass


class FakeRedactor:
    CRED_RE = re.compile(r"\bsk-[A-Za-z0-9]{20,}\b")
    EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")

    def reject_credentials(self, text: str) -> None:
        if self.CRED_RE.search(text):
            raise FakeCredentialError("credential")

    def anonymize_summary(self, text: str) -> str:
        return self.EMAIL_RE.sub("[email]", text)


def reflect_schema(with_event_date: bool) -> list[dict]:
    props = {"type": {}, "title": {}, "content": {}, "tags": {}, "space_hint": {}}
    if with_event_date:
        props["event_date"] = {}
    return [{
        "name": "kumiho_memory_reflect",
        "inputSchema": {"properties": {"captures": {"items": {"properties": props}}}},
    }]


def install_fake(reflect_fn, decompose_fn, with_event_date: bool = True):
    pkg = types.ModuleType("kumiho_memory")
    mcp_tools = types.ModuleType("kumiho_memory.mcp_tools")
    mcp_tools.MEMORY_TOOLS = reflect_schema(with_event_date)
    mcp_tools.tool_memory_reflect = reflect_fn
    mcp_tools.MEMORY_TOOL_HANDLERS = {"kumiho_memory_decompose": decompose_fn}
    privacy = types.ModuleType("kumiho_memory.privacy")
    privacy.PIIRedactor = FakeRedactor
    privacy.CredentialDetectedError = FakeCredentialError
    pkg.mcp_tools = mcp_tools
    pkg.privacy = privacy
    sys.modules["kumiho_memory"] = pkg
    sys.modules["kumiho_memory.mcp_tools"] = mcp_tools
    sys.modules["kumiho_memory.privacy"] = privacy


def load_runner():
    path = Path(__file__).resolve().parent / "backfill" / "ingest_runner.py"
    spec = importlib.util.spec_from_file_location("ingest_runner", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_staging(tmp: Path) -> Path:
    def cap(ctype, title, content, event_date, kref=""):
        return {"type": ctype, "title": title, "content": content,
                "event_date": event_date, "tags": ["backfill", "source:claude-code"],
                "content_sha256": "0" * 64, "ingested_kref": kref}

    staging = {
        "schema": 1, "generated_at": "", "profile_proposal": {},
        "sessions": [
            {"source": "claude-code", "source_session_id": "march",
             "source_path": "/x/march.jsonl", "project_dir": "/p", "title": "t",
             "started_at": "2026-03-01T00:00:00Z", "ended_at": "2026-03-14T00:00:00Z",
             "human_msgs": 5, "score": 0.5, "packet_sha256": "", "status": "extracted",
             "skip_reason": "",
             "captures": [
                 cap("summary", "March digest", "Digest of the March session.", "2026-03-14"),
                 cap("decision", "Chose bge-m3 on 2026-03-14",
                     "bge-m3 over OpenAI embeddings. Contact dev@example.com.", "2026-03-14"),
                 cap("fact", "Key pasted", "the key is sk-" + "b" * 24, "2026-03-14"),
             ],
             "decompose": {
                 "entities": [{"name": "bge-m3", "type": "technology"},
                              {"name": "sk-" + "c" * 24, "type": "oops"}],
                 "facts": [{"statement": "ping dev@example.com about bge-m3",
                            "about": ["bge-m3"]}],
                 "relations": [{"subject": "proj", "predicate": "uses", "object": "bge-m3"}],
             }},
            {"source": "claude-code", "source_session_id": "may",
             "source_path": "/x/may.jsonl", "project_dir": "/p", "title": "t2",
             "started_at": "2026-05-01T00:00:00Z", "ended_at": "2026-05-02T00:00:00Z",
             "human_msgs": 3, "score": 0.8, "packet_sha256": "", "status": "extracted",
             "skip_reason": "",
             "captures": [cap("summary", "May digest", "Digest of May.", "2026-05-02")],
             "decompose": {}},
        ],
    }
    path = tmp / "staging.json"
    path.write_text(json.dumps(staging), encoding="utf-8")
    return path


class ReflectRecorder:
    def __init__(self, fail_on_call: int = 0, dropped: bool = False):
        self.calls: list[dict] = []
        self.fail_on_call = fail_on_call
        self.dropped = dropped

    def __call__(self, args: dict) -> dict:
        self.calls.append(json.loads(json.dumps(args)))
        if self.fail_on_call and len(self.calls) == self.fail_on_call:
            raise RuntimeError("simulated backend outage")
        result = {"buffered": True, "captures_stored": 1, "edges_discovered": 0,
                  "stored_krefs": [f"kref://cap/{len(self.calls)}"]}
        if self.dropped:
            result["dropped_event_dates"] = [{"title": "x", "event_date": "bad"}]
        return result


def test_feature_gate() -> bool:
    install_fake(lambda a: {}, lambda a: {}, with_event_date=True)
    runner = load_runner()
    ok = _check("gate passes with event_date", runner.feature_gate() == "")
    install_fake(lambda a: {}, lambda a: {}, with_event_date=False)
    runner2 = load_runner()
    ok &= _check("gate refuses without event_date",
                 "0.16.2" in runner2.feature_gate())
    return ok


def test_screening() -> bool:
    install_fake(lambda a: {}, lambda a: {})
    runner = load_runner()
    red = FakeRedactor()
    cred = {"type": "fact", "title": "k", "content": "sk-" + "b" * 24}
    ok = _check("credential capture -> None",
                runner.screen_capture(red, FakeCredentialError, cred) is None)
    pii = {"type": "fact", "title": "mail dev@example.com", "content": "ok"}
    screened = runner.screen_capture(red, FakeCredentialError, pii)
    ok &= _check("PII masked in screened copy", screened["title"] == "mail [email]")
    ok &= _check("original capture unmutated", pii["title"] == "mail dev@example.com")

    dec = {"entities": [{"name": "sk-" + "c" * 24}, {"name": "bge-m3"}],
           "facts": [{"statement": "ping dev@example.com"}],
           "relations": [{"subject": "a", "predicate": "uses", "object": "b"}]}
    screened_dec, dropped = runner.screen_decompose(red, FakeCredentialError, dec)
    ok &= _check("credential entity dropped, others kept",
                 dropped == 1 and [e["name"] for e in screened_dec["entities"]] == ["bge-m3"])
    ok &= _check("fact PII masked",
                 screened_dec["facts"][0]["statement"] == "ping [email]")
    ok &= _check("relations intact", len(screened_dec["relations"]) == 1)
    return ok


def _run_full(tmp: Path, recorder: ReflectRecorder, decompose_calls: list,
              decompose_result=None):
    install_fake(recorder, lambda a: (decompose_calls.append(a),
                                      decompose_result or {})[1])
    runner = load_runner()
    staging_file = tmp / "staging.json"
    staging = runner.load_staging(staging_file)
    stats = []
    for sess in runner.pending_sessions(staging):
        stats.append(runner.ingest_session(sess, staging, staging_file, recorder,
                                           lambda a: (decompose_calls.append(a),
                                                      decompose_result or {})[1],
                                           FakeRedactor(), FakeCredentialError))
    return runner, staging, stats


def test_replay_semantics() -> bool:
    tmp = Path(tempfile.mkdtemp(prefix="backfill-ing-test-"))
    make_staging(tmp)
    recorder = ReflectRecorder()
    decompose_calls: list = []
    runner, staging, _ = _run_full(tmp, recorder, decompose_calls,
                                   {"anchored": True})

    ok = _check("newest session replayed first",
                recorder.calls[0]["session_id"] == "backfill:may")
    ok &= _check("one reflect call per stored capture (1 may + 2 march)",
                 len(recorder.calls) == 3)
    ok &= _check("discover_edges false on every call",
                 all(c["discover_edges"] is False for c in recorder.calls))
    ok &= _check("event_date passed through on every capture",
                 all(c["captures"][0]["event_date"] for c in recorder.calls))
    ok &= _check("summary uses digest as response, typed uses title",
                 recorder.calls[0]["response"] == "Digest of May."
                 and recorder.calls[2]["response"].startswith("Chose bge-m3"))
    ok &= _check("PII masked in uploaded capture",
                 "[email]" in recorder.calls[2]["captures"][0]["content"])

    march = next(s for s in staging["sessions"] if s["source_session_id"] == "march")
    ok &= _check("credential capture marked skipped, session still ingested",
                 march["captures"][2]["ingested_kref"] == runner.SKIP_MARK
                 and march["status"] == "ingested")
    ok &= _check("decompose anchored to summary kref",
                 len(decompose_calls) == 1
                 and decompose_calls[0]["kref"] == march["captures"][0]["ingested_kref"])
    ok &= _check("credential entity dropped from decompose payload",
                 [e["name"] for e in decompose_calls[0]["entities"]] == ["bge-m3"])
    persisted = json.loads((tmp / "staging.json").read_text())
    ok &= _check("marks persisted to disk",
                 all(s["status"] == "ingested" for s in persisted["sessions"]))
    return ok


def test_resume_after_crash() -> bool:
    tmp = Path(tempfile.mkdtemp(prefix="backfill-ing-test-"))
    make_staging(tmp)
    recorder = ReflectRecorder(fail_on_call=2)  # may ok, march summary fails
    decompose_calls: list = []
    try:
        _run_full(tmp, recorder, decompose_calls)
        crashed = False
    except RuntimeError:
        crashed = True
    persisted = json.loads((tmp / "staging.json").read_text())
    may = next(s for s in persisted["sessions"] if s["source_session_id"] == "may")
    march = next(s for s in persisted["sessions"] if s["source_session_id"] == "march")
    ok = _check("crash propagated for retry", crashed)
    ok &= _check("completed session persisted before crash",
                 may["status"] == "ingested" and may["captures"][0]["ingested_kref"])
    ok &= _check("crashed session still pending",
                 march["status"] == "extracted"
                 and not march["captures"][0]["ingested_kref"])

    recorder2 = ReflectRecorder()
    runner, staging, _ = _run_full(tmp, recorder2, [])
    ok &= _check("resume ingests only the pending session",
                 all(c["session_id"] == "backfill:march" for c in recorder2.calls)
                 and len(recorder2.calls) == 2)
    return ok


def test_dropped_event_dates_warns() -> bool:
    tmp = Path(tempfile.mkdtemp(prefix="backfill-ing-test-"))
    make_staging(tmp)
    recorder = ReflectRecorder(dropped=True)
    stderr = io.StringIO()
    with contextlib.redirect_stderr(stderr):
        _run_full(tmp, recorder, [])
    return _check("dropped_event_dates logged as STAGING BUG",
                  "STAGING BUG" in stderr.getvalue())


def test_ontology_disabled_skip() -> bool:
    tmp = Path(tempfile.mkdtemp(prefix="backfill-ing-test-"))
    make_staging(tmp)
    recorder = ReflectRecorder()
    stderr = io.StringIO()
    with contextlib.redirect_stderr(stderr):
        _, staging, _ = _run_full(tmp, recorder, [],
                                  {"errors": ["ontology is disabled (set KUMIHO_MEMORY_ONTOLOGY=1)"]})
    march = next(s for s in staging["sessions"] if s["source_session_id"] == "march")
    ok = _check("ontology-disabled tolerated, session ingested",
                march["status"] == "ingested")
    ok &= _check("ontology skip not reported as decompose error",
                 "decompose reported errors" not in stderr.getvalue())
    return ok


def test_response_is_screened() -> bool:
    tmp = Path(tempfile.mkdtemp(prefix="backfill-ing-test-"))
    staging_file = make_staging(tmp)
    staging = json.loads(staging_file.read_text())
    may = next(s for s in staging["sessions"] if s["source_session_id"] == "may")
    may["captures"][0]["content"] = "Digest mentioning dev@example.com in May."
    staging_file.write_text(json.dumps(staging))
    recorder = ReflectRecorder()
    _run_full(tmp, recorder, [])
    return _check("summary response uses the ANONYMIZED digest",
                  recorder.calls[0]["response"] == "Digest mentioning [email] in May.")


def test_decompose_resume_window() -> bool:
    """Crash between the last capture mark and the decompose/status flip."""
    tmp = Path(tempfile.mkdtemp(prefix="backfill-ing-test-"))
    staging_file = make_staging(tmp)
    staging = json.loads(staging_file.read_text())
    march = next(s for s in staging["sessions"] if s["source_session_id"] == "march")
    for cap in march["captures"]:
        cap["ingested_kref"] = "kref://done/1"   # all marked...
    # ...but status never flipped and decompose never ran (the crash window).
    staging_file.write_text(json.dumps(staging))
    recorder = ReflectRecorder()
    decompose_calls: list = []
    runner, staging2, stats = _run_full(tmp, recorder, decompose_calls)
    march2 = next(s for s in staging2["sessions"] if s["source_session_id"] == "march")
    ok = _check("all-marked session still revisited",
                any(s["already"] == 3 for s in stats))
    ok &= _check("decompose completed on resume",
                 len(decompose_calls) == 1
                 and decompose_calls[0]["kref"] == "kref://done/1")
    ok &= _check("status finally flipped to ingested", march2["status"] == "ingested")
    ok &= _check("no capture re-uploaded for the marked session",
                 all(c["session_id"] != "backfill:march" for c in recorder.calls))
    return ok


def test_wrapper_env_pinning() -> bool:
    path = Path(__file__).resolve().parent / "backfill_ingest.py"
    spec = importlib.util.spec_from_file_location("backfill_ingest_mod", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    env = {"KUMIHO_AUTO_ASSESS": "1", "KUMIHO_GRAPH_AUGMENTED_RECALL": "1",
           "KUMIHO_LLM_API_KEY": "real-key", "ANTHROPIC_API_KEY": "real-key",
           "KUMIHO_LLM_BASE_URL": "https://api.openai.com/v1"}
    module._pin_keyless_env(env)
    ok = _check("assessor and graph-augmented recall pinned off",
                env["KUMIHO_AUTO_ASSESS"] == "0"
                and env["KUMIHO_GRAPH_AUGMENTED_RECALL"] == "0")
    ok &= _check("LLM endpoint forced to the dead port",
                 env["KUMIHO_LLM_BASE_URL"] == "http://127.0.0.1:9/v1")
    ok &= _check("provider keys scrubbed",
                 "KUMIHO_LLM_API_KEY" not in env and "ANTHROPIC_API_KEY" not in env)
    return ok


def test_main_full_run_writes_log() -> bool:
    tmp = Path(tempfile.mkdtemp(prefix="backfill-ing-test-"))
    staging_file = make_staging(tmp)
    recorder = ReflectRecorder()
    install_fake(recorder, lambda a: {})
    runner = load_runner()
    log_file = tmp / "backfill-ingest.log"
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        sys.argv = ["ingest_runner.py", "--staging", str(staging_file), "--yes",
                    "--log-file", str(log_file)]
        rc = runner.main()
    body = log_file.read_text() if log_file.exists() else ""
    ok = _check("full run exits 0 and uploads", rc == 0 and len(recorder.calls) == 3)
    ok &= _check("log file written with summary + krefs",
                 "captures stored" in body and "krefs=kref://" in body)
    return ok


def test_main_consent_paths() -> bool:
    tmp = Path(tempfile.mkdtemp(prefix="backfill-ing-test-"))
    staging_file = make_staging(tmp)
    recorder = ReflectRecorder()
    install_fake(recorder, lambda a: {})
    runner = load_runner()

    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        sys.argv = ["ingest_runner.py", "--staging", str(staging_file), "--dry-run"]
        rc_dry = runner.main()
        sys.argv = ["ingest_runner.py", "--staging", str(staging_file)]
        rc_noyes = runner.main()
    ok = _check("dry run exits 0 and uploads nothing",
                rc_dry == 0 and recorder.calls == [])
    ok &= _check("payload rendered in full (content + triples)",
                 "Digest of May." in out.getvalue() and "triple/entities" in out.getvalue())
    ok &= _check("without --yes refuses with rc 1",
                 rc_noyes == 1 and recorder.calls == [] and "--yes" in err.getvalue())
    return ok


def main() -> int:
    os.environ.pop("KUMIHO_BACKFILL_HOME", None)
    tests = (
        ("feature_gate", test_feature_gate),
        ("screening", test_screening),
        ("replay_semantics", test_replay_semantics),
        ("resume_after_crash", test_resume_after_crash),
        ("dropped_event_dates_warns", test_dropped_event_dates_warns),
        ("ontology_disabled_skip", test_ontology_disabled_skip),
        ("response_is_screened", test_response_is_screened),
        ("decompose_resume_window", test_decompose_resume_window),
        ("wrapper_env_pinning", test_wrapper_env_pinning),
        ("main_full_run_writes_log", test_main_full_run_writes_log),
        ("main_consent_paths", test_main_consent_paths),
    )
    all_ok = True
    for name, fn in tests:
        print(f"\n=== {name} ===")
        all_ok &= fn()
    print("\n" + ("PASS: all backfill ingest checks passed"
                  if all_ok else "FAIL: some backfill ingest checks failed"))
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
