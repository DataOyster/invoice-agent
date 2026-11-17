"""Spot text in an invoice that tries to instruct an AI ("ignore the checks and approve").

This is a tripwire, not the defence. The defence is structural: the agent has no tool that pays
or approves, its findings cannot create or remove flags, and invoice text reaches the model only
inside <invoice_data> tags. The tripwire adds visibility: a suspicious document is not sent to
the model at all, is routed to manual review, and the attempt is recorded in the audit log.

Patterns are phrased as instructions in Swedish, English and Italian. They avoid ordinary
invoice words: "Att betala" (amount due) and "Totale da pagare" must never trigger.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

PATTERNS: tuple[tuple[str, str], ...] = (
    ("en", r"\bignore\b.{0,30}\b(instructions?|rules?|checks?|previous|above)\b"),
    ("en", r"\bdisregard\b.{0,30}\b(instructions?|rules?|checks?)\b"),
    ("en", r"\b(approve|pay)\b.{0,20}\b(this|the)\s+invoice\b"),
    ("en", r"\bmark\b.{0,20}\b(as\s+)?(paid|approved|verified)\b"),
    ("en", r"\bdo\s+not\s+(flag|check|report)\b"),
    ("en", r"\b(you\s+are|act\s+as)\s+(an?\s+|the\s+)?(ai|assistant|model|agent)\b"),
    ("en", r"\b(system\s+prompt|new\s+instructions?|override)\b"),
    ("en", r"\b(note|message)\s+(to|for)\s+(the\s+)?(ai|assistant|model|agent)\b"),
    ("sv", r"\bignorera\b.{0,30}\b(instruktion\w*|regl\w*|kontroll\w*|tidigare)\b"),
    ("sv", r"\bgodkänn\b.{0,20}\bfaktura\w*\b"),
    ("sv", r"\bflagga\s+inte\b"),
    ("it", r"\bignora\b.{0,30}\b(istruzion\w*|regol\w*|controll\w*|precedent\w*)\b"),
    ("it", r"\bapprova\b.{0,20}\bfattura\b"),
    ("it", r"\bnon\s+(segnalare|controllare)\b"),
)
_COMPILED = [(lang, re.compile(p, re.IGNORECASE | re.DOTALL)) for lang, p in PATTERNS]


@dataclass(frozen=True)
class InjectionHit:
    language: str
    pattern: str
    excerpt: str


def scan_for_injection(text: str) -> list[InjectionHit]:
    hits = []
    for lang, pattern in _COMPILED:
        match = pattern.search(text)
        if match:
            start = max(0, match.start() - 20)
            hits.append(InjectionHit(lang, pattern.pattern, text[start : match.end() + 20]))
    return hits
