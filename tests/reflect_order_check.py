"""Shared check for the reflect-ordering rule: no instruction may require a tool
call AFTER the final answer.

Observed 2026-10-12: a protocol that said "reflect after responding" made the
assistant write its full answer, call ``kumiho_memory_reflect``, and then write a
second, rephrased copy of the whole answer. The rule is now "reflect first, then
write the answer once"; the only call allowed to follow reflect is a silent
follow-up that needs its result. This module is the single definition of "an
instruction that puts a call after the answer", used by the test trees that pin
the protocol text:

* ``claude/scripts/test_session_bootstrap.py`` and ``test_memory_reflex.py``
* ``cloud-mcp/tests/test_directory_bundle.py``

It lives in the repository-root ``tests/`` directory because that is the one place
both trees can reach; neither tree is a package, so each loads it by path with
``importlib`` (see ``load()`` in those files). It is test support only: nothing
that ships in a plugin imports it.

Contract: ``calls_after_the_answer(text)`` returns the sentences of ``text`` that
tell the model to call, run, reflect or capture after answering. ``POSITIVES``
must all be flagged and ``NEGATIVES`` (the silent-follow-up and
already-answered sentences the protocol legitimately contains) must not be; each
tree asserts both so the helper cannot drift unnoticed.
"""

from __future__ import annotations

import re

_FLAGS = re.IGNORECASE
_QUALIFIER = r"(?:substantive |final |full |complete )?"

#: "After a substantive response", "after you answer", "after writing your answer",
#: "once you have answered" -- an anchor that places something AFTER the answer.
_ANCHORS = tuple(re.compile(p, _FLAGS) for p in (
    rf"\bafter (?:a |the |your )?{_QUALIFIER}(?:answer|answering|responding|response|reply|replying)\b",
    r"\bafter you (?:have )?(?:answer|answered|respond|responded|reply|replied)\b",
    rf"\bafter (?:writing|giving|delivering|sending|finishing|posting) (?:a |the |your )?{_QUALIFIER}"
    r"(?:answer|response|reply)\b",
    r"\bonce (?:you(?: have|'ve)? )?(?:answered|responded|replied)\b",
))

#: "Write the full answer, then call kumiho_memory_reflect": the answer comes first
#: and a call follows it in the same sentence.
_THEN_CALL = re.compile(
    r"\b(?:answer|response|reply)\b[^.;]*\bthen\b[^.;]*(?:\bcall\b|\brun\b|\breflect\b|\bkumiho_)",
    _FLAGS)

#: What counts as "making a call": a tool name, or the verbs that mean one. Reflect
#: itself is a call word: "Reflect after you reply" is the instruction at issue.
_CALL = re.compile(r"\bkumiho_\w+|\b(?:call|run|reflect|capture|consolidate)\b", _FLAGS)

#: The sentences the protocol legitimately contains: the turn already answered
#: because tools were needed after answering, so it ends right after reflect.
_EXEMPT = re.compile(r"already written before reflect", _FLAGS)


def _sentences(text: str) -> list:
    return re.split(r"(?<=[.;])\s+", " ".join(text.split()))


def calls_after_the_answer(text: str) -> list:
    """Sentences that tell the model to make a call after the final answer."""
    found = []
    for sentence in _sentences(text):
        if _EXEMPT.search(sentence):
            continue
        anchored = any(anchor.search(sentence) for anchor in _ANCHORS) and _CALL.search(sentence)
        if anchored or _THEN_CALL.search(sentence):
            found.append(sentence)
    return found


#: Instructions that put a call after the answer; every one must be flagged.
POSITIVES = (
    "After a substantive response, call kumiho_memory_reflect with your response text.",
    "After you answer, run the reflect call.",
    "Write the full answer, then call kumiho_memory_reflect.",
    "Once you have answered, call kumiho_memory_reflect.",
    "After writing your answer, call kumiho_memory_reflect.",
    "Reflect after you reply.",
    "REFLECT — after you respond.",
    "Capture what matters after responding.",
    "kumiho_memory_reflect (after responding).",
)

#: Sentences from the protocol that must NOT be flagged.
NEGATIVES = (
    "A follow-up that needs reflect's result runs silently right after it, with no "
    "user-visible text.",
    "If the full answer was already written before reflect (the turn needed tools after "
    "answering), end the turn right after reflect and any such follow-up, with no "
    "restatement, or at most one short line that adds new information.",
    "Then write the answer once.",
    "Pass a concise summary of the answer you are about to give as `response`, never a "
    "verbatim second copy of it, plus the captures.",
    "Every meaningful turn after bootstrap uses two natural reflexes, in this order: "
    "engage first, then the work, then reflect as the last memory call before the final "
    "answer, apart from silent follow-ups that need its result, and only then the final "
    "answer, written once.",
)
