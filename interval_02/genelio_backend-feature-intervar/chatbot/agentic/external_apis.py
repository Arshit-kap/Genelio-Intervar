"""External-API clients: OMIM, Orphanet, HPO REST.

Spec §1 describes a four-source architecture (local DB + HPO API +
OMIM API + Orphanet API). The local DB is authoritative for whatever
it carries; the external APIs are **enrichment fallbacks** for fields
the CSV doesn't pre-compute (detailed disease descriptions, full
phenotype lists, prevalence, inheritance clinicalSynopsis, …).

Design principles:

* **Pluggable + off-by-default.** Each client is enabled only when its
  base URL (and where relevant, API key) is configured via Django
  settings. When disabled, every method returns ``None`` so callers
  always fall back to local cleanly. This is what keeps a misconfigured
  deploy from leaking PHI to a third-party API — fail closed, not open.
* **Local-first.** Callers must call ``local_context.*`` first and
  only reach for these clients when the local cell was empty/sparse.
  The orchestrator enforces this — see Stage 4 "Enrichment (if needed)".
* **Short timeouts + bounded retries.** A slow external API must not
  stall the patient's chat. Default 5s timeout, no retries by default
  (the user can re-ask if they care enough).
* **No PHI in URLs or logs.** We send disease IDs, gene names, HPO
  IDs — never patient-identifying data. Log lines truncate everything
  past the path.

Configuration (settings.py / .env):

  HPO_API_BASE_URL       e.g. https://ontology.jax.org/api
  OMIM_API_BASE_URL      e.g. https://api.omim.org/api
  OMIM_API_KEY           required by OMIM if enabled
  ORPHANET_API_BASE_URL  e.g. https://api.orphacode.org

Any unset URL = client disabled.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from django.conf import settings

log = logging.getLogger(__name__)


# Default request timeout — fail fast so the chat thread isn't blocked.
_DEFAULT_TIMEOUT_SECS = 5.0


def _is_set(name: str) -> str | None:
    """Return the configured value if non-empty, else None."""
    val = getattr(settings, name, None)
    if val is None:
        return None
    s = str(val).strip()
    return s or None


def _http_get_json(url: str, *, params: dict | None = None,
                   headers: dict | None = None) -> dict | None:
    """Minimal HTTP-GET → JSON helper. Returns None on any failure.

    ``requests`` is imported lazily so Django boot doesn't depend on
    it (and tests that don't exercise external_apis don't pay the
    import cost).
    """
    try:
        import requests  # type: ignore[import-untyped]
    except ImportError:  # pragma: no cover — installed transitively
        log.warning("requests not installed; external_apis disabled")
        return None
    try:
        resp = requests.get(
            url, params=params or {}, headers=headers or {},
            timeout=_DEFAULT_TIMEOUT_SECS,
        )
    except Exception as exc:  # noqa: BLE001 — fail closed
        log.warning("external API %s failed: %s", url, exc)
        return None
    if resp.status_code != 200:
        log.info("external API %s → HTTP %s", url, resp.status_code)
        return None
    try:
        return resp.json()
    except ValueError:
        log.warning("external API %s returned non-JSON", url)
        return None


# ---------------------------------------------------------------------------
# HPO API client — Monarch / JAX HPO REST
# Endpoints we use (spec §2 + §C2):
#   GET /api/hpo/search?q=<lay phrase>     → list of HP terms
#   GET /api/hpo/term/<HP_ID>/genes        → gene set for the term
#   GET /api/hpo/term/<HP_ID>              → term metadata (incl. children)
#   GET /api/hpo/disease/OMIM:<id>         → phenotype list for a disease
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class HPOTermInfo:
    hpo_id: str
    name: str
    genes: tuple[str, ...] = field(default_factory=tuple)


class HPOAPIClient:
    """Thin wrapper around the public HPO REST API."""

    @property
    def base_url(self) -> str | None:
        return _is_set("HPO_API_BASE_URL")

    @property
    def enabled(self) -> bool:
        return self.base_url is not None

    def search(self, query: str) -> HPOTermInfo | None:
        if not self.enabled or not query:
            return None
        data = _http_get_json(
            f"{self.base_url}/hpo/search", params={"q": query},
        )
        if not data:
            return None
        # Shape varies by HPO API vendor — try the common ones.
        hits = data.get("terms") or data.get("results") or []
        if not hits:
            return None
        top = hits[0]
        hp_id = top.get("ontologyId") or top.get("id") or top.get("hpoId")
        name = top.get("name") or top.get("label") or ""
        if not hp_id:
            return None
        return HPOTermInfo(hpo_id=hp_id, name=name)

    def genes_for_term(self, hpo_id: str) -> tuple[str, ...]:
        if not self.enabled or not hpo_id:
            return ()
        data = _http_get_json(f"{self.base_url}/hpo/term/{hpo_id}/genes")
        if not data:
            return ()
        items = data.get("genes") or data.get("results") or []
        out: list[str] = []
        for it in items:
            sym = it.get("symbol") or it.get("geneSymbol") or it.get("name")
            if sym:
                out.append(sym)
        return tuple(sorted(set(out)))

    def phenotypes_for_disease(self, disease_id: str) -> list[HPOTermInfo]:
        """Spec §D1: GET /api/hpo/disease/OMIM:<id> → phenotype list."""
        if not self.enabled or not disease_id:
            return []
        data = _http_get_json(f"{self.base_url}/hpo/disease/{disease_id}")
        if not data:
            return []
        items = data.get("phenotypes") or data.get("hpoTerms") or []
        return [
            HPOTermInfo(
                hpo_id=it.get("ontologyId") or it.get("id") or "",
                name=it.get("name") or it.get("label") or "",
            )
            for it in items
            if (it.get("ontologyId") or it.get("id"))
        ]


# ---------------------------------------------------------------------------
# OMIM API client
# Endpoints used (spec §D1, §D2, §E1):
#   GET /api/entry?mimNumber=<id>&include=text,clinicalSynopsis,geneMap
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class OMIMEntry:
    mim_number: str
    title: str = ""
    description: str = ""
    inheritance: str | None = None  # clinicalSynopsis "Inheritance" field
    gene_symbols: tuple[str, ...] = field(default_factory=tuple)


class OMIMClient:
    """OMIM REST API client (api.omim.org)."""

    @property
    def base_url(self) -> str | None:
        return _is_set("OMIM_API_BASE_URL")

    @property
    def api_key(self) -> str | None:
        return _is_set("OMIM_API_KEY")

    @property
    def enabled(self) -> bool:
        return self.base_url is not None and self.api_key is not None

    def entry(self, mim_number: str) -> OMIMEntry | None:
        if not self.enabled or not mim_number:
            return None
        # Strip an "OMIM:" prefix if present — OMIM expects the raw number.
        mim = mim_number.split(":", 1)[-1].strip()
        data = _http_get_json(
            f"{self.base_url}/entry",
            params={
                "mimNumber": mim,
                "include": "text,clinicalSynopsis,geneMap",
                "apiKey": self.api_key,
                "format": "json",
            },
        )
        if not data:
            return None
        try:
            entry = (
                data.get("omim", {})
                .get("entryList", [{}])[0]
                .get("entry", {})
            )
        except (AttributeError, IndexError):
            return None
        if not entry:
            return None
        title = (
            entry.get("titles", {}).get("preferredTitle")
            or entry.get("titles", {}).get("includedTitles")
            or ""
        )
        description = ""
        for section in entry.get("textSectionList", []) or []:
            ts = section.get("textSection", {})
            if ts.get("textSectionName") in ("description", "clinicalFeatures"):
                description = ts.get("textSectionContent", "") or description
        inheritance = None
        cs = entry.get("clinicalSynopsis", {})
        if isinstance(cs, dict):
            inheritance = cs.get("inheritance")
        gene_map = entry.get("geneMap", {})
        symbols = ()
        if isinstance(gene_map, dict):
            raw = gene_map.get("geneSymbols", "") or ""
            symbols = tuple(s.strip() for s in raw.split(",") if s.strip())
        return OMIMEntry(
            mim_number=mim, title=title, description=description,
            inheritance=inheritance, gene_symbols=symbols,
        )


# ---------------------------------------------------------------------------
# Orphanet API client
# Endpoints used (spec §1 + §D2):
#   GET /disorders/orphacode/<orpha_number>
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class OrphanetEntry:
    orpha_code: str
    name: str = ""
    summary: str = ""
    prevalence: str | None = None


class OrphanetClient:
    """Orphanet REST API client."""

    @property
    def base_url(self) -> str | None:
        return _is_set("ORPHANET_API_BASE_URL")

    @property
    def enabled(self) -> bool:
        return self.base_url is not None

    def by_orpha_code(self, code: str) -> OrphanetEntry | None:
        if not self.enabled or not code:
            return None
        c = code.split(":", 1)[-1].strip().lstrip("Orphanet").strip(": ")
        data = _http_get_json(f"{self.base_url}/disorders/orphacode/{c}")
        if not data:
            return None
        disorder = data.get("data") or data
        name = disorder.get("name") or disorder.get("preferredTerm", "")
        summary = (
            disorder.get("summary")
            or disorder.get("disorderSummary", {}).get("text", "")
            or ""
        )
        prevalence = None
        prev_list = disorder.get("prevalence") or disorder.get("prevalences", [])
        if isinstance(prev_list, list) and prev_list:
            prevalence = prev_list[0].get("class") or prev_list[0].get("value")
        return OrphanetEntry(
            orpha_code=c, name=name, summary=summary, prevalence=prevalence,
        )


# ---------------------------------------------------------------------------
# Module-level singletons (cheap — they hold no state)
# ---------------------------------------------------------------------------

hpo_api = HPOAPIClient()
omim_api = OMIMClient()
orphanet_api = OrphanetClient()


def status() -> dict[str, bool]:
    """Diagnostic — which external APIs are reachable in this deploy."""
    return {
        "hpo_api": hpo_api.enabled,
        "omim_api": omim_api.enabled,
        "orphanet_api": orphanet_api.enabled,
    }
