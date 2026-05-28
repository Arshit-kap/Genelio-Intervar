"""InterVar TXT analyzer.

Parses the 34-column tab-separated InterVar annotation file (the kind
produced by the standard InterVar + ANNOVAR pipeline) into the uniform
analyzer-output shape consumed by the chatbot pipeline.

Key shape differences from ``clinical_csv``:

* **Tab-separated** (not comma); whitespace around the ClinVar /
  InterVar column values must be tolerated (the headers themselves
  have leading/trailing spaces in the source files).
* **76k+ rows per patient is normal** — most variants are common /
  benign by gnomAD frequency; the headline findings are the small
  subset where InterVar produces "Likely pathogenic" / "Pathogenic"
  or ClinVar carries a pathogenic call.
* **ACMG evidence is packed into one string** in the InterVar column,
  e.g. ``InterVar: Benign PVS1=0 PS=[0, 0, 0, 0, 0] PM=[1, 0, ...]
  PP=[0, 0, 0, 0, 0, 0] BA1=1 BS=[1, 0, 0, 0, 0] BP=[0, 0, 0, 1, 0,
  0, 1, 0]``. We parse it into an :class:`ACMGFlags` dict so the
  agentic filter layer can ask boolean questions like "PVS1=1 OR
  PS_any=1" without re-string-parsing on every query.
* **Disease links are in OMIM / Phenotype_MIM / OrphaNumber / Orpha**
  (cross-DB IDs) rather than free-text ClinVar disease names. The
  agentic disease-link router uses these.
"""
from __future__ import annotations

import csv
import logging
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Column reference digest — distilled from Intervar-Column-Reference.pdf.
# Embedded in the chat prompt so the LLM can quote canonical definitions
# of CADD / SIFT / GERP++ / phyloP / ACMG codes without inventing them.
# ---------------------------------------------------------------------------
COLUMN_REFERENCE: dict[str, str] = {
    "#Chr": "Chromosome (1-22, X, Y, M/MT). Autosomal vs sex-linked vs mitochondrial inheritance.",
    "Start": "Genomic start position (1-based, GRCh37 typically for InterVar).",
    "End": "Genomic end position. For SNVs equals Start; for deletions End-Start+1 = length.",
    "Ref": "Reference allele (A/T/G/C, multi-base for indels, '-' for insertions).",
    "Alt": "Patient's alternate allele.",
    "Ref.Gene": "RefSeq gene symbol (HGNC). Primary identifier for OMIM / ClinVar / HPO lookup.",
    "Func.refGene": "Functional region: exonic, splicing, exonic;splicing, intronic, UTR3/5, intergenic, ncRNA_*.",
    "ExonicFunc.refGene": "When Func=exonic: synonymous SNV (silent, low impact), nonsynonymous SNV (missense, moderate), stopgain (HIGH), stoploss (HIGH), frameshift deletion/insertion (HIGH), nonframeshift deletion/insertion (moderate), unknown.",
    "Gene.ensGene": "Ensembl gene symbol (cross-check against Ref.Gene).",
    "avsnp147": "dbSNP rsID; '.' if novel.",
    "AAChange.ensGene": "HGVS on Ensembl transcript (Gene:ENST:exon:c.X:p.X).",
    "AAChange.refGene": "HGVS on RefSeq transcript (Gene:NM_:exon:c.X:p.X) — the clinical standard.",
    "clinvar: Clinvar": "ClinVar significance: UNK (not in ClinVar), Pathogenic, Likely_pathogenic, Uncertain_significance, Likely_benign, Benign, Conflicting_interpretations_of_pathogenicity, drug_response, risk_factor, protective.",
    "InterVar: InterVar and Evidence": "InterVar automated ACMG verdict (Pathogenic/Likely pathogenic/Uncertain significance/Likely benign/Benign) PLUS evidence trail: PVS1=0/1, PS=[5 ints], PM=[7 ints], PP=[6 ints], BA1=0/1, BS=[5 ints], BP=[8 ints].",
    "Freq_gnomAD_genome_ALL": "Global gnomAD genome AF. >0.05 triggers BA1 (stand-alone benign).",
    "Freq_esp6500siv2_all": "ESP6500 exome AF — secondary frequency check.",
    "Freq_1000g2015aug_all": "1000 Genomes AF — tertiary frequency check.",
    "CADD_raw": "Raw CADD pathogenicity score (not directly comparable across variants — use phred).",
    "CADD_phred": "Phred-scaled CADD (≥10 mildly del, ≥15 moderately, 20-30 likely deleterious — common filter, >30 top 0.1%).",
    "SIFT_score": "Float 0-1. <0.05 = damaging; ≥0.05 = tolerated. Missense only.",
    "GERP++_RS": "Evolutionary conservation. <0 fast-evolving; 2-4 conserved; >4 highly conserved.",
    "phyloP46way_placental": "Conservation across 46 placental mammals. >2 highly conserved/functional.",
    "dbscSNV_ADA_SCORE": "AdaBoost splicing-disruption probability; ≥0.6 likely affects splicing.",
    "dbscSNV_RF_SCORE": "Random-Forest splicing-disruption probability; ≥0.6 likely affects splicing.",
    "Interpro_domain": "Affected protein domain (InterPro). Variants in known catalytic / binding domains are more suspect.",
    "AAChange.knownGene": "HGVS on UCSC Known Genes (third cross-check).",
    "rmsk": "RepeatMasker annotation; non-'.' means variant lies in a repetitive element (less reliably called).",
    "MetaSVM_score": "Meta-predictor combining SIFT/Polyphen/GERP. ≥0 damaging, <0 tolerated.",
    "Freq_gnomAD_genome_POPs": "Comma-separated per-ancestry AFs: AFR, AMR, EAS, FIN, NFE, OTH, ASJ.",
    "OMIM": "OMIM gene/disease MIM ID (6 digits).",
    "Phenotype_MIM": "OMIM phenotype MIM ID(s) for diseases caused by the gene.",
    "OrphaNumber": "Orphanet (European rare-disease) ID.",
    "Orpha": "Orphanet disease name(s); semicolon-separated.",
    "Otherinfo": "Zygosity: het (heterozygous), hom (homozygous), hemi (X-linked male), '.' if unknown.",
    # ACMG codes (parsed out of the InterVar string)
    "PVS1": "Very Strong pathogenic: null variant (nonsense/frameshift/canonical splice) in a LOF-known gene. 0/1.",
    "PS": "Strong pathogenic — 5 sub-criteria (PS1..PS5): same AA change, de novo, functional studies, etc.",
    "PM": "Moderate pathogenic — 7 sub-criteria (PM1..PM7): hotspot domain, absent from controls, in-frame indel, etc.",
    "PP": "Supporting pathogenic — 6 sub-criteria (PP1..PP6): cosegregation, gene-disease established, computational, etc.",
    "BA1": "Stand-Alone Benign: AF > 5% in any large population.",
    "BS": "Strong benign — 5 sub-criteria.",
    "BP": "Supporting benign — 8 sub-criteria.",
}


# ---------------------------------------------------------------------------
# InterVar ACMG-string parser
# ---------------------------------------------------------------------------

# Examples seen in real data:
#   "InterVar: Benign PVS1=0 PS=[0, 0, 0, 0, 0] PM=[0, 0, 0, 0, 0, 0, 0]
#    PP=[0, 0, 0, 0, 0, 0] BA1=1 BS=[1, 0, 0, 0, 0] BP=[0, 0, 0, 1, 0, 0, 1, 0]"
_INTERVAR_VERDICT_RE = re.compile(
    r"InterVar\s*:\s*(?P<verdict>[A-Za-z ]+?)\s+PVS1=", re.IGNORECASE,
)
_PVS1_RE = re.compile(r"PVS1\s*=\s*([01])")
_BA1_RE = re.compile(r"BA1\s*=\s*([01])")
_ARRAY_RE = {
    "PS": re.compile(r"PS\s*=\s*\[([0-9,\s]+)\]"),
    "PM": re.compile(r"PM\s*=\s*\[([0-9,\s]+)\]"),
    "PP": re.compile(r"PP\s*=\s*\[([0-9,\s]+)\]"),
    "BS": re.compile(r"BS\s*=\s*\[([0-9,\s]+)\]"),
    "BP": re.compile(r"BP\s*=\s*\[([0-9,\s]+)\]"),
}


def parse_intervar_string(s: str | None) -> dict[str, Any]:
    """Unpack the InterVar column string into a structured ACMG dict.

    Output shape::

        {
            "verdict": "Benign" | "Likely benign" | "Uncertain significance"
                       | "Likely pathogenic" | "Pathogenic" | "Unknown",
            "PVS1": 0 | 1,
            "PS":   [0,0,0,0,0],     # length 5
            "PM":   [0,0,0,0,0,0,0], # length 7
            "PP":   [0,0,0,0,0,0],   # length 6
            "BA1":  0 | 1,
            "BS":   [0,0,0,0,0],     # length 5
            "BP":   [0,0,0,0,0,0,0,0], # length 8
            # Convenience flags for downstream filters
            "PS_any": bool, "PM_any": bool, "PP_any": bool,
            "BS_any": bool, "BP_any": bool,
            "any_pathogenic_evidence": bool,
        }
    """
    if not s:
        return {
            "verdict": "Unknown", "PVS1": 0, "BA1": 0,
            "PS": [0]*5, "PM": [0]*7, "PP": [0]*6, "BS": [0]*5, "BP": [0]*8,
            "PS_any": False, "PM_any": False, "PP_any": False,
            "BS_any": False, "BP_any": False,
            "any_pathogenic_evidence": False,
        }
    verdict_m = _INTERVAR_VERDICT_RE.search(s)
    verdict = verdict_m.group("verdict").strip() if verdict_m else "Unknown"
    pvs1 = _PVS1_RE.search(s)
    ba1 = _BA1_RE.search(s)

    out: dict[str, Any] = {
        "verdict": verdict,
        "PVS1": int(pvs1.group(1)) if pvs1 else 0,
        "BA1": int(ba1.group(1)) if ba1 else 0,
    }
    for name, regex in _ARRAY_RE.items():
        m = regex.search(s)
        if m:
            out[name] = [int(x.strip()) for x in m.group(1).split(",")]
        else:
            out[name] = [0] * (5 if name in ("PS", "BS") else 6 if name == "PP" else 7 if name == "PM" else 8)

    out["PS_any"] = any(out["PS"])
    out["PM_any"] = any(out["PM"])
    out["PP_any"] = any(out["PP"])
    out["BS_any"] = any(out["BS"])
    out["BP_any"] = any(out["BP"])
    out["any_pathogenic_evidence"] = (
        out["PVS1"] == 1 or out["PS_any"] or out["PM_any"] or out["PP_any"]
    )
    return out


def parse_clinvar_string(s: str | None) -> str:
    """Strip the ``clinvar:`` prefix; return canonical significance string."""
    if not s:
        return "UNK"
    s = s.strip()
    if s.lower().startswith("clinvar:"):
        s = s.split(":", 1)[1].strip()
    return s or "UNK"


def parse_zygosity(s: str | None) -> str:
    """Otherinfo → het/hom/hemi/unknown."""
    if not s:
        return "unknown"
    s = s.strip().split()[0].lower() if s.strip() else "unknown"
    if s in ("het", "hom", "hemi"):
        return s
    return "unknown"


def _coerce(v: str | None) -> str | None:
    """ANNOVAR's '.' / 'NONE' / 'UNKNOWN' → None; everything else trimmed."""
    if v is None:
        return None
    s = v.strip()
    if not s or s in (".", "NA", "na", "NONE", "UNKNOWN"):
        return None
    return s


def _float(v: str | None) -> float | None:
    s = _coerce(v)
    if s is None:
        return None
    try:
        return float(s)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Per-row structuring
# ---------------------------------------------------------------------------

def _structured_row(raw: dict[str, str | None], idx: int) -> dict[str, Any]:
    """Pluck the most useful fields + parse compact derivatives."""
    intervar_str = raw.get("InterVar: InterVar and Evidence ") or raw.get(" InterVar: InterVar and Evidence ") or raw.get("InterVar: InterVar and Evidence")
    acmg = parse_intervar_string(intervar_str)

    clinvar = parse_clinvar_string(raw.get("clinvar: Clinvar ") or raw.get(" clinvar: Clinvar ") or raw.get("clinvar: Clinvar"))
    zyg = parse_zygosity(raw.get("Otherinfo"))

    out: dict[str, Any] = {
        "i": idx,
        "chr": _coerce(raw.get("#Chr")),
        "start": _coerce(raw.get("Start")),
        "end": _coerce(raw.get("End")),
        "ref": _coerce(raw.get("Ref")),
        "alt": _coerce(raw.get("Alt")),
        "gene": _coerce(raw.get("Ref.Gene")),
        "func": _coerce(raw.get("Func.refGene")),
        "exonic_func": _coerce(raw.get("ExonicFunc.refGene")),
        "rsid": _coerce(raw.get("avsnp147")),
        "aac_refgene": _coerce(raw.get("AAChange.refGene")),
        "clinvar": clinvar,
        "intervar": acmg["verdict"],
        "acmg": acmg,
        "freq_gnomad_all": _float(raw.get("Freq_gnomAD_genome_ALL")),
        "freq_esp6500": _float(raw.get("Freq_esp6500siv2_all")),
        "freq_1000g": _float(raw.get("Freq_1000g2015aug_all")),
        "cadd_phred": _float(raw.get("CADD_phred")),
        "sift_score": _float(raw.get("SIFT_score")),
        "gerp_rs": _float(raw.get("GERP++_RS")),
        "phylop": _float(raw.get("phyloP46way_placental")),
        "dbsc_ada": _float(raw.get("dbscSNV_ADA_SCORE")),
        "dbsc_rf": _float(raw.get("dbscSNV_RF_SCORE")),
        "metasvm": _float(raw.get("MetaSVM_score")),
        "domain": _coerce(raw.get("Interpro_domain")),
        "rmsk": _coerce(raw.get("rmsk")),
        "pop_freqs": _coerce(raw.get("Freq_gnomAD_genome_POPs")),
        "omim": _coerce(raw.get("OMIM")),
        "phenotype_mim": _coerce(raw.get("Phenotype_MIM")),
        "orpha_num": _coerce(raw.get("OrphaNumber")),
        "orpha": _coerce(raw.get("Orpha")),
        "zygosity": zyg,
    }
    return out


# ---------------------------------------------------------------------------
# Aggregates / counts
# ---------------------------------------------------------------------------

def _build_counts(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """High-level totals + breakdowns. Cap top-N to keep payload reasonable."""
    intervar = Counter(r["intervar"] or "Unknown" for r in rows)
    clinvar = Counter(r["clinvar"] or "UNK" for r in rows)
    func = Counter(r["func"] or "Unknown" for r in rows)
    exonic_func = Counter(r["exonic_func"] or "." for r in rows if r["func"] == "exonic")
    zyg = Counter(r["zygosity"] for r in rows)
    chrom = Counter(r["chr"] or "?" for r in rows)
    by_gene = Counter(r["gene"] or "Unknown" for r in rows)

    # ACMG flag totals across whole report
    flag_counts = {
        "PVS1=1": sum(1 for r in rows if r["acmg"]["PVS1"] == 1),
        "BA1=1": sum(1 for r in rows if r["acmg"]["BA1"] == 1),
        "any_PS": sum(1 for r in rows if r["acmg"]["PS_any"]),
        "any_PM": sum(1 for r in rows if r["acmg"]["PM_any"]),
        "any_PP": sum(1 for r in rows if r["acmg"]["PP_any"]),
        "any_BS": sum(1 for r in rows if r["acmg"]["BS_any"]),
        "any_BP": sum(1 for r in rows if r["acmg"]["BP_any"]),
    }

    return {
        "total_variants": len(rows),
        "by_intervar": dict(intervar.most_common()),
        "by_clinvar": dict(clinvar.most_common(15)),
        "by_func": dict(func.most_common(15)),
        "by_exonic_func": dict(exonic_func.most_common(15)),
        "by_zygosity": dict(zyg),
        "by_chromosome": dict(chrom),
        "top_genes": dict(by_gene.most_common(25)),
        "acmg_flag_totals": flag_counts,
    }


# ---------------------------------------------------------------------------
# Headline finding selection
# ---------------------------------------------------------------------------

# Set of ClinVar verdict strings (lower-cased) that count as a pathogenic
# call. Membership test is intentionally exact-token — the previous
# ``"pathogenic" in cv`` substring match was wrong for
# ``Conflicting_interpretations_of_pathogenicity`` (contains "pathogenic"
# as a substring even though the curator review is conflicted) and is
# the exact bug the bioinformatician (Saurabh) flagged.
_CLINVAR_PATHOGENIC_TOKENS: frozenset[str] = frozenset({
    "pathogenic",
    "likely_pathogenic",
    "pathogenic/likely_pathogenic",
    "pathogenic,_low_penetrance",
    "likely_pathogenic,_low_penetrance",
})

# InterVar verdict strings (case-insensitive) that count as a pathogenic
# call. We test the *first token* on the verdict so "Likely pathogenic"
# matches but "Conflicting" / "Uncertain" / "Likely benign" don't.
_INTERVAR_PATHOGENIC_VERDICTS: frozenset[str] = frozenset({
    "pathogenic",
    "likely pathogenic",
})


def _clinvar_is_pathogenic(cv: str | None) -> bool:
    """Strict token match — no substring leakage from 'Conflicting_*'."""
    if not cv:
        return False
    cv_norm = cv.strip().lower()
    # ClinVar uses underscores between words; split on those.
    return cv_norm in _CLINVAR_PATHOGENIC_TOKENS


def _intervar_is_pathogenic(verdict: str | None) -> bool:
    """Strict prefix-token match on the InterVar verdict string."""
    if not verdict:
        return False
    v = verdict.strip().lower()
    # Verdicts are short fixed strings; do exact-set lookup, not substring.
    return v in _INTERVAR_PATHOGENIC_VERDICTS


def _is_headline(row: dict[str, Any]) -> bool:
    """Headline-finding selector — strict.

    Conditions (all must respect the BA1 drop):

      A. InterVar verdict is *exactly* Pathogenic / Likely pathogenic, OR
      B. ClinVar significance is *exactly* a pathogenic call (no
         substring matching against ``Conflicting_*``), OR
      C. PVS1 = 1 (loss-of-function in LOF-known gene)

      AND
      D. BA1 != 1 (stand-alone benign on allele frequency >5%; an ACMG
         BA1 trumps any pathogenic call by definition of the rule).

    This fixes the two bioinformatician-reported bugs:
    * ``Conflicting_interpretations_of_pathogenicity`` was being treated
      as pathogenic because of a naïve substring ``"pathogenic" in cv``.
    * Variants with ``PVS1=1`` AND ``BA1=1`` (contradictory data) were
      surfacing as headline; the spec's rule is that BA1 wins.
    """
    if row["acmg"]["BA1"] == 1:
        return False  # BA1 drops the row regardless of other signals
    if _intervar_is_pathogenic(row.get("intervar")):
        return True
    if _clinvar_is_pathogenic(row.get("clinvar")):
        return True
    if row["acmg"]["PVS1"] == 1:
        return True
    return False


def _build_indexes(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Pre-compute lookup indexes used by the agentic Stage-2 executor.

    Returns a dict of:
      - ``by_chrpos``: ``(chr, start) -> [row_index, ...]``
      - ``by_rsid``:   ``rsid -> [row_index, ...]``
      - ``by_gene``:   ``gene -> [row_index, ...]``  (case-insensitive)
    Indexes are stored alongside ``raw_rows`` so the orchestrator can
    answer coord / rsid / gene queries in O(1) without re-scanning 76k
    rows on every chat turn.
    """
    by_chrpos: dict[str, list[int]] = defaultdict(list)
    by_rsid: dict[str, list[int]] = defaultdict(list)
    by_gene: dict[str, list[int]] = defaultdict(list)
    for i, r in enumerate(rows):
        if r["chr"] and r["start"]:
            by_chrpos[f"{r['chr']}:{r['start']}"].append(i)
        if r["rsid"]:
            by_rsid[r["rsid"]].append(i)
        if r["gene"]:
            # Split multi-gene entries ("NOC2L,SAMD11")
            for g in r["gene"].split(","):
                by_gene[g.strip().upper()].append(i)
    return {
        "by_chrpos": dict(by_chrpos),
        "by_rsid": dict(by_rsid),
        "by_gene": dict(by_gene),
    }


# ---------------------------------------------------------------------------
# LLM-facing context
# ---------------------------------------------------------------------------

def _render_headline_block(rows: list[dict[str, Any]]) -> str:
    """Compact text rendering of the headline findings."""
    headline = [r for r in rows if _is_headline(r)]
    lines = [f"=== HEADLINE FINDINGS ({len(headline)} variants) ==="]
    if not headline:
        lines.append("  (no Pathogenic / Likely pathogenic / PVS1=1 variants in this report.)")
    for r in headline[:30]:  # cap for prompt budget
        aac = (r["aac_refgene"] or "").split(",")[0]
        lines.append(
            f"  • {r['gene']} · {aac} · {r['func']}/{r['exonic_func'] or '-'} "
            f"· ClinVar={r['clinvar']} · InterVar={r['intervar']} "
            f"· {r['zygosity']} · CADD={r['cadd_phred']} "
            f"· ACMG: PVS1={r['acmg']['PVS1']} BA1={r['acmg']['BA1']} "
            f"PS={int(r['acmg']['PS_any'])} PM={int(r['acmg']['PM_any'])} "
            f"PP={int(r['acmg']['PP_any'])}"
        )
    if len(headline) > 30:
        lines.append(f"  …({len(headline) - 30} more headline findings — list truncated for prompt budget)")
    return "\n".join(lines)


def _build_analysis_context(rows: list[dict[str, Any]], counts: dict[str, Any]) -> str:
    """High-level summary text. Used for aggregate-style questions and as
    fallback context when the agentic Stage-2 executor returns nothing."""
    lines = [
        "=== INTERVAR REPORT SUMMARY ===",
        f"Total annotated variants: {counts['total_variants']}",
        "",
        "InterVar verdict breakdown:",
    ]
    for k, v in counts["by_intervar"].items():
        lines.append(f"  {k}: {v}")
    lines.append("")
    lines.append("ClinVar significance breakdown (top 15):")
    for k, v in counts["by_clinvar"].items():
        lines.append(f"  {k}: {v}")
    lines.append("")
    lines.append("Functional region breakdown:")
    for k, v in counts["by_func"].items():
        lines.append(f"  {k}: {v}")
    lines.append("")
    lines.append("Exonic-variant subtypes (top 15):")
    for k, v in counts["by_exonic_func"].items():
        lines.append(f"  {k}: {v}")
    lines.append("")
    lines.append("ACMG flag totals across the report:")
    for k, v in counts["acmg_flag_totals"].items():
        lines.append(f"  {k}: {v}")
    lines.append("")
    lines.append("Zygosity breakdown:")
    for k, v in counts["by_zygosity"].items():
        lines.append(f"  {k}: {v}")
    lines.append("")
    lines.append("Top 25 genes by variant count:")
    for k, v in counts["top_genes"].items():
        lines.append(f"  {k}: {v}")
    lines.append("")
    lines.append(_render_headline_block(rows))
    return "\n".join(lines)


def _build_structured_summary(counts: dict[str, Any]) -> str:
    n = counts["total_variants"]
    iv = counts["by_intervar"]
    cv = counts["by_clinvar"]
    parts = [f"{n} variants annotated"]
    lp = iv.get("Likely pathogenic", 0)
    p = iv.get("Pathogenic", 0)
    if p:
        parts.append(f"{p} InterVar Pathogenic")
    if lp:
        parts.append(f"{lp} InterVar Likely pathogenic")
    # Token-level membership test — matches the same strict rule
    # _is_headline uses, so the count agrees with the headline list and
    # never inflates due to Conflicting_* substring leakage.
    cv_pat = sum(
        n for verdict, n in cv.items()
        if _clinvar_is_pathogenic(verdict)
    )
    if cv_pat:
        parts.append(f"{cv_pat} ClinVar Pathogenic-class")
    return " · ".join(parts)


def _column_reference_digest() -> str:
    lines = [
        "Each variant row exposes the following columns "
        "(InterVar TXT format — tab-separated):",
    ]
    for col, desc in COLUMN_REFERENCE.items():
        lines.append(f"  {col}: {desc}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Parser entry — public API matching the analyzer registry contract.
# ---------------------------------------------------------------------------

def _parse_rows(file_path: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with open(file_path, encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh, delimiter="\t")
        # Strip whitespace from headers — the source file has spaces in
        # "clinvar: Clinvar " and " InterVar: InterVar and Evidence ".
        reader.fieldnames = [(f or "").strip() for f in (reader.fieldnames or [])]
        for i, raw in enumerate(reader):
            # Renormalise keys (DictReader uses the original whitespace
            # from the header line, but we want stripped names).
            clean: dict[str, str | None] = {k.strip(): v for k, v in raw.items()}
            rows.append(_structured_row(clean, i))
    return rows


def analyze(file_path: str | Path) -> dict:
    """Parse an InterVar TXT into the uniform analyzer output shape."""
    path = Path(file_path)
    try:
        rows = _parse_rows(str(path))
    except Exception as exc:  # noqa: BLE001
        log.exception("intervar analyzer failed for %s", path)
        return {
            "status": "failed", "site": "intervar", "report": {},
            "analysis_context": "", "message": f"Failed to parse InterVar TXT: {exc}",
        }

    if not rows:
        return {
            "status": "ok", "site": "intervar",
            "report": {"variants": [], "raw_rows": [], "counts": _build_counts([]), "indexes": {}},
            "analysis_context": "Empty InterVar file — no variant rows.",
            "structured_summary": "0 variants annotated",
        }

    counts = _build_counts(rows)
    indexes = _build_indexes(rows)

    return {
        "status": "ok",
        "site": "intervar",
        "report": {
            "raw_rows": rows,
            "counts": counts,
            "indexes": indexes,
            # Compact ``variants`` list for any UI that wants a small slice
            # without iterating raw_rows. We surface the headline findings.
            "variants": [r for r in rows if _is_headline(r)][:50],
        },
        "analysis_context": _build_analysis_context(rows, counts),
        "structured_summary": _build_structured_summary(counts),
        "schema_reference": _column_reference_digest(),
    }
