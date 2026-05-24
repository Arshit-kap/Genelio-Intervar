"""
Phase 6: ClinVar / NCBI E-utilities Client
Fetches variant data from ClinVar using NCBI REST APIs.
Free, no key required (but HF_TOKEN / NCBI_API_KEY env var speeds up rate limits).
"""
import logging
import os
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

NCBI_BASE    = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
NCBI_API_KEY = os.getenv("NCBI_API_KEY", "")    # optional, raises rate limit 3→10 req/s


async def search_clinvar_by_rsid(rsid: str) -> Dict[str, Any]:
    """Search ClinVar for a variant by rsID and return summary records."""
    if not rsid:
        return {"error": "No rsid provided"}
    try:
        import httpx
        params = {"db": "clinvar", "term": f"{rsid}[rs]",
                  "retmax": 5, "retmode": "json"}
        if NCBI_API_KEY:
            params["api_key"] = NCBI_API_KEY

        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(f"{NCBI_BASE}/esearch.fcgi", params=params)
            r.raise_for_status()
            ids: List[str] = r.json().get("esearchresult", {}).get("idlist", [])

            if not ids:
                return {"rsid": rsid, "clinvar_ids": [], "records": [],
                        "message": "No ClinVar records found"}

            sum_params = {"db": "clinvar", "id": ",".join(ids[:5]), "retmode": "json"}
            if NCBI_API_KEY:
                sum_params["api_key"] = NCBI_API_KEY
            r2 = await client.get(f"{NCBI_BASE}/esummary.fcgi", params=sum_params)
            r2.raise_for_status()
            raw = r2.json().get("result", {})

            records = []
            for uid in ids:
                rec = raw.get(uid, {})
                if not rec:
                    continue
                records.append({
                    "clinvar_id":          uid,
                    "title":               rec.get("title", ""),
                    "clinical_sig":        _extract_clinsig(rec),
                    "review_status":       rec.get("review_status", ""),
                    "gene_sort":           rec.get("gene_sort", ""),
                    "chromosome":          rec.get("chr_sort", ""),
                    "variation_type":      rec.get("obj_type", ""),
                    "last_evaluated":      rec.get("last_evaluated", ""),
                    "clinvar_url":         f"https://www.ncbi.nlm.nih.gov/clinvar/variation/{uid}/",
                })

            return {"rsid": rsid, "clinvar_ids": ids, "records": records}

    except ImportError:
        return {"rsid": rsid, "error": "httpx not installed — run: pip install httpx"}
    except Exception as e:
        logger.error(f"ClinVar search error for {rsid}: {e}")
        return {"rsid": rsid, "error": str(e)}


async def search_clinvar_by_variant(chrom: str, pos: int,
                                     ref: str, alt: str) -> Dict[str, Any]:
    """Search ClinVar using HGVS-style coordinates."""
    query = f"{chrom}[chr] AND {pos}[chrpos37]"
    try:
        import httpx
        params = {"db": "clinvar", "term": query, "retmax": 5, "retmode": "json"}
        if NCBI_API_KEY:
            params["api_key"] = NCBI_API_KEY

        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(f"{NCBI_BASE}/esearch.fcgi", params=params)
            r.raise_for_status()
            ids = r.json().get("esearchresult", {}).get("idlist", [])
            return {"query": query, "clinvar_ids": ids, "count": len(ids)}
    except ImportError:
        return {"error": "httpx not installed"}
    except Exception as e:
        logger.error(f"ClinVar variant search error: {e}")
        return {"error": str(e)}


def _extract_clinsig(rec: Dict) -> str:
    """Parse clinical significance from ClinVar summary record."""
    sig = rec.get("clinical_significance", {})
    if isinstance(sig, dict):
        return sig.get("description", "")
    return str(sig)
