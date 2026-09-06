# -*- coding: utf-8 -*-
"""Offline checks for the short-turn fast-path gate (kumiho-plugins#97).

Deterministic, no network/model: the policy surfaces (Codex SKILL.md, AGENTS.md,
Claude SKILL.md) carry a consistent gate with the four modes, the mandatory
exceptions, and an explicit no-buffer-only-reflect rule on CONTEXT_ONLY; the
routing fixtures are well-formed and cover the required matrix; and the compacted
Codex policy is measured against the proposed budget. The 60+ case agent
behavioral evaluation (live, pinned model, 3 repeats/host) is the separate,
open acceptance criterion this fixture seeds — it is not run here.
"""
import importlib.util
import json
import re
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
PLUGIN = HERE.parent
REPO = PLUGIN.parent
CODEX_SKILL = PLUGIN / "skills" / "kumiho-memory" / "SKILL.md"
CODEX_AGENTS = PLUGIN / "AGENTS.md"
CLAUDE_SKILL = REPO / "claude" / "skills" / "kumiho-memory" / "SKILL.md"
FIXTURES = HERE / "routing_fixtures.json"
CAPTURE_REF = PLUGIN / "skills" / "kumiho-memory" / "references" / "capture-and-decisions.md"

MODES = ["CONTEXT_ONLY", "READ_ONLY", "WRITE_ONLY", "READ_WRITE"]


def _budget():
    spec = importlib.util.spec_from_file_location("policy_token_budget", HERE / "policy_token_budget.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Policy surfaces carry a consistent gate
# ---------------------------------------------------------------------------


def test_codex_skill_defines_the_four_mode_gate():
    text = CODEX_SKILL.read_text(encoding="utf-8")
    assert "turn gate" in text.lower()
    for mode in MODES:
        assert mode in text, f"Codex SKILL.md missing mode {mode}"
    assert "needs_recall" in text and "needs_capture" in text
    # The core behavioral fix: CONTEXT_ONLY does not even buffer-only reflect.
    assert "buffer-only reflect" in text
    # Routing must not add a classifier call.
    assert "never a separate classifier call" in text or "never a separate\nclassifier call" in text


def test_codex_skill_keeps_mandatory_exceptions_inline():
    text = CODEX_SKILL.read_text(encoding="utf-8").lower()
    for needle in ("secret", "privacy", "bootstrap", "correction", "forget",
                   "decision memory", "question you asked"):
        assert needle in text, f"Codex SKILL.md missing mandatory exception: {needle}"


def test_agents_and_claude_align_with_the_gate():
    agents = CODEX_AGENTS.read_text(encoding="utf-8")
    assert "turn gate" in agents.lower()
    assert "CONTEXT_ONLY" in agents
    assert "not even a buffer-only" in agents.lower()
    # AGENTS.md must not duplicate the full protocol — it points at the skill.
    assert "skills/kumiho-memory/SKILL.md" in agents

    claude = CLAUDE_SKILL.read_text(encoding="utf-8")
    assert "CONTEXT_ONLY" in claude
    assert "skip reflect entirely" in claude
    # The old unconditional trivial-reflect instruction is gone.
    assert "For trivial exchanges, call reflect without captures to buffer the response only." not in claude


def test_moved_detail_lives_in_the_reference():
    ref = CAPTURE_REF.read_text(encoding="utf-8")
    for needle in ("Engage", "Reflect", "space_hint", "decompose",
                   "supersedes", "Decision Memory", "every answer"):
        assert needle.lower() in ref.lower(), f"capture reference missing {needle}"
    # And the main skill links to it.
    assert "capture-and-decisions.md" in CODEX_SKILL.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Token budget (proposed <=1000; measured + reported)
# ---------------------------------------------------------------------------


def test_codex_skill_is_substantially_compacted():
    # The compaction must be real. The pre-#97 skill measured ~2,900 proxy
    # tokens; assert the main doc is well under half that. The <=1,000 proposal
    # is reported (below) as the remaining target — reaching it would require
    # moving mandatory exceptions out, which the issue forbids.
    tokens = _budget().measure(CODEX_SKILL)
    assert tokens <= 1400, f"Codex SKILL.md is {tokens} proxy-tokens; expected <=1400 after compaction"


def test_budget_tool_flags_overage():
    mod = _budget()
    assert mod.main(["--max", "1", str(CODEX_SKILL)]) == 1  # any real doc exceeds 1
    assert mod.main(["--max", "100000", str(CODEX_SKILL)]) == 0


# ---------------------------------------------------------------------------
# Routing fixtures are well-formed and cover the matrix
# ---------------------------------------------------------------------------


def test_routing_fixtures_are_well_formed():
    data = json.loads(FIXTURES.read_text(encoding="utf-8"))
    assert data["meta"]["version"]
    assert data["meta"]["modes"] == MODES
    cases = data["cases"]
    ids = [c["id"] for c in cases]
    assert len(ids) == len(set(ids)), "duplicate fixture ids"
    langs = set()
    modes_seen = set()
    for c in cases:
        assert c["lang"] in ("en", "ko")
        assert c["context"] and c["input"] and c["why"]
        assert c["expected_mode"] in MODES
        assert isinstance(c["critical"], list)
        langs.add(c["lang"])
        modes_seen.add(c["expected_mode"])
    assert langs == {"en", "ko"}, "fixtures must include Korean and English"
    assert modes_seen == set(MODES), f"fixtures must cover every mode; missing {set(MODES) - modes_seen}"


def test_required_matrix_rows_are_present():
    data = json.loads(FIXTURES.read_text(encoding="utf-8"))
    by_id = {c["id"]: c for c in data["cases"]}
    # A few load-bearing rows from the issue's required matrix.
    assert by_id["ack_thanks_ko"]["expected_mode"] == "CONTEXT_ONLY"
    assert by_id["personalization_followup_ko"]["expected_mode"] == "CONTEXT_ONLY"
    assert by_id["accept_pending_choice_ko"]["expected_mode"] == "WRITE_ONLY"
    assert "asked_question" in by_id["accept_pending_choice_ko"]["critical"]
    # Explicit remember with no referent must clarify, never fabricate.
    assert by_id["explicit_remember_no_referent_ko"]["expected_mode"] == "CONTEXT_ONLY"
    # A correction is always flagged critical.
    for cid in ("correct_known_choice_ko", "ambiguous_correction_ko"):
        assert "correction" in by_id[cid]["critical"]
    # Privacy/forget rows carry the privacy flag.
    assert "privacy" in by_id["privacy_forget_item_ko"]["critical"]


def test_fifty_acknowledgements_case_is_context_only():
    data = json.loads(FIXTURES.read_text(encoding="utf-8"))
    case = next(c for c in data["cases"] if c["id"] == "fifty_acks_no_pending_en")
    assert case["expected_mode"] == "CONTEXT_ONLY"
