"""HPO JAX API client — organ/body-system terms → HP IDs → gene lists.

When the local HPO TSV resolver can't map a term (broad organ names like
"lungs", "heart", etc.) this module queries the HPO JAX REST API as a
fallback.

JAX HPO REST API:
  GET https://hpo.jax.org/api/hpo/search?q=<query>&max=5
  GET https://hpo.jax.org/api/hpo/term/<HP_ID>/genes

Example usage:
  >>> from app.hpo.api_client import resolve_organ_query
  >>> genes, terms = resolve_organ_query("lungs")
  >>> print(terms)
  [('HP:0002086', 'Abnormal lung morphology', 312)]
  >>> print(genes[:5])
  ['ABCA3', 'CFTR', 'SFTPB', 'SFTPC', 'SLC34A2']
"""
from __future__ import annotations

import logging
from functools import lru_cache
from typing import Dict, List, Optional, Tuple

log = logging.getLogger(__name__)

# ── Organ / body-system → canonical HPO term IDs ─────────────────────────────
# Pre-mapped so we avoid a round-trip search for the most common queries.
# HP IDs sourced from: https://hpo.jax.org/

ORGAN_TO_HPO: Dict[str, List[str]] = {
    # Respiratory / Lungs
    "lung":             ["HP:0002086"],   # Abnormal lung morphology
    "lungs":            ["HP:0002086"],
    "pulmonary":        ["HP:0002088"],   # Abnormal pulmonary interstitial morphology
    "respiratory":      ["HP:0002088"],
    "breathing":        ["HP:0002086"],
    "lung disease":     ["HP:0002086"],
    "lung problems":    ["HP:0002086"],
    "lung issues":      ["HP:0002086"],

    # Cardiovascular / Heart
    "heart":            ["HP:0001626"],   # Abnormal heart morphology
    "cardiac":          ["HP:0001626"],
    "cardiovascular":   ["HP:0001626"],
    "heart disease":    ["HP:0001626"],
    "heart problems":   ["HP:0001626"],
    "cardiomyopathy":   ["HP:0001638"],

    # Liver / Hepatic
    "liver":            ["HP:0001392"],   # Abnormality of the liver
    "hepatic":          ["HP:0001392"],
    "liver disease":    ["HP:0001392"],
    "liver problems":   ["HP:0001392"],

    # Kidney / Renal
    "kidney":           ["HP:0000077"],   # Abnormality of the kidney
    "kidneys":          ["HP:0000077"],
    "renal":            ["HP:0000077"],
    "kidney disease":   ["HP:0000077"],
    "kidney problems":  ["HP:0000077"],

    # Brain / Neurological
    "brain":            ["HP:0012443"],   # Abnormality of brain morphology
    "neurological":     ["HP:0000707"],   # Abnormality of the nervous system
    "nervous system":   ["HP:0000707"],
    "brain disease":    ["HP:0012443"],
    "neurology":        ["HP:0000707"],

    # Muscle / Musculature
    "muscle":           ["HP:0003011"],   # Abnormality of the musculature
    "muscles":          ["HP:0003011"],
    "muscular":         ["HP:0003011"],
    "muscle disease":   ["HP:0003011"],

    # Bone / Skeletal
    "bone":             ["HP:0011844"],   # Abnormal appendicular skeleton morphology
    "bones":            ["HP:0011844"],
    "skeletal":         ["HP:0011844"],
    "skeleton":         ["HP:0011844"],

    # Blood / Hematological
    "blood":            ["HP:0001871"],   # Abnormality of blood and blood-forming tissues
    "hematological":    ["HP:0001871"],
    "blood disease":    ["HP:0001871"],

    # Eye / Vision
    "eye":              ["HP:0000478"],   # Abnormality of the eye
    "eyes":             ["HP:0000478"],
    "vision":           ["HP:0000504"],   # Abnormality of vision
    "retina":           ["HP:0000479"],   # Abnormality of the retina
    "optic":            ["HP:0000587"],

    # Ear / Hearing
    "ear":              ["HP:0000598"],   # Abnormality of the ear
    "ears":             ["HP:0000598"],
    "hearing":          ["HP:0000365"],   # Hearing impairment
    "deafness":         ["HP:0000365"],

    # Skin / Dermatological
    "skin":             ["HP:0000951"],   # Abnormality of the skin
    "dermatological":   ["HP:0000951"],

    # Thyroid / Endocrine
    "thyroid":          ["HP:0000830"],   # Abnormality of the thyroid gland
    "endocrine":        ["HP:0000818"],   # Abnormality of the endocrine system

    # Pancreas / Diabetes
    "pancreas":         ["HP:0001732"],   # Abnormality of the pancreas
    "diabetes":         ["HP:0000819"],   # Diabetes mellitus
    "diabetic":         ["HP:0000819"],

    # Immune / Immunodeficiency
    "immune":           ["HP:0002715"],   # Abnormality of the immune system
    "immunodeficiency": ["HP:0002664"],   # Neoplasm (closest for immune deficiency)
    "autoimmune":       ["HP:0002715"],

    # GI / Digestive
    "colon":            ["HP:0002590"],
    "bowel":            ["HP:0025031"],
    "gut":              ["HP:0025031"],
    "digestive":        ["HP:0025031"],
    "intestine":        ["HP:0025031"],
    "gastrointestinal": ["HP:0025031"],

    # Reproductive
    "ovarian":          ["HP:0000786"],
    "breast":           ["HP:0100013"],
    "prostate":         ["HP:0008711"],
    "uterus":           ["HP:0000133"],
}

_HPO_API_BASE = "https://hpo.jax.org/api/hpo"
_HTTP_TIMEOUT = 8.0   # seconds — generous for first call, short for cached


def _normalize_organ(term: str) -> Optional[str]:
    """Find the best organ key for a given term string.

    Returns the matching key from ORGAN_TO_HPO, or None.
    """
    t = term.lower().strip()
    # 1. Direct exact match
    if t in ORGAN_TO_HPO:
        return t
    # 2. Prefix / suffix overlap
    for key in ORGAN_TO_HPO:
        if t.startswith(key) or key.startswith(t):
            return key
    # 3. Single-word containment (e.g. "pulmonary fibrosis" → "pulmonary")
    for key in ORGAN_TO_HPO:
        if key in t:
            return key
    return None


def search_hpo_term(query: str, max_results: int = 5) -> List[Tuple[str, str]]:
    """Search HPO for terms matching a free-text query.

    Returns list of (hpo_id, term_name) tuples.
    Hits:  GET https://hpo.jax.org/api/hpo/search?q=<query>&max=<n>
    """
    try:
        import httpx
        url = f"{_HPO_API_BASE}/search"
        params = {"q": query, "max": max_results}
        r = httpx.get(url, params=params, timeout=_HTTP_TIMEOUT)
        r.raise_for_status()
        data = r.json()
        terms = data.get("terms", [])
        return [(t.get("id", ""), t.get("name", "")) for t in terms if t.get("id")]
    except Exception as e:
        log.debug("HPO JAX search failed for %r: %s", query, e)
        return []


@lru_cache(maxsize=512)
def get_genes_for_hp_term(hp_id: str) -> Tuple[str, ...]:
    """Fetch gene list for a single HP term from the JAX API.

    Returns a sorted, deduplicated tuple of HGNC gene symbols.
    Result is LRU-cached to avoid repeated API calls.

    Hits:  GET https://hpo.jax.org/api/hpo/term/<HP_ID>/genes
    """
    try:
        import httpx
        url = f"{_HPO_API_BASE}/term/{hp_id}/genes"
        r = httpx.get(url, timeout=_HTTP_TIMEOUT)
        r.raise_for_status()
        data = r.json()
        # Response: {"genes": [{"geneSymbol": "CFTR", "geneId": 1080}, ...]}
        genes_data = data.get("genes", [])
        genes = tuple(sorted({
            g.get("geneSymbol", "")
            for g in genes_data
            if g.get("geneSymbol")
        }))
        log.debug("HPO API: %s → %d genes", hp_id, len(genes))
        return genes
    except Exception as e:
        log.debug("HPO API gene fetch failed for %s: %s", hp_id, e)
        return ()


def resolve_organ_query(
    query: str,
) -> Tuple[List[str], List[Tuple[str, str, int]]]:
    """Resolve an organ/body-system query to a gene list via HPO.

    Resolution strategy:
      1. Normalize query → check ORGAN_TO_HPO mapping (instant, offline)
      2. For each mapped HP ID, fetch gene list from JAX API
      3. Fallback: JAX API text search if no direct mapping found

    Returns:
      genes         — deduplicated list of HGNC gene symbols (up to 500)
      resolved_terms — [(hp_id, term_name, gene_count), ...] for transparency
    """
    hp_ids: List[str] = []
    resolved_terms: List[Tuple[str, str, int]] = []

    norm = _normalize_organ(query)
    if norm and norm in ORGAN_TO_HPO:
        hp_ids = ORGAN_TO_HPO[norm]
        log.info("Organ resolver: %r → %s via ORGAN_TO_HPO map", query, hp_ids)
    else:
        # Fallback: search HPO API
        results = search_hpo_term(query, max_results=3)
        hp_ids = [r[0] for r in results]
        log.info("Organ resolver: %r → %s via JAX API text search", query, hp_ids)

    all_genes: List[str] = []
    for hp_id in hp_ids[:3]:   # cap at 3 HP terms to avoid huge gene sets
        genes = get_genes_for_hp_term(hp_id)
        if not genes:
            continue
        # Get the canonical term name — try local index first, then use hp_id
        term_name = hp_id
        try:
            from app.hpo.resolver import _index as _hpo_index
            idx = _hpo_index()
            term_name = idx.id_to_name.get(hp_id, hp_id)
        except Exception:
            pass
        resolved_terms.append((hp_id, term_name, len(genes)))
        all_genes.extend(genes)

    # Deduplicate while preserving first-seen order
    seen: set = set()
    unique_genes: List[str] = []
    for g in all_genes:
        if g not in seen:
            seen.add(g)
            unique_genes.append(g)

    return unique_genes[:500], resolved_terms  # cap gene pool at 500
