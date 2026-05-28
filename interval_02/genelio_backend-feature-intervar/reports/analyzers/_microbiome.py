"""Generic parser for microbiome PDF reports.

All four body-site reports (gut / oral / skin / vaginal) share the same
anatomy:

1. **Patient info** — a simple key/value block near the top.
2. **Shannon Diversity Index value** — a prose sentence stating the SDIV
   (or SDVI — the lab mis-spells it for vaginal) number and comparing it
   to a site-specific healthy range.
3. **Top organisms** — one tabular list of the most abundant species
   with columns: *Scientific Name, Abundance, Reference Range, Significance*.
   Frequently spills onto the next page as a headerless continuation table.
4. **Condition-specific microbial markers** — one or more ``Microbial
   markers for <Condition>`` sub-sections, each followed by one *or more*
   tables whose header begins with *Microbial marker*. Skin reports in
   particular split a single condition across multiple tables.

Instead of hard-coding page numbers (which varies per report and breaks
as soon as the lab re-paginates), we parse pattern-driven: for each page
we collect table positions via ``page.find_tables()`` and condition-heading
positions via ``page.extract_text_lines()``, then interleave the two by
``(page_idx, y_top)`` so every table gets assigned to the most recent
preceding heading — mirrors how a human reads the PDF top-to-bottom.

Per-site analyzers call :func:`parse_microbiome_report` with a
:class:`SiteConfig` giving the site label and the healthy diversity
range.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Iterator, Sequence

from ._core import (
    clean_name,
    compare_to_range,
    parse_percentage,
    parse_range,
    status_emoji,
)

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SiteConfig:
    """Per-body-site tuning for the generic parser."""
    site: str                                       # e.g. "gut", "oral"
    display: str                                    # e.g. "Gut Microbiome"
    healthy_diversity_range: tuple[float, float]    # e.g. (2.34, 4.5)
    #: Keywords that indicate a species is a keystone / protective. Some
    #: reports flag keystone species in the significance column; we
    #: promote any top-organism whose significance mentions one of these.
    keystone_keywords: Sequence[str] = field(
        default_factory=lambda: ("keystone", "protecting", "protective"),
    )


# ---------------------------------------------------------------------------
# Diversity regexes
# ---------------------------------------------------------------------------
# Patterns are tried in order; the first numerically-sane match wins.
# We deliberately anchor each pattern on the user-specific phrasing
# ("your … microbiome has …" / "Shannon value for your …") so the generic
# "healthy range of 0.2 to 1.01" sentence can't accidentally leak its
# first digit as a diversity score.
#
# ``SD[IV]{2,3}`` accepts the three spellings observed in real reports:
# SDIV (oral), SDVI (vaginal), and plain SDI/SDV variants sometimes used.

_DIVERSITY_PATTERNS: tuple[re.Pattern[str], ...] = (
    # "your oral microbiome exhibits an SDIV of 3.581"
    # "Your vaginal microbiome has an SDVI of 0.02"
    re.compile(
        r"your\s+[\w\-]+\s+microbiome\s+"
        r"(?:has\s+an?|exhibits\s+an?|is\s+an?)\s+"
        r"SD[IV]{2,3}\s+(?:value\s+)?of\s+(\d+(?:\.\d+)?)",
        re.IGNORECASE,
    ),
    # "The Shannon value for your skin microbiome is 0.571"
    re.compile(
        r"Shannon\s+(?:diversity\s+index\s+)?value\s+"
        r"for\s+your\s+[\w\-]+\s+microbiome\s+is\s+"
        r"(\d+(?:\.\d+)?)",
        re.IGNORECASE,
    ),
    # "Shannon Diversity Index value of 3.28" / "Shannon value of 3.28"
    re.compile(
        r"Shannon[^.\n]{0,80}?value\s+of\s+(\d+(?:\.\d+)?)",
        re.IGNORECASE,
    ),
    # Fallback: "SDIV/SDVI of X" anywhere in the doc (avoiding the definition
    # line "SDIV for a healthy … is in the range of 0.2 to 1.01" — that one
    # is followed by "for", not "of", so it won't match).
    re.compile(r"SD[IV]{2,3}\s+(?:value\s+)?of\s+(\d+(?:\.\d+)?)", re.IGNORECASE),
    # Last-ditch: "diversity score X"
    re.compile(r"diversity\s+score\s+(\d+(?:\.\d+)?)", re.IGNORECASE),
)


# Primary and fallback condition-heading patterns.
# Primary: explicit "Microbial markers for <Condition>" heading.
# Fallback: descriptive sentences like "Decreased abundance of following
# markers is linked with melasma." which the skin report uses for a few
# conditions that don't get a dedicated heading.
_HEADING_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^\s*Microbial\s+markers?\s+for\s+(.+?)\s*$", re.IGNORECASE),
    re.compile(
        r"^(?:Increased|Decreased|Reduced)\s+abundance[^.\n]*?"
        r"(?:linked\s+with|associated\s+with|positively\s+correlated\s+with|"
        r"negatively\s+correlated\s+with|correlated\s+with)\s+"
        r"([A-Za-z][A-Za-z\s\-]+?)\s*\.?\s*$",
        re.IGNORECASE,
    ),
)


def _match_heading(line: str) -> str | None:
    """Return the condition name if ``line`` is a condition heading, else None."""
    stripped = line.strip()
    # Skip lines that are clearly not section headings (sentences mid-paragraph
    # often start with "Increased abundance" too — we only want it when that
    # phrase is the entire line, which the regex's ^/$ anchors already enforce).
    if not stripped:
        return None
    for pattern in _HEADING_PATTERNS:
        m = pattern.match(stripped)
        if m:
            name = m.group(1).strip().rstrip(".")
            # Reject obviously-wrong captures (short noise words, overly long
            # sentence fragments, etc.).
            if 2 <= len(name) <= 60 and not name.lower().startswith("the "):
                return name
    return None


# ---------------------------------------------------------------------------
# Table classifiers
# ---------------------------------------------------------------------------

def _header_text(table: Sequence[Sequence[str | None]]) -> str:
    if not table or not table[0]:
        return ""
    return " ".join(
        str(cell).replace("\n", " ").lower()
        for cell in table[0]
        if cell is not None
    )


def _is_condition_table(table: Sequence[Sequence[str | None]]) -> bool:
    """True if the first row looks like ``Microbial marker | Abundance | Reference``."""
    header = _header_text(table)
    return (
        "microbial marker" in header
        and "abundance" in header
        and "reference" in header
    )


def _is_top_organism_table(table: Sequence[Sequence[str | None]]) -> bool:
    """True if the table header matches the top-organisms listing.

    The lab varies the column set across sites ("Significance" / "Known
    function/effect" / "Function"), but every variant mentions a scientific-name
    column and an abundance column, plus either a reference range or a
    significance/function column.
    """
    header = _header_text(table)
    if "scientific name" not in header and "microbes" not in header:
        return False
    has_abundance = "abundance" in header
    has_meta = (
        "significance" in header
        or "reference" in header
        or "function" in header
        or "effect" in header
    )
    return has_abundance and has_meta


# ---------------------------------------------------------------------------
# Row parsers
# ---------------------------------------------------------------------------

def _parse_condition_row(row: Sequence[str | None]) -> dict | None:
    """Parse a condition-table row into a marker dict.

    Condition tables are narrow (3 columns in the source PDF) but pdfplumber
    occasionally emits them as wider tables with empty padding cells. We pick
    the first non-empty cell as the marker name and then hunt for the first
    cell that looks like an abundance and the first cell that looks like a
    reference range.
    """
    if not row:
        return None
    cells = [clean_name(c) for c in row]
    name = ""
    abundance_raw = ""
    reference_raw = ""
    for cell in cells:
        if not cell:
            continue
        lower = cell.lower()
        if lower.startswith("microbial marker") or lower == "microbial marker":
            return None
        if not name and not _looks_like_percentage(cell) and not _looks_like_range(cell):
            name = cell
            continue
        if not abundance_raw and _looks_like_percentage(cell):
            abundance_raw = cell
            continue
        if not reference_raw and (_looks_like_range(cell) or cell.upper() == "ND"):
            reference_raw = cell
            continue
    if not name or not abundance_raw:
        return None

    abundance = parse_percentage(abundance_raw)
    low, high = parse_range(reference_raw)
    return {
        "name": name,
        "abundance": abundance,
        "abundance_raw": abundance_raw,
        "reference": reference_raw,
        "reference_low": low,
        "reference_high": high,
        "status": compare_to_range(abundance, low, high),
    }


def _parse_organism_row(row: Sequence[str | None]) -> dict | None:
    """Parse a top-organism row into a species dict.

    The top-organism tables have slightly different column orderings across
    the four sites and pdfplumber injects padding columns too, so we pick
    cells by *shape* rather than by fixed index: first non-percentage /
    non-range cell is the species name, first percentage cell is abundance,
    first range cell is the reference, and the longest remaining text cell
    is the significance blurb.
    """
    if not row or not any(c for c in row):
        return None
    cells = [clean_name(c) for c in row]
    # Skip header rows that re-appeared mid-table.
    if any("scientific name" in c.lower() for c in cells if c):
        return None

    name = ""
    abundance_raw = ""
    reference_raw = ""
    significance = ""

    for cell in cells:
        if not cell:
            continue
        if not name and not _looks_like_percentage(cell) and not _looks_like_range(cell):
            name = cell
            continue
        if not abundance_raw and _looks_like_percentage(cell) and not _looks_like_range(cell):
            abundance_raw = cell
            continue
        if not reference_raw and (_looks_like_range(cell) or cell.upper() == "ND"):
            reference_raw = cell
            continue
        if len(cell) > len(significance):
            significance = cell

    if not name or not abundance_raw:
        return None

    abundance = parse_percentage(abundance_raw)
    low, high = parse_range(reference_raw)
    return {
        "name": name,
        "abundance": abundance,
        "abundance_raw": abundance_raw,
        "reference": reference_raw,
        "reference_low": low,
        "reference_high": high,
        "status": compare_to_range(abundance, low, high),
        "significance": significance,
    }


def _looks_like_percentage(cell: str) -> bool:
    if cell.upper() == "ND":
        return True
    # Accept "3.80%", "<0.101%", "5.7758%%" (real typo in skin report).
    return bool(re.fullmatch(r"<?\s*-?\d+(?:\.\d+)?\s*%{1,2}", cell))


def _looks_like_range(cell: str) -> bool:
    if cell.upper() == "ND":
        return True
    # Allow unicode dashes (– / —) used by some reports alongside ASCII '-'.
    return bool(re.search(r"\d+(?:\.\d+)?\s*%?\s*[-–—]\s*\d+(?:\.\d+)?\s*%?", cell))


def _coalesce_continuation_rows(
    table: Sequence[Sequence[str | None]],
) -> list[list[str | None]]:
    """Merge logical rows that pdfplumber split into two physical rows.

    When a species name *and* its reference range both wrap onto a second
    line ("Cutibacterium" / "avidum", "0.00% -" / "0.08%"), pdfplumber's
    line-detection sometimes treats the wrap as a separate row. We detect
    these "continuation" rows by their shape — very few non-empty cells,
    each one either a lowercase epithet or a numeric fragment — and fold
    them back into the preceding row.
    """
    rows = [list(r) for r in table]
    if len(rows) < 2:
        return rows

    merged: list[list[str | None]] = []
    i = 0
    while i < len(rows):
        current = rows[i]
        # Look ahead and consume any continuation rows that follow.
        while i + 1 < len(rows) and _is_continuation_row(rows[i + 1]):
            current = _merge_row_pair(current, rows[i + 1])
            i += 1
        merged.append(current)
        i += 1
    return merged


_CONTINUATION_WORD_RE = re.compile(r"^[a-z][a-zA-Z0-9\-]*$")
_CONTINUATION_NUM_RE = re.compile(
    r"^<?\s*-?\d+(?:\.\d+)?\s*%{0,2}\s*[-–—]?\s*\d*(?:\.\d+)?\s*%{0,2}$"
)


def _is_continuation_row(row: Sequence[str | None]) -> bool:
    """True if ``row`` looks like a continuation of the row above it.

    A continuation row is sparse (≤ 2 non-empty cells) and each non-empty
    cell is either a species epithet (lowercase word, no spaces) or a
    short numeric fragment (e.g. ``"0.08%"``, ``"2.20%"``).
    """
    non_empty = [str(c).strip() for c in row if c is not None and str(c).strip()]
    if not non_empty or len(non_empty) > 2:
        return False
    for cell in non_empty:
        # Reject anything multi-word: those are real data rows, not wraps.
        if " " in cell or "\n" in cell:
            return False
        if _CONTINUATION_WORD_RE.match(cell):
            continue
        if _CONTINUATION_NUM_RE.match(cell):
            continue
        return False
    return True


def _merge_row_pair(
    primary: Sequence[str | None],
    continuation: Sequence[str | None],
) -> list[str | None]:
    """Fold ``continuation`` into ``primary`` cell-by-cell.

    For each column, concatenate the two cells with a space when both are
    non-empty; otherwise pick whichever is non-empty.
    """
    width = max(len(primary), len(continuation))
    out: list[str | None] = []
    for idx in range(width):
        a = primary[idx] if idx < len(primary) else None
        b = continuation[idx] if idx < len(continuation) else None
        a_str = (a or "").strip()
        b_str = (b or "").strip()
        if a_str and b_str:
            out.append(f"{a_str} {b_str}")
        elif a_str:
            out.append(a_str)
        elif b_str:
            out.append(b_str)
        else:
            out.append(None)
    return out


def _table_has_data_like_first_row(table: Sequence[Sequence[str | None]]) -> bool:
    """True if the first row looks like data (species + %) rather than a header.

    Used to detect top-organism continuation tables on the next page, which
    frequently lack a header because the PDF just continues the previous
    page's table underneath.
    """
    if not table or not table[0]:
        return False
    cells = [clean_name(c) for c in table[0]]
    if not cells:
        return False
    non_empty = [c for c in cells if c]
    if not non_empty:
        return False
    # If any cell contains "microbial marker" or "scientific name" it's a header.
    for c in non_empty:
        lower = c.lower()
        if "microbial marker" in lower or "scientific name" in lower:
            return False
    # Look for the species-name + percentage pattern.
    has_pct = any(_looks_like_percentage(c) for c in non_empty)
    first_is_name = bool(non_empty[0]) and not _looks_like_percentage(non_empty[0]) \
        and not _looks_like_range(non_empty[0])
    return has_pct and first_is_name


# ---------------------------------------------------------------------------
# Diversity extraction
# ---------------------------------------------------------------------------

def _extract_diversity(text: str, healthy_range: tuple[float, float]) -> dict:
    """Find the user's Shannon diversity value in ``text`` and grade it."""
    low, high = healthy_range

    for pattern in _DIVERSITY_PATTERNS:
        for m in pattern.finditer(text):
            try:
                score = float(m.group(1))
            except (ValueError, IndexError):
                continue
            # Shannon values are bounded — anything outside [0, 10] is almost
            # certainly a stray percentage / reference-range number picked up
            # by accident.
            if not 0 <= score <= 10:
                continue
            if score < low:
                status = "below_range"
            elif score > high:
                status = "above_range"
            else:
                status = "within_range"
            return {
                "score": score,
                "range": f"{low}-{high}",
                "range_low": low,
                "range_high": high,
                "status": status,
            }
    return {}


# ---------------------------------------------------------------------------
# Patient info
# ---------------------------------------------------------------------------
# The Client Information block renders with two labels per line ("Name: ...
# Date of Birth: ...") and the lab's PDF tooling occasionally inserts a
# stray space inside a label ("Provincial He alth Number"). Rather than
# enumerating every typo, we tokenise the block by scanning for any
# capitalised-words-then-colon label and only keep the ones we recognise
# (after collapsing whitespace).
#
# Most patient fields in real PDFs are intentionally blank (PII redacted)
# — that's fine; we return only the populated ones.

def _flex_label_pattern(label: str) -> str:
    """Return a regex matching ``label`` with up to one optional space between
    any two adjacent characters, plus loose whitespace between words.

    Lets us recognise "Provincial He alth Number" (real lab typo) as
    "Provincial Health Number" without enumerating every typo by hand.
    """
    words: list[str] = []
    for word in label.split():
        if not word:
            continue
        chars = [re.escape(c) for c in word]
        words.append((r"\s?").join(chars))
    return r"\s+".join(words)


# Pre-compile one regex per canonical label, keyed by canonical name, so we
# can scan the patient block once for every known label without ever
# accidentally absorbing a value (e.g. "Skin Swab") as a fake label.
_PATIENT_LABEL_REGEXES: list[tuple[str, re.Pattern[str]]] = [
    (canonical, re.compile(_flex_label_pattern(canonical) + r"\s*:", re.IGNORECASE))
    for canonical in (
        "Date of Birth",
        "Sample Receiving Date",
        "Provincial Health Number",
        "Collection Date",
        "Report Date",
        "Receive Date",
        "Sample Type",
        "PHN",
        "Name",
    )
]


def _extract_patient(text: str) -> dict:
    """Parse the ``Client Information`` block into a flat dict.

    We narrow to the Client Information section, then scan that slice
    once for every known label. Each match's value runs from the end of
    the colon to either the next label or the next newline — whichever
    comes first.
    """
    # Bound the search to the Client Information section so unrelated
    # "X:" tokens elsewhere in the document can't pollute the patient
    # dict. Every report ends the block with a "Note:" disclaimer or the
    # next "What is …" section heading.
    block_match = re.search(
        r"Client\s+Information(.*?)(?:Note\s*:|What\s+is\s+the)",
        text, re.DOTALL | re.IGNORECASE,
    )
    block = block_match.group(1) if block_match else text[:2000]

    # Collect every label hit as (start, end, canonical_name). Order them
    # by position so we can compute each value as the slice running up to
    # the next label.
    hits: list[tuple[int, int, str]] = []
    seen_spans: list[tuple[int, int]] = []
    for canonical, pattern in _PATIENT_LABEL_REGEXES:
        for m in pattern.finditer(block):
            span = (m.start(), m.end())
            # Skip overlaps — longer labels (e.g. "Sample Receiving Date")
            # take precedence over shorter ones ("Sample") since we sort
            # them long-first below.
            if any(s <= span[0] < e or s < span[1] <= e for s, e in seen_spans):
                continue
            seen_spans.append(span)
            hits.append((span[0], span[1], canonical))
    hits.sort(key=lambda h: h[0])

    info: dict[str, str] = {}
    for idx, (start, end, canonical) in enumerate(hits):
        next_start = hits[idx + 1][0] if idx + 1 < len(hits) else len(block)
        value = block[end:next_start]
        # The value lives on the same line as its label.
        value = value.split("\n", 1)[0].strip()
        if value:
            info.setdefault(canonical, value)
    return info


# ---------------------------------------------------------------------------
# Page walker — interleaves headings with tables by y-position
# ---------------------------------------------------------------------------

def _page_events(page) -> list[tuple[float, str, Any]]:
    """Return ordered ``(y_top, kind, payload)`` events for a page.

    ``kind`` is one of ``"heading"`` or ``"table"``. Events are sorted by
    their vertical position so a consumer walking the list sees the same
    order a human reader would.
    """
    events: list[tuple[float, str, Any]] = []

    # Tables (with bbox for y-position)
    try:
        tables = page.find_tables() or []
    except Exception as exc:  # noqa: BLE001
        log.debug("find_tables failed on page: %s", exc)
        tables = []
    for table in tables:
        try:
            data = table.extract()
        except Exception as exc:  # noqa: BLE001
            log.debug("table.extract failed: %s", exc)
            continue
        if not data:
            continue
        y_top = table.bbox[1] if getattr(table, "bbox", None) else 0.0
        events.append((y_top, "table", data))

    # Headings from text lines
    try:
        lines = page.extract_text_lines() or []
    except Exception as exc:  # noqa: BLE001
        log.debug("extract_text_lines failed: %s", exc)
        lines = []
    for line in lines:
        name = _match_heading(line.get("text", ""))
        if name:
            events.append((float(line.get("top", 0.0)), "heading", name))

    events.sort(key=lambda e: e[0])
    return events


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def parse_microbiome_report(pdf_path: str, config: SiteConfig) -> dict:
    """Parse a microbiome PDF into a structured dict.

    Returns keys:
      * ``site``            - the body site (``config.site``)
      * ``display``         - human label for the site
      * ``patient``         - dict of patient/client info fields
      * ``diversity``       - SDIV value + healthy range + status
      * ``top_organisms``   - list of top species with abundance vs ref
      * ``keystone_species`` - subset of top_organisms flagged as keystone
      * ``conditions``      - ``{condition_name: {"markers": [...]}}``
      * ``full_text``       - every page's extracted text (for RAG)
    """
    # Lazy import so Django can boot without pdfplumber installed — missing
    # deps surface as an upload-time failure, not an import-time crash.
    import pdfplumber  # noqa: WPS433

    report: dict = {
        "site": config.site,
        "display": config.display,
        "patient": {},
        "diversity": {},
        "top_organisms": [],
        "keystone_species": [],
        "conditions": {},
        "full_text": "",
    }

    pages_text: list[str] = []
    current_condition: str | None = None
    # The last classified table kind, used to route continuation tables
    # (headerless tables that pick up where the previous one left off).
    last_table_kind: str | None = None
    condition_order = 0
    # Case-folded name -> canonical key stored in report["conditions"].
    # The lab often uses Title Case in section headings ("Microbial markers
    # for Atopic dermatitis") but lowercase in the descriptive sentence just
    # beneath it ("Reduced abundance of … associated with atopic dermatitis."),
    # so we merge case variants onto the first-seen casing.
    canonical_key: dict[str, str] = {}

    def _ensure_condition(name: str) -> str:
        """Register ``name`` as a condition and return the canonical key.

        Reuses the first-seen casing for duplicates so the condition
        dict has one entry per distinct name, case-insensitively.
        """
        nonlocal condition_order
        folded = name.casefold()
        if folded in canonical_key:
            return canonical_key[folded]
        report["conditions"][name] = {"markers": [], "order": condition_order}
        canonical_key[folded] = name
        condition_order += 1
        return name

    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            pages_text.append(page.extract_text() or "")

            for _y, kind, payload in _page_events(page):
                if kind == "heading":
                    # Use the canonical (first-seen) casing so case-variant
                    # headings like "Microbial markers for Atopic dermatitis"
                    # and the follow-up "linked with atopic dermatitis"
                    # collapse onto a single condition entry.
                    current_condition = _ensure_condition(payload)
                    # Seeing a new heading resets the continuation pointer —
                    # the next table belongs to the new condition, not a
                    # continuation of the previous one.
                    last_table_kind = None
                    continue

                # kind == "table"
                # Coalesce wrap-induced row splits before classifying / parsing
                # so a row whose name and reference range both wrapped onto a
                # second line (skin: "Cutibacterium" / "avidum") isn't emitted
                # as two half-organisms.
                table = _coalesce_continuation_rows(payload)

                if _is_top_organism_table(table):
                    for row in table[1:]:
                        parsed = _parse_organism_row(row)
                        if parsed:
                            report["top_organisms"].append(parsed)
                    last_table_kind = "top_organism"

                elif _is_condition_table(table):
                    if current_condition is None:
                        current_condition = _ensure_condition("General markers")
                    bucket = report["conditions"][current_condition]["markers"]
                    for row in table[1:]:
                        parsed = _parse_condition_row(row)
                        if parsed:
                            bucket.append(parsed)
                    last_table_kind = "condition"

                elif _table_has_data_like_first_row(table):
                    # Headerless continuation table: route based on what the
                    # previous recognised table was.
                    if last_table_kind == "top_organism":
                        for row in table:
                            parsed = _parse_organism_row(row)
                            if parsed:
                                report["top_organisms"].append(parsed)
                    elif last_table_kind == "condition" and current_condition:
                        bucket = report["conditions"][current_condition]["markers"]
                        for row in table:
                            parsed = _parse_condition_row(row)
                            if parsed:
                                bucket.append(parsed)
                    # else: unrecognised orphan — ignore quietly; raw text
                    # still available in full_text for RAG lookups.

    report["full_text"] = "\n".join(pages_text)
    report["patient"] = _extract_patient(report["full_text"])
    report["diversity"] = _extract_diversity(
        report["full_text"], config.healthy_diversity_range,
    )

    # Promote top organisms flagged in their significance text as keystone
    # or protective species. Some reports also mark keystones visually
    # (bold / colour) which pdfplumber strips — we accept the significance
    # text as a proxy.
    for org in report["top_organisms"]:
        sig = (org.get("significance") or "").lower()
        if any(kw in sig for kw in config.keystone_keywords):
            report["keystone_species"].append(org)

    # Tidy: strip internal ordering keys from the final output.
    for cond in report["conditions"].values():
        cond.pop("order", None)

    return report


# ---------------------------------------------------------------------------
# Analysis-context builder (the LLM's structured input)
# ---------------------------------------------------------------------------

def build_analysis_context(report: dict) -> str:
    """Render ``parse_microbiome_report`` output as a text block for the LLM.

    The format mirrors the legacy ``build_full_analysis_context`` used by
    the gut pipeline so the system prompt's references to a "structured
    analysis" block stay valid for all body sites.
    """
    lines: list[str] = []
    display = report.get("display") or report.get("site", "Microbiome").title()
    lines.append(f"=== {display.upper()} — STRUCTURED ANALYSIS ===")
    lines.append("")

    # Diversity
    d = report.get("diversity") or {}
    if d:
        lines.append("## Shannon Diversity Index (SDIV)")
        lines.append(
            f"- Value: {d.get('score')}"
            f"  |  Healthy range: {d.get('range')}"
            f"  |  Status: {status_emoji(d.get('status', ''))}"
        )
        lines.append("")

    # Top organisms
    organisms = report.get("top_organisms") or []
    if organisms:
        lines.append(f"## Top organisms ({len(organisms)})")
        for org in organisms:
            lines.append(
                f"- {org['name']}: {org['abundance_raw']} "
                f"(ref {org.get('reference') or 'ND'}) "
                f"{status_emoji(org['status'])}"
            )
        lines.append("")

    # Keystone / protective species
    keystones = report.get("keystone_species") or []
    if keystones:
        lines.append(f"## Keystone / protective species ({len(keystones)})")
        for k in keystones:
            lines.append(f"- {k['name']} {status_emoji(k['status'])}")
        lines.append("")

    # Conditions
    conditions = report.get("conditions") or {}
    if conditions:
        lines.append(f"## Condition-specific microbial markers ({len(conditions)} conditions)")
        for cond_name, data in conditions.items():
            markers = data.get("markers") or []
            lines.append(f"\n### {cond_name} ({len(markers)} markers)")
            for m in markers:
                lines.append(
                    f"- {m['name']}: {m['abundance_raw']} "
                    f"(ref {m.get('reference') or 'ND'}) "
                    f"{status_emoji(m['status'])}"
                )
        lines.append("")

    # Patient summary at the bottom — useful for the LLM but not the lead.
    patient = report.get("patient") or {}
    if patient:
        lines.append("## Client information")
        for k, v in patient.items():
            lines.append(f"- {k}: {v}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Small helpers used by build_analysis_context + tests
# ---------------------------------------------------------------------------

def iter_all_markers(report: dict) -> Iterator[dict]:
    """Yield every biomarker / organism in the report (flat)."""
    yield from report.get("top_organisms") or []
    for cond in (report.get("conditions") or {}).values():
        yield from cond.get("markers") or []
