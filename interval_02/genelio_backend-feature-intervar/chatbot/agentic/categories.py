"""Per-category deterministic response renderers.

Spec §4: every question category has a fixed response template. The
LLM is allowed to fill in *plain-language paraphrase* fields, but the
clinical claims — gene names, HGVS, ClinVar/InterVar verdict, ACMG
codes, inheritance pattern, zygosity-conditional risk paragraph — come
from local data. This stops the model from inventing reassuring or
alarming wording that the data does not support.

Each renderer takes a small structured payload and returns a finished
patient-facing string (Markdown). Disclaimers and counselor-referral
safety tags are added by the renderer, not by an LLM postscript, so
they are always present.

Categories rendered here (one-to-one with PDF §4):

  A1  "Are any of my variants harmful?"
  A2  "Why does ClinVar say one thing and InterVar another?"
  B1  "What does this column in my report mean?"
  C1  "Do I have any variants related to <body system>?"
  C2  "I have <symptom1> and <symptom2> — what could it be?"
  D1  "Do I have anything related to <disease>?"
  D2  "Tell me about the disease this gene causes."
  E1  "How is this condition inherited?"
  E2  "Will my children inherit this?"
  F1  "Am I a carrier for any recessive conditions?"
  F2  "What secondary findings do I have?"
"""
from __future__ import annotations

from typing import Any, Iterable

from . import local_context as lc
from .tools import BUCKET_LABEL, Bucket


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_STD_DISCLAIMER = (
    "\n\n— *This is a finding, not a diagnosis. Please discuss with your "
    "physician or a certified genetic counselor before acting on it.*"
)

_REPRO_DISCLAIMER = (
    "\n\n— *Reproductive decisions require a counselor-led recurrence-risk "
    "assessment based on your full family history, your partner's "
    "genetic status, and other factors. Please consult a certified "
    "genetic counselor.*"
)


def _aachange(row: dict[str, Any]) -> str:
    """Single canonical HGVS string from the comma-joined column."""
    aac = row.get("AAChange.refGene") or row.get("HGVSg") or ""
    if not aac:
        return ""
    return aac.split(",")[0].strip()


def _gene(row: dict[str, Any]) -> str:
    return (row.get("Ref.Gene") or "").strip() or "?"


def _omim_link(row: dict[str, Any]) -> str | None:
    ids = lc.omim_ids(row)
    if not ids:
        return None
    return f"OMIM:{ids[0]}"


def _row_one_liner(row: dict[str, Any], bucket: Bucket) -> str:
    """The reusable per-variant bullet used by A1, C1, C2, D1, F1."""
    parts = [
        f"**{_gene(row)}** ({_aachange(row)})" if _aachange(row) else f"**{_gene(row)}**",
        f"ClinVar: {row.get('clinvar: Clinvar') or 'no_clinvar'}",
        f"InterVar: {(row.get('InterVar: InterVar and Evidence') or '').split(' ')[0] or 'n/a'}",
        f"Zygosity: {lc.zygosity(row) or '?'}",
    ]
    inh = lc.inheritance_pattern(row)
    if inh:
        parts.append(f"Inheritance: {inh}")
    omim = _omim_link(row)
    if omim:
        parts.append(omim)
    return f"{' · '.join(parts)}  \n  *Evidence tier: {BUCKET_LABEL[bucket]}*"


# ---------------------------------------------------------------------------
# Category A — Variant Pathogenicity
# ---------------------------------------------------------------------------

def render_A1(
    matched_with_buckets: Iterable[tuple[dict[str, Any], Bucket]],
    universe: int,
) -> str:
    """A1 "Are any of my variants harmful?"

    Spec template:
       "I found {N} variants that both ClinVar and InterVar classify as
        pathogenic or likely pathogenic: <list>. Additionally, {M} are
        flagged by one source but not the other — worth discussing with
        a counselor."
    """
    pairs = list(matched_with_buckets)
    strong = [(r, b) for r, b in pairs if b is Bucket.STRONG_PATHOGENIC]
    one_sided = [
        (r, b) for r, b in pairs
        if b in (Bucket.CLINVAR_PATHOGENIC, Bucket.INTERVAR_PATHOGENIC,
                 Bucket.CONFLICTING_PATHOGENIC, Bucket.VUS_HIGH_SCORE)
    ]

    lines: list[str] = []
    if strong:
        lines.append(
            f"I found **{len(strong)}** variant{'s' if len(strong) != 1 else ''} "
            f"that both ClinVar and InterVar classify as pathogenic or "
            f"likely pathogenic:\n"
        )
        for i, (row, bucket) in enumerate(strong, 1):
            lines.append(f"{i}. {_row_one_liner(row, bucket)}")
        lines.append("")
    else:
        lines.append(
            "I did not find any variants where **both** ClinVar and "
            "InterVar agree on a pathogenic or likely-pathogenic call."
        )
        lines.append("")

    if one_sided:
        lines.append(
            f"Additionally, **{len(one_sided)}** variant"
            f"{'s are' if len(one_sided) != 1 else ' is'} flagged by one "
            f"source but not the other (or surface only under high "
            f"computational scores) — these are worth discussing with a "
            f"counselor:\n"
        )
        for i, (row, bucket) in enumerate(one_sided, 1):
            lines.append(f"{i}. {_row_one_liner(row, bucket)}")
        lines.append("")

    lines.append(f"*Scanned {universe} variants in your report.*")
    return "\n".join(lines).rstrip() + _STD_DISCLAIMER


def render_A2(row: dict[str, Any]) -> str:
    """A2 "Why does ClinVar say one thing and InterVar another?"

    Spec template explains the **source** difference (curated humans vs
    algorithmic ACMG) and pulls the patient's specific star rating +
    ACMG codes from the row.
    """
    clinvar = row.get("clinvar: Clinvar") or "(none)"
    stars = row.get("ClinVar_Stars") or "?"
    review = row.get("ClinVar_Review_Status") or "?"
    intervar = row.get("InterVar: InterVar and Evidence") or "(none)"
    # The InterVar string is "VERDICT FLAG1=1, FLAG2=0, …"
    iv_verdict = intervar.split(" ", 1)[0] if intervar else ""
    iv_codes = ""
    if " " in intervar:
        iv_codes = intervar.split(" ", 1)[1]

    lines = [
        f"### Why ClinVar and InterVar can disagree on **{_gene(row)} "
        f"{_aachange(row) or ''}**",
        "",
        f"**ClinVar** is curated from human submitters — multiple labs "
        f"review the same variant and submit their interpretations. "
        f"For your variant the ClinVar status is **{clinvar}** with a "
        f"star rating of **{stars}** ({review}).",
        "",
        f"**InterVar** applies the ACMG/AMP rules algorithmically. For "
        f"your variant it flagged: `{iv_codes or '(no codes reported)'}` "
        f"→ verdict **{iv_verdict or 'unknown'}**.",
        "",
        "When the two disagree:",
        "* ClinVar with **high star rating** (≥2, multi-submitter or "
        "expert panel) is generally the more reliable source — it "
        "reflects human expert review across labs.",
        "* ClinVar with **low stars** or **Conflicting** status is "
        "where InterVar's algorithmic call adds useful signal.",
        "* In your case the row sits at **" + str(stars) + " star(s)** — "
        "weigh the human-curation accordingly.",
    ]
    return "\n".join(lines) + _STD_DISCLAIMER


# ---------------------------------------------------------------------------
# Category B — Column Definitions
# ---------------------------------------------------------------------------

def render_B1(column_name: str, definition: str | None,
              actual_value: Any | None = None) -> str:
    """B1 "What does this column in my report mean?"

    Spec template:
       "{column_name}: {definition}. For your variant the value is
        {actual_value}, which means: {interpretation}."

    We always quote the definition verbatim from the bundled column
    reference; the LLM is not allowed to paraphrase ACMG codes.
    """
    if not definition:
        return (
            f"I don't have a stored definition for **{column_name}** in "
            f"the column-reference dictionary. Could you double-check the "
            f"column name from your report?"
        )
    out = [f"**{column_name}**: {definition}"]
    if actual_value is not None and str(actual_value).strip() not in ("", "."):
        out.append("")
        out.append(f"For your variant, the value is **{actual_value}**.")
    return "\n".join(out) + (
        "\n\n— *Definitions are quoted from the report's column reference; "
        "the interpretation of a specific value should be done with a "
        "clinician.*"
    )


# ---------------------------------------------------------------------------
# Category C — Symptom-driven (HPO)
# ---------------------------------------------------------------------------

def render_C1(
    body_system_name: str,
    hpo_root_id: str,
    matched_with_buckets: Iterable[tuple[dict[str, Any], Bucket]],
    universe: int,
) -> str:
    """C1 body-system query (e.g. "any variants related to my lungs?")."""
    pairs = list(matched_with_buckets)
    if not pairs:
        return (
            f"I searched your variants against genes associated with the "
            f"**{body_system_name}** (anchored on `{hpo_root_id}`). "
            f"I did not find any variants in those genes that pass the "
            f"combined ClinVar + InterVar pathogenicity filter "
            f"(scanned {universe} variants)."
            + _STD_DISCLAIMER
        )
    lines = [
        f"I searched your variants against genes associated with the "
        f"**{body_system_name}** (anchored on `{hpo_root_id}`). "
        f"I found **{len(pairs)}** that could be relevant:\n"
    ]
    for i, (row, bucket) in enumerate(pairs, 1):
        lines.append(f"{i}. {_row_one_liner(row, bucket)}")
        # Carrier paragraph when applicable.
        repro = lc.reproductive_paragraph(
            lc.inheritance_pattern(row), lc.zygosity(row),
        )
        if repro:
            lines.append(f"   - {repro}")
    return "\n".join(lines) + _STD_DISCLAIMER


def render_C2(
    symptoms: list[str],
    resolved_hpo: list[dict[str, Any]],
    matched_with_buckets: Iterable[tuple[dict[str, Any], Bucket]],
    universe: int,
    intersect_mode: bool,
) -> str:
    """C2 multi-symptom query — supports intersect or union of gene sets.

    Spec §C2: "gene lists, take intersection or union." We default to
    union (matches the existing orchestrator behaviour) but flag when
    the intersect was used so the response makes the strictness clear.
    """
    pairs = list(matched_with_buckets)
    resolved_names = [
        f"`{r['hpo_id']}` *{r['name']}* (from {r.get('input_text','?')!r})"
        for r in resolved_hpo
    ]
    mode_str = "all of them (intersection)" if intersect_mode else "any of them (union)"

    if not pairs:
        return (
            f"I mapped your symptoms to these HPO phenotypes: "
            f"{', '.join(resolved_names) or '(none resolved)'} and looked "
            f"for genes linked to {mode_str}. "
            f"I did not find any of your {universe} variants in those "
            f"genes that pass the combined pathogenicity filter."
            + _STD_DISCLAIMER
        )

    lines = [
        f"I mapped your symptoms to these HPO phenotypes: "
        f"{', '.join(resolved_names) or '(none)'} and searched for genes "
        f"linked to {mode_str}.\n",
        f"In your variants:",
    ]
    for i, (row, bucket) in enumerate(pairs, 1):
        lines.append(f"{i}. {_row_one_liner(row, bucket)}")
        repro = lc.reproductive_paragraph(
            lc.inheritance_pattern(row), lc.zygosity(row),
        )
        if repro:
            lines.append(f"   - {repro}")
    return "\n".join(lines) + _STD_DISCLAIMER


# ---------------------------------------------------------------------------
# Category D — Disease-driven
# ---------------------------------------------------------------------------

def render_D1(
    disease_name: str,
    omim_id: str | None,
    orpha_code: str | None,
    inheritance: str | None,
    typical_phenotypes: list[str],
    matched_with_buckets: Iterable[tuple[dict[str, Any], Bucket]],
    candidate_genes: list[str],
) -> str:
    """D1 disease-named query (e.g. "Do I have anything related to Marfan?")."""
    pairs = list(matched_with_buckets)
    head = [
        f"### {disease_name}" + (
            f" ({', '.join(filter(None, [omim_id, orpha_code]))})"
            if (omim_id or orpha_code) else ""
        )
    ]
    if inheritance:
        head.append(f"**Inheritance:** {inheritance}")
    if candidate_genes:
        gene_list = ", ".join(f"`{g}`" for g in candidate_genes[:8])
        head.append(f"**Associated genes:** {gene_list}"
                    f"{' …' if len(candidate_genes) > 8 else ''}")
    if typical_phenotypes:
        head.append(
            "**Typical features:** "
            + ", ".join(typical_phenotypes[:5])
            + ("…" if len(typical_phenotypes) > 5 else "")
        )
    head.append("")
    head.append("**In your variants:**")
    if pairs:
        for i, (row, bucket) in enumerate(pairs, 1):
            head.append(f"{i}. {_row_one_liner(row, bucket)}")
    else:
        head.append(
            f"I did not find any variants in {', '.join(candidate_genes[:5]) or 'the associated genes'} "
            f"that meet the combined pathogenicity criteria."
        )
    head.append("")
    head.append(
        "*This does not confirm or rule out the condition — clinical "
        "diagnosis requires more than genetic data alone.*"
    )
    return "\n".join(head) + _STD_DISCLAIMER


def render_D2(
    gene: str,
    diseases: list[dict[str, Any]],
    matched_with_buckets: Iterable[tuple[dict[str, Any], Bucket]],
) -> str:
    """D2 "Tell me about the disease this gene causes."

    ``diseases`` items: ``{omim_id, omim_description, orpha_code,
    orphanet_summary, prevalence, phenotypes, inheritance}``.
    """
    pairs = list(matched_with_buckets)
    lines = [f"### {gene} — disease associations\n"]
    if not diseases:
        lines.append(
            f"My local cross-references for **{gene}** did not return a "
            f"linked disease; if the external OMIM/Orphanet enrichment is "
            f"available it would deepen this answer."
        )
    for d in diseases[:3]:
        omim = d.get("omim_id")
        orpha = d.get("orpha_code")
        ids = " · ".join(filter(None, [
            f"OMIM:{omim}" if omim else None,
            f"Orphanet:{orpha}" if orpha else None,
        ]))
        lines.append(f"**{d.get('name', '(unnamed condition)')}** ({ids})")
        if d.get("omim_description"):
            lines.append(d["omim_description"][:500].rstrip() + "…")
        if d.get("orphanet_summary"):
            lines.append(
                "Orphanet summary: " + d["orphanet_summary"][:300].rstrip() + "…"
            )
        if d.get("prevalence"):
            lines.append(f"Prevalence: {d['prevalence']}")
        if d.get("inheritance"):
            lines.append(f"Inheritance: {d['inheritance']}")
        if d.get("phenotypes"):
            lines.append(
                "Common phenotypes: "
                + ", ".join(d["phenotypes"][:5])
                + ("…" if len(d["phenotypes"]) > 5 else "")
            )
        lines.append("")

    if pairs:
        lines.append(f"**Your {gene} variant(s):**")
        for row, bucket in pairs:
            lines.append(f"- {_row_one_liner(row, bucket)}")

    return "\n".join(lines) + _STD_DISCLAIMER


# ---------------------------------------------------------------------------
# Category E — Inheritance pattern
# ---------------------------------------------------------------------------

def render_E1(row: dict[str, Any], disease_name: str | None = None) -> str:
    """E1 "How is this condition inherited?" — patient-conditional."""
    gene = _gene(row)
    inh = lc.inheritance_pattern(row)
    zyg = lc.zygosity(row)
    omim = _omim_link(row)

    head = [
        f"The condition associated with **{gene}**"
        + (f" — **{disease_name}**" if disease_name else "")
        + (f" ({omim})" if omim else "")
        + f", follows **{inh or 'an unspecified'}** inheritance.",
        "",
        "**What this means in your case:**",
    ]
    head.append(f"- Your zygosity for this variant: **{zyg or 'unknown'}**")

    if inh and inh in lc.INHERITANCE_SIGNIFICANCE:
        head.append(f"- {lc.INHERITANCE_SIGNIFICANCE[inh]}")

    repro = lc.reproductive_paragraph(inh, zyg)
    if repro:
        head.append(f"- {repro}")

    return "\n".join(head) + _REPRO_DISCLAIMER


def render_E2(row: dict[str, Any], disease_name: str | None = None) -> str:
    """E2 "Will my children inherit this?" — E1 reframed for reproductive risk."""
    e1 = render_E1(row, disease_name=disease_name).rstrip(_REPRO_DISCLAIMER.strip())
    inh = lc.inheritance_pattern(row)
    extra = [
        "",
        "**For your children:**",
        "- If you carry one copy and the condition is autosomal "
        "dominant → 50% chance per child of inheriting the variant.",
        "- If you carry one copy and the condition is autosomal "
        "recessive → 50% chance each child is a carrier; affected "
        "only if the other parent also passes a variant in the same gene.",
        "- For X-linked patterns, risk depends on the sex of the "
        "child and the parent.",
    ]
    if inh:
        extra.append("")
        extra.append(
            f"Your variant follows **{inh}** inheritance — the most "
            f"relevant scenario above is the one matching that pattern."
        )
    return e1 + "\n" + "\n".join(extra) + _REPRO_DISCLAIMER


# ---------------------------------------------------------------------------
# Category F — Carrier and Secondary Findings
# ---------------------------------------------------------------------------

def render_F1(rows: list[dict[str, Any]]) -> str:
    """F1 "Am I a carrier for any recessive conditions?"

    Spec §F1: filter ``Carrier_Status = YES AND Mode_of_Inheritance in
    {AR, X-Linked}``.
    """
    qualifying = [
        r for r in rows
        if lc.is_carrier(r)
        and (lc.inheritance_pattern(r) or "") in (
            "Autosomal Recessive", "X-Linked Recessive", "X-Linked",
        )
    ]
    if not qualifying:
        return (
            "You do not have any variants flagged as a carrier for "
            "recessive conditions in this report."
            + _REPRO_DISCLAIMER
        )
    lines = ["You are a **heterozygous carrier** for variants associated "
             "with these conditions:\n"]
    for i, row in enumerate(qualifying, 1):
        diseases = lc.clinvar_disease_names(row) or ["(unnamed condition)"]
        gene = _gene(row)
        inh = lc.inheritance_pattern(row) or "?"
        omim = _omim_link(row)
        lines.append(
            f"{i}. **{diseases[0]}** (gene: `{gene}`"
            + (f", {omim}" if omim else "")
            + f") — inherited as **{inh}**. "
            "Carriers are typically unaffected but can pass the variant "
            "to children."
        )
    lines.append("")
    lines.append(
        "If you are planning a family, **partner carrier screening** for "
        "these conditions is often recommended."
    )
    return "\n".join(lines) + _REPRO_DISCLAIMER


def render_F2(rows: list[dict[str, Any]]) -> str:
    """F2 "What secondary findings do I have?"

    Spec §F2: ``Secondary_Finding == YES``. ACMG SF list is pre-computed
    in the analyzer; local DB only.
    """
    qualifying = [r for r in rows if lc.is_secondary_finding(r)]
    if not qualifying:
        return (
            "Your report does not list any secondary (incidental) "
            "findings from the ACMG SF list."
            + _STD_DISCLAIMER
        )
    lines = [
        f"Your report has **{len(qualifying)}** secondary finding"
        f"{'s' if len(qualifying) != 1 else ''} — variant"
        f"{'s' if len(qualifying) != 1 else ''} in genes on the ACMG "
        f"Secondary Findings list that are actionable regardless of why "
        f"the test was originally done:\n"
    ]
    for i, row in enumerate(qualifying, 1):
        gene = _gene(row)
        diseases = lc.clinvar_disease_names(row) or ["(condition not in ClinVar disease field)"]
        omim = _omim_link(row)
        clinvar = row.get("clinvar: Clinvar") or "n/a"
        actionable_note = ""
        if lc.is_actionable(row):
            actionable_note = " — *flagged as Actionable in this report*"
        lines.append(
            f"{i}. **{gene}** — {diseases[0]}"
            + (f" ({omim})" if omim else "")
            + f". ClinVar: {clinvar}.{actionable_note}"
        )
    lines.append("")
    lines.append(
        "*These findings warrant prompt discussion with a clinician.*"
    )
    return "\n".join(lines) + _STD_DISCLAIMER
