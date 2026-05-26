"""
Gene Disease Enricher — prevents LLM from hallucinating disease associations.

Priority order per gene:
  1. Current result row Orpha/OMIM/Phenotype_MIM columns (fast, no extra query)
  2. Cross-row DB lookup — find Orpha/OMIM from ANY row of the same gene
  3. HPO gene API fallback — https://hpo.jax.org/api/hpo/gene/{gene}

The enriched _diseases field is injected into LLM context so it ONLY
reports what the patient's own database records say — not training memory.
"""
import logging
import re
from functools import lru_cache
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_HPO_GENE_URL = "https://hpo.jax.org/api/hpo/gene/{gene}"
_TIMEOUT = 4.0


# ── Orpha column parser ────────────────────────────────────────────────────────

def _parse_orpha_column(orpha_str: str) -> List[str]:
    """
    Parse pipe-delimited Orpha column into disease name list.

    Format: "OrphaNum|DiseaseName|Prevalence|Inheritance|Onset|OMIM ~OrphaNum2|..."
    Returns up to 3 disease names.
    """
    if not orpha_str or orpha_str.strip() in (".", "-", "", "None", "nan"):
        return []
    diseases: List[str] = []
    for block in re.split(r'\s*~\s*', orpha_str):
        block = block.strip()
        if not block:
            continue
        parts = block.split("|")
        if len(parts) >= 2:
            name = parts[1].strip()
            # Strip HTML tags that slip in from the Orphanet export
            name = re.sub(r"<[^>]+>|&[a-z]+;|&nbsp;", " ", name).strip()
            name = re.sub(r"\s{2,}", " ", name).strip()
            if name and name not in (".", "-", ""):
                diseases.append(name)
        if len(diseases) >= 3:
            break
    return diseases


# ── HPO gene API ───────────────────────────────────────────────────────────────

@lru_cache(maxsize=500)
def _fetch_hpo_gene_diseases(gene_symbol: str) -> List[Dict]:
    """Query HPO gene→disease API. Cached per gene. Fails silently."""
    try:
        import httpx
        url = _HPO_GENE_URL.format(gene=gene_symbol)
        r = httpx.get(url, timeout=_TIMEOUT)
        if r.status_code == 200:
            data = r.json()
            return [
                {
                    "disease": d.get("diseaseName", ""),
                    "omim_id": d.get("diseaseId", ""),
                }
                for d in data.get("diseases", [])[:5]
                if d.get("diseaseName")
            ]
    except Exception as e:
        logger.debug(f"HPO gene API failed for {gene_symbol}: {e}")
    return []


# ── Per-row disease extraction ─────────────────────────────────────────────────

def _disease_from_row(row: dict) -> Optional[str]:
    """Extract disease info from DB columns (Orpha → Phenotype_MIM → OMIM)."""
    # 1. Orpha — best source: human-readable disease name
    orpha = row.get("Orpha") or row.get("orpha_label") or ""
    parsed = _parse_orpha_column(str(orpha))
    if parsed:
        return "; ".join(parsed)

    # 2. Phenotype_MIM — OMIM phenotype IDs
    pmim = row.get("Phenotype_MIM") or ""
    if str(pmim).strip() not in (".", "-", "", "None", "nan"):
        return f"OMIM phenotype(s): {str(pmim).strip()[:120]}"

    # 3. OMIM gene ID
    omim = row.get("OMIM") or ""
    if str(omim).strip() not in (".", "-", "", "None", "nan"):
        return f"OMIM gene: {str(omim).strip()}"

    return None


# ── Cross-row DB lookup ────────────────────────────────────────────────────────

def _db_gene_lookup(gene: str, db: Any) -> Optional[str]:
    """
    Cross-row lookup: pathogenic rows often have NULL Orpha but other rows
    for the same gene carry full Orpha data. Finds any annotated row.
    """
    if db is None:
        return None
    try:
        from sqlalchemy import text
        sql = text(
            'SELECT Orpha, OMIM, Phenotype_MIM FROM variants '
            'WHERE "Ref.Gene" = :gene '
            'AND (Orpha IS NOT NULL AND Orpha != "" AND Orpha NOT IN (".", "-", "nan")) '
            'LIMIT 1'
        )
        row = db.execute(sql, {"gene": gene}).fetchone()
        if row:
            fake = {
                "Orpha": row[0] or "",
                "OMIM": row[1] or "",
                "Phenotype_MIM": row[2] or "",
            }
            return _disease_from_row(fake)

        # No Orpha row — try OMIM/Phenotype_MIM from any row
        sql2 = text(
            'SELECT OMIM, Phenotype_MIM FROM variants '
            'WHERE "Ref.Gene" = :gene '
            'AND (OMIM IS NOT NULL AND OMIM NOT IN (".", "-", "", "nan")) '
            'LIMIT 1'
        )
        row2 = db.execute(sql2, {"gene": gene}).fetchone()
        if row2:
            fake2 = {"Orpha": "", "OMIM": row2[0] or "", "Phenotype_MIM": row2[1] or ""}
            return _disease_from_row(fake2)
    except Exception as e:
        logger.debug(f"DB gene lookup failed for {gene}: {e}")
    return None


# ── Main entry point ───────────────────────────────────────────────────────────

def enrich_rows_with_diseases(rows: List[dict], db: Any = None) -> List[dict]:
    """
    Add _diseases field to rows using DB columns first, HPO API as fallback.

    Priority per gene:
      1. Orpha/OMIM columns in the current result row
      2. Cross-row DB lookup (same gene, any other row that has Orpha data)
      3. HPO gene API
      4. "No disease association in database"

    The LLM MUST use _diseases for disease names — never training memory.
    """
    if not rows:
        return rows

    # Build gene → disease mapping (one lookup per unique gene)
    gene_disease: Dict[str, str] = {}

    for row in rows:
        gene = str(row.get("Ref.Gene") or row.get("gene_symbol") or "").strip()
        if not gene or gene in ("NONE", ".", "", "None"):
            continue
        if gene in gene_disease:
            continue

        # Step 1: columns in this row
        db_disease = _disease_from_row(row)
        if db_disease:
            gene_disease[gene] = db_disease
            continue

        # Step 2: cross-row DB lookup (critical for pathogenic rows that lack Orpha)
        if db is not None:
            cross = _db_gene_lookup(gene, db)
            if cross:
                gene_disease[gene] = cross
                continue

        # Step 3: HPO API fallback (only when DB columns are completely empty)
        hpo = _fetch_hpo_gene_diseases(gene)
        if hpo:
            gene_disease[gene] = "; ".join(d["disease"] for d in hpo[:3])
        else:
            gene_disease[gene] = "No disease association in database"

    # Annotate rows
    for row in rows:
        gene = str(row.get("Ref.Gene") or row.get("gene_symbol") or "").strip()
        row["_diseases"] = gene_disease.get(gene, "Not recorded")

    return rows
