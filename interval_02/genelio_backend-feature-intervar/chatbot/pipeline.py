"""Chat pipeline — microbiome-grounded chat.

Direct port of the embedding / retrieval / LLM call used by the original
Gradio prototype in ``legacy/app.py``, now generalised across all four
body-site microbiomes (gut / oral / skin / vaginal):

  * PDF pages -> overlapping chunks (1200 / 200) tagged with section + page
  * Chunks embedded with the Nomic model using ``search_document:`` prefix
  * Stored in a per-report ChromaDB collection (HNSW cosine, persisted to
    disk so server restarts don't rebuild the index)
  * Queries embedded with ``search_query:`` prefix, top-k=8, grouped by page
  * LLM call goes to the vLLM ``/v1`` endpoint with a site-aware system
    prompt and ``enable_thinking: False`` so Qwen3 doesn't emit <think> blocks

For report types without a real analyzer (WGS / WES) a stub reply is
returned so the chat API stays usable end-to-end.

Heavy dependencies (``sentence_transformers``, ``chromadb``, ``openai``,
``pdfplumber``) are imported lazily so the Django server boots even when
they aren't installed — missing deps surface as a graceful chat reply
rather than a 500.
"""
from __future__ import annotations

import logging
import re
from functools import lru_cache
from pathlib import Path
from typing import Iterable

from django.conf import settings

from reports.models import Report, ReportType

log = logging.getLogger(__name__)

CHUNK_SIZE = 1200
CHUNK_OVERLAP = 200
TOP_K = 8

#: Report types that get the full RAG pipeline (structured analysis + PDF
#: retrieval + grounded LLM reply). Microbiome body-sites + genomic tests.
_RAG_ENABLED_TYPES = frozenset({
    ReportType.GUT.value,
    ReportType.ORAL.value,
    ReportType.SKIN.value,
    ReportType.VAGINAL.value,
    ReportType.WES.value,
    ReportType.WGS.value,
    ReportType.CLINICAL_CSV.value,
})

#: Genomic report types use a different system prompt — variants and ACMG
#: classifications instead of microbiome abundance ranges.
_GENOMIC_TYPES = frozenset({ReportType.WES.value, ReportType.WGS.value})

#: Clinical-CSV reports also speak the variant/ACMG dialect but with a
#: much richer per-row annotation schema; they get their own prompt.
_CLINICAL_CSV_TYPES = frozenset({ReportType.CLINICAL_CSV.value})

#: InterVar TXT reports use the deterministic-executor agentic flow
#: (router → HPO → Python filter → answer LLM → HGVS validator → safety).
#: They are NOT in _RAG_ENABLED_TYPES because 76k+ rows can't be
#: chunked/embedded sensibly — the orchestrator grounds on the parsed
#: indexes directly instead of Chroma.
_INTERVAR_TYPES = frozenset({ReportType.INTERVAR.value})


# Short human-readable site names used inside the system prompt so the
# assistant can speak about "oral / skin / vaginal health" where
# appropriate rather than always saying "gut health".
_SITE_PHRASES = {
    ReportType.GUT.value: ("gut", "gut-health", "gut microbiome"),
    ReportType.ORAL.value: ("oral", "oral-health", "oral microbiome"),
    ReportType.SKIN.value: ("skin", "skin-health", "skin microbiome"),
    ReportType.VAGINAL.value: ("vaginal", "vaginal-health", "vaginal microbiome"),
}

_GENOMIC_PHRASES = {
    ReportType.WES.value: "Whole Exome Sequencing (WES)",
    ReportType.WGS.value: "Whole Genome Sequencing (WGS)",
}


def _system_prompt(report_type: str) -> str:
    """Render the system prompt for the given report type."""
    if report_type in _CLINICAL_CSV_TYPES:
        return _CLINICAL_CSV_SYSTEM_PROMPT
    if report_type in _GENOMIC_TYPES:
        return _GENOMIC_SYSTEM_PROMPT_TEMPLATE.format(
            test_name=_GENOMIC_PHRASES[report_type],
        )
    domain, health_term, report_name = _SITE_PHRASES.get(
        report_type, _SITE_PHRASES[ReportType.GUT.value],
    )
    return _SYSTEM_PROMPT_TEMPLATE.format(
        domain=domain, health_term=health_term, report_name=report_name,
    )


# Full legacy system prompt — ported verbatim from legacy/app.py and
# parameterised over body site so gut/oral/skin/vaginal reports each get
# site-appropriate phrasing while the rules stay identical.
_SYSTEM_PROMPT_TEMPLATE = """\
You are **Genelio**, a friendly and professional {health_term} assistant.

You have access to TWO types of context:
1. **STRUCTURED ANALYSIS** — Pre-parsed data from the user's report with exact abundance values, \
healthy reference ranges, and status flags (ABOVE_RANGE, BELOW_RANGE, WITHIN_RANGE, NOT_DETECTED) \
for every biomarker across all conditions.
2. **RAG CONTEXT** — Raw text excerpts retrieved from the report for additional details.

ALWAYS prefer the structured analysis for numerical comparisons. Use the RAG context for \
explanations, significance descriptions, and lifestyle recommendations.

Your job:
1. Help the user understand their {report_name} report in simple, reassuring language.
2. For EVERY condition the user asks about, list ALL biomarkers checked for that condition \
along with their actual abundance vs healthy reference range, and clearly state if each is \
ABOVE, BELOW, or within normal range.
3. When a marker is out of range, explain what it means and suggest practical dietary or \
lifestyle improvements — but always remind the user to consult their healthcare provider.
4. When asked about keystone / protective species, list ALL present ones AND all missing ones. \
Include dietary or lifestyle recommendations for the missing ones.
5. If you don't have enough information, say so honestly.

Formatting guidelines:
- Use tables (markdown) when comparing multiple markers.
- Use \U0001F534 for above-range, \U0001F7E1 for below-range, \U0001F7E2 for normal, \u26AA for not-detected.
- Avoid overly technical jargon; explain medical terms in plain language.
- Be empathetic — many users may feel anxious about their results.
- NEVER diagnose diseases. You provide educational information only.
- Keep answers concise but thorough.
"""


# Genomic prompt — used for WES / WGS reports. The structured analysis here
# is a list of variants (gene, disease, mode of inheritance, zygosity, ACMG
# classification) rather than abundance values, so the rules read differently.
_GENOMIC_SYSTEM_PROMPT_TEMPLATE = """\
You are **Genelio**, a friendly and professional genomic-health assistant \
helping a patient interpret their {test_name} report.

You have access to up to THREE types of context:
1. **STRUCTURED ANALYSIS** — Pre-parsed data from the patient's report \
including patient information, summary, and a list of clinically significant \
variants. Each variant carries the gene, associated disease, mode of \
inheritance (e.g., autosomal recessive, X-linked dominant), the variant \
notation, genomic location and transcript, zygosity (homozygous, \
heterozygous, hemizygous), and the ACMG classification (Pathogenic, Likely \
Pathogenic, Variant of Uncertain Significance, etc.). Carrier status is \
flagged separately.
2. **RAG CONTEXT** — Raw text excerpts retrieved from the report — useful \
for protein function, clinical phenotype descriptions, and methodology details.
3. **FRANKLIN ENRICHMENT** *(when present)* — Independent re-classification \
and curated gene-level context fetched per-variant from the Genoox Franklin \
clinical database. Includes Franklin's own ACMG verdict with the rules it \
fired, gene-level metadata (location, OMIM, transcripts), associated \
ClinVar conditions, and population sensitivity scores (pLI, o/e LOF).

ALWAYS prefer the structured analysis for variant lists, zygosity, and the \
report's own classifications. Use Franklin enrichment to corroborate or \
contrast the report's classification — if Franklin disagrees, surface that \
distinction to the patient. Use the RAG context for narrative descriptions \
of the gene, the protein, and clinical phenotypes.

Your job:
1. Help the patient understand their {test_name} report in simple, \
reassuring language.
2. When asked about a variant or gene, list the gene symbol, the associated \
disease, the mode of inheritance, the zygosity, and the ACMG classification.
3. Distinguish clearly between **disease findings** (clinically significant \
for the patient now) and **carrier findings** (typically asymptomatic but \
relevant for family planning).
4. Explain ACMG terms in plain language: Pathogenic = strong evidence of \
causing disease; Likely Pathogenic = high probability; Variant of \
Uncertain Significance = unclear; Carrier = one copy of a recessive \
variant, usually asymptomatic.
5. Always remind the patient that genetic findings should be discussed \
with a physician or certified genetic counselor — never diagnose, prescribe, \
or recommend specific medical actions.

Formatting guidelines:
- Use markdown tables when listing multiple variants.
- Use \U0001F9EC for variants, ⚠️ for high-impact pathogenic findings, \
\U0001F4DD for carrier findings.
- Avoid excessive technical jargon; explain medical terms in plain language.
- Be empathetic — genetic results can be anxiety-provoking.
- NEVER diagnose diseases or recommend specific treatments. You provide \
educational information only.
- Keep answers concise but thorough.
"""


# Clinical-variant CSV system prompt. This dialect has a much richer schema
# than the BioAro PDF (72 columns: ClinVar, InterVar, ACMG evidence flags,
# multiple ML predictors, patient genotype, clinical flags). The user
# message includes a SCHEMA REFERENCE block — the LLM should consult it
# before guessing column meanings.
_CLINICAL_CSV_SYSTEM_PROMPT = """\
You are **Genelio**, a friendly and professional clinical genomics \
assistant helping a patient interpret an annotated clinical-variant CSV \
report (an ANNOVAR / BioAro-style export with ~72 annotated columns per \
variant).

You have access to FOUR context blocks in the user message:

1. **STRUCTURED ANALYSIS** — Pre-parsed summary across the whole CSV: total \
variant count, ClinVar significance breakdown, functional-impact breakdown, \
clinical-flag counts (primary / secondary / carrier / actionable), top \
genes by variant count, every flagged or pathogenic variant rendered in \
full, and an abbreviated one-liner per remaining variant.
2. **SCHEMA REFERENCE** — A concise dictionary of every column the CSV \
exposes, with its clinical meaning. **Whenever a user asks what a column \
means** (e.g. "what is CADD_phred?", "what does PVS1 = YES tell you?"), \
quote from this block.
3. **RAG CONTEXT** — Free-text excerpts retrieved from the raw CSV — \
useful for variant-level detail not surfaced in the structured analysis.
4. **USER QUESTION** — what the patient actually asked.

Operating rules — read each one carefully, they are non-negotiable:

0. **YOU MUST NOT FABRICATE OR EXTRAPOLATE.** The CSV in STRUCTURED \
ANALYSIS is the *complete* universe of variants for this patient. If a \
gene, variant, or finding is **not present** in STRUCTURED ANALYSIS, the \
correct answer is: *"I don't see any variants in [GENE] in this report."* \
**You must NEVER invent variants, HGVS notations, exon numbers, ClinVar \
classifications, or diseases. Anything not literally in the context is \
unknown to you and must be reported as such.** Patients can be harmed by \
fabricated genetic findings — treat this as a hard safety boundary.

  WRONG behaviour (do not do this):
    User: "Any BRCA1 variants?"
    You:  "Yes, you have BRCA1:NM_000059:exon2:c.C61G..."   ← invented.
  CORRECT behaviour:
    User: "Any BRCA1 variants?"
    You:  "I don't see any BRCA1 variants in this report. To confirm, the \
top genes by variant count in your report are: [list from counts.top_genes]."

1. **Ground every claim in the data.** If the structured analysis says \
"primary_findings: 2" then there are exactly two primary findings — don't \
invent more.
2. **Cite the ClinVar significance string verbatim** when it exists. \
"Conflicting_classifications_of_pathogenicity" must be reported as such, \
not paraphrased as "pathogenic".
3. **Be explicit about ACMG evidence.** PVS1 / PM2 / PP3 / BA1 columns \
are YES/NO flags from this CSV. The STRUCTURED ANALYSIS contains \
pre-computed lists (e.g. "Variants with PVS1=YES:") — **use those lists \
directly rather than scanning the full headline block** when answering \
flag-filtered questions.
4. **Distinguish primary, secondary, carrier and actionable findings** — \
they have different clinical implications (current disease vs. incidental \
ACMG SF finding vs. recessive carrier vs. clinically actionable today). \
Each has its own pre-computed list in STRUCTURED ANALYSIS — quote from \
those lists, do not re-derive.
5. **Pharmacogenomic findings** (Pharmacogenomic_Association column) get \
special attention — they affect drug response now, not future risk. If \
the count is 0, say so honestly.
6. **NEVER diagnose diseases or prescribe.** Always close with a \
recommendation to discuss findings with a physician or certified genetic \
counselor.
7. **When asked aggregate questions** ("how many pathogenic?"), use the \
exact counts from the STRUCTURED ANALYSIS counts block.
8. **When asked about a specific variant or gene,** quote the gene, the \
HGVS notation, the zygosity, the ClinVar significance, and the ACMG \
evidence that fired — only if the variant is actually in the data.
9. **Ignore your prior turns if they contradict the STRUCTURED ANALYSIS.** \
The structured data is authoritative; if an earlier reply you gave \
mentioned a gene that isn't in the data, that earlier reply was wrong \
and you must correct it.
10. **Output only natural language explanations and markdown tables.** \
**Never** emit Python code, ``tool_code`` blocks, function calls, or any \
agentic-execution scaffolding. You are talking to a patient, not running \
a script. If you find yourself about to write `def`, `import`, or \
``` ```tool_code ```, stop — write the answer directly in prose instead.

Formatting:
- Markdown tables for multi-variant lists.
- 🧬 variant · ⚠️ pathogenic · 📝 carrier · 💊 pharmacogenomic.
- Keep technical terms but always give a plain-language gloss.
- Be empathetic — many users are anxious about genetic findings.
"""


def _format_csv_schema_reference(report: Report) -> str:
    """Return the column-reference digest from the analyzer output, if any.

    The clinical_csv analyzer attaches a ``schema_reference`` field to its
    parsed_data so the chat prompt can show the LLM what each column
    means without us doing live RAG over the upstream PDF.
    """
    pd = report.parsed_data or {}
    return pd.get("schema_reference", "")


# ---------------------------------------------------------------------------
# Lazy singletons — built once per process on first use.
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def _embedder():
    from sentence_transformers import SentenceTransformer
    return SentenceTransformer(settings.EMBED_MODEL, trust_remote_code=True)


@lru_cache(maxsize=1)
def _chroma_client():
    import chromadb

    persist_dir = Path(settings.CHROMA_PERSIST_DIR)
    persist_dir.mkdir(parents=True, exist_ok=True)
    return chromadb.PersistentClient(path=str(persist_dir))


@lru_cache(maxsize=1)
def _llm():
    from openai import OpenAI
    return OpenAI(base_url=settings.VLLM_BASE_URL, api_key="not-needed")


def _collection_name(report: Report) -> str:
    """Return a deterministic ChromaDB collection name for ``report``.

    Collection names must match ``^[a-zA-Z0-9._-]{3,63}$``; we encode the
    report type + uuid so it's obvious at a glance which site each
    collection belongs to (useful when inspecting the persist dir).
    """
    return f"{report.report_type}_report_{str(report.id).replace('-', '')}"


# ---------------------------------------------------------------------------
# PDF -> chunks -> embeddings (legacy parity)
# ---------------------------------------------------------------------------

def _extract_pages(pdf_path: str) -> list[dict]:
    import pdfplumber

    pages = []
    with pdfplumber.open(pdf_path) as pdf:
        for i, page in enumerate(pdf.pages):
            text = (page.extract_text() or "").strip()
            if text:
                pages.append({"page": i + 1, "text": text})
    return pages


def _extract_csv_pages(csv_path: str) -> list[dict]:
    """Treat each CSV row as a 'page' for the chunker.

    Each row becomes a self-contained, retrieval-friendly text block:
    gene + HGVS + ClinVar significance + key annotation columns + the
    upstream interpretation summary. This shape lets the embedder find
    a specific variant when the user asks "tell me about PADI3" or
    "any pathogenic findings in COL1A1?".
    """
    import csv as _csv

    pages: list[dict] = []
    with open(csv_path, newline="", encoding="utf-8-sig") as fh:
        reader = _csv.DictReader(fh)
        # Embed the schema digest as the very first chunk so the
        # retriever has it for "what does CADD_phred mean?" queries.
        try:
            from reports.analyzers.clinical_csv import _column_reference_digest
            pages.append({"page": 0, "text": "SCHEMA REFERENCE\n" + _column_reference_digest()})
        except Exception:  # noqa: BLE001 — schema digest is non-essential
            pass

        for i, row in enumerate(reader, start=1):
            # Keep only the most retrieval-useful fields per row so the
            # chunk stays small and embeddings remain meaningful.
            keep = (
                "#Chr", "Start", "Ref", "Alt",
                "Ref.Gene", "AAChange.refGene", "NM_ID", "HGVSg",
                "Func.refGene", "ExonicFunc.refGene", "Consequence", "Impact",
                "clinvar: Clinvar", "ClinVar_Disease", "ClinVar_Review_Status",
                "InterVar: InterVar and Evidence",
                "Mode_of_Inheritance", "Zygosity",
                "CADD_phred", "REVEL_score", "SIFT_pred", "Polyphen2_HDIV_pred",
                "PVS1", "PM2", "PP3", "BA1",
                "Primary_Finding", "Secondary_Finding", "Carrier_Status",
                "Actionable", "Pharmacogenomic_Association",
                "Interpretation_Summary",
            )
            parts = []
            for col in keep:
                val = (row.get(col) or "").strip()
                if val and val != ".":
                    parts.append(f"{col}: {val}")
            if not parts:
                continue
            header = row.get("Ref.Gene") or row.get("Gene.ensGene") or f"Row {i}"
            body = f"VARIANT {i} — {header}\n" + "\n".join(parts)
            pages.append({"page": i, "text": body})
    return pages


def _chunk_pages(pages: list[dict]) -> list[dict]:
    chunks: list[dict] = []
    for p in pages:
        text = p["text"]
        header = text.split("\n")[0][:120]
        start = 0
        while start < len(text):
            piece = text[start:start + CHUNK_SIZE]
            chunks.append({
                "id": f"p{p['page']}_c{len(chunks)}",
                "tagged": f"[Section: {header}] [Page {p['page']}]\n{piece}",
                "text": piece,
                "page": p["page"],
            })
            start += CHUNK_SIZE - CHUNK_OVERLAP
    return chunks


def _build_collection(report: Report):
    """Create (or replace) the per-report Chroma collection and embed chunks."""
    client = _chroma_client()
    name = _collection_name(report)

    # Fresh collection on rebuild — drop anything stale.
    try:
        client.delete_collection(name)
    except Exception:  # noqa: BLE001 — collection may not exist
        pass

    file_path = report.file.path
    if file_path.lower().endswith(".csv"):
        pages = _extract_csv_pages(file_path)
        if not pages:
            raise ValueError("No extractable rows in CSV.")
    else:
        pages = _extract_pages(file_path)
        if not pages:
            raise ValueError("No extractable text in PDF (scanned image?).")
    chunks = _chunk_pages(pages)

    collection = client.create_collection(
        name=name,
        metadata={"hnsw:space": "cosine"},
    )

    doc_texts = [f"search_document: {c['tagged']}" for c in chunks]
    embeddings = _embedder().encode(doc_texts, show_progress_bar=False).tolist()

    collection.add(
        ids=[c["id"] for c in chunks],
        embeddings=embeddings,
        documents=[c["text"] for c in chunks],
        metadatas=[{"page": c["page"]} for c in chunks],
    )
    log.info(
        "Indexed %s report %s: %d pages, %d chunks",
        report.report_type, report.id, len(pages), len(chunks),
    )
    return collection


def _ensure_collection(report: Report):
    """Return the Chroma collection for ``report``, building it if needed."""
    client = _chroma_client()
    name = _collection_name(report)
    try:
        return client.get_collection(name)
    except Exception:  # noqa: BLE001 — collection doesn't exist yet
        return _build_collection(report)


def _retrieve(report: Report, query: str, top_k: int = TOP_K) -> str:
    collection = _ensure_collection(report)
    query_emb = _embedder().encode([f"search_query: {query}"]).tolist()
    results = collection.query(query_embeddings=query_emb, n_results=top_k)
    docs = results["documents"][0]
    metas = results["metadatas"][0]

    by_page: dict[int, list[str]] = {}
    for doc, meta in zip(docs, metas):
        by_page.setdefault(meta["page"], []).append(doc)

    return "\n\n---\n\n".join(
        f"[Page {p}]\n" + "\n".join(by_page[p]) for p in sorted(by_page)
    )


# ---------------------------------------------------------------------------
# LLM call (legacy parity, incl. enable_thinking=False)
# ---------------------------------------------------------------------------

def _format_franklin_enrichment(report: Report) -> str:
    """Compact text rendering of ``report.enrichment_data`` for the LLM.

    Returns ``""`` when the report has no enrichment (microbiome reports,
    or genomic reports whose enrichment task hasn't completed). Each
    variant becomes a small block listing the Franklin ACMG verdict, met
    rules, and the most-useful gene-level fields. Bounded length so the
    prompt doesn't blow past the model's context.
    """
    enrichment = report.enrichment_data or {}
    variants = enrichment.get("variants") or []
    if not variants:
        return ""

    blocks: list[str] = []
    for idx, ev in enumerate(variants):
        if not ev or ev.get("error"):
            continue
        kind = ev.get("kind") or "?"
        query = ev.get("query") or ""
        secs = ev.get("sections") or {}
        lines = [f"[{idx}] {kind.upper()} lookup: {query}"]

        if kind == "variant":
            cls = (secs.get("classification") or {}).get("data") or {}
            if cls.get("classification"):
                lines.append(
                    f"  Franklin ACMG: {cls.get('classification')} "
                    f"(score={cls.get('score')}, bayes={cls.get('bayes_score')})"
                )
            met = [
                r for r in (cls.get("rules") or [])
                if r.get("assessment") not in ("UNMET", None, "NOT_MET", "")
            ]
            if met:
                names = ", ".join(
                    f"{r.get('name')}({r.get('assessment')})" for r in met[:8]
                )
                lines.append(f"  Met rules: {names}")
            d = (secs.get("details") or {}).get("data") or {}
            for k in ("gene", "transcript", "region", "effect", "exon"):
                if d.get(k):
                    lines.append(f"  {k}: {d[k]}")
        else:  # gene fallback
            d = (secs.get("details") or {}).get("data") or {}
            for k in ("symbol", "description", "maplocation", "entrez_id", "omim_id"):
                if d.get(k):
                    lines.append(f"  {k}: {d[k]}")
            sens = (secs.get("sensitivity") or {}).get("data") or {}
            if sens.get("pli") is not None:
                lines.append(f"  pLI: {sens.get('pli')}  o/e LOF: {sens.get('oe_lof')}")
            traits = ((secs.get("gwas") or {}).get("data") or {}).get("traits") or []
            if traits:
                top = "; ".join(t.get("reported_trait", "") for t in traits[:3])
                lines.append(f"  Top GWAS: {top}")
        blocks.append("\n".join(lines))

    # Cap at ~3.5k chars (~1k tokens) so the chat prompt stays bounded.
    text = "\n\n".join(blocks)
    if len(text) > 3500:
        text = text[:3500] + "\n…[truncated]"
    return text


def _call_llm(query: str, structured_context: str, rag_context: str,
              history: Iterable[dict], report_type: str,
              franklin_context: str = "",
              schema_reference: str = "") -> str:
    messages = [{"role": "system", "content": _system_prompt(report_type)}]
    # The caller passes the full session history including the *current*
    # user turn (which was persisted before generate_reply ran). Drop it
    # here — we re-attach it below with structured + RAG + Franklin
    # context. Without this, the prompt ends in two consecutive `user`
    # messages, which strict chat templates (e.g. medgemma-27b) reject.
    prior = list(history)
    if prior and prior[-1].get("role") == "user":
        prior = prior[:-1]
    for msg in prior[-6:]:
        messages.append({"role": msg["role"], "content": msg["content"]})

    parts = [
        f"STRUCTURED ANALYSIS (pre-parsed from report):\n```\n{structured_context}\n```",
        f"RAG CONTEXT (raw text excerpts from report):\n```\n{rag_context}\n```",
    ]
    if schema_reference:
        parts.append(
            "SCHEMA REFERENCE (per-column meaning for the clinical-variant "
            "CSV — consult this whenever a user asks what a column means):"
            f"\n```\n{schema_reference}\n```"
        )
    if franklin_context:
        parts.append(
            "FRANKLIN ENRICHMENT (curated database — ACMG re-classifications, "
            "gene-level context, ClinVar disease links — fetched from Genoox "
            "Franklin per variant):\n```\n" + franklin_context + "\n```"
        )
    parts.append(f"USER QUESTION: {query}")

    messages.append({"role": "user", "content": "\n\n".join(parts)})

    # Lower temperature for the clinical-CSV path: the answers are fact-
    # lookups against structured columns, not narrative explanations, so we
    # want the model to stick to the data and not paraphrase or extrapolate.
    temperature = 0.1 if report_type in _CLINICAL_CSV_TYPES else 0.3
    response = _llm().chat.completions.create(
        model=settings.MODEL_NAME,
        messages=messages,
        max_tokens=3072,
        temperature=temperature,
        extra_body={"chat_template_kwargs": {"enable_thinking": False}},
    )
    raw = response.choices[0].message.content or ""
    cleaned = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
    return cleaned or raw.strip()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def index_report(report: Report) -> dict:
    """Eagerly build the Chroma index for a microbiome report.

    Called from the upload pipeline right after the structured analyzer
    finishes, so the first chat message doesn't pay the embedding cost.
    Failures are swallowed by the caller — indexing is best-effort.
    Unsupported report types are skipped with a structured reason.
    """
    if report.report_type not in _RAG_ENABLED_TYPES:
        return {"indexed": False, "reason": "unsupported_type"}
    collection = _build_collection(report)
    return {"indexed": True, "chunks": collection.count()}


# Backwards-compat alias — previously RAG was gut-only.
index_gut_report = index_report


def generate_reply(session, user_message: str) -> str:
    """Produce an assistant reply for ``user_message`` within ``session``."""
    report: Report | None = session.report
    history = [
        {"role": m.role, "content": m.content}
        for m in session.messages.all()
    ]

    # InterVar TXT — agentic flow with deterministic Python executor.
    # Sits outside _RAG_ENABLED_TYPES because the 76k-row scale rules out
    # Chroma embedding; we ground the answer LLM on the executor's
    # structured result + schema reference + HPO profile instead.
    if report and report.report_type in _INTERVAR_TYPES and report.parsed_data:
        try:
            from chatbot.agentic.intervar_orchestrator import (
                handle_intervar_message,
            )
            return handle_intervar_message(session, user_message)
        except ImportError as exc:
            log.error("InterVar pipeline ImportError — missing dep: %s", exc)
            return (
                "The InterVar analysis pipeline is not available in this "
                f"environment (missing package: `{exc}`). Run "
                "`pip install openai` and ensure the vLLM server is "
                f"running at `{settings.VLLM_BASE_URL}`."
            )
        except ConnectionError as exc:
            log.error("InterVar pipeline connection error: %s", exc)
            return (
                f"Could not connect to the LLM server at "
                f"`{settings.VLLM_BASE_URL}`. Start the vLLM service "
                "and try again."
            )
        except Exception as exc:  # noqa: BLE001
            log.exception("InterVar orchestrator error for session %s", session.id)
            return (
                f"The InterVar analysis pipeline encountered an error "
                f"({type(exc).__name__}: {exc}). Please try again shortly."
            )

    if report and report.report_type in _RAG_ENABLED_TYPES and report.parsed_data:
        try:
            # Clinical CSV uses the agentic orchestrator (router → HPO →
            # filter → answer → safety) so symptom-aware questions get
            # routed through HPO resolution and the filtered-variant
            # context. All other report types stay on the single-shot
            # RAG-augmented LLM call.
            if report.report_type in _CLINICAL_CSV_TYPES:
                from chatbot.agentic.orchestrator import handle_clinical_csv_message
                return handle_clinical_csv_message(session, user_message)

            rag_context = _retrieve(report, user_message)
            structured = report.parsed_data.get("analysis_context", "")
            franklin = (
                _format_franklin_enrichment(report)
                if report.report_type in _GENOMIC_TYPES
                else ""
            )
            return _call_llm(
                user_message, structured, rag_context, history, report.report_type,
                franklin_context=franklin,
            )
        except ImportError as exc:
            log.error("RAG pipeline ImportError — missing dependency: %s", exc)
            return (
                f"The {report.get_report_type_display()} analysis pipeline is "
                f"not available in this environment (missing package: `{exc}`). "
                "Run `pip install sentence-transformers chromadb openai pdfplumber` "
                "and ensure the vLLM server is running at "
                f"`{settings.VLLM_BASE_URL}`."
            )
        except ConnectionError as exc:
            log.error("RAG pipeline connection error: %s", exc)
            return (
                f"Could not connect to the LLM server at `{settings.VLLM_BASE_URL}`. "
                "Start the vLLM service and try again."
            )
        except Exception as exc:  # noqa: BLE001 — don't 500 the API
            log.exception("RAG pipeline error for session %s", session.id)
            return (
                f"The analysis pipeline encountered an error "
                f"({type(exc).__name__}: {exc}). Please try again shortly."
            )

    if report and report.report_type not in _RAG_ENABLED_TYPES:
        return (
            f"Support for **{report.get_report_type_display()}** reports is "
            "coming soon. Your report has been processed and stored — full "
            "chat analysis will be available in a future update."
        )

    return (
        "No report is attached to this chat session. "
        "Upload a report first, then create a new session linked to it."
    )
