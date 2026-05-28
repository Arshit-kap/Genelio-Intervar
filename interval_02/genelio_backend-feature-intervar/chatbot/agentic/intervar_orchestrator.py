"""Agentic orchestrator for InterVar-TXT chat sessions.

Mirrors the 5-stage flow we built for clinical_csv, but with an
InterVar-specific router (6 intent categories from the spec PDF) and
a **deterministic Python query executor** that does all filtering /
aggregation / counting BEFORE the LLM is invoked. Because InterVar
files have 76k+ rows per patient — three orders of magnitude more than
clinical_csv — we cannot ground the answer LLM on the whole report;
we ground it on the executor's structured result (≤30 rows or one
counts table) plus the standard schema reference and HPO profile.

Public entry: :func:`handle_intervar_message`.
"""
from __future__ import annotations

import json
import logging
import re
from collections import Counter
from dataclasses import asdict, dataclass, field
from typing import Any

from django.conf import settings
from django.utils import timezone

from . import hpo, tools
from .intervar_prompts import ANSWER_SYSTEM_PROMPT, ROUTER_SYSTEM_PROMPT
from .tools import BUCKET_LABEL, BUCKET_PRIORITY, Bucket
from .validator import validate_answer

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Stage 1 — Router decision
# ---------------------------------------------------------------------------

@dataclass
class RouterDecision:
    intent: str = "other"
    needs_hpo: bool = False
    symptoms: list[str] = field(default_factory=list)

    chr: str | None = None
    start: int | None = None
    end: int | None = None
    rsid: str | None = None
    gene: str | None = None

    func: str | None = None
    exonic_func: str | None = None
    zygosity: str | None = None
    in_repeat: bool | None = None

    clinvar_includes: str | None = None
    intervar_verdict: str | None = None
    acmg_flag: str | None = None
    acmg_flag_value: int | None = None

    cadd_min: float | None = None
    cadd_max: float | None = None
    gnomad_max: float | None = None
    gnomad_min: float | None = None
    sift_max: float | None = None
    metasvm_min: float | None = None

    disease_term: str | None = None

    group_by: str | None = None
    agg_func: str | None = None
    limit: int | None = None

    wants_schema: bool = False
    target_column: str | None = None

    raw: dict = field(default_factory=dict)


# Map InterVar intent → safety tag (counselor referral on clinical
# interpretation; general disclaimer on schema lookups).
_SAFETY_TAG = {
    "coord_lookup":  "general_disclaimer",
    "biofilter":     "counselor_referral",
    "acmg_clinvar":  "counselor_referral",
    "disease_link":  "counselor_referral",
    "aggregate":     "general_disclaimer",
    "hpo_symptom":   "counselor_referral",
    "schema_lookup": "general_disclaimer",
    "summary":       "general_disclaimer",
    "other":         "general_disclaimer",
}


# ---------------------------------------------------------------------------
# LLM helpers
# ---------------------------------------------------------------------------

def _llm_call(messages: list[dict], *, temperature: float, max_tokens: int) -> str:
    """Shared vLLM call. Reuses the chatbot.pipeline singleton."""
    from chatbot.pipeline import _llm
    response = _llm().chat.completions.create(
        model=settings.MODEL_NAME,
        messages=messages,
        max_tokens=max_tokens,
        temperature=temperature,
        extra_body={"chat_template_kwargs": {"enable_thinking": False}},
    )
    raw = response.choices[0].message.content or ""
    return re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()


_JSON_BLOCK_RE = re.compile(r"\{[\s\S]*\}")


def _safe_parse_json(text: str) -> dict | None:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```\s*$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = _JSON_BLOCK_RE.search(text)
        if not m:
            return None
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            return None


def classify(user_message: str) -> RouterDecision:
    """Stage 1 — single LLM call, JSON out, mapped to RouterDecision."""
    raw = _llm_call(
        [
            {"role": "system", "content": ROUTER_SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ],
        temperature=0.0,
        max_tokens=600,
    )
    parsed = _safe_parse_json(raw) or {}
    log.info("intervar router raw=%r parsed=%r", raw[:200], parsed)

    def _opt_int(v):
        if v is None:
            return None
        try:
            return int(v)
        except (TypeError, ValueError):
            return None

    def _opt_float(v):
        if v is None:
            return None
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    def _opt_str(v):
        if v is None:
            return None
        s = str(v).strip()
        return s or None

    return RouterDecision(
        intent=_opt_str(parsed.get("intent")) or "other",
        needs_hpo=bool(parsed.get("needs_hpo")),
        symptoms=[s.strip() for s in (parsed.get("symptoms") or []) if isinstance(s, str) and s.strip()],
        chr=_opt_str(parsed.get("chr")),
        start=_opt_int(parsed.get("start")),
        end=_opt_int(parsed.get("end")),
        rsid=_opt_str(parsed.get("rsid")),
        gene=_opt_str(parsed.get("gene")),
        func=_opt_str(parsed.get("func")),
        exonic_func=_opt_str(parsed.get("exonic_func")),
        zygosity=_opt_str(parsed.get("zygosity")),
        in_repeat=parsed.get("in_repeat") if isinstance(parsed.get("in_repeat"), bool) else None,
        clinvar_includes=_opt_str(parsed.get("clinvar_includes")),
        intervar_verdict=_opt_str(parsed.get("intervar_verdict")),
        acmg_flag=_opt_str(parsed.get("acmg_flag")),
        acmg_flag_value=_opt_int(parsed.get("acmg_flag_value")),
        cadd_min=_opt_float(parsed.get("cadd_min")),
        cadd_max=_opt_float(parsed.get("cadd_max")),
        gnomad_max=_opt_float(parsed.get("gnomad_max")),
        gnomad_min=_opt_float(parsed.get("gnomad_min")),
        sift_max=_opt_float(parsed.get("sift_max")),
        metasvm_min=_opt_float(parsed.get("metasvm_min")),
        disease_term=_opt_str(parsed.get("disease_term")),
        group_by=_opt_str(parsed.get("group_by")),
        agg_func=_opt_str(parsed.get("agg_func")),
        limit=_opt_int(parsed.get("limit")),
        wants_schema=bool(parsed.get("wants_schema")),
        target_column=_opt_str(parsed.get("target_column")),
        raw=parsed,
    )


# ---------------------------------------------------------------------------
# Stage 1.5 — Session profile + HPO resolution (reuses chatbot.agentic.hpo)
# ---------------------------------------------------------------------------

_SYMPTOM_SPLIT_RE = re.compile(
    r"\s*(?:,|;|/| and | or | plus | also | with )\s*", re.IGNORECASE,
)
# After splitting, strip a leading conjunction that survived (e.g. when
# the input was "fatigue, and abdominal pain" → comma split → " and
# abdominal pain" — leading "and" needs to go).
_LEADING_CONJ_RE = re.compile(r"^(?:and|or|plus|also|with)\s+", re.IGNORECASE)


def _split_symptoms(symptoms: list[str]) -> list[str]:
    """Split compound symptom phrases the router LLM emits as a single string.

    Bug #6: the router occasionally returns ``["lethargic, muscle pain"]``
    instead of two entries; resolving the joined string against HPO never
    matches anything real. We split on commas, semicolons, slashes, and
    the conjunctions "and / or / plus / also / with" so each clinical
    finding gets a clean lookup. Singletons pass through unchanged; empty
    fragments and 1-2-char debris are dropped.
    """
    out: list[str] = []
    seen: set[str] = set()
    for raw in symptoms:
        if not raw:
            continue
        for piece in _SYMPTOM_SPLIT_RE.split(raw):
            p = piece.strip().rstrip(".,!?;:").strip()
            p = _LEADING_CONJ_RE.sub("", p).strip()
            if len(p) < 3:
                continue
            key = p.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(p)
    return out


def resolve_and_merge_symptoms(profile, symptoms: list[str]) -> dict:
    """Same shape as the clinical_csv orchestrator's merge step.

    Returns a summary dict describing what was added this turn so the
    answer LLM can mention it explicitly.
    """
    from chatbot.models import SessionPhenotypeProfile
    assert isinstance(profile, SessionPhenotypeProfile)

    # Bug #6 fix — split compound phrases before resolution.
    symptoms = _split_symptoms(symptoms)

    seen = {t.get("input_text", "").lower() for t in (profile.hpo_terms or [])}
    added_this_turn: list[dict] = []
    unresolved: list[str] = []

    for phrase in symptoms:
        key = phrase.lower().strip()
        if key in seen:
            continue
        seen.add(key)
        match = hpo.resolve(phrase)
        if not match.hpo_id:
            unresolved.append(phrase)
            continue
        added_this_turn.append({
            "input_text": phrase,
            "hpo_id": match.hpo_id,
            "name": match.name,
            "confidence": match.confidence,
            "matched_via": match.matched_via,
            "gene_count": len(match.genes),
        })
        profile.hpo_terms = (profile.hpo_terms or []) + [{
            "input_text": phrase,
            "hpo_id": match.hpo_id,
            "name": match.name,
            "confidence": match.confidence,
            "matched_via": match.matched_via,
        }]
        existing = set(profile.candidate_genes or [])
        existing.update(match.genes)
        profile.candidate_genes = sorted(existing)

    if added_this_turn or unresolved:
        profile.updated_at = timezone.now()
        profile.save()

    return {
        "added": added_this_turn,
        "unresolved": unresolved,
        "total_terms": len(profile.hpo_terms or []),
        "total_candidate_genes": len(profile.candidate_genes or []),
    }


# ---------------------------------------------------------------------------
# Stage 2 — Deterministic query executor
# ---------------------------------------------------------------------------

# Cap on how many rows we surface to the answer LLM in any single call.
_MAX_RESULT_ROWS = 20


@dataclass
class ExecutorResult:
    kind: str                                  # "rows" | "aggregate" | "schema" | "summary" | "empty"
    intent: str
    rows: list[dict[str, Any]] = field(default_factory=list)
    total_matched: int = 0
    universe: int = 0
    aggregate_table: list[dict[str, Any]] = field(default_factory=list)
    aggregate_caption: str = ""
    description: str = ""           # human-readable filter description
    schema_definition: str | None = None
    schema_column: str | None = None


def _intervar_includes(verdict_str: str | None, target: str | None) -> bool:
    """Case-insensitive substring match on the InterVar verdict."""
    if not target:
        return True
    return target.strip().lower() in (verdict_str or "").lower()


def _clinvar_includes(cv: str | None, target: str | None) -> bool:
    """Case-insensitive substring match; treat empty target as pass."""
    if not target:
        return True
    return target.strip().lower() in (cv or "").lower()


def _passes_acmg_flag(row: dict[str, Any], flag: str | None, want: int | None) -> bool:
    if not flag:
        return True
    acmg = row.get("acmg") or {}
    if want is None:
        want = 1  # default to "criterion fired"
    if flag in ("PVS1", "BA1"):
        return int(acmg.get(flag, 0)) == int(want)
    # PS / PM / PP / BS / BP — "any" semantics: at least one sub-criterion fired
    key = f"{flag}_any"
    return bool(acmg.get(key)) == bool(want)


def _passes_in_repeat(row: dict[str, Any], want: bool | None) -> bool:
    if want is None:
        return True
    has_repeat = bool(row.get("rmsk"))
    return has_repeat == want


def _passes_disease(row: dict[str, Any], term: str | None) -> bool:
    """Match a disease query against Orpha (free-text) and Phenotype_MIM."""
    if not term:
        return True
    t = term.strip().lower()
    haystack = " ".join(
        str(row.get(k) or "") for k in ("orpha", "phenotype_mim", "omim")
    ).lower()
    return t in haystack


def _passes_thresholds(row: dict[str, Any], d: RouterDecision) -> bool:
    if d.cadd_min is not None:
        if row.get("cadd_phred") is None or row["cadd_phred"] < d.cadd_min:
            return False
    if d.cadd_max is not None:
        if row.get("cadd_phred") is None or row["cadd_phred"] > d.cadd_max:
            return False
    if d.gnomad_max is not None:
        # Variant must have a frequency lower than gnomad_max. We treat
        # missing freq as "ultra-rare" (0.0) so they pass a `< 0.01` gate.
        f = row.get("freq_gnomad_all") or 0.0
        if f > d.gnomad_max:
            return False
    if d.gnomad_min is not None:
        f = row.get("freq_gnomad_all") or 0.0
        if f < d.gnomad_min:
            return False
    if d.sift_max is not None:
        s = row.get("sift_score")
        if s is None or s > d.sift_max:
            return False
    if d.metasvm_min is not None:
        m = row.get("metasvm")
        if m is None or m < d.metasvm_min:
            return False
    return True


def _passes_gene(row: dict[str, Any], gene: str | None) -> bool:
    if not gene:
        return True
    g = (row.get("gene") or "").upper()
    # Handle multi-gene cells ("NOC2L,SAMD11")
    return gene.upper() in {x.strip().upper() for x in g.split(",")}


def _matches_decision(row: dict[str, Any], d: RouterDecision) -> bool:
    if not _passes_gene(row, d.gene):
        return False
    if d.func and (row.get("func") or "").lower() != d.func.lower():
        return False
    if d.exonic_func and (row.get("exonic_func") or "").lower() != d.exonic_func.lower():
        return False
    if d.zygosity and row.get("zygosity") != d.zygosity:
        return False
    if not _passes_in_repeat(row, d.in_repeat):
        return False
    if not _clinvar_includes(row.get("clinvar"), d.clinvar_includes):
        return False
    if d.intervar_verdict:
        # Match exactly on word boundaries — "Likely benign" should NOT
        # match "Likely pathogenic" or "Benign" alone.
        verdict = (row.get("intervar") or "").strip().lower()
        if verdict != d.intervar_verdict.strip().lower():
            return False
    if not _passes_acmg_flag(row, d.acmg_flag, d.acmg_flag_value):
        return False
    if not _passes_thresholds(row, d):
        return False
    if not _passes_disease(row, d.disease_term):
        return False
    return True


# Pathogenicity ranking for "list all" surfacing order.
_INTERVAR_RANK = {
    "pathogenic": 0,
    "likely pathogenic": 1,
    "uncertain significance": 2,
    "likely benign": 3,
    "benign": 4,
    "unknown": 5,
}
_CLINVAR_RANK_KEYWORDS = [
    ("pathogenic", 1),
    ("likely_pathogenic", 2),
    ("conflicting", 3),
    ("uncertain", 4),
    ("likely_benign", 5),
    ("benign", 6),
    ("unk", 7),
]


def _row_priority(row: dict[str, Any]) -> tuple[int, int, int, float]:
    """Sort key — bucket priority first, then existing tie-breakers.

    Adding the combined-rule bucket as the *primary* key means Strong
    evidence (both ClinVar + InterVar agree pathogenic) surfaces above
    Algorithm-predicted (InterVar only) which surfaces above Conflicting
    — even if the legacy InterVar/ClinVar ranks would have ordered them
    differently. Matches the clinical_csv flow's surfacing order so the
    user gets a consistent experience across report types.
    """
    bucket_score = BUCKET_PRIORITY.get(bucket_for(row), 99)
    iv_score = _INTERVAR_RANK.get((row.get("intervar") or "").lower(), 99)
    cv = (row.get("clinvar") or "").lower()
    cv_score = 99
    for kw, score in _CLINVAR_RANK_KEYWORDS:
        if kw in cv:
            cv_score = score
            break
    cadd = row.get("cadd_phred") or 0.0
    return (bucket_score, iv_score, cv_score, -cadd)


def execute(parsed_data: dict, decision: RouterDecision, profile=None) -> ExecutorResult:
    """Run the deterministic Stage-2 query against parsed_data."""
    report = (parsed_data or {}).get("report") or {}
    rows: list[dict[str, Any]] = report.get("raw_rows") or []
    universe = len(rows)
    intent = decision.intent

    # -------- coord_lookup --------
    if intent == "coord_lookup":
        if decision.rsid:
            idx = (report.get("indexes") or {}).get("by_rsid") or {}
            matches = [rows[i] for i in idx.get(decision.rsid, [])]
            desc = f"variant with rsID = {decision.rsid}"
        elif decision.chr is not None and decision.start is not None:
            idx = (report.get("indexes") or {}).get("by_chrpos") or {}
            key = f"{decision.chr}:{decision.start}"
            matches = [rows[i] for i in idx.get(key, [])]
            desc = f"variant at chr{decision.chr}:{decision.start}"
        else:
            matches = []
            desc = "coordinate lookup (no chr/pos/rsid given)"
        return ExecutorResult(
            kind="rows" if matches else "empty",
            intent=intent, rows=matches[:_MAX_RESULT_ROWS],
            total_matched=len(matches), universe=universe, description=desc,
        )

    # -------- aggregate --------
    if intent == "aggregate":
        # Pre-filter by anything decision specifies (verdict, ClinVar, gene…)
        filtered = [r for r in rows if _matches_decision(r, decision)]
        gb = decision.group_by
        agg = decision.agg_func or "count"
        cap = decision.limit or 10
        table: list[dict[str, Any]] = []
        caption = ""

        if agg == "count":
            key_fn = {
                "gene": lambda r: r.get("gene") or "Unknown",
                "exonic_func": lambda r: r.get("exonic_func") or "(non-exonic)",
                "clinvar": lambda r: r.get("clinvar") or "UNK",
                "intervar": lambda r: r.get("intervar") or "Unknown",
                "chr": lambda r: f"chr{r.get('chr') or '?'}",
            }.get(gb, lambda r: r.get("gene") or "Unknown")
            counts = Counter(key_fn(r) for r in filtered)
            for label, n in counts.most_common(cap):
                table.append({"label": label, "count": n})
            caption = (
                f"count grouped by {gb or 'gene'}"
                + (f" (after filter: {_describe_filter(decision)})" if filtered != rows else "")
            )
        elif agg == "avg_cadd":
            buckets: dict[str, list[float]] = {}
            for r in filtered:
                if r.get("cadd_phred") is None:
                    continue
                key = (r.get(gb) if gb else None) or r.get("exonic_func") or "(unknown)"
                buckets.setdefault(key, []).append(r["cadd_phred"])
            for label, vals in sorted(buckets.items(), key=lambda kv: -sum(kv[1])/len(kv[1])):
                table.append({
                    "label": label,
                    "avg_cadd": round(sum(vals) / len(vals), 3),
                    "n": len(vals),
                })
            table = table[:cap]
            caption = f"average CADD_phred grouped by {gb or 'exonic_func'}"
        else:
            caption = f"unsupported agg_func={agg}; falling back to gene count"
            counts = Counter((r.get("gene") or "Unknown") for r in filtered)
            for label, n in counts.most_common(cap):
                table.append({"label": label, "count": n})

        return ExecutorResult(
            kind="aggregate" if table else "empty",
            intent=intent,
            aggregate_table=table,
            aggregate_caption=caption,
            total_matched=len(filtered),
            universe=universe,
            description=_describe_filter(decision),
        )

    # -------- summary --------
    if intent == "summary":
        return ExecutorResult(
            kind="summary", intent=intent,
            universe=universe, total_matched=universe,
            description="whole-report summary requested",
        )

    # -------- schema_lookup --------
    if intent == "schema_lookup":
        from reports.analyzers.intervar import COLUMN_REFERENCE
        col = decision.target_column or ""
        defn = COLUMN_REFERENCE.get(col)
        if defn is None:
            # Try case-insensitive
            for k, v in COLUMN_REFERENCE.items():
                if k.lower() == col.lower():
                    defn = v
                    col = k
                    break
        return ExecutorResult(
            kind="schema", intent=intent,
            universe=universe,
            schema_column=col,
            schema_definition=defn or "(no entry for that column in the schema reference)",
        )

    # -------- biofilter / acmg_clinvar / disease_link --------
    if intent in ("biofilter", "acmg_clinvar", "disease_link"):
        # Light optimisation: use the gene index when the user named a gene
        if decision.gene:
            idx = (report.get("indexes") or {}).get("by_gene") or {}
            candidate_rows = [rows[i] for i in idx.get(decision.gene.upper(), [])]
        else:
            candidate_rows = rows
        matched = [r for r in candidate_rows if _matches_decision(r, decision)]

        # Apply the combined ClinVar+InterVar pathogenicity rule UNLESS
        # the user explicitly asked for a benign / VUS / specific-verdict
        # row (in which case the filter would zero out their query).
        explicit_negative_verdict = (
            decision.intervar_verdict
            and decision.intervar_verdict.lower() in (
                "benign", "likely benign", "uncertain significance",
            )
        ) or (
            decision.clinvar_includes
            and decision.clinvar_includes.lower() in (
                "benign", "likely_benign", "uncertain_significance",
            )
        )
        if intent in ("acmg_clinvar", "disease_link") and not explicit_negative_verdict:
            matched = [r for r in matched if _is_clinically_interesting(r)]

        matched.sort(key=_row_priority)
        return ExecutorResult(
            kind="rows" if matched else "empty",
            intent=intent,
            rows=matched[:_MAX_RESULT_ROWS],
            total_matched=len(matched), universe=universe,
            description=_describe_filter(decision),
        )

    # -------- hpo_symptom --------
    if intent == "hpo_symptom":
        # The genes are accumulated by the orchestrator into the session
        # profile BEFORE this is called. Filter rows whose gene ∈ that set.
        candidate_genes: set[str] = set()
        if profile and (profile.candidate_genes or []):
            candidate_genes = {g.upper() for g in profile.candidate_genes}
        # We additionally filter to "clinically interesting" variants —
        # not the wall of common benign rows. Min standard: exonic /
        # splicing AND (Pathogenic-ish ClinVar OR any pathogenic ACMG
        # evidence OR CADD ≥ 20).
        matched: list[dict[str, Any]] = []
        idx = (report.get("indexes") or {}).get("by_gene") or {}
        seen_ids: set[int] = set()
        for g in candidate_genes:
            for row_idx in idx.get(g, []):
                if row_idx in seen_ids:
                    continue
                seen_ids.add(row_idx)
                r = rows[row_idx]
                if not _is_clinically_interesting(r):
                    continue
                matched.append(r)
        matched.sort(key=_row_priority)
        return ExecutorResult(
            kind="rows" if matched else "empty",
            intent=intent,
            rows=matched[:_MAX_RESULT_ROWS],
            total_matched=len(matched), universe=universe,
            description=(
                f"intersect HPO-derived genes ({len(candidate_genes)}) "
                f"with patient variants, then keep clinically-interesting"
            ),
        )

    # -------- other --------
    return ExecutorResult(
        kind="empty", intent=intent, universe=universe,
        description="no matching intent — fell through",
    )


# ---------------------------------------------------------------------------
# Combined ClinVar+InterVar pathogenicity rule (spec §3) — InterVar shape
# ---------------------------------------------------------------------------
#
# The bioinformatician (Saurabh) flagged that the InterVar flow was
# surfacing ``Conflicting_interpretations_of_pathogenicity`` rows AND
# ``BA1=1`` rows as disease-causing. The root cause was naïve substring
# matching (``"pathogenic" in cv``) plus no check on BA1. We now route
# every variant through the same :func:`tools.pathogenicity_bucket`
# function the clinical_csv flow uses — single source of truth for the
# rule across both report types.
#
# Because InterVar parsed rows use lower-cased keys (``gene``, ``clinvar``,
# ``intervar``, ``cadd_phred``, ``freq_gnomad_all``, ``acmg.{PVS1, BA1}``)
# vs. the column-cased keys clinical_csv uses (``Ref.Gene``,
# ``clinvar: Clinvar``, ``InterVar: InterVar and Evidence``,
# ``CADD_phred``, ``Freq_gnomAD_genome_ALL``, ``PVS1``, ``BA1``), we
# adapt the shape before calling the shared rule.


def _adapt_intervar_row(row: dict[str, Any]) -> dict[str, Any]:
    """Re-shape an InterVar parsed row into clinical_csv column names.

    Only the columns the bucket rule reads are mapped. Returning the
    same dict shape lets us delegate to :func:`tools.pathogenicity_bucket`
    without duplicating the rule logic.
    """
    acmg = row.get("acmg") or {}
    # InterVar's ``intervar`` is the parsed verdict ("Pathogenic" /
    # "Likely pathogenic" / "Uncertain significance" / "Likely benign"
    # / "Benign" / "Unknown") — the rule's ``InterVar: InterVar and
    # Evidence`` column expects "Verdict[ + evidence text]". We pass
    # the verdict alone, which the bucket's first-token parse handles.
    iv_verdict = (row.get("intervar") or "").strip()
    return {
        "Ref.Gene":                  row.get("gene"),
        "Func.refGene":              row.get("func"),
        "ExonicFunc.refGene":        row.get("exonic_func"),
        "AAChange.refGene":          row.get("aac_refgene"),
        "clinvar: Clinvar":          row.get("clinvar"),
        "InterVar: InterVar and Evidence": iv_verdict,
        "Freq_gnomAD_genome_ALL":    row.get("freq_gnomad_all"),
        "Freq_esp6500siv2_all":      row.get("freq_esp6500"),
        "Freq_1000g2015aug_all":     row.get("freq_1000g"),
        "CADD_phred":                row.get("cadd_phred"),
        "REVEL_score":               row.get("revel_score") if row.get("revel_score") is not None
                                     else row.get("metasvm"),
        # ACMG sub-criteria flags. The bucket rule only reads PVS1 / BA1
        # directly; the rest are surfaced for downstream UI consistency.
        "PVS1": "YES" if acmg.get("PVS1") == 1 else "NO",
        "PM2":  "YES" if acmg.get("PM_any") else "NO",
        "PP3":  "YES" if acmg.get("PP_any") else "NO",
        "BA1":  "YES" if acmg.get("BA1") == 1 else "NO",
        "Zygosity": row.get("zygosity"),
    }


def bucket_for(row: dict[str, Any]) -> Bucket:
    """Public: return the combined-rule bucket for an InterVar row."""
    return tools.pathogenicity_bucket(_adapt_intervar_row(row))


def _is_clinically_interesting(row: dict[str, Any]) -> bool:
    """True iff the bucket rule would surface this row.

    Replaces the previous naïve ``"pathogenic" in clinvar/intervar``
    substring match — that match was the root of Saurabh's complaint
    that Conflicting/BA1=1 variants were leaking into the disease-causing
    list. The new test delegates to :func:`bucket_for`; anything that
    isn't :data:`Bucket.DROP` is considered surfaceworthy.
    """
    return bucket_for(row) is not Bucket.DROP


def _describe_filter(d: RouterDecision) -> str:
    """Human-readable summary of the filter for the answer prompt."""
    parts = []
    if d.gene: parts.append(f"gene={d.gene}")
    if d.func: parts.append(f"func={d.func}")
    if d.exonic_func: parts.append(f"exonic_func={d.exonic_func}")
    if d.zygosity: parts.append(f"zygosity={d.zygosity}")
    if d.in_repeat is True: parts.append("in_repeat_region")
    if d.in_repeat is False: parts.append("NOT_in_repeat_region")
    if d.clinvar_includes: parts.append(f"clinvar~'{d.clinvar_includes}'")
    if d.intervar_verdict: parts.append(f"intervar={d.intervar_verdict}")
    if d.acmg_flag: parts.append(f"{d.acmg_flag}={d.acmg_flag_value or 1}")
    if d.cadd_min is not None: parts.append(f"CADD>{d.cadd_min}")
    if d.cadd_max is not None: parts.append(f"CADD<{d.cadd_max}")
    if d.gnomad_max is not None: parts.append(f"gnomAD<{d.gnomad_max}")
    if d.gnomad_min is not None: parts.append(f"gnomAD>{d.gnomad_min}")
    if d.sift_max is not None: parts.append(f"SIFT<{d.sift_max}")
    if d.metasvm_min is not None: parts.append(f"MetaSVM>{d.metasvm_min}")
    if d.disease_term: parts.append(f"disease~'{d.disease_term}'")
    return ", ".join(parts) or "no filter"


# ---------------------------------------------------------------------------
# Stage 3 — Answer renderer + LLM call
# ---------------------------------------------------------------------------

def _render_row(r: dict[str, Any]) -> str:
    aac = (r.get("aac_refgene") or "").split(",")[0]
    head = f"• **{r.get('gene') or '?'}** · {aac or '(no HGVS)'} · {r.get('func') or '?'}/{r.get('exonic_func') or '-'}"
    # Combined-rule evidence tier — same vocabulary as the clinical_csv
    # flow, so a patient seeing both report types in one chat gets a
    # consistent surface ("Strong evidence" vs "Algorithm-predicted"
    # vs "Conflicting" vs "Uncertain significance, but high computational
    # scores"). The label is verbatim from tools.BUCKET_LABEL so the LLM
    # can't paraphrase the tier wording.
    bucket = bucket_for(r)
    bits = [
        f"  - **Evidence tier:** {BUCKET_LABEL[bucket]}",
        f"  - chr{r.get('chr')}:{r.get('start')} {r.get('ref')}>{r.get('alt')}",
        f"  - ClinVar: {r.get('clinvar')}",
        f"  - InterVar: {r.get('intervar')}",
        f"  - Zygosity: {r.get('zygosity')}",
    ]
    if r.get("rsid"):
        bits.append(f"  - rsID: {r['rsid']}")
    if r.get("cadd_phred") is not None:
        bits.append(f"  - CADD_phred: {r['cadd_phred']}")
    if r.get("sift_score") is not None:
        bits.append(f"  - SIFT_score: {r['sift_score']}")
    if r.get("freq_gnomad_all") is not None:
        bits.append(f"  - gnomAD AF: {r['freq_gnomad_all']}")
    acmg = r.get("acmg") or {}
    flags = []
    if acmg.get("PVS1") == 1: flags.append("PVS1")
    if acmg.get("PS_any"): flags.append("PS")
    if acmg.get("PM_any"): flags.append("PM")
    if acmg.get("PP_any"): flags.append("PP")
    if acmg.get("BA1") == 1: flags.append("BA1")
    if acmg.get("BS_any"): flags.append("BS")
    if acmg.get("BP_any"): flags.append("BP")
    if flags:
        bits.append(f"  - ACMG evidence fired: {', '.join(flags)}")
    if r.get("omim") or r.get("orpha") or r.get("phenotype_mim"):
        dz = []
        if r.get("omim"): dz.append(f"OMIM:{r['omim']}")
        if r.get("phenotype_mim"): dz.append(f"PhenotypeMIM:{r['phenotype_mim']}")
        if r.get("orpha"): dz.append(f"Orpha:{r['orpha']}")
        bits.append(f"  - Disease links: {' · '.join(dz)}")
    return head + "\n" + "\n".join(bits)


def _render_executor(result: ExecutorResult) -> str:
    if result.kind == "empty":
        return (
            f"MATCHED RECORDS — 0 matches (universe: {result.universe} variants).\n"
            f"Filter applied: {result.description}\n"
        )
    if result.kind == "rows":
        head = (
            f"MATCHED RECORDS — {result.total_matched} match"
            f"{'es' if result.total_matched != 1 else ''} "
            f"(showing top {len(result.rows)} ranked by InterVar verdict "
            f"then ClinVar then CADD; universe: {result.universe}).\n"
            f"Filter applied: {result.description}\n"
        )
        return head + "\n" + "\n\n".join(_render_row(r) for r in result.rows)
    if result.kind == "aggregate":
        head = f"MATCHED RECORDS — {result.aggregate_caption} (universe: {result.universe}, filtered: {result.total_matched}).\n"
        if not result.aggregate_table:
            return head + "(no rows after filter)"
        # Render as Markdown-ish table
        cols = list(result.aggregate_table[0].keys())
        lines = [head, "| " + " | ".join(cols) + " |", "| " + " | ".join(["---"] * len(cols)) + " |"]
        for row in result.aggregate_table:
            lines.append("| " + " | ".join(str(row[c]) for c in cols) + " |")
        return "\n".join(lines)
    if result.kind == "schema":
        return (
            f"MATCHED RECORDS — schema lookup for column **{result.schema_column}**:\n"
            f"{result.schema_definition}\n"
        )
    if result.kind == "summary":
        return f"MATCHED RECORDS — full report summary requested (universe: {result.universe})."
    return f"MATCHED RECORDS — kind={result.kind} (no rendering)"


def _build_answer_user_message(
    user_message: str,
    parsed_data: dict,
    executor: ExecutorResult,
    profile,
    decision: RouterDecision,
    resolution_summary: dict,
) -> str:
    parts: list[str] = []

    # When the executor returned NO rows but we deliberately fell through
    # (broad genomic question with no concrete filter), surfacing a
    # "MATCHED RECORDS — 0 matches" header alongside the report-summary
    # block causes the LLM to pick the misleading "0 matches" signal and
    # contradict the report ("no disease-causing variants" when 6 LP
    # exist). Replace the executor render with an explicit framing that
    # points the LLM at the STRUCTURED ANALYSIS as the source for this
    # answer.
    broad_fallthrough = (
        executor.kind == "empty"
        and not (decision.gene or decision.rsid or decision.chr is not None
                 or decision.disease_term or decision.symptoms)
        and decision.intent in ("summary", "other", "aggregate")
    )
    if broad_fallthrough:
        parts.append(
            "QUERY CONTEXT — the user asked a broad question with no "
            "concrete filter (no gene, rsid, coordinate, ACMG flag, or "
            "specific verdict). Do NOT report \"0 matches\" or \"no "
            "variants.\" Use the STRUCTURED ANALYSIS block below as your "
            "source of truth — it contains the report's verdict counts, "
            "ACMG-flag totals, and the headline list of every Pathogenic / "
            "Likely pathogenic / PVS1=1 variant in the report. Quote "
            "those numbers and rows verbatim."
        )
    else:
        parts.append(_render_executor(executor))

    # Schema reference — always included, capped by analyzer to ~5kB
    schema_ref = (parsed_data or {}).get("schema_reference", "")
    if schema_ref:
        parts.append(
            "SCHEMA REFERENCE (column meanings — quote verbatim when "
            "the user asks what a column means):\n"
            f"```\n{schema_ref}\n```"
        )

    # Whole-report counts when intent=summary OR no executor rows
    structured = (parsed_data or {}).get("analysis_context", "")
    if structured and (decision.intent == "summary" or executor.kind == "empty"):
        parts.append(
            "STRUCTURED ANALYSIS (whole-report summary):\n"
            f"```\n{structured}\n```"
        )

    # HPO profile if any
    if profile and profile.hpo_terms:
        lines = ["SESSION HPO PROFILE (symptoms the user has reported):"]
        for t in profile.hpo_terms:
            if not t.get("hpo_id"):
                continue
            lines.append(
                f"  • {t['hpo_id']} {t['name']} "
                f"(matched from {t.get('input_text','?')!r}, "
                f"confidence={t.get('confidence', 0):.2f})"
            )
        parts.append("\n".join(lines))

    if resolution_summary.get("added"):
        lines = ["NEW SYMPTOMS RESOLVED THIS TURN:"]
        for a in resolution_summary["added"]:
            lines.append(
                f"  • {a['input_text']!r} → {a['hpo_id']} {a['name']!r} "
                f"({a['gene_count']} associated genes)"
            )
        parts.append("\n".join(lines))
    if resolution_summary.get("unresolved"):
        parts.append(
            "UNRESOLVED SYMPTOMS (do NOT fabricate HPO mappings — ask "
            "the user to clarify):\n  "
            + ", ".join(f"{s!r}" for s in resolution_summary["unresolved"])
        )

    parts.append(f"USER QUESTION: {user_message}")
    return "\n\n".join(parts)


def generate_answer(
    user_message: str,
    parsed_data: dict,
    executor: ExecutorResult,
    profile,
    decision: RouterDecision,
    resolution_summary: dict,
    history: list[dict],
) -> str:
    user_msg = _build_answer_user_message(
        user_message, parsed_data, executor, profile, decision, resolution_summary,
    )
    messages: list[dict] = [{"role": "system", "content": ANSWER_SYSTEM_PROMPT}]
    # Keep history short — same anti-poisoning policy as clinical_csv.
    prior = list(history)
    if prior and prior[-1].get("role") == "user":
        prior = prior[:-1]
    for m in prior[-2:]:
        messages.append({"role": m["role"], "content": m["content"]})
    messages.append({"role": "user", "content": user_msg})
    return _llm_call(messages, temperature=0.1, max_tokens=2048)


# ---------------------------------------------------------------------------
# Stage 4 — Safety tag
# ---------------------------------------------------------------------------

_DISCLAIMERS = {
    "counselor_referral": (
        "\n\n— *These findings are educational, not a diagnosis. Please "
        "discuss with a certified genetic counselor or your physician "
        "before acting on any of them.*"
    ),
    "general_disclaimer": (
        "\n\n— *Genomic information should be interpreted by a qualified "
        "clinician. This response is informational only.*"
    ),
}


def apply_safety_tag(answer: str, decision: RouterDecision) -> str:
    tag = _SAFETY_TAG.get(decision.intent, "general_disclaimer")
    if any(p in answer.lower() for p in (
        "genetic counselor", "discuss with a", "consult your",
        "discuss these findings",
    )):
        return answer
    return answer + _DISCLAIMERS[tag]


# ---------------------------------------------------------------------------
# Public entry
# ---------------------------------------------------------------------------

_VALID_INTENTS: frozenset[str] = frozenset({
    "coord_lookup", "biofilter", "acmg_clinvar", "disease_link",
    "aggregate", "hpo_symptom", "schema_lookup", "summary", "other",
})


# Salvage only fires for first-person STATEMENTS at the start of the
# message (anchored on ^) — "I feel X", "I get X", "I'm feeling X",
# "I sometimes feel X". This excludes question-form phrasings like
# "do I have any X?" / "do you have X?" which are NOT symptom reports.
_SYMPTOM_PATTERN_RE = re.compile(
    r"^\s*(?:i\s+(?:sometimes\s+|usually\s+|often\s+|always\s+)?"
    r"(?:feel|am\s+feeling|get|got|experience|experienced|suffer\s+from)"
    r"|i'?m\s+feeling|i'?ve\s+been\s+feeling|feeling)\s+"
    r"((?:[a-z\- ]{3,60}?))(?:[\.,;\?\!]|$| and\b| but\b)",
    re.IGNORECASE,
)

# Words that disqualify a message from symptom salvage even if it
# matches the pattern — these are unambiguously variant-lookup language.
_LOOKUP_DISQUALIFIERS_RE = re.compile(
    r"\b(?:variant|variants|mutation|mutations|gene|genes|allele|"
    r"rs\d{3,}|chr\d|chr[xy]|exon|intron|c\.\w|p\.\w|ACMG|PVS1|PS\d|"
    r"PM\d|PP\d|BA1|BS\d|BP\d|CADD|ClinVar|InterVar|OMIM|Orpha|"
    r"pathogenic|benign|likely)\b",
    re.IGNORECASE,
)


def _salvage_symptom_from_message(message: str) -> str | None:
    """When the router doesn't classify a "I feel X" message as hpo_symptom,
    extract X here so it still reaches HPO resolution. Returns None when
    no symptomatic phrasing is found, or when the message clearly looks
    like a gene/variant lookup (so we don't accidentally treat "I have
    any CFTR variants?" as a symptom of "any CFTR variants").
    """
    if not message:
        return None
    # Reject if the message has variant-lookup vocabulary anywhere.
    if _LOOKUP_DISQUALIFIERS_RE.search(message):
        return None
    m = _SYMPTOM_PATTERN_RE.match(message)
    if not m:
        return None
    chunk = m.group(1).strip()
    if not (3 <= len(chunk) <= 60):
        return None
    # Reject if the salvage starts with an uppercase token (looks like
    # a gene name): the original message probably wanted a biofilter.
    first_word = chunk.split()[0] if chunk.split() else ""
    if first_word and first_word[0].isupper() and first_word.isalnum() and len(first_word) <= 10:
        return None
    return chunk


def _normalize_intent(decision: RouterDecision, user_message: str = "") -> RouterDecision:
    """Map invalid / fuzzy intents back onto the supported vocabulary.

    The router LLM occasionally emits intents that aren't in our spec
    (e.g. ``gene``, ``variant_lookup``, ``filter``) or picks ``other``
    when extracted entities clearly imply a more specific intent. We
    deterministically re-route based on which entity was extracted —
    gene → biofilter, symptoms → hpo_symptom, rsid/chr → coord_lookup.
    This protects the executor from running with an intent that drops
    into the empty fallback when the user has provided a real filter.

    Salvage step: if the router missed an explicit "I feel / I have /
    I get X" symptom phrasing, we extract X here and force the intent
    to ``hpo_symptom`` so the HPO clarification path is reachable.
    """
    invalid = decision.intent not in _VALID_INTENTS
    too_generic = decision.intent == "other" and (
        decision.gene or decision.symptoms or decision.rsid or decision.chr is not None
    )
    needs_router_fix = invalid or too_generic

    # Symptom salvage — only when router did NOT extract symptoms and
    # message has an explicit "I feel/have/get X" pattern.
    if not decision.symptoms and user_message:
        salvaged = _salvage_symptom_from_message(user_message)
        if salvaged:
            log.warning(
                "intervar router: salvaging symptom %r from %r",
                salvaged, user_message[:80],
            )
            decision.symptoms = [salvaged]
            decision.intent = "hpo_symptom"
            decision.needs_hpo = True
            return decision

    if not needs_router_fix:
        return decision

    log.warning(
        "intervar router: re-routing intent=%r (gene=%r symptoms=%r rsid=%r chr=%r)",
        decision.intent, decision.gene, decision.symptoms, decision.rsid, decision.chr,
    )
    if decision.rsid or decision.chr is not None:
        decision.intent = "coord_lookup"
    elif decision.symptoms:
        decision.intent = "hpo_symptom"
        decision.needs_hpo = True
    elif decision.gene:
        decision.intent = "biofilter"
    else:
        decision.intent = "other"
    return decision


def _render_empty_answer(
    decision: RouterDecision,
    executor: ExecutorResult,
    resolution_summary: dict | None = None,
) -> str:
    """Deterministic 0-match reply — never lets the LLM hallucinate.

    Triggered when the executor returns ``kind="empty"``. Because the
    universe (3,252+ variants) is fully indexed, an empty result is
    *truth*; the safe rendering is a short factual statement that the
    LLM cannot improve on and is known to corrupt when given the chance.

    For HPO symptoms specifically: if the user's symptom phrase didn't
    resolve to any HPO term at all (e.g. "weird"), we surface that as a
    clarification request instead of confidently claiming the report has
    no matches "for weird" — that's Bug #7.
    """
    universe = executor.universe or 0
    intent = decision.intent
    summary = resolution_summary or {}

    if intent == "biofilter" and decision.gene:
        return (
            f"Your report contains **0 variants in the {decision.gene} gene**. "
            f"This was a complete scan across all {universe:,} annotated rows."
        )
    if intent == "coord_lookup" and decision.rsid:
        return (
            f"No variant with rsID **{decision.rsid}** is present in your "
            f"report. The scan covered all {universe:,} annotated rows."
        )
    if intent == "coord_lookup" and decision.chr is not None:
        loc = f"chr{decision.chr}:{decision.start}" if decision.start else f"chr{decision.chr}"
        return (
            f"No variant at **{loc}** is present in your report "
            f"(scanned {universe:,} annotated rows)."
        )
    if intent == "hpo_symptom":
        added = summary.get("added") or []
        unresolved = summary.get("unresolved") or []
        # Bug #7 — if NOTHING resolved against HPO, ask the user to rephrase.
        if not added and unresolved:
            unr = ", ".join(f'"{s}"' for s in unresolved)
            return (
                f"I couldn't map {unr} to a recognised clinical symptom "
                "(HPO — the Human Phenotype Ontology). Could you rephrase "
                "with more specific clinical language? For example: "
                "*muscle weakness*, *hearing loss*, *seizures*, *abdominal "
                "pain*, *fatigue*. The more specific the symptom name, "
                "the better I can search your report for related variants."
            )
        # At least one symptom resolved; report on those.
        resolved_names = [a.get("name") for a in added if a.get("name")]
        sym_str = ", ".join(resolved_names) if resolved_names else (
            ", ".join(decision.symptoms) if decision.symptoms else "those symptoms"
        )
        msg = (
            f"Your report contains **0 variants** in any gene currently "
            f"associated with {sym_str} (via HPO). The scan covered all "
            f"{universe:,} annotated rows. This is a meaningful clinical "
            "finding — the report does not show variants in the canonical "
            "genes linked to those symptoms; further targeted testing may "
            "still be warranted."
        )
        if unresolved:
            unr = ", ".join(f'"{s}"' for s in unresolved)
            msg += (
                f"\n\n*Note: I couldn't map {unr} to an HPO clinical term, "
                "so it wasn't part of the search. Try rephrasing with a "
                "more specific symptom name if that's relevant.*"
            )
        return msg
    if intent == "disease_link" and decision.disease_term:
        return (
            f"Your report contains **0 variants** with an OMIM/Orpha "
            f"annotation mentioning **{decision.disease_term}** "
            f"(scanned {universe:,} annotated rows)."
        )
    if intent == "acmg_clinvar":
        bits = []
        if decision.clinvar_includes: bits.append(f"ClinVar~'{decision.clinvar_includes}'")
        if decision.intervar_verdict: bits.append(f"InterVar='{decision.intervar_verdict}'")
        if decision.acmg_flag:
            v = decision.acmg_flag_value if decision.acmg_flag_value is not None else 1
            bits.append(f"{decision.acmg_flag}={v}")
        flt = " and ".join(bits) if bits else "this filter"
        return (
            f"Your report contains **0 variants** matching {flt} "
            f"(scanned {universe:,} annotated rows)."
        )
    # Catch-all (intent="other" or anything else) — graceful redirect,
    # no internal jargon. Fixes Edge #3 (off-topic queries).
    return (
        "I'm here to help you explore your **InterVar variant report**. "
        "I can answer things like:\n"
        "- *How many variants do I have? What classifications?*\n"
        "- *Do I have variants in [GENE]?* / *Tell me about rs…*\n"
        "- *Any likely pathogenic findings?* / *Anything with PVS1=1?*\n"
        "- *I have [symptom] — any related variants?*\n"
        "- *What does [column] mean?*\n\n"
        "Could you rephrase your question along one of those lines?"
    )


_GENOMIC_SIGNAL_WORDS = (
    # Variant / gene words
    "variant", "variants", "gene", "genes", "mutation", "mutations",
    "allele", "alleles", "snp", "snv", "indel", "rsid", "rs ",
    # Verdict / pathogenicity language
    "pathogenic", "benign", "vus", "uncertain", "harmful", "harm",
    "concerning", "concern", "actionable", "clinically", "clinical",
    "disease causing", "disease-causing", "disease", "diseases",
    "serious", "significant", "risk", "risky",
    # ACMG / scores
    "acmg", "pvs", "ps", "pm", "pp", "ba", "bs", "bp",
    "cadd", "clinvar", "intervar", "omim", "orpha",
    # Report words
    "report", "findings", "finding", "result", "results",
    # InterVar-specific
    "exon", "intron", "exonic", "intronic", "stopgain", "frameshift",
    "missense", "synonymous", "nonsynonymous", "splicing", "utr",
    # Symptom / phenotype framing
    "symptom", "symptoms", "carrier", "inherited", "heritable",
)


def _looks_like_genomic_question(message: str) -> bool:
    """True iff the message mentions anything report-related.

    Used to distinguish a broad genomic question that intent="other"
    couldn't categorise ("any disease-causing variants?") from a truly
    off-topic question ("what's the weather?"). Broad genomic questions
    are routed to the LLM with structured_analysis context so they get
    a real answer instead of the friendly redirect.
    """
    m = message.lower()
    return any(sig in m for sig in _GENOMIC_SIGNAL_WORDS)


def handle_intervar_message(session, user_message: str) -> str:
    """End-to-end agentic handler for one InterVar chat turn."""
    from chatbot.models import SessionPhenotypeProfile

    # Stage 1 — classify, then normalise to the supported intent set so
    # downstream code never sees a free-form router emission.
    decision = classify(user_message)
    decision = _normalize_intent(decision, user_message)
    log.warning(
        "intervar router: intent=%s needs_hpo=%s gene=%r symptoms=%r",
        decision.intent, decision.needs_hpo, decision.gene, decision.symptoms,
    )

    # Stage 1.5
    profile, _ = SessionPhenotypeProfile.objects.get_or_create(session=session)
    resolution_summary: dict = {"added": [], "unresolved": [], "total_terms": 0, "total_candidate_genes": 0}
    if decision.needs_hpo and decision.symptoms:
        resolution_summary = resolve_and_merge_symptoms(profile, decision.symptoms)

    # Stage 2 — deterministic executor
    parsed_data = (session.report.parsed_data if session.report else {}) or {}
    executor = execute(parsed_data, decision, profile=profile)
    log.warning(
        "intervar executor: kind=%s matched=%d universe=%d",
        executor.kind, executor.total_matched, executor.universe,
    )

    # Stage 2.5 — short-circuit ONLY when a specific filter failed.
    #
    # Originally short-circuited on every empty result to prevent
    # adversarial-absent-gene hallucinations (BRCA1 etc.). That over-
    # caught broad exploration questions like "any disease-causing
    # variants?" where the user expects the LLM to use the structured
    # analysis (verdict counts + headline LP list) to answer. So we
    # now short-circuit only when the user named a concrete entity that
    # the indexed scan failed to find — gene / rsid / chr:pos / disease
    # term / unresolved HPO symptom only. Everything else falls through
    # to the LLM with structured_analysis context so it can still answer.
    has_specific_filter = bool(
        decision.gene or decision.rsid or decision.chr is not None
        or decision.disease_term
    )
    hpo_all_unresolved = (
        decision.intent == "hpo_symptom"
        and not resolution_summary.get("added")
        and resolution_summary.get("unresolved")
    )
    if executor.kind == "empty" and (has_specific_filter or hpo_all_unresolved):
        empty_reply = _render_empty_answer(decision, executor, resolution_summary)
        return apply_safety_tag(empty_reply, decision)

    # Off-topic guard: intent=other with NO biological signal and NO
    # entity at all — still redirect gracefully (e.g. "what's the
    # weather?"). For broad genomic questions ("any disease-causing
    # variants?"), let the LLM answer with the report's summary.
    if executor.kind == "empty" and decision.intent == "other":
        if not _looks_like_genomic_question(user_message):
            empty_reply = _render_empty_answer(decision, executor, resolution_summary)
            return apply_safety_tag(empty_reply, decision)

    # Stage 3 — answer LLM
    history = [
        {"role": m.role, "content": m.content}
        for m in session.messages.all()
    ]
    answer = generate_answer(
        user_message, parsed_data, executor, profile, decision,
        resolution_summary, history,
    )

    # Stage 3.5 — HGVS validator (same as clinical_csv)
    answer, fabricated = validate_answer(answer, parsed_data)
    if fabricated:
        log.warning(
            "intervar validator stripped %d fabricated token(s): %s",
            len(fabricated), fabricated,
        )

    # Stage 4 — safety
    return apply_safety_tag(answer, decision)


def _decision_dict(d: RouterDecision) -> dict:  # pragma: no cover
    return asdict(d)
