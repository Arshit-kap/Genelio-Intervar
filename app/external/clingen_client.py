"""
Phase 6: ClinGen Evidence Repository Client
Two-step workflow: rsID → CAID (ClinGen Allele ID) → Expert Panel assertions.
API reference: https://reg.clinicalgenome.org  /  https://erepo.clinicalgenome.org
"""
import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

ALLELE_REG  = "https://reg.clinicalgenome.org/allele"
EREPO_API   = "https://erepo.clinicalgenome.org/evrepo/api/classifications"


async def get_caid_from_rsid(rsid: str) -> Optional[str]:
    """Look up the ClinGen CAID for an rsID."""
    if not rsid:
        return None
    try:
        import httpx
        url = f"{ALLELE_REG}?hgvsOrDescriptor={rsid}"
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(url, headers={"Accept": "application/json"})
            if r.status_code == 200:
                data = r.json()
                if isinstance(data, list) and data:
                    return data[0].get("@id", "").split("/")[-1]
                if isinstance(data, dict):
                    return data.get("@id", "").split("/")[-1]
    except ImportError:
        logger.warning("httpx not installed")
    except Exception as e:
        logger.debug(f"CAID lookup failed for {rsid}: {e}")
    return None


async def get_clingen_assertions(rsid: Optional[str] = None,
                                  caid: Optional[str] = None) -> Dict[str, Any]:
    """
    Fetch ClinGen expert panel variant assertions.
    Resolves CAID from rsID if not provided.
    """
    if not caid and rsid:
        caid = await get_caid_from_rsid(rsid)

    if not caid:
        return {
            "rsid": rsid, "caid": None,
            "assertions": [],
            "message": "Could not resolve CAID — variant may not be in ClinGen Allele Registry",
        }

    try:
        import httpx
        params = {"allele": caid, "limit": 10}
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(EREPO_API, params=params,
                                 headers={"Accept": "application/json"})
            if r.status_code == 200:
                data = r.json()
                items = data.get("results", data) if isinstance(data, dict) else data
                assertions = []
                for item in (items if isinstance(items, list) else []):
                    assertions.append({
                        "uuid":            item.get("uuid", ""),
                        "classification":  item.get("variant_classification", ""),
                        "condition":       item.get("disease", {}).get("label", ""),
                        "panel":           item.get("affiliation", {}).get("name", ""),
                        "date":            item.get("evaluated_date", ""),
                        "assertion_method": item.get("assertion_method", {}).get("method", ""),
                        "url": (f"https://erepo.clinicalgenome.org/evrepo/ui/classification/{item.get('uuid', '')}"
                                if item.get("uuid") else ""),
                    })
                return {"rsid": rsid, "caid": caid, "assertions": assertions,
                        "total": len(assertions)}
            return {"rsid": rsid, "caid": caid, "assertions": [],
                    "error": f"HTTP {r.status_code}"}
    except ImportError:
        return {"rsid": rsid, "caid": caid, "error": "httpx not installed"}
    except Exception as e:
        logger.error(f"ClinGen ERepo error for {caid}: {e}")
        return {"rsid": rsid, "caid": caid, "error": str(e)}
