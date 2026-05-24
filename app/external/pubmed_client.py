"""
Phase 6: PubMed / NCBI E-utilities Client
Fetches literature citations for gene / variant queries.
"""
import logging
import os
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

NCBI_BASE    = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
NCBI_API_KEY = os.getenv("NCBI_API_KEY", "")


async def search_pubmed(query: str, max_results: int = 5) -> Dict[str, Any]:
    """Search PubMed and return article summaries."""
    if not query:
        return {"error": "Empty query"}
    try:
        import httpx
        params = {
            "db": "pubmed", "term": query,
            "retmax": min(max_results, 20),
            "retmode": "json", "sort": "relevance",
        }
        if NCBI_API_KEY:
            params["api_key"] = NCBI_API_KEY

        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(f"{NCBI_BASE}/esearch.fcgi", params=params)
            r.raise_for_status()
            result  = r.json().get("esearchresult", {})
            ids     = result.get("idlist", [])
            total   = int(result.get("count", 0))

            if not ids:
                return {"query": query, "total": 0, "articles": []}

            summaries = await _fetch_summaries(client, ids)

        return {"query": query, "total": total, "articles": summaries}

    except ImportError:
        return {"error": "httpx not installed — run: pip install httpx"}
    except Exception as e:
        logger.error(f"PubMed search error: {e}")
        return {"query": query, "error": str(e)}


async def search_variant_literature(gene: Optional[str] = None,
                                     rsid: Optional[str] = None,
                                     max_results: int = 5) -> Dict[str, Any]:
    """Build a gene/variant-focused PubMed query."""
    parts = []
    if rsid:
        parts.append(f'"{rsid}"')
    if gene:
        parts.append(f'"{gene}"[Gene Name]')
    if not parts:
        return {"error": "Provide gene or rsid"}
    query = " AND ".join(parts)
    return await search_pubmed(query, max_results)


async def _fetch_summaries(client: Any, pmids: List[str]) -> List[Dict]:
    """Fetch article metadata for a list of PMIDs."""
    try:
        params = {"db": "pubmed", "id": ",".join(pmids), "retmode": "json"}
        if NCBI_API_KEY:
            params["api_key"] = NCBI_API_KEY
        r = await client.get(f"{NCBI_BASE}/esummary.fcgi", params=params)
        r.raise_for_status()
        raw = r.json().get("result", {})

        articles = []
        for pmid in pmids:
            art = raw.get(pmid, {})
            if not art:
                continue
            articles.append({
                "pmid":    pmid,
                "title":   art.get("title", ""),
                "authors": [a.get("name", "") for a in art.get("authors", [])[:4]],
                "journal": art.get("source", ""),
                "year":    art.get("pubdate", "")[:4],
                "url":     f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
            })
        return articles
    except Exception as e:
        logger.error(f"PubMed summary fetch error: {e}")
        return []
