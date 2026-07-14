#!/usr/bin/env python3
"""History Backfill ingest runner — deterministic staging replay (stage 2).

Executed INSIDE the kumiho venv by backfill_ingest.py (which hydrates auth /
endpoint env and pins the keyless environment before spawning us). No LLM is
called here, ever: captures were already distilled by the host agent at the
extract stage, so ingest is pure replay through the documented write path.

Per docs/BACKFILL_DESIGN.md Stage 2:

* feature gate  — reflect's capture schema must expose ``event_date``
  (feature-detect, not version arithmetic)
* consent       — renders EVERY capture and decompose triple; the caller
  confirms with --yes after the user reviewed the payload (--dry-run prints
  the same and exits)
* screening     — per capture: ``reject_credentials`` (hit -> skip THAT
  capture) then ``anonymize_summary`` (mask residual pattern-PII); the same
  two steps run over every decompose entity / fact / relation (hit -> drop
  the triple, not the session)
* replay        — sessions newest -> oldest (earliest mention ends up the
  last-stacked revision, so the surfaced ``event_date`` is the origin);
  one ``tool_memory_reflect`` call per capture with ``discover_edges: false``
  so per-capture krefs and resume marks are exact
* decompose     — anchored to the summary capture's kref; skipped gracefully
  when the result reports the ontology is disabled
* marking       — staging is rewritten atomically after every capture, so an
  interrupted run resumes at the exact capture

One reflect call per capture deviates from the design's two-call sketch on
purpose: reflect's ``stored_krefs`` only appends successes, so a mid-batch
store failure would leave kref->capture mapping ambiguous. Serial-per-capture
keeps marking exact; kumiho-SDKs#71 (batch-aware reflect) supersedes this
loop wholesale once BatchCreateRevisions lands.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

SKIP_MARK = "skipped:credential"


def _log(msg: str) -> None:
    print(f"[backfill-ingest] {msg}", flush=True)


def _warn(msg: str) -> None:
    print(f"[backfill-ingest] WARNING: {msg}", file=sys.stderr, flush=True)


def load_staging(path: Path) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def save_staging(staging: dict, path: Path) -> None:
    tmp = path.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(staging, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def feature_gate() -> str:
    """Empty string when reflect supports event_date, else the refusal message."""
    from kumiho_memory import mcp_tools

    for tool in mcp_tools.MEMORY_TOOLS:
        if tool.get("name") == "kumiho_memory_reflect":
            props = (tool.get("inputSchema", {}).get("properties", {})
                     .get("captures", {}).get("items", {}).get("properties", {}))
            if "event_date" in props:
                return ""
            break
    return ("kumiho-memory too old: reflect captures lack event_date "
            "(needs >= 0.16.2) — re-run /kumiho-onboard to upgrade")


def screen_capture(redactor, errcls, cap: dict) -> dict | None:
    """Screened copy of a capture, or None when a credential shape is inside."""
    text = f"{cap.get('title', '')}\n{cap.get('content', '')}"
    try:
        redactor.reject_credentials(text)
    except errcls:
        return None
    return dict(
        cap,
        title=redactor.anonymize_summary(cap.get("title", "")),
        content=redactor.anonymize_summary(cap.get("content", "")),
    )


def screen_decompose(redactor, errcls, dec: dict) -> tuple[dict, int]:
    """Screened copy of decompose triples + count of dropped items."""
    dropped = 0

    def clean(texts: list[str]) -> list[str] | None:
        try:
            redactor.reject_credentials("\n".join(texts))
        except errcls:
            return None
        return [redactor.anonymize_summary(t) for t in texts]

    out: dict = {}
    for ent in dec.get("entities") or []:
        cleaned = clean([str(ent.get("name", ""))])
        if cleaned is None:
            dropped += 1
            continue
        out.setdefault("entities", []).append(dict(ent, name=cleaned[0]))
    for fact in dec.get("facts") or []:
        cleaned = clean([str(fact.get("statement", ""))])
        if cleaned is None:
            dropped += 1
            continue
        out.setdefault("facts", []).append(dict(fact, statement=cleaned[0]))
    for rel in dec.get("relations") or []:
        parts = [str(rel.get(k, "")) for k in ("subject", "predicate", "object")]
        cleaned = clean(parts)
        if cleaned is None:
            dropped += 1
            continue
        out.setdefault("relations", []).append(
            dict(rel, subject=cleaned[0], predicate=cleaned[1], object=cleaned[2]))
    return out, dropped


def pending_sessions(staging: dict) -> list[dict]:
    """Extracted sessions with un-ingested captures, newest ended first."""
    todo = [s for s in staging["sessions"]
            if s.get("status") == "extracted"
            and any(not c.get("ingested_kref") for c in s.get("captures") or [])]
    return sorted(todo, key=lambda s: s.get("ended_at", ""), reverse=True)


def render_payload(sessions: list[dict]) -> None:
    print("\n=== History Backfill — FULL upload payload (review before confirming) ===")
    for sess in sessions:
        print(f"\n--- session {sess['source_session_id']}  "
              f"({sess.get('ended_at', '')[:10]}, {sess.get('project_dir', '')})")
        for cap in sess.get("captures") or []:
            state = cap.get("ingested_kref") or "pending"
            print(f"  [{cap.get('type')}] {cap.get('title')}  "
                  f"(event_date={cap.get('event_date')}, {state})")
            for line in str(cap.get("content", "")).splitlines():
                print(f"      {line}")
        dec = sess.get("decompose") or {}
        for kind in ("entities", "facts", "relations"):
            for item in dec.get(kind) or []:
                print(f"  triple/{kind}: {json.dumps(item, ensure_ascii=False)}")
    total = sum(len(s.get("captures") or []) for s in sessions)
    print(f"\n=== {len(sessions)} sessions, {total} captures ===\n")


def ingest_session(sess: dict, staging: dict, staging_file: Path,
                   reflect, decompose, redactor, errcls) -> dict:
    """Replay one session; returns counters. Staging saved after every capture."""
    stats = {"stored": 0, "screened": 0, "already": 0, "dropped_triples": 0}
    sid = f"backfill:{sess['source_session_id']}"
    captures = sess.get("captures") or []
    digest = captures[0].get("content", "") if captures else ""

    for cap in captures:
        if cap.get("ingested_kref"):
            stats["already"] += 1
            continue
        screened = screen_capture(redactor, errcls, cap)
        if screened is None:
            cap["ingested_kref"] = SKIP_MARK
            stats["screened"] += 1
            save_staging(staging, staging_file)
            _warn(f"capture screened out (credential shape): {cap.get('title', '')[:60]!r}")
            continue
        response = digest if screened.get("type") == "summary" else screened.get("title", "")
        result = reflect({
            "session_id": sid,
            "response": response,
            "captures": [{
                "type": screened["type"],
                "title": screened["title"],
                "content": screened["content"],
                "event_date": screened.get("event_date", ""),
                "tags": screened.get("tags") or [],
            }],
            "discover_edges": False,
        })
        if result.get("dropped_event_dates"):
            _warn(f"STAGING BUG: reflect dropped event_date {result['dropped_event_dates']} "
                  f"for {cap.get('title', '')[:60]!r} — staging validation should have caught this")
        krefs = result.get("stored_krefs") or []
        if not krefs:
            raise RuntimeError(
                f"reflect stored nothing for capture {cap.get('title', '')[:60]!r} "
                f"(session {sess['source_session_id']}) — aborting so resume can retry")
        cap["ingested_kref"] = krefs[0]
        stats["stored"] += 1
        save_staging(staging, staging_file)

    anchor = captures[0].get("ingested_kref", "") if captures else ""
    dec = sess.get("decompose") or {}
    if dec and anchor and anchor != SKIP_MARK and not sess.get("decomposed"):
        screened_dec, dropped = screen_decompose(redactor, errcls, dec)
        stats["dropped_triples"] = dropped
        if any(screened_dec.get(k) for k in ("entities", "facts", "relations")):
            result = decompose({"kref": anchor, **screened_dec}) or {}
            errors = result.get("errors") or []
            if any("ontology" in str(e) for e in errors):
                _log("decompose skipped: ontology disabled on this backend")
            elif errors:
                _warn(f"decompose reported errors: {errors}")
        sess["decomposed"] = True

    if all(c.get("ingested_kref") for c in captures):
        sess["status"] = "ingested"
        sess["ingested_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    save_staging(staging, staging_file)
    return stats


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--staging", default="")
    parser.add_argument("--yes", action="store_true",
                        help="confirm upload (the payload was already reviewed)")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=0, help="max sessions this run")
    args = parser.parse_args()

    gate_msg = feature_gate()
    if gate_msg:
        _warn(gate_msg)
        return 3

    staging_file = Path(args.staging).expanduser() if args.staging else (
        Path(os.getenv("KUMIHO_BACKFILL_HOME", str(Path.home() / ".kumiho" / "backfill")))
        / "staging.json")
    if not staging_file.is_file():
        _warn(f"no staging file at {staging_file} — run the extract stage first")
        return 1
    staging = load_staging(staging_file)
    todo = pending_sessions(staging)
    if args.limit > 0:
        todo = todo[: args.limit]
    if not todo:
        _log("nothing to ingest — all extracted sessions are already ingested")
        return 0

    render_payload(todo)
    if args.dry_run:
        _log("dry run — nothing uploaded")
        return 0
    if not args.yes:
        _warn("refusing to upload without --yes (review the payload above, "
              "or the staging file itself, then re-run with --yes)")
        return 1

    from kumiho_memory import mcp_tools
    from kumiho_memory.privacy import CredentialDetectedError, PIIRedactor
    reflect = mcp_tools.tool_memory_reflect
    decompose = mcp_tools.MEMORY_TOOL_HANDLERS.get(
        "kumiho_memory_decompose", lambda _args: {"errors": ["decompose tool unavailable"]})
    redactor = PIIRedactor()

    totals = {"stored": 0, "screened": 0, "already": 0, "dropped_triples": 0, "sessions": 0}
    for sess in todo:
        _log(f"session {sess['source_session_id']} ({sess.get('ended_at', '')[:10]})")
        stats = ingest_session(sess, staging, staging_file, reflect, decompose,
                               redactor, CredentialDetectedError)
        for key, val in stats.items():
            totals[key] += val
        totals["sessions"] += 1

    _log(f"done: {totals['sessions']} sessions, {totals['stored']} captures stored, "
         f"{totals['screened']} screened out, {totals['already']} already ingested, "
         f"{totals['dropped_triples']} triples dropped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
