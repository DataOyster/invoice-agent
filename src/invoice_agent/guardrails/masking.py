"""Mask sensitive identifiers before anything is logged or written to the audit log.

IBANs, Swedish organisation numbers and Swedish personal identity numbers are replaced by a
mask that keeps only the last 4 characters, enough for a human to tell two accounts apart.
The data here is synthetic, but the rule is written as if it were not.
"""

from __future__ import annotations

import logging
import re

# IBAN: 2 letters, 2 check digits, then 11-30 letters/digits, optionally in groups of 4.
IBAN = re.compile(r"\b[A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]{4}){2,7}(?:[ ]?[A-Z0-9]{1,4})?\b")
# Personal identity number (YYYYMMDD-XXXX or YYMMDD-XXXX) and organisation number (NNNNNN-NNNN).
PERSONAL_NUMBER = re.compile(r"\b(?:19|20)\d{6}[-+]\d{4}\b")
ORG_NUMBER = re.compile(r"\b\d{6}[-+]\d{4}\b")


def _keep_last4(match: re.Match) -> str:
    compact = match.group(0).replace(" ", "")
    return f"[MASKED ...{compact[-4:]}]"


def mask_sensitive(text: str) -> str:
    text = IBAN.sub(_keep_last4, text)
    text = PERSONAL_NUMBER.sub(_keep_last4, text)
    return ORG_NUMBER.sub(_keep_last4, text)


class MaskingFilter(logging.Filter):
    """Logging filter: the final message is masked, whatever was passed as arguments."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = mask_sensitive(record.getMessage())
        record.args = None
        return True


def get_logger(name: str) -> logging.Logger:
    """A logger whose records are always masked, whichever handler prints them."""
    logger = logging.getLogger(name)
    if not any(isinstance(f, MaskingFilter) for f in logger.filters):
        logger.addFilter(MaskingFilter())
    return logger
