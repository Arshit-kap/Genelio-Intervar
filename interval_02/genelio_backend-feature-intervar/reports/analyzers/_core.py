"""Shared primitives for microbiome report analyzers.

All four body sites (gut / oral / skin / vaginal) report abundance values
and healthy reference ranges in the same numeric format, so the parsing
helpers below are shared. Per-site analyzers import from here rather than
duplicating the logic.
"""
from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Status vocabulary
# ---------------------------------------------------------------------------
# Kept as plain strings rather than an Enum so values serialise cleanly to
# JSON (parsed_data is stored as JSONField) and render trivially in the
# analysis-context text block sent to the LLM.

STATUS_WITHIN = "within_range"
STATUS_ABOVE = "above_range"
STATUS_BELOW = "below_range"
STATUS_NOT_DETECTED = "not_detected"
STATUS_NO_REFERENCE = "no_reference"

_STATUS_EMOJI = {
    STATUS_ABOVE: "🔴 ABOVE",
    STATUS_BELOW: "🟡 BELOW",
    STATUS_WITHIN: "🟢 Normal",
    STATUS_NOT_DETECTED: "⚪ ND",
    STATUS_NO_REFERENCE: "— (no reference)",
}


def status_emoji(status: str) -> str:
    """Return the user-facing emoji label for a status code."""
    return _STATUS_EMOJI.get(status, "—")


# ---------------------------------------------------------------------------
# Numeric parsing
# ---------------------------------------------------------------------------

# Strips commas / trailing % and handles '<' / '>' prefixes ("<0.01%").
_NUMBER_RE = re.compile(r"-?\d+(?:\.\d+)?")


def parse_percentage(raw: str | None) -> float | None:
    """Parse an abundance cell like ``'3.80%'`` / ``'<0.101%'`` / ``'ND'``.

    Returns ``None`` for ``'ND'``, empty, or unparseable values so downstream
    code can reliably distinguish "not detected" from "zero".
    """
    if raw is None:
        return None
    s = str(raw).strip()
    if not s or s.upper() == "ND":
        return None
    # Drop percent sign and comparator prefixes.
    s = s.replace("%", "").lstrip("<>").strip()
    m = _NUMBER_RE.search(s)
    if not m:
        return None
    try:
        return float(m.group(0))
    except ValueError:
        return None


def parse_range(raw: str | None) -> tuple[float | None, float | None]:
    """Parse a reference-range cell like ``'0.26%-3.14%'`` / ``'<0.05%'`` / ``'ND'``.

    Returns a ``(low, high)`` tuple. For ``'<X'`` returns ``(0.0, X)``.
    For ``'ND'`` or unparseable values returns ``(None, None)``.
    """
    if raw is None:
        return None, None
    s = str(raw).strip()
    if not s or s.upper() == "ND":
        return None, None
    # Collapse line breaks and surrounding whitespace — real-world tables
    # wrap '0.00% -\n3.14%' across lines on narrow PDF columns.
    s = re.sub(r"\s+", " ", s.replace("\n", " ")).replace("%", "").strip()

    m = re.match(r"<\s*(-?\d+(?:\.\d+)?)", s)
    if m:
        return 0.0, float(m.group(1))

    m = re.match(
        r"(-?\d+(?:\.\d+)?)\s*-\s*(-?\d+(?:\.\d+)?)", s,
    )
    if m:
        return float(m.group(1)), float(m.group(2))

    return None, None


def compare_to_range(
    abundance: float | None,
    low: float | None,
    high: float | None,
) -> str:
    """Return a status code comparing ``abundance`` to the ``[low, high]`` range."""
    if abundance is None:
        return STATUS_NOT_DETECTED
    if low is None and high is None:
        return STATUS_NO_REFERENCE
    if low is not None and abundance < low:
        return STATUS_BELOW
    if high is not None and abundance > high:
        return STATUS_ABOVE
    return STATUS_WITHIN


# ---------------------------------------------------------------------------
# String normalisation
# ---------------------------------------------------------------------------

def clean_name(raw: str | None) -> str:
    """Normalise a species / marker name extracted from a PDF table cell.

    PDF table cells frequently contain soft line breaks from narrow column
    widths (``'Lactobacillus\\ncrispatus'``). Also strips stray footnote
    markers, trailing punctuation, and collapses whitespace.
    """
    if not raw:
        return ""
    s = str(raw).replace("\n", " ")
    s = re.sub(r"\s+", " ", s).strip()
    # Drop trailing punctuation occasionally left over from the PDF render.
    return s.rstrip(".,;:")
