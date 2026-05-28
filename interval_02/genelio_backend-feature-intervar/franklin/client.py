"""Franklin (Genoox) clinical-db scraper.

Mirrors the homepage search bar at https://franklin.genoox.com/clinical-db/home.
Two input modes:

* Gene symbol (e.g. "BRCA1") — hits the gene-page APIs.
* Variant (HGVS like "NM_000540.3:c.-568T>C", or chr-pos-ref-alt) —
  resolves to canonical coords via /api/parse_search and then pulls
  variant details + ACMG classification.

The CLI lives in the upstream prototype; this module is the importable
library version (no argparse, no prints).
"""
from __future__ import annotations

import concurrent.futures
import re
from typing import Any

import requests

BASE = "https://franklin.genoox.com"

# Public endpoints observed in the SPA's network calls on the gene page.
# Two flavours of query-param name show up (`gene_symbol=` vs `gene=`).
GENE_ENDPOINTS: list[dict[str, str]] = [
    {
        "key": "details",
        "label": "Gene details (OMIM / Entrez / Ensembl / external links)",
        "url": "/api/gene/details?gene_symbol={gene}&reference_version={ref}",
    },
    {
        "key": "aliases",
        "label": "Gene aliases",
        "url": "/api/gene/aliases?gene_symbol={gene}",
    },
    {
        "key": "expression",
        "label": "GTEx tissue expression",
        "url": "/api/gene/expression?gene_symbol={gene}&reference_version={ref}",
    },
    {
        "key": "curated_variants_distribution",
        "label": "Curated variant distribution (LOF / missense / synonymous / non-coding)",
        "url": "/api/curated_variants/distribution?gene={gene}&reference_version={ref}",
    },
    {
        "key": "sensitivity",
        "label": "Gene pathogenicity & LOF sensitivity (ClinGen, gnomAD, Decipher)",
        "url": "/api/gene/sensitivity?gene={gene}&reference_version={ref}",
    },
    {
        "key": "gwas",
        "label": "GWAS traits",
        "url": "/api/gene/gwas_data?gene_symbol={gene}",
    },
    {
        "key": "clinvar_diseases",
        "label": "ClinVar associated conditions",
        "url": "/api/gene/clinvar/diseases_data?gene_symbol={gene}",
    },
    {
        "key": "transcripts",
        "label": "Transcripts (RefSeq)",
        "url": "/api/transcripts/auto_complete?gene_symbol={gene}&str=",
    },
]


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0 Safari/537.36"
            ),
            "Accept": "application/json, text/plain, */*",
            "Referer": f"{BASE}/clinical-db/home",
            "Origin": BASE,
        }
    )
    return s


def _warm(session: requests.Session) -> None:
    # Hit the homepage once to pick up any cookies the API checks.
    try:
        session.get(f"{BASE}/clinical-db/home", timeout=15)
    except requests.RequestException:
        pass


def _fetch_one(
    session: requests.Session, ep: dict[str, str], gene: str, ref: str
) -> dict[str, Any]:
    url = BASE + ep["url"].format(gene=gene, ref=ref)
    try:
        r = session.get(url, timeout=30)
        try:
            body = r.json()
        except ValueError:
            body = r.text
        return {
            "key": ep["key"],
            "label": ep["label"],
            "url": url,
            "status": r.status_code,
            "data": body if r.ok else None,
            "error": None if r.ok else f"HTTP {r.status_code}",
        }
    except requests.RequestException as exc:
        return {
            "key": ep["key"],
            "label": ep["label"],
            "url": url,
            "status": None,
            "data": None,
            "error": str(exc),
        }


def scrape_gene(gene: str, reference: str = "HG19") -> dict[str, Any]:
    gene = gene.strip().upper()
    reference = reference.upper()
    if reference not in ("HG19", "HG38"):
        raise ValueError("reference must be hg19 or hg38")

    session = _session()
    _warm(session)

    sections: dict[str, Any] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(GENE_ENDPOINTS)) as pool:
        futures = [pool.submit(_fetch_one, session, ep, gene, reference) for ep in GENE_ENDPOINTS]
        for fut in concurrent.futures.as_completed(futures):
            res = fut.result()
            sections[res["key"]] = res

    return {
        "kind": "gene",
        "query": gene,
        "reference": reference,
        "page_url": f"{BASE}/clinical-db/gene/{reference.lower()}/{gene}",
        "sections": sections,
    }


# ---------------------------------------------------------------------------
# Variant scraping
# ---------------------------------------------------------------------------
# /api/parse_search is the SPA's universal resolver — takes HGVS or
# chr-pos-ref-alt and returns canonical (chrom, pos, ref, alt) plus matching
# gene. Note: /api/fetch_variant_details uses `chr` while every other variant
# endpoint uses `chrom`. _variant_payload() carries both.


def looks_like_variant(query: str) -> bool:
    q = query.strip()
    return bool(
        re.search(r"[:>]", q)
        or re.match(r"^(chr)?\w+[-: ]\d+[-: ][ACGTN]+[-: ][ACGTN]+$", q, re.I)
        or re.search(r"\bc\.|\bp\.|\bg\.", q)
    )


def _variant_payload(v: dict[str, Any]) -> dict[str, Any]:
    """Normalize a parsed variant into the shape every endpoint wants."""
    return {
        "chrom": v["chrom"],
        "chr": v["chrom"],  # /api/fetch_variant_details uses `chr`
        "pos": v["pos"],
        "ref": v["ref"],
        "alt": v["alt"],
        "reference_version": v.get("reference_version", "HG19"),
    }


def parse_search(session: requests.Session, query: str, reference: str) -> dict[str, Any]:
    r = session.post(
        f"{BASE}/api/parse_search",
        json={"search_text_input": query, "reference_version": reference},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()


def scrape_variant(query: str, reference: str = "HG19") -> dict[str, Any]:
    reference = reference.upper()
    if reference not in ("HG19", "HG38"):
        raise ValueError("reference must be hg19 or hg38")

    session = _session()
    _warm(session)

    # Step 1: resolve the user's input to canonical coordinates.
    try:
        parsed = parse_search(session, query, reference)
    except requests.RequestException as exc:
        return {
            "kind": "variant",
            "query": query,
            "reference": reference,
            "error": f"parse_search failed: {exc}",
        }

    snps = parsed.get("snp_variants") or []
    if not snps:
        return {
            "kind": "variant",
            "query": query,
            "reference": reference,
            "parse_search": parsed,
            "error": "parse_search returned no snp_variants — input may be a gene, CNV, or unsupported syntax",
        }
    v = snps[0]
    payload = _variant_payload(v)

    # Step 2: fan out to the per-variant endpoints in parallel.
    calls = {
        "details": (
            "POST",
            "/api/fetch_variant_details",
            {k: payload[k] for k in ("chr", "pos", "ref", "alt", "reference_version")},
            "Variant details (gene, c./p. notation, region, exon, domains)",
        ),
        "classification": (
            "POST",
            "/api/classify",
            {"variant": {k: payload[k] for k in ("chrom", "pos", "ref", "alt", "reference_version")}},
            "ACMG classification & rule-by-rule evidence",
        ),
        "community_posts": (
            "POST",
            "/api/feed/get_posts",
            {"variant": {k: payload[k] for k in ("chrom", "pos", "ref", "alt", "reference_version")}},
            "Community / case-feed posts for this variant",
        ),
    }

    def _do(item):
        key, (method, path, body, label) = item
        url = BASE + path
        try:
            r = session.request(method, url, json=body, timeout=30)
            try:
                data = r.json()
            except ValueError:
                data = r.text
            return key, {
                "key": key,
                "label": label,
                "url": url,
                "status": r.status_code,
                "data": data if r.ok else None,
                "error": None if r.ok else f"HTTP {r.status_code}",
            }
        except requests.RequestException as exc:
            return key, {
                "key": key, "label": label, "url": url,
                "status": None, "data": None, "error": str(exc),
            }

    sections: dict[str, Any] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(calls)) as pool:
        for key, sec in pool.map(_do, calls.items()):
            sections[key] = sec

    coords = f"{v['chrom']}-{v['pos']}-{v['ref']}-{v['alt']}"
    return {
        "kind": "variant",
        "query": query,
        "reference": reference,
        "resolved": {
            "chrom": v["chrom"],
            "pos": v["pos"],
            "ref": v["ref"],
            "alt": v["alt"],
            "transcripts": v.get("transcripts", []),
            "warnings": v.get("warnings", []),
        },
        "page_url": f"{BASE}/clinical-db/variant/snp/{coords}",
        "sections": sections,
    }
