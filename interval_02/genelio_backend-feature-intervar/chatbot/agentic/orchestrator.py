"""Agentic orchestrator for clinical_csv chat — 6-stage pipeline.

Implements the architecture in §6 of the multi-API integration spec.
Reworked from the earlier 4-stage flow to:

  Stage 1   Intent Classifier (LLM)
              → category + extracted entities
  Stage 2   Entity Resolution
              → symptoms → HPO; disease → OMIM/Orphanet; gene → direct;
                column → docs lookup; body-system → curated HP root
  Stage 3   Local DB Query
              → combined ClinVar+InterVar pathogenicity filter; reads
                Mode_of_Inheritance / HPO_ID / MONDO_ID / OMIM_ID from
                the row before going external
  Stage 4   Enrichment (if needed)
              → OMIM API for disease descriptions, Orphanet API for
                rare-disease context, HPO API for phenotype lists.
                Only called when the local column was sparse — fail-safe
                when the external clients are disabled (default).
  Stage 5   Inheritance Resolution
              → local Mode_of_Inheritance → OMIM clinicalSynopsis fallback
                → HPO HP:0000005 subtree fallback
  Stage 6   Answer Assembly
              → deterministic category renderer (categories.py). LLM is
                used ONLY for free-form fallback (intent_category="other"
                or a category we couldn't ground).

Single public entry: :func:`handle_clinical_csv_message`.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass, field

from django.conf import settings
from django.utils import timezone

from . import (
    body_systems,
    categories,
    external_apis,
    hpo,
    local_context as lc,
    tools,
)
from .prompts import ANSWER_SYSTEM_PROMPT, ROUTER_SYSTEM_PROMPT
from .validator import validate_answer

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Stage 1 — Router output type
# ---------------------------------------------------------------------------

@dataclass
class RouterDecision:
    intent_category: str = "other"
    needs_hpo: bool = False
    symptoms: list[str] = field(default_factory=list)
    target_gene: str | None = None
    target_variant: str | None = None
    target_disease: str | None = None
    target_body_system: str | None = None
    target_column: str | None = None
    intersect_symptoms: bool = False
    wants_schema_lookup: bool = False
    wants_summary: bool = False
    wants_count_aggregate: bool = False
    raw: dict = field(default_factory=dict)


# Safety-tag map per the PDF §6 (reproductive questions always get the
# counselor referral; everything else gets the general disclaimer).
_SAFETY_TAG = {
    "A1": "counselor_referral",
    "A2": "general_disclaimer",
    "B1": "general_disclaimer",
    "C1": "counselor_referral",
    "C2": "counselor_referral",
    "D1": "counselor_referral",
    "D2": "counselor_referral",
    "E1": "counselor_referral",
    "E2": "counselor_referral",
    "F1": "counselor_referral",
    "F2": "counselor_referral",
    "other": "general_disclaimer",
}


# ---------------------------------------------------------------------------
# LLM client (reuses the chatbot.pipeline singleton)
# ---------------------------------------------------------------------------

def _llm_call(messages: list[dict], *, temperature: float, max_tokens: int) -> str:
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


# ---------------------------------------------------------------------------
# Stage 1 — Router
# ---------------------------------------------------------------------------

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


_VALID_CATEGORIES: frozenset[str] = frozenset(
    {"A1", "A2", "B1", "C1", "C2", "D1", "D2", "E1", "E2", "F1", "F2", "other"}
)


def classify(user_message: str) -> RouterDecision:
    raw = _llm_call(
        [
            {"role": "system", "content": ROUTER_SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ],
        temperature=0.0,
        max_tokens=500,
    )
    parsed = _safe_parse_json(raw) or {}
    log.info("router raw=%r parsed=%r", raw[:200], parsed)
    cat = parsed.get("intent_category") or "other"
    if cat not in _VALID_CATEGORIES:
        cat = "other"
    return RouterDecision(
        intent_category=cat,
        needs_hpo=bool(parsed.get("needs_hpo")),
        symptoms=[s.strip() for s in (parsed.get("symptoms") or [])
                  if isinstance(s, str) and s.strip()],
        target_gene=(parsed.get("target_gene") or None),
        target_variant=(parsed.get("target_variant") or None),
        target_disease=(parsed.get("target_disease") or None),
        target_body_system=(parsed.get("target_body_system") or None),
        target_column=(parsed.get("target_column") or None),
        intersect_symptoms=bool(parsed.get("intersect_symptoms")),
        wants_schema_lookup=bool(parsed.get("wants_schema_lookup")),
        wants_summary=bool(parsed.get("wants_summary")),
        wants_count_aggregate=bool(parsed.get("wants_count_aggregate")),
        raw=parsed,
    )


# ---------------------------------------------------------------------------
# Stage 2 — Entity resolution helpers
# ---------------------------------------------------------------------------

def resolve_and_merge_symptoms(profile, symptoms: list[str]) -> dict:
    """Resolve each NEW symptom against HPO; merge into the profile."""
    from chatbot.models import SessionPhenotypeProfile
    assert isinstance(profile, SessionPhenotypeProfile)

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
        existing_genes = set(profile.candidate_genes or [])
        existing_genes.update(match.genes)
        profile.candidate_genes = sorted(existing_genes)

    if added_this_turn or unresolved:
        profile.updated_at = timezone.now()
        profile.save()

    return {
        "added": added_this_turn,
        "unresolved": unresolved,
        "total_terms": len(profile.hpo_terms or []),
        "total_candidate_genes": len(profile.candidate_genes or []),
    }


def _rows(parsed_data: dict) -> list[dict]:
    return (parsed_data or {}).get("report", {}).get("raw_rows", []) or []


def _filter_for_gene_set(
    parsed_data: dict, gene_set: set[str], *, max_results: int = 8,
) -> tools.FilterResult:
    return tools.filter_variants(
        parsed_data, gene_filter=gene_set,
        apply_pathogenicity_filter=True, max_results=max_results,
    )


def _zip_matched(fr: tools.FilterResult) -> list[tuple[dict, tools.Bucket]]:
    """Pair each matched row with its bucket — ready for category renderers."""
    return list(zip(fr.matched, fr.buckets or []))


# ---------------------------------------------------------------------------
# Stage 5 — Inheritance resolution
# ---------------------------------------------------------------------------

def _resolve_inheritance(row: dict) -> str | None:
    """Local Mode_of_Inheritance → OMIM clinicalSynopsis → HPO subtree.

    External calls are skipped automatically when their clients are
    disabled (the default in dev). The local column carries the answer
    for ~95% of rows in practice.
    """
    inh = lc.inheritance_pattern(row)
    if inh:
        return inh
    # Fallback 1: OMIM clinicalSynopsis.
    for omim_id in lc.omim_ids(row)[:1]:
        entry = external_apis.omim_api.entry(omim_id)
        if entry and entry.inheritance:
            return entry.inheritance
    # Fallback 2: HPO inheritance subtree — placeholder; requires OBO.
    return None


# ---------------------------------------------------------------------------
# Stage 6 — Category dispatch
# ---------------------------------------------------------------------------

def _render_A1(parsed_data: dict, decision: RouterDecision) -> str:
    fr = tools.filter_variants(
        parsed_data, apply_pathogenicity_filter=True, max_results=20,
    )
    return categories.render_A1(_zip_matched(fr), fr.universe)


def _render_A2(parsed_data: dict, decision: RouterDecision) -> str | None:
    """Pick the variant under discussion. Without a target, fall back."""
    rows = _rows(parsed_data)
    target_row: dict | None = None
    if decision.target_variant:
        v = decision.target_variant.lower()
        for r in rows:
            if v in (r.get("AAChange.refGene") or "").lower():
                target_row = r
                break
    elif decision.target_gene:
        g = decision.target_gene.upper()
        for r in rows:
            if (r.get("Ref.Gene") or "").upper() == g:
                target_row = r
                break
    if target_row is None:
        return None
    return categories.render_A2(target_row)


def _render_B1(parsed_data: dict, decision: RouterDecision) -> str | None:
    col = decision.target_column
    if not col:
        return None
    defn = tools.lookup_column(col)
    return categories.render_B1(col, defn)


def _render_C1(parsed_data: dict, decision: RouterDecision) -> str | None:
    bs = decision.target_body_system
    if not bs:
        return None
    match = body_systems.resolve_body_system(bs)
    if match is None:
        return None
    fr = _filter_for_gene_set(parsed_data, set(match.genes), max_results=10)
    return categories.render_C1(
        body_system_name=match.body_system.name,
        hpo_root_id=match.body_system.root_hpo_id,
        matched_with_buckets=_zip_matched(fr),
        universe=fr.universe,
    )


def _render_C2(parsed_data: dict, decision: RouterDecision,
               profile, resolution: dict) -> str | None:
    added = resolution.get("added") or []
    if not added:
        return None
    # Build gene sets per symptom for intersect/union.
    gene_lists = [set(hpo.genes_for(a["hpo_id"])) for a in added]
    gene_lists = [g for g in gene_lists if g]
    if not gene_lists:
        return None
    if decision.intersect_symptoms and len(gene_lists) > 1:
        gene_set = set.intersection(*gene_lists)
        intersect_used = True
        # If intersection is empty, fall back to union — surface SOMETHING.
        if not gene_set:
            gene_set = set.union(*gene_lists)
            intersect_used = False
    else:
        gene_set = set.union(*gene_lists)
        intersect_used = False
    fr = _filter_for_gene_set(parsed_data, gene_set, max_results=8)
    return categories.render_C2(
        symptoms=decision.symptoms,
        resolved_hpo=added,
        matched_with_buckets=_zip_matched(fr),
        universe=fr.universe,
        intersect_mode=intersect_used,
    )


def _render_D1(parsed_data: dict, decision: RouterDecision) -> str | None:
    disease = decision.target_disease
    if not disease:
        return None
    rows = _rows(parsed_data)
    # Step 1 — find local rows whose ClinVar_Disease matches the term.
    needle = disease.lower()
    candidate_rows = [
        r for r in rows
        if any(needle in n.lower() for n in lc.clinvar_disease_names(r))
    ]
    candidate_genes = sorted({(r.get("Ref.Gene") or "").upper()
                              for r in candidate_rows
                              if r.get("Ref.Gene")})
    omim_id = next(
        (i for r in candidate_rows for i in lc.omim_ids(r) if i),
        None,
    )
    # Step 2 — enrich.
    inheritance = (
        next((lc.inheritance_pattern(r) for r in candidate_rows
              if lc.inheritance_pattern(r)), None)
        or (external_apis.omim_api.entry(omim_id).inheritance
            if omim_id and external_apis.omim_api.entry(omim_id) else None)
    )
    phenotypes: list[str] = []
    if omim_id:
        pheno = external_apis.hpo_api.phenotypes_for_disease(
            f"OMIM:{omim_id.split(':',1)[-1]}"
        )
        phenotypes = [p.name for p in pheno if p.name]
    # Step 3 — apply pathogenicity filter to the patient's variants in those genes.
    fr = _filter_for_gene_set(parsed_data, set(candidate_genes), max_results=8) \
        if candidate_genes else tools.FilterResult(
            matched=[], skipped=0, universe=len(rows), candidate_genes=[],
        )
    return categories.render_D1(
        disease_name=disease,
        omim_id=f"OMIM:{omim_id}" if omim_id else None,
        orpha_code=None,  # populated by Orphanet enrichment when enabled
        inheritance=inheritance,
        typical_phenotypes=phenotypes,
        matched_with_buckets=_zip_matched(fr),
        candidate_genes=candidate_genes,
    )


def _render_D2(parsed_data: dict, decision: RouterDecision) -> str | None:
    gene = decision.target_gene
    if not gene:
        return None
    g = gene.upper()
    rows = [r for r in _rows(parsed_data)
            if (r.get("Ref.Gene") or "").upper() == g]
    if not rows:
        return None
    # Local-first disease list.
    seen: set[str] = set()
    diseases: list[dict] = []
    for row in rows:
        names = lc.clinvar_disease_names(row)
        omim = next(iter(lc.omim_ids(row) or []), None)
        for name in (names or [None]):
            key = (name or "").lower() or (omim or "")
            if key in seen:
                continue
            seen.add(key)
            entry = {
                "name": name or "(condition not in ClinVar disease field)",
                "omim_id": omim,
                "orpha_code": None,
                "omim_description": "",
                "orphanet_summary": "",
                "prevalence": None,
                "phenotypes": [],
                "inheritance": lc.inheritance_pattern(row),
            }
            # Enrich (only if external API enabled).
            if omim:
                om = external_apis.omim_api.entry(omim)
                if om:
                    entry["omim_description"] = om.description
                    entry["inheritance"] = entry["inheritance"] or om.inheritance
                pheno = external_apis.hpo_api.phenotypes_for_disease(
                    f"OMIM:{omim}"
                )
                entry["phenotypes"] = [p.name for p in pheno if p.name]
            diseases.append(entry)

    fr = _filter_for_gene_set(parsed_data, {g}, max_results=6)
    return categories.render_D2(
        gene=g, diseases=diseases, matched_with_buckets=_zip_matched(fr),
    )


def _pick_row_for_inheritance(parsed_data: dict,
                              decision: RouterDecision) -> dict | None:
    rows = _rows(parsed_data)
    if decision.target_variant:
        v = decision.target_variant.lower()
        for r in rows:
            if v in (r.get("AAChange.refGene") or "").lower():
                return r
    if decision.target_gene:
        g = decision.target_gene.upper()
        for r in rows:
            if (r.get("Ref.Gene") or "").upper() == g:
                return r
    return None


def _render_E1(parsed_data: dict, decision: RouterDecision) -> str | None:
    row = _pick_row_for_inheritance(parsed_data, decision)
    if row is None:
        return None
    # Mutate the inheritance back into the row dict so the renderer
    # sees the resolved-by-fallback value when the local column was empty.
    resolved = _resolve_inheritance(row)
    if resolved and not row.get("Mode_of_Inheritance"):
        row = {**row, "Mode_of_Inheritance": resolved}
    disease = next(iter(lc.clinvar_disease_names(row)), None)
    return categories.render_E1(row, disease_name=disease)


def _render_E2(parsed_data: dict, decision: RouterDecision) -> str | None:
    row = _pick_row_for_inheritance(parsed_data, decision)
    if row is None:
        return None
    resolved = _resolve_inheritance(row)
    if resolved and not row.get("Mode_of_Inheritance"):
        row = {**row, "Mode_of_Inheritance": resolved}
    disease = next(iter(lc.clinvar_disease_names(row)), None)
    return categories.render_E2(row, disease_name=disease)


def _render_F1(parsed_data: dict, decision: RouterDecision) -> str:
    return categories.render_F1(_rows(parsed_data))


def _render_F2(parsed_data: dict, decision: RouterDecision) -> str:
    return categories.render_F2(_rows(parsed_data))


# Lookup table — keep dispatch flat so adding a new category is one line.
_DISPATCH = {
    "A1": _render_A1,
    "A2": _render_A2,
    "B1": _render_B1,
    "C1": _render_C1,
    "C2": _render_C2,
    "D1": _render_D1,
    "D2": _render_D2,
    "E1": _render_E1,
    "E2": _render_E2,
    "F1": _render_F1,
    "F2": _render_F2,
}


# ---------------------------------------------------------------------------
# Stage 6 fallback — free-form LLM answer for "other" / unrecognised
# ---------------------------------------------------------------------------

def _build_freeform_user_message(
    user_message: str,
    parsed_data: dict,
    filter_result: tools.FilterResult,
    profile,
    decision: RouterDecision,
    resolution_summary: dict,
) -> str:
    parts: list[str] = []
    structured = (parsed_data or {}).get("analysis_context", "")
    if structured and not filter_result.matched:
        parts.append(
            "STRUCTURED ANALYSIS (whole-report summary):\n"
            f"```\n{structured}\n```"
        )
    elif structured:
        cutoff = structured.find("=== PRE-COMPUTED FILTERED LISTS ===")
        if cutoff > 0:
            parts.append(
                "STRUCTURED ANALYSIS (top-level counts only):\n"
                f"```\n{structured[:cutoff].strip()}\n```"
            )
        else:
            parts.append("STRUCTURED ANALYSIS:\n"
                         f"```\n{structured[:1500]}\n```")
    parts.append(tools.render_filter_result(filter_result))
    schema_ref = (parsed_data or {}).get("schema_reference", "")
    if schema_ref:
        parts.append("SCHEMA REFERENCE (column meanings):\n"
                     f"```\n{schema_ref}\n```")
    if profile and profile.hpo_terms:
        hpo_block = tools.render_hpo_profile(profile.hpo_terms)
        if hpo_block:
            parts.append(hpo_block)
    parts.append(f"USER QUESTION: {user_message}")
    return "\n\n".join(parts)


def generate_freeform_answer(
    user_message: str, parsed_data: dict, filter_result: tools.FilterResult,
    profile, decision: RouterDecision, resolution_summary: dict,
    history: list[dict],
) -> str:
    user_msg = _build_freeform_user_message(
        user_message, parsed_data, filter_result, profile,
        decision, resolution_summary,
    )
    messages: list[dict] = [{"role": "system", "content": ANSWER_SYSTEM_PROMPT}]
    prior = list(history)
    if prior and prior[-1].get("role") == "user":
        prior = prior[:-1]
    for m in prior[-2:]:
        messages.append({"role": m["role"], "content": m["content"]})
    messages.append({"role": "user", "content": user_msg})
    return _llm_call(messages, temperature=0.1, max_tokens=2048)


# ---------------------------------------------------------------------------
# Safety tag (compatible with existing tests + InterVar orchestrator)
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
    """Idempotent — won't append a disclaimer if one is already present.

    Category renderers add their own disclaimer; this guard catches the
    free-form LLM path where the model may have skipped it.
    """
    tag = _SAFETY_TAG.get(decision.intent_category, "general_disclaimer")
    if any(p in answer.lower() for p in (
        "genetic counselor", "discuss with a", "consult your",
        "discuss these findings", "discuss with your physician",
    )):
        return answer
    return answer + _DISCLAIMERS[tag]


# ---------------------------------------------------------------------------
# Public entry — 6-stage flow
# ---------------------------------------------------------------------------

def handle_clinical_csv_message(session, user_message: str) -> str:
    """End-to-end agentic handler for a single clinical_csv chat turn."""
    from chatbot.models import SessionPhenotypeProfile

    # Stage 1 — classify
    decision = classify(user_message)
    log.info("router decision: %s", decision)

    # Stage 2 — entity resolution
    profile, _ = SessionPhenotypeProfile.objects.get_or_create(session=session)
    resolution_summary: dict = {"added": [], "unresolved": [],
                                "total_terms": 0, "total_candidate_genes": 0}
    if decision.needs_hpo and decision.symptoms:
        resolution_summary = resolve_and_merge_symptoms(profile, decision.symptoms)

    parsed_data = (session.report.parsed_data if session.report else {}) or {}

    # Stages 3–6 — category dispatch (deterministic, local-first).
    answer: str | None = None
    handler = _DISPATCH.get(decision.intent_category)
    if handler is not None:
        try:
            if decision.intent_category == "C2":
                answer = handler(parsed_data, decision, profile, resolution_summary)
            else:
                answer = handler(parsed_data, decision)
        except Exception:  # noqa: BLE001 — fall back to free-form below
            log.exception(
                "category %s handler crashed; falling back to free-form",
                decision.intent_category,
            )
            answer = None

    if answer is None:
        # Free-form fallback (intent_category="other", or a category
        # handler that returned None because it couldn't anchor on an
        # entity). Still uses the combined pathogenicity rule via
        # filter_variants() so the model sees the right ranking.
        filter_result = tools.filter_variants(
            parsed_data,
            gene_filter=(profile.candidate_genes or None)
            if decision.needs_hpo else None,
            apply_pathogenicity_filter=True, max_results=8,
        )
        history = [
            {"role": m.role, "content": m.content}
            for m in session.messages.all()
        ]
        answer = generate_freeform_answer(
            user_message, parsed_data, filter_result, profile,
            decision, resolution_summary, history,
        )
        # Validator only matters for LLM-generated text. Category renderers
        # quote from the rows directly, so they're safe.
        answer, fabricated = validate_answer(answer, parsed_data)
        if fabricated:
            log.warning(
                "orchestrator: validator stripped %d fabricated token(s): %s",
                len(fabricated), fabricated,
            )

    return apply_safety_tag(answer, decision)


# Debug helper — kept for parity with the InterVar orchestrator.
def _decision_dict(d: RouterDecision) -> dict:  # pragma: no cover
    return asdict(d)
