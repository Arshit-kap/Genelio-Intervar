"""Clinical Variant CSV analyzer.

Parses a BioAro / ANNOVAR-style annotated CSV (72 known columns covering
variant call → gene annotation → ClinVar → ACMG evidence → ML predictors
→ patient genotype → clinical flags) into the uniform analyzer output
shape consumed by ``chatbot.pipeline``.

Designed for the file shape distributed as
``UDB-XXX_FINAL_VISUAL_CLINICAL_REPORT.csv``. The schema is documented in
``COLUMN_REFERENCE`` below — a compact one-line-per-column digest of the
upstream PDF reference, kept in code so the chat prompt has it in-band.

Notes on robustness:

* Many cells use ``"."`` as a sentinel for "not available". We treat that
  as missing.
* ``ClinVar_Accession_ID`` is a ``|``-joined list; we count, don't enumerate.
* Float columns (CADD/REVEL/SIFT/etc.) may be missing or "." — coerce safely.
* The CSV may contain commas inside double-quoted fields; we use stdlib
  ``csv.DictReader`` which handles RFC-4180 quoting correctly.
"""
from __future__ import annotations

import csv
import logging
from collections import Counter
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Compact column reference — fed into the chat prompt so the LLM can answer
# "what does CADD_phred mean" without us needing live RAG over the PDF.
# Source: excel-file-column-reference.pdf (33 pages condensed to ~70 lines).
# Keep this < 4kB total so the prompt budget stays sane.
# ---------------------------------------------------------------------------
COLUMN_REFERENCE: dict[str, str] = {
    "#Chr": "Chromosome (chr1–22, chrX, chrY, chrM/chrMT). Sex-linked vs autosomal vs mitochondrial inheritance.",
    "Start": "Genomic start position (1-based, GRCh38). For SNVs equals End.",
    "End": "Genomic end position. Deletions: End-Start+1 = length.",
    "Ref": "Reference allele at this position (A/T/G/C or '-' for insertions).",
    "Alt": "Patient's alternate allele. The Ref→Alt is the actual mutation.",
    "Ref.Gene": "RefSeq gene symbol (HGNC nomenclature). The primary gene name.",
    "Func.refGene": "Functional region: exonic, intronic, splicing, UTR3/5, intergenic, ncRNA.",
    "ExonicFunc.refGene": "Exonic variant subtype: nonsynonymous SNV, synonymous SNV, frameshift, stopgain, stoploss.",
    "Gene.ensGene": "Ensembl gene ID (parallel annotation to RefSeq).",
    "avsnp147": "dbSNP rs ID (variant cross-reference).",
    "AAChange.ensGene": "HGVS protein/cDNA notation on the Ensembl transcript.",
    "AAChange.refGene": "HGVS protein/cDNA notation on the RefSeq transcript (used clinically).",
    "clinvar: Clinvar": "ClinVar significance string (Pathogenic, Likely_pathogenic, VUS, Likely_benign, Benign, Conflicting_classifications_of_pathogenicity).",
    "ClinVar_Disease": "ClinVar-linked disease name(s) for this variant.",
    "ClinVar_Disease_DB": "Cross-database disease identifiers (MedGen, OMIM, SNOMED_CT).",
    "ClinVar_Review_Status": "ClinVar review confidence: practice_guideline > reviewed_by_expert_panel > multiple/single submitters > no_assertion.",
    "InterVar: InterVar and Evidence": "InterVar automated ACMG classification + which rules fired.",
    "Freq_gnomAD_genome_ALL": "Global gnomAD genome allele frequency (rarity proxy). <1e-4 → ultra-rare.",
    "Freq_esp6500siv2_all": "ESP6500 cohort allele frequency.",
    "Freq_1000g2015aug_all": "1000 Genomes allele frequency.",
    "CADD_raw": "Raw CADD pathogenicity score (higher = more deleterious).",
    "CADD_phred": "Phred-scaled CADD (≥20 = top 1% deleterious, ≥30 = top 0.1%).",
    "SIFT_score": "Numerical SIFT prediction (≤0.05 = damaging).",
    "GERP++_RS": "GERP++ evolutionary conservation (positive = conserved → more likely functional).",
    "dbscSNV_ADA_SCORE": "ADA splicing-disruption probability (≥0.6 = disrupts splicing).",
    "dbscSNV_RF_SCORE": "Random-Forest splicing-disruption probability (≥0.6 = disrupts).",
    "Interpro_domain": "Protein domain(s) impacted by the variant.",
    "MetaSVM_score": "MetaSVM ensemble score combining multiple predictors.",
    "ClinVar_Accession_ID": "Pipe-joined ClinVar submission accessions (count → evidence depth).",
    "ClinVar_Variation_ID": "Stable ClinVar variation ID for cross-reference.",
    "NM_ID": "Reference RefSeq transcript NM_ accession for the reported variant.",
    "Impact": "SnpEff/VEP impact category: HIGH (LOF), MODERATE (missense), LOW, MODIFIER.",
    "Consequence": "Sequence Ontology consequence term (missense_variant, stop_gained, splice_donor_variant, etc.).",
    "Canonical_Transcript": "YES if the variant is reported on the canonical Ensembl transcript.",
    "MANE_Select": "MANE Select transcript flag — agreed-upon clinical reference transcript.",
    "APPRIS": "APPRIS principal-isoform annotation (P1 = primary).",
    "TSL": "Transcript Support Level (1 = strongest evidence).",
    "CCDS": "Consensus CDS identifier (NCBI/EBI agreed coding sequence).",
    "HGVSg": "Genomic HGVS nomenclature (chr:g.posRef>Alt).",
    "UniProt_SwissProt": "Reviewed UniProt protein accession.",
    "UniProt_TrEMBL": "Unreviewed UniProt accession.",
    "UniParc": "UniProt cross-database identifier.",
    "UniProt_Isoform": "Specific protein isoform affected.",
    "OMIM_ID": "OMIM phenotype / gene identifier (cross-reference to disease).",
    "HPO_ID": "Human Phenotype Ontology terms associated with the gene.",
    "MONDO_ID": "MONDO disease ontology identifier.",
    "Mode_of_Inheritance": "Inheritance pattern: AD, AR, XLD, XLR, mitochondrial, multifactorial.",
    "Genotype": "Patient genotype string (0/1, 1/1, 0|1 phased, etc.).",
    "Zygosity": "Heterozygous (one allele), Homozygous (both), Hemizygous (chrX/Y in male).",
    "Read_Depth": "Total sequencing depth at this site (higher = higher confidence).",
    "Allele_Depth": "Per-allele read counts (ref,alt).",
    "Variant_Allele_Frequency": "VAF = alt_reads / total_reads. ~0.5 for het, ~1.0 for hom in germline.",
    "Phase_Set": "Haplotype phase group identifier when phased.",
    "PVS1": "ACMG Very Strong Pathogenic evidence — typically null variants in LOF-intolerant genes. YES = fired.",
    "PM2": "ACMG Moderate evidence — variant absent from population databases (ultra-rare).",
    "PP3": "ACMG Supporting evidence — computational predictors agree on deleterious effect.",
    "BA1": "ACMG Stand-Alone Benign — allele frequency too high to be disease-causing (typically ≥5%).",
    "ClinVar_Stars": "ClinVar review-confidence star rating (0–4).",
    "SIFT_pred": "SIFT categorical: D (damaging) / T (tolerated).",
    "Polyphen2_HDIV_pred": "Polyphen2 HumDiv: D (probably damaging) / P (possibly) / B (benign).",
    "MutationTaster_pred": "MutationTaster: A (disease-causing automatic) / D (disease-causing) / N (polymorphism) / P (polymorphism automatic).",
    "FATHMM_pred": "FATHMM categorical: D (damaging) / T (tolerated).",
    "MetaSVM_pred": "MetaSVM categorical: D (damaging) / T (tolerated).",
    "REVEL_score": "REVEL ensemble missense pathogenicity (0–1; ≥0.5 = likely pathogenic, ≥0.75 = strong).",
    "phyloP100way_vertebrate": "phyloP conservation across 100 vertebrates (higher = more conserved).",
    "phastCons100way_vertebrate": "phastCons probability of being a conserved element (0–1).",
    "Primary_Finding": "YES = clinically significant finding for the patient's stated indication.",
    "Secondary_Finding": "YES = ACMG SF list incidental finding (medically actionable, e.g. BRCA, MMR genes).",
    "Carrier_Status": "YES = patient is a carrier of a recessive disease allele (often asymptomatic).",
    "Actionable": "YES = the variant has an established clinical action (treatment, screening, surveillance).",
    "Pharmacogenomic_Association": "Drug-response association (e.g. clopidogrel, warfarin) when applicable.",
    "Interpretation_Summary": "Free-text per-variant interpretation produced by the upstream pipeline.",
}


# Columns the analyzer extracts per variant for the structured payload.
# Order matters for the LLM-facing text rendering.
_VARIANT_DISPLAY_FIELDS = (
    "Ref.Gene", "AAChange.refGene", "Consequence", "Impact",
    "clinvar: Clinvar", "ClinVar_Disease", "ClinVar_Review_Status",
    "InterVar: InterVar and Evidence",
    "Zygosity", "Mode_of_Inheritance",
    "CADD_phred", "REVEL_score",
    "PVS1", "PM2", "PP3", "BA1",
    "Primary_Finding", "Secondary_Finding", "Carrier_Status", "Actionable",
    "Pharmacogenomic_Association",
    "Interpretation_Summary",
)


def _coerce(v: str | None) -> Any:
    """Convert ANNOVAR's '.' sentinel to None; keep everything else as string."""
    if v is None:
        return None
    s = v.strip()
    if s in ("", ".", "NA", "na"):
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


def _parse_rows(file_path: str) -> list[dict[str, Any]]:
    """Read the CSV into row dicts, coercing missing-value sentinels."""
    rows: list[dict[str, Any]] = []
    with open(file_path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        for raw in reader:
            row = {k: _coerce(v) for k, v in raw.items()}
            rows.append(row)
    return rows


def _structured_variant(row: dict[str, Any], idx: int) -> dict[str, Any]:
    """Pluck the most clinically meaningful fields into a tidy dict."""
    out: dict[str, Any] = {"index": idx}
    for col in _VARIANT_DISPLAY_FIELDS:
        if col in row and row[col] is not None:
            out[col] = row[col]
    # Always include a stable locus + transcript pair
    chr_ = row.get("#Chr")
    start = row.get("Start")
    if chr_ and start:
        out["locus"] = f"{chr_}:{start}"
    if row.get("NM_ID"):
        out["transcript"] = row["NM_ID"]
    if row.get("HGVSg"):
        out["hgvs_g"] = row["HGVSg"]
    # Numerics with safe coercion
    cadd = _float(row.get("CADD_phred"))
    if cadd is not None:
        out["CADD_phred"] = cadd
    revel = _float(row.get("REVEL_score"))
    if revel is not None:
        out["REVEL_score"] = revel
    vaf = _float(row.get("Variant_Allele_Frequency"))
    if vaf is not None:
        out["VAF"] = vaf
    return out


def _build_counts(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate counts that drive the headline numbers in the chat prompt."""
    clinvar = Counter(row.get("clinvar: Clinvar") or "Unknown" for row in rows)
    impact = Counter(row.get("Impact") or "Unknown" for row in rows)
    by_gene = Counter(row.get("Ref.Gene") or "Unknown" for row in rows)
    chrom = Counter(row.get("#Chr") or "Unknown" for row in rows)

    def _yes_count(col: str) -> int:
        return sum(1 for row in rows if (row.get(col) or "").upper() == "YES")

    return {
        "total_variants": len(rows),
        "by_clinvar_significance": dict(clinvar.most_common()),
        "by_impact": dict(impact.most_common()),
        "top_genes": dict(by_gene.most_common(15)),
        "by_chromosome": dict(chrom.most_common()),
        "flags": {
            "primary_findings":      _yes_count("Primary_Finding"),
            "secondary_findings":    _yes_count("Secondary_Finding"),
            "carrier_findings":      _yes_count("Carrier_Status"),
            "actionable_findings":   _yes_count("Actionable"),
            "pvs1_met":              _yes_count("PVS1"),
            "pm2_met":               _yes_count("PM2"),
            "pp3_met":               _yes_count("PP3"),
            "ba1_met":               _yes_count("BA1"),
        },
    }


def _column_reference_digest() -> str:
    """Render COLUMN_REFERENCE as a compact text block for the LLM prompt."""
    lines = [
        "Each variant row exposes the following columns "
        "(name — concise clinical meaning):",
    ]
    for col, desc in COLUMN_REFERENCE.items():
        lines.append(f"  {col}: {desc}")
    return "\n".join(lines)


def _short_hgvs(row: dict[str, Any]) -> str | None:
    """Pick a single canonical HGVS string out of AAChange.refGene's
    comma-joined transcript list, so list rendering stays compact."""
    aac = row.get("AAChange.refGene") or row.get("HGVSg") or ""
    if not aac:
        return None
    # AAChange.refGene is a comma-joined list of GENE:TRANSCRIPT:exon:c./p.
    # entries — keep only the first transcript for the list view.
    return aac.split(",")[0].strip()


def _short_label(row: dict[str, Any]) -> str:
    """One-liner used inside the pre-computed flag-specific lists."""
    return " · ".join(filter(None, [
        row.get("Ref.Gene"),
        _short_hgvs(row),
        row.get("clinvar: Clinvar"),
    ]))


def _build_flag_lists(rows: list[dict[str, Any]]) -> dict[str, list[str]]:
    """Pre-compute the answer to common flag-filtered questions.

    Pinning these in the prompt lets the LLM quote a curated list instead
    of scanning ~80 variant blocks (which is where T4/T8 failed).
    """
    out: dict[str, list[str]] = {}
    for label, predicate in (
        ("Variants with PVS1=YES",
         lambda r: (r.get("PVS1") or "").upper() == "YES"),
        ("Variants with PM2=YES",
         lambda r: (r.get("PM2") or "").upper() == "YES"),
        ("Variants with PP3=YES",
         lambda r: (r.get("PP3") or "").upper() == "YES"),
        ("Variants with BA1=YES (stand-alone benign)",
         lambda r: (r.get("BA1") or "").upper() == "YES"),
        ("ClinVar = Pathogenic (strict)",
         lambda r: (r.get("clinvar: Clinvar") or "") == "Pathogenic"),
        ("ClinVar = Pathogenic/Likely_pathogenic",
         lambda r: (r.get("clinvar: Clinvar") or "") == "Pathogenic/Likely_pathogenic"),
        ("Primary findings",
         lambda r: (r.get("Primary_Finding") or "").upper() == "YES"),
        ("Secondary/incidental findings (ACMG SF list)",
         lambda r: (r.get("Secondary_Finding") or "").upper() == "YES"),
        ("Carrier-status findings",
         lambda r: (r.get("Carrier_Status") or "").upper() == "YES"),
        ("Actionable findings",
         lambda r: (r.get("Actionable") or "").upper() == "YES"),
        ("Pharmacogenomic findings",
         lambda r: bool(r.get("Pharmacogenomic_Association"))),
    ):
        matches = [(i, r) for i, r in enumerate(rows) if predicate(r)]
        # Cap per-list rendering at 20 to keep prompt size sane.
        out[label] = [f"  [row {i}] {_short_label(r)}" for i, r in matches[:20]]
        if len(matches) > 20:
            out[label].append(f"  …({len(matches) - 20} more — see full list)")
    return out


def _build_analysis_context(rows: list[dict[str, Any]], counts: dict[str, Any]) -> str:
    """Dense, LLM-ready text describing the whole report.

    Length-capped (~16 KB) so the chat prompt stays inside the model's
    context window even with 80+ variants.
    """
    lines: list[str] = []

    # 1. Headline
    lines.append("=== CLINICAL VARIANT CSV SUMMARY ===")
    lines.append(f"Total variants reported: {counts['total_variants']}")
    lines.append("")

    # 2. Counts
    lines.append("ClinVar significance breakdown:")
    for k, v in counts["by_clinvar_significance"].items():
        lines.append(f"  {k}: {v}")
    lines.append("")
    lines.append("Functional impact breakdown:")
    for k, v in counts["by_impact"].items():
        lines.append(f"  {k}: {v}")
    lines.append("")
    lines.append("Clinical flag counts:")
    for k, v in counts["flags"].items():
        lines.append(f"  {k}: {v}")
    lines.append("")
    lines.append("Top genes by variant count:")
    for k, v in counts["top_genes"].items():
        lines.append(f"  {k}: {v}")
    lines.append("")

    # 2.5. Pre-computed per-flag lists — pinned here so the LLM quotes
    # them directly rather than scanning the headline block.
    lines.append("=== PRE-COMPUTED FILTERED LISTS ===")
    lines.append("(Use these directly when asked 'which variants have X', "
                 "'how many pathogenic', etc.)")
    flag_lists = _build_flag_lists(rows)
    for label, items in flag_lists.items():
        lines.append(f"{label} ({len(items)} shown):")
        if items:
            lines.extend(items)
        else:
            lines.append("  (none)")
        lines.append("")

    # 3. Headline variants — anything flagged Primary, Secondary, Actionable,
    # or with ClinVar Pathogenic / Likely_pathogenic. Always listed.
    def _flagged(row: dict[str, Any]) -> bool:
        if (row.get("Primary_Finding") or "").upper() == "YES":
            return True
        if (row.get("Secondary_Finding") or "").upper() == "YES":
            return True
        if (row.get("Actionable") or "").upper() == "YES":
            return True
        if (row.get("Carrier_Status") or "").upper() == "YES":
            return True
        cv = (row.get("clinvar: Clinvar") or "").lower()
        if "pathogenic" in cv:  # pathogenic, likely_pathogenic
            return True
        return False

    flagged_rows = [r for r in rows if _flagged(r)]
    lines.append(f"=== HEADLINE / FLAGGED VARIANTS ({len(flagged_rows)}) ===")
    for i, row in enumerate(rows):
        if not _flagged(row):
            continue
        lines.extend(_render_variant_block(row, i))
        lines.append("")

    # 4. Remaining VUS / benign — abbreviated, capped, so the LLM still
    # has the data if a user asks about a specific gene that isn't flagged.
    rest = [r for r in rows if not _flagged(r)]
    if rest:
        lines.append(f"=== REMAINING VARIANTS ({len(rest)}) — abbreviated ===")
        for i, row in enumerate(rows):
            if _flagged(row):
                continue
            gene = row.get("Ref.Gene") or "?"
            cv = row.get("clinvar: Clinvar") or "no_clinvar"
            cons = row.get("Consequence") or row.get("ExonicFunc.refGene") or "?"
            cadd = row.get("CADD_phred") or "."
            zyg = row.get("Zygosity") or "?"
            lines.append(
                f"  [{i}] {gene} · {cons} · {cv} · "
                f"CADD={cadd} · {zyg}"
            )

    text = "\n".join(lines)
    # Hard cap. The full prompt is approximately:
    #   system (~5k) + schema_ref (~6k) + analysis_context + rag (~3k)
    # medgemma-27b max_model_len is 32k tokens (~100k chars). 28k for the
    # analysis_context leaves comfortable headroom.
    if len(text) > 28_000:
        text = text[:28_000] + "\n…[truncated for prompt budget]"
    return text


def _render_variant_block(row: dict[str, Any], idx: int) -> list[str]:
    out = [f"[{idx}] " + " · ".join(filter(None, [
        row.get("Ref.Gene"),
        row.get("AAChange.refGene"),
        row.get("Consequence") or row.get("ExonicFunc.refGene"),
    ])) ]
    for col in (
        "clinvar: Clinvar", "ClinVar_Disease", "ClinVar_Review_Status",
        "InterVar: InterVar and Evidence", "Impact",
        "Zygosity", "Mode_of_Inheritance",
        "CADD_phred", "REVEL_score",
        "Primary_Finding", "Secondary_Finding", "Carrier_Status", "Actionable",
        "Pharmacogenomic_Association",
    ):
        v = row.get(col)
        if v:
            out.append(f"    {col}: {v}")
    # ACMG evidence
    acmg = [c for c in ("PVS1", "PM2", "PP3", "BA1")
            if (row.get(c) or "").upper() == "YES"]
    if acmg:
        out.append(f"    ACMG rules MET: {', '.join(acmg)}")
    if row.get("Interpretation_Summary"):
        out.append(f"    Interpretation: {row['Interpretation_Summary']}")
    return out


def _build_structured_summary(rows: list[dict[str, Any]], counts: dict[str, Any]) -> str:
    """Short patient-facing summary suitable for a UI header."""
    n = counts["total_variants"]
    pf = counts["flags"]["primary_findings"]
    sf = counts["flags"]["secondary_findings"]
    car = counts["flags"]["carrier_findings"]
    act = counts["flags"]["actionable_findings"]
    parts = [f"{n} variant{'s' if n != 1 else ''} reported"]
    if pf: parts.append(f"{pf} primary finding{'s' if pf != 1 else ''}")
    if sf: parts.append(f"{sf} secondary finding{'s' if sf != 1 else ''}")
    if car: parts.append(f"{car} carrier finding{'s' if car != 1 else ''}")
    if act: parts.append(f"{act} actionable")
    return " · ".join(parts)


# ---------------------------------------------------------------------------
# Public API — matches the analyzer registry contract.
# ---------------------------------------------------------------------------

def analyze(file_path: str | Path) -> dict:
    """Parse a clinical-variant CSV into the uniform analyzer output shape."""
    path = Path(file_path)
    try:
        rows = _parse_rows(str(path))
    except Exception as exc:  # noqa: BLE001 — surface to status
        log.exception("clinical_csv analyzer failed for %s", path)
        return {
            "status": "failed",
            "site": "clinical_csv",
            "report": {},
            "analysis_context": "",
            "message": f"Failed to parse CSV: {exc}",
        }

    if not rows:
        return {
            "status": "ok",
            "site": "clinical_csv",
            "report": {"variants": [], "counts": _build_counts([])},
            "analysis_context": "Empty CSV — no variant rows.",
            "structured_summary": "0 variants reported",
        }

    counts = _build_counts(rows)
    structured_variants = [_structured_variant(r, i) for i, r in enumerate(rows)]

    return {
        "status": "ok",
        "site": "clinical_csv",
        "report": {
            "variants": structured_variants,
            "counts": counts,
            # Raw rows — kept so downstream filters can answer
            # "show me variants where REVEL > 0.7" without re-parsing.
            "raw_rows": rows,
        },
        "analysis_context": _build_analysis_context(rows, counts),
        "structured_summary": _build_structured_summary(rows, counts),
        # Compact column-reference digest — fed into the chat prompt by
        # chatbot.pipeline so the LLM can answer schema questions without
        # us needing live RAG over the upstream column-reference PDF.
        "schema_reference": _column_reference_digest(),
    }
