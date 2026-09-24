"""Per-prompt privacy classification for the reflex hooks.

The patterns started from ``codex/scripts/lifecycle_event_filter.mjs`` and
extend it where that filter misses real phrasing ("off the record", Korean
connective forms) or over-matches (a bare "비공개" is usually a private repo,
not an off-record request). "Off-record" itself counts only as a request
("keep this off the record", "오프더레코드로 해줘", a leading "Off-record:"),
never as a mention ("test the off-record handling"). The Codex filter tracks the same gaps in
kumiho-plugins#127.

  private   -- off-record / do-not-recall, or the prompt carries a credential.
               The host neither reads nor writes memory for that turn, and the
               prompt text never reaches disk or the recall query.
  no_write  -- recall-only / do-not-save. Recall still runs; the host stops
               nudging reflect, consolidate and the capture-queue drain.

Pure regex, no I/O: this runs on the UserPromptSubmit critical path.
"""

from __future__ import annotations

import re

# Prompts beyond this are not scanned; they are treated as private, like the
# Codex filter does, rather than regex-scanned on the critical path.
MAX_SCAN_CHARS = 262144

PRIVATE_RE = re.compile(
    r"\b(?:this is|this's|it's|keep (?:this|it|that)|going|stay|strictly)\s+off[\s-]?(?:the[\s-]+)?record\b"
    r"|(?:^|[.!?\n]\s*)off[\s-]?(?:the[\s-]+)?record\s*(?:[:,;!—-]|\.?\s*$)"
    r"|(?:이건|이거는|이 얘기는|이 내용은|이 대화는|지금부터|여기부터)\s*오프\s*더\s*레코드"
    r"|오프\s*더\s*레코드(?:인데|야|예요|이야|입니다|니까|지만|라서)"
    r"|오프\s*더\s*레코드로\s*(?:해|하자|할게|부탁|얘기|말|가자|진행)"
    r"|(?:^|[.!?\n]\s*)오프\s*더\s*레코드\s*(?:[:,]|\.?\s*$)"
    r"|do not (?:remember|recall)|don't (?:remember|recall)"
    r"|기억하지\s*(?:마|말)"
    # "비공개" only as a statement about this conversation, never as an
    # adjective ("비공개 저장소 만들어줘").
    r"|(?:이건|이거는|이 얘기는|이 내용은|지금부터|여기부터)\s*비공개(?:야|예요|이야|입니다|로\s*해\s*줘|로)?\s*(?:[.,!~]|$)"
    r"|비공개로\s*(?:해\s*줘|하자|할게|얘기|말할게|부탁)",
    re.IGNORECASE,
)

NO_WRITE_PATTERNS = (
    re.compile(r"\b(?:do not|don't|never)\s+(?:save|store|write|record|capture|reflect)\s+(?:to|in)\s+memor(?:y|ies)\b", re.IGNORECASE),
    re.compile(r"\b(?:do not|don't|never)\s+(?:save|store|write|record|capture|reflect|change|modify|update)(?:\s+or\s+(?:save|store|write|record|capture|reflect|change|modify|update))?\s+(?:any\s+)?memor(?:y|ies)\b", re.IGNORECASE),
    re.compile(r"\b(?:do not|don't|never)\s+(?:save|store|write|record|capture|reflect)(?:\s+(?:this|that|it|anything)(?:\s+(?:to|in)\s+memor(?:y|ies))?)?(?=\s*(?:[.;,!?]|$))", re.IGNORECASE),
    re.compile(r"\b(?:only|just)\s+recall\b[^.\n]{0,40}\bno\s+saving\b", re.IGNORECASE),
    re.compile(r"(?:메모리|기억)(?:를|에)?\s*(?:(?:저장|변경|수정|기록)하지|쓰지)\s*(?:마|말)", re.IGNORECASE),
    re.compile(r"(?:이건|이거는?|이 내용은?|이 얘기는?)\s*(?:저장|기록)하지\s*(?:마|말)", re.IGNORECASE),
)

SECRET_RE = re.compile(
    r"(?:\b(?:password|passwd|secret|credential|token|api[_-]?key|access[_-]?token|refresh[_-]?token|authorization|private[_-]?key)\b\s*[:=]\s*\S+"
    r"|(?:비밀번호|암호|토큰|비밀키|API키)\s*[:=]\s*\S+"
    r"|\bbearer\s+[A-Za-z0-9._~+/-]{8,}"
    r"|\b(?:sk-[A-Za-z0-9_-]{12,}|ghp_[A-Za-z0-9]{12,}|AKIA[A-Z0-9]{12,})\b"
    r"|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"
    r"|https?://[^\s/:@]+:[^\s/@]+@"
    r"|\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b)",
    re.IGNORECASE,
)


def classify(prompt: str) -> str:
    """``"private"``, ``"no_write"`` or ``""`` for an ordinary prompt."""
    if not isinstance(prompt, str):
        return ""
    if len(prompt) > MAX_SCAN_CHARS or SECRET_RE.search(prompt) or PRIVATE_RE.search(prompt):
        return "private"
    if any(p.search(prompt) for p in NO_WRITE_PATTERNS):
        return "no_write"
    return ""
