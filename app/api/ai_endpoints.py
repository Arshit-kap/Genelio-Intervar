"""
Phase 4: AI / Chat & Text-to-SQL API Endpoints
Natural language powered by Qwen3 (HuggingFace).

Endpoints:
  POST /api/ai/chat    — conversational interface (general + data questions)
  POST /api/ai/query   — structured text-to-SQL (legacy, returns raw SQL + rows)
  GET  /api/ai/status  — LLM backend status
  GET  /api/ai/examples
  GET  /api/ai/schema
"""
import re
import time
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, field_validator

from app.database import SessionLocal
from app.models import QueryLog

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/ai", tags=["AI / Chat"])


# ── Models ─────────────────────────────────────────────────────────────────────

class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="ignore")
    role: str = "user"
    content: str = ""

    @field_validator("content", mode="before")
    @classmethod
    def coerce_content(cls, v):
        if v is None:
            return ""
        if isinstance(v, (list, dict)):
            return str(v)
        return str(v)

    @field_validator("role", mode="before")
    @classmethod
    def coerce_role(cls, v):
        return str(v) if v is not None else "user"

class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    message: str
    history: List[ChatMessage] = []
    max_rows: int = 20
    include_sql: bool = True

class ChatResponse(BaseModel):
    response: str
    type: str              # "data_query" | "general" | "hybrid"
    sql: Optional[str] = None
    sql_source: Optional[str] = None
    data: Optional[List[Dict]] = None
    row_count: int = 0
    execution_time_ms: float = 0
    error: Optional[str] = None

class NLQueryRequest(BaseModel):
    question: str
    max_rows: int = 100
    include_sql: bool = True

class NLQueryResponse(BaseModel):
    success: bool
    question: str
    response: str
    sql: Optional[str] = None
    sql_source: Optional[str] = None
    row_count: int = 0
    execution_time_ms: float = 0
    error: Optional[str] = None


# ── Intent classifier ──────────────────────────────────────────────────────────

_QUESTION_PREFIX = re.compile(
    r'^\s*(what\s+(is|are|does|do)|explain|describe|define|tell\s+me\s+(about|what)'
    r'|how\s+does|why\s+(is|are|does)|meaning\s+of|what\s+does|who\s+(is|are)'
    r'|hello|hi\b|help\s+me|can\s+you)',
    re.IGNORECASE
)

_DATA_ACTION = re.compile(
    r'\b(show|find|get|list|count|how\s+many|search|query|select|fetch|display'
    r'|look\s*up|give\s+me|retrieve|average|mean|avg)\b',
    re.IGNORECASE
)

_DATA_OBJECT = re.compile(
    r'\b(variants?|genes?|chromosome|rsid|rs\d+|avsnp|database|rows?|records?|'
    r'missense|frameshift|stopgain|pathogenic|benign|vus|uncertain|'
    r'exonic|splicing|heterozygous|homozygous|clinvar|intervar|gnomad|'
    r'disease.caus|disease-caus|harmful|dangerous)\b',
    re.IGNORECASE
)

# Genomic coordinates / dbSNP IDs / specific gene names always mean data (non-question context)
_GENOMIC_SIGNAL = re.compile(
    r'\b(rs\d+|chromosome\s+\d+|chr\s*\d+|position\s+\d+|pos\s+\d+|start_pos|'
    r'BRCA[12]|TP53|CFTR|MLH1|MSH[26]|PMS2|MECP2|DDX11L2|HNF1A|GCK|RYR2|CSMD1|'
    r'gnomad|cadd\s*[><=]\s*\d|hgvs|dbsnp|zygosity|frameshift|stopgain|missense|'
    r'synonymous|splicing|in.?frame|heterozygous|homozygous|intervar|clinvar|'
    r'pathogenic|disease.caus|disease-caus)\b',
    re.IGNORECASE
)

# Strong coordinate signals — route to data even inside "what is...?" questions
_COORD_SIGNAL = re.compile(
    r'\b(rs\d+|chr(?:omosome)?\s*\d+|position\s+\d+|pos\s*[=:]\s*\d+)',
    re.IGNORECASE
)

# Gene-phenotype association queries: "which genes are linked to X?"
_GENE_PHENOTYPE = re.compile(
    r'\b(which|what)\s+(genes?|variants?|mutations?)\s+(are\s+)?(linked|associated|related|connected|cause[sd]?|responsible)\b|'
    r'\b(genes?|variants?)\s+(linked|associated|related|causing|responsible)\s+(?:to|for|with)\b',
    re.IGNORECASE
)

# Patient report / symptom queries — always data_query
# THE DATABASE IS THE PATIENT'S REPORT — any reference to "my report/results/data/file"
# must route to the database immediately.
_PATIENT_REPORT = re.compile(
    r'\b(my\s+(report|variants?|results?|genome|data|sample|file|wgs|sequencing)|'
    r'in\s+(my|the)\s+(report|results?|data|file)|'
    r'disease.{0,2}caus\w*|any\s+(pathogen|disease|harmful)|'
    r'what\s+gene\s+should\s+i\s+(look|check)|find\s+(me\s+)?the\s+gene|'
    r'my\s+symptoms?\s+(are|is|include|like)|behind\s+my\s+(condition|disease)|'
    r'gene\s+(behind|for|responsible|causing)\s+my|'
    r'are\s+there\s+any\s+(disease|pathogen|harmful|dangerous|bad)|'
    r'do\s+i\s+have\s+(any\s+)?(variant|gene|mutation|pathogenic|risk|carrier)|'
    r'am\s+i\s+(a\s+)?carrier|'
    r'check\s+my\s+(report|results?|data|file)|'
    r'is\s+(it|this|that|any)\s+in\s+my\s+(report|results?|data)|'
    r'show\s+me\s+my|list\s+my\s+(variants?|genes?|results?)|'
    r'find\s+in\s+my\s+(report|data)|'
    r'is\s+any\s+(of\s+(those|them|these)|gene|variant)\s+in\s+my)\b',
    re.IGNORECASE
)

# Pronoun references to genes mentioned in previous turns
_PRONOUN_CONTEXT = re.compile(
    r'\b(those\s+genes?|any\s+of\s+(those|them|these|the\s+above)|'
    r'the\s+genes?\s+(you|i)\s+mentioned|the\s+(above|listed)\s+genes?|'
    r'are\s+(any|all)\s+of\s+(those|them)|'
    r'is\s+any\s+of\s+(those|them|it)\s+in|'
    r'do\s+i\s+have\s+(those|them|any\s+of\s+those))\b',
    re.IGNORECASE
)

_DEFINE_PATTERN = re.compile(
    r'^\s*(what\s+(is|are|does)\s+\w[\w\s]*\bmean\b|what\s+does\s+\w+\s+stand\s+for)',
    re.IGNORECASE,
)


# ── Layer 0 — Safety refuse ────────────────────────────────────────────────────

_SAFETY_PATTERNS = [
    (re.compile(
        r'\b(do\s+i\s+have|have\s+i\s+got|am\s+i\s+(sick|ill|dying|infected|positive))\b',
        re.IGNORECASE), "diagnosis"),
    (re.compile(
        r'\b(will\s+i\s+(die|survive|live|make\s+it)|how\s+long\s+(do\s+i\s+have|will\s+i\s+live)'
        r'|is\s+it\s+(fatal|terminal|curable|treatable))\b',
        re.IGNORECASE), "prognosis"),
    (re.compile(
        r'\b(should\s+i\s+(take|stop|start|avoid|use|get)\s+(?!a\s+test|tested)'
        r'|what\s+(medication|drug|treatment|medicine|pill|dose|therapy)\s+should'
        r'|prescribe|my\s+treatment)\b',
        re.IGNORECASE), "treatment"),
    (re.compile(
        r'\b(can\s+i\s+(have|get)\s+(children|pregnant|kids|babies)'
        r'|should\s+i\s+(have|get)\s+(children|pregnant|kids)'
        r'|reproductive\s+(decision|choice|option))\b',
        re.IGNORECASE), "reproductive"),
    (re.compile(
        r'\b(kill\s+myself|end\s+my\s+life|suicide|self.harm|want\s+to\s+die'
        r'|hurt\s+myself|take\s+my\s+own\s+life)\b',
        re.IGNORECASE), "self_harm"),
]

_SAFETY_RESPONSES = {
    "diagnosis": (
        "I'm not able to make a diagnosis. The variant data in this system is for "
        "educational and research use only.\n\n"
        "Please speak with a **certified genetic counselor or physician** who can "
        "interpret your complete clinical picture alongside these genomic findings.\n\n"
        "⚠️ This system provides educational genomic information only — not medical advice."
    ),
    "prognosis": (
        "I can't provide prognosis information. Genomic variant data alone cannot "
        "predict disease outcomes — that requires clinical evaluation by a specialist.\n\n"
        "Please consult a **physician or genetic counselor** for guidance.\n\n"
        "⚠️ This system provides educational genomic information only — not medical advice."
    ),
    "treatment": (
        "I'm not able to give treatment or medication advice. Treatment decisions "
        "must be made by a qualified physician with full knowledge of your medical history.\n\n"
        "Please discuss these findings with your **doctor or genetic counselor**.\n\n"
        "⚠️ This system provides educational genomic information only — not medical advice."
    ),
    "reproductive": (
        "Reproductive decisions based on genetic findings require careful specialist counseling. "
        "I'm not able to advise on this.\n\n"
        "Please consult a **genetic counselor** who specialises in reproductive genetics.\n\n"
        "⚠️ This system provides educational genomic information only — not medical advice."
    ),
    "self_harm": (
        "I'm concerned about what you've shared. If you're in distress, please reach out:\n\n"
        "**Crisis lines:** 988 (US Suicide & Crisis Lifeline) · 116 123 (UK Samaritans) "
        "· 13 11 14 (Australia Lifeline)\n\n"
        "A genetic counselor or psychologist can also help you process difficult "
        "genetic findings in a supportive setting."
    ),
}


def _safety_refuse(message: str) -> Optional[str]:
    """Layer 0: return a fixed response if message hits a safety pattern, else None.

    "diagnosis" checks for "do I have" are suppressed when the message clearly
    asks about variant/gene data — e.g. "What variants do I have in SERPINA1?"
    is a data query, not a diagnosis question.
    """
    for pattern, category in _SAFETY_PATTERNS:
        if pattern.search(message):
            # Skip "do I have" refusals when the question is clearly about variants/genes
            if category == "diagnosis" and _DATA_OBJECT.search(message):
                continue
            return _SAFETY_RESPONSES[category]
    return None


# ── Layer 1 addition — HPO symptom intent ──────────────────────────────────────

_SYMPTOM_QUERY_RE = re.compile(
    r'\b(i\s+(have|feel|get|experience|suffer|had|am\s+having|am\s+experiencing)|'
    r'my\s+symptoms?\s+(include|are|is|were)|'
    r'suffering\s+from|experiencing\s+(symptoms?|problems?)|'
    r'i\'?ve\s+(been\s+(having|experiencing|feeling)|had)|'
    r'dealing\s+with|living\s+with|diagnosed\s+with)\b',
    re.IGNORECASE,
)

# Words that disqualify symptom detection — unambiguous variant-lookup terms
_SYMPTOM_DISQUALIFIER_RE = re.compile(
    r'\b(variant|variants|mutation|gene|genes|allele|rs\d{3,}|chr\d|exon|intron'
    r'|c\.\w|p\.\w|acmg|pvs1|cadd|clinvar|intervar|pathogenic|benign'
    r'|frameshift|stopgain|missense|synonymous|splicing)\b',
    re.IGNORECASE,
)

_SYMPTOM_EXTRACT_RE = re.compile(
    r'(?:i\s+(?:have|feel|get|experience|suffer\s+from|am\s+having|am\s+experiencing|'
    r'\'?ve\s+(?:been\s+)?(?:having|experiencing|feeling))|'
    r'my\s+symptoms?\s+(?:include|are|is|were)|'
    r'suffering\s+from|dealing\s+with|living\s+with|diagnosed\s+with)\s+'
    r'([a-z][a-z\s,\-]{2,80}?)(?:\.|,\s*and\s+(?:i\s+)?(?:also\s+)?|$|\?|!)',
    re.IGNORECASE,
)


def _extract_symptom_phrases(message: str) -> List[str]:
    """Pull symptom phrases from a patient message."""
    raw_phrases: List[str] = []
    for m in _SYMPTOM_EXTRACT_RE.finditer(message):
        chunk = m.group(1).strip().rstrip(".,!?;:")
        if chunk:
            raw_phrases.append(chunk)

    # Fall back: use the whole message tail after trigger words
    if not raw_phrases and _SYMPTOM_QUERY_RE.search(message):
        tail = _SYMPTOM_QUERY_RE.sub("", message).strip().rstrip(".,!?;:")
        if len(tail) > 3:
            raw_phrases = [tail]

    # Split compound phrases on commas and "and"
    result: List[str] = []
    for phrase in raw_phrases:
        for part in re.split(r',\s*(?:and\s+)?|(?<!\w)\band\b(?!\w)', phrase):
            part = part.strip().rstrip(".,!?;:")
            if len(part) >= 3:
                result.append(part)
    return result


# ── Layer 2 — HPO processing ───────────────────────────────────────────────────

def _already_asked_clarification(history: List[ChatMessage]) -> bool:
    """True if the last assistant turn already asked a clarification question."""
    for msg in reversed(history):
        if msg.role == "assistant":
            content = (msg.content or "").lower()
            return "could you clarify" in content or "could you describe" in content
    return False


def _process_hpo(message: str, history: List[ChatMessage]) -> dict:
    """Layer 2: resolve HPO symptoms → ranked gene list (capped at 300).

    Returns dict with keys:
      resolved    : list of HPOTerm dicts (successfully resolved)
      unresolved  : list of str (phrases that didn't map to HPO)
      genes       : list of str (up to 300 genes, specificity-ranked)
      hpo_summary : human-readable string for LLM context
      needs_clarification : bool
      clarification_question : str | None
    """
    from app.hpo.resolver import resolve_many, rank_and_cap_genes

    phrases = _extract_symptom_phrases(message)
    if not phrases:
        return {
            "resolved": [], "unresolved": [], "genes": [],
            "hpo_summary": "", "needs_clarification": False,
            "clarification_question": None,
        }

    terms = resolve_many(phrases)
    resolved   = [t for t in terms if t.resolved()]
    unresolved = [phrases[i] for i, t in enumerate(terms) if not t.resolved()]

    # If nothing resolved and we haven't asked yet → ask one clarification
    if not resolved and unresolved and not _already_asked_clarification(history):
        first = unresolved[0]
        question = (
            f"I couldn't map **\"{first}\"** to a recognised clinical symptom. "
            "Could you describe it using more specific medical terms? "
            "For example: *muscle weakness*, *seizures*, *hearing loss*, "
            "*fatigue*, *joint pain*, *developmental delay*."
        )
        return {
            "resolved": [], "unresolved": unresolved, "genes": [],
            "hpo_summary": "", "needs_clarification": True,
            "clarification_question": question,
        }

    genes = rank_and_cap_genes(resolved, cap=300)

    summary_parts = []
    for t in resolved:
        summary_parts.append(
            f"  • {t.name} ({t.hpo_id}) — {len(t.genes):,} associated genes "
            f"[matched via {t.matched_via}, confidence {t.confidence:.0%}]"
        )
    if unresolved:
        summary_parts.append(
            f"  • Unresolved (skipped): {', '.join(unresolved)}"
        )

    hpo_summary = (
        f"Symptom → HPO resolution:\n" + "\n".join(summary_parts) +
        f"\nCandidate gene pool: {len(genes)} genes (specificity-ranked, capped at 300)"
    )

    return {
        "resolved": [t.as_dict() for t in resolved],
        "unresolved": unresolved,
        "genes": genes,
        "hpo_summary": hpo_summary,
        "needs_clarification": False,
        "clarification_question": None,
    }


# ── Layer 3 — Deterministic SQL validation ─────────────────────────────────────

_QUOTED_DOT_COLS = re.compile(r'(?<!")\b\w+\.\w+\b(?!")')


def _deterministic_validate(sql: str) -> tuple:
    """Python-only checks before executing any SQL. Returns (ok, error_message)."""
    if not re.match(r'^\s*SELECT\b', sql, re.IGNORECASE):
        return False, "Query must start with SELECT"
    if re.search(r'\b(DROP|DELETE|UPDATE|INSERT|ALTER|TRUNCATE|EXEC|GRANT|REVOKE)\b',
                 sql, re.IGNORECASE):
        return False, "Query contains unsafe operations"
    if "LIMIT" not in sql.upper():
        return False, "Query is missing a LIMIT clause"
    if not re.search(r'\bFROM\s+variants\b', sql, re.IGNORECASE):
        return False, "Query must reference the 'variants' table"
    unquoted = _QUOTED_DOT_COLS.findall(sql)
    if unquoted:
        return False, f"Unquoted dotted column names (must be double-quoted): {unquoted}"
    return True, ""


# ── Layer 4 — Top-10 cap ───────────────────────────────────────────────────────

def _apply_top10_cap(rows: List[Dict], total_count: int) -> tuple:
    """Always cap patient-facing results at 10. Returns (capped_rows, note_str)."""
    capped = rows[:10]
    note = ""
    if total_count > 10:
        note = (
            f"*Showing top 10 of {total_count:,} matches ordered by damage score (CADD). "
            "Narrow your search by adding a gene name, variant type, or classification "
            "filter to see more specific results.*"
        )
    return capped, note


# ── Layer 6 — Disclaimer middleware ───────────────────────────────────────────

_DISCLAIMER_TRIGGERS_RE = re.compile(
    r'\b(pathogenic|likely\s+pathogenic|disease.caus|acmg|pvs1|ps\d|pm\d|pp\d|ba1|bs\d|bp\d'
    r'|heterozygous|homozygous|hemizygous|\bhet\b|\bhom\b|\bhemi\b'
    r'|inheritance|dominant|recessive|de\s+novo'
    r'|clinvar|intervar'
    r'|variant.{0,15}classif|classif.{0,15}variant)\b',
    re.IGNORECASE,
)

_DISCLAIMER_TEXT = (
    "\n\n⚠️ **Educational only** — This information does not constitute medical advice. "
    "Please discuss these findings with a certified genetic counselor or physician "
    "before making any clinical decision."
)

_DISCLAIMER_ALREADY_RE = re.compile(
    r'(educational only|genetic counselor|not medical advice|not constitute medical)',
    re.IGNORECASE,
)


def _apply_disclaimer(response: str, intent: str) -> str:
    """Layer 6: append disclaimer when clinically relevant. Never duplicate."""
    if _DISCLAIMER_ALREADY_RE.search(response):
        return response
    # Always add for HPO and data queries with clinical content
    if intent in ("hpo_query", "hpo_clarification"):
        return response + _DISCLAIMER_TEXT
    if _DISCLAIMER_TRIGGERS_RE.search(response):
        return response + _DISCLAIMER_TEXT
    return response


def _extract_genes_from_history(history: List[ChatMessage]) -> List[str]:
    """Extract known gene names mentioned in recent assistant turns."""
    from app.ai.text_to_sql import _KNOWN_GENES
    recent = [m.content for m in history[-8:] if m.role == "assistant"]
    text = " ".join(recent)
    found: List[str] = []
    for m in _KNOWN_GENES.finditer(text):
        g = m.group(1)
        if g not in found:
            found.append(g)
    return found[:12]


def _maybe_expand_with_history(message: str, history: List[ChatMessage]) -> str:
    """If message uses pronouns for prior genes, expand to name them explicitly."""
    if not _PRONOUN_CONTEXT.search(message) or not history:
        return message
    genes = _extract_genes_from_history(history)
    if not genes:
        return message
    gene_list = ", ".join(genes)
    return f"Show variants for these specific genes: {gene_list} — original question: {message}"


def _classify_intent(message: str, history: List[ChatMessage] = None) -> str:
    """Return intent: 'hpo_query' | 'anaphora_query' | 'data_query' | 'general'."""
    # Anaphora — pronoun references to genes from prior turns (check before everything else)
    if _PRONOUN_CONTEXT.search(message) and history:
        genes = _extract_genes_from_history(history)
        if genes:
            return "anaphora_query"

    # HPO symptom query — "I have X", "I feel X", "my symptoms include X"
    # Only when no variant-lookup vocabulary is present
    if _SYMPTOM_QUERY_RE.search(message) and not _SYMPTOM_DISQUALIFIER_RE.search(message):
        return "hpo_query"

    # "What does X mean?" / "What does X stand for?" → always a definition question
    if _DEFINE_PATTERN.match(message):
        return "general"
    # Patient report / "my data" queries → always data
    if _PATIENT_REPORT.search(message):
        return "data_query"
    # "Which genes/variants are linked to X?" / "What genes cause Y?" → DB lookup
    if _GENE_PHENOTYPE.search(message):
        return "data_query"
    # Question/explanation phrases
    if _QUESTION_PREFIX.match(message):
        if _DATA_ACTION.search(message) and _DATA_OBJECT.search(message):
            return "data_query"
        if _DATA_OBJECT.search(message) and _GENOMIC_SIGNAL.search(message):
            return "data_query"
        if _COORD_SIGNAL.search(message):
            return "data_query"
        return "general"
    # Non-question genomic signals → data
    if _GENOMIC_SIGNAL.search(message):
        return "data_query"
    has_action = bool(_DATA_ACTION.search(message))
    has_object = bool(_DATA_OBJECT.search(message))
    if has_action and has_object:
        return "data_query"
    if has_action:
        return "data_query"
    return "general"


# ── General knowledge answering ─────────────────────────────────────────────────

_GENOMICS_CONTEXT = """
You are a genomics expert assistant for the Genomic Q&A Engine.
You have access to a database of 4,863,751 genomic variants annotated using InterVar (ACMG/AMP 2015).

Key facts about the system:
- Database: SQLite with 4.8M variants from InterVar
- Classification tiers: Pathogenic, Likely Pathogenic, Uncertain Significance (VUS), Likely Benign, Benign
- ACMG/AMP 2015 criteria: 28 evidence codes (PVS1, PS1-4, PM1-6, PP1-5, BA1, BS1-4, BP1-7)
- Genes covered: 24,383 unique genes
- Scores: CADD, SIFT, MetaSVM, GERP, PhyloP, gnomAD frequencies

Answer concisely and accurately. If the user asks for data from the database, tell them to use /api/ai/query.
"""

def _answer_general(message: str, history: List[ChatMessage], llm: Any) -> str:
    """Answer a general genomics question — Qwen3 first, static KB as fallback."""
    if llm is not None and hasattr(llm, "answer_general"):
        try:
            hist_dicts = [{"role": m.role, "content": m.content} for m in history]
            return llm.answer_general(message, history=hist_dicts)
        except Exception as e:
            logger.warning(f"LLM general answer failed: {e}")
    return _static_answer(message)


def _static_answer(message: str) -> str:
    """Comprehensive static knowledge base for genomics domain questions."""
    msg = message.lower()

    # ── Greetings ──────────────────────────────────────────────────────────────
    if re.search(r'\b(hello|hi|hey|greetings|good\s+(morning|evening|afternoon))\b', msg):
        return (
            "Hello! I'm the Genomic Q&A assistant.\n\n"
            "I can help you:\n"
            "• **Query the database** — variants in BRCA1, TP53, CFTR, rsID lookups\n"
            "• **Explain ACMG criteria** — PVS1, PS1-4, PM1-6, PP1-5, BA1, BS1-4, BP1-7\n"
            "• **Interpret scores** — CADD, SIFT, gnomAD, MetaSVM, PolyPhen\n"
            "• **Classification tiers** — Pathogenic, Likely Pathogenic, VUS, Likely Benign, Benign\n\n"
            "Try: 'Show pathogenic variants in BRCA1' or 'What is PVS1?'"
        )

    # ── Specific ACMG criteria ─────────────────────────────────────────────────
    if "pvs1" in msg:
        return (
            "**PVS1 — Pathogenic Very Strong 1**\n\n"
            "Triggered for **null variants** (loss-of-function) in genes where LOF is a known disease mechanism:\n"
            "• Stopgain (nonsense) mutations\n"
            "• Frameshift deletions/insertions\n"
            "• Canonical splice site disruptions (±1,2 bp)\n\n"
            "A single PVS1 + one PS criterion = **Pathogenic**. PVS1 alone = **Likely Pathogenic**."
        )
    if "ba1" in msg:
        return (
            "**BA1 — Benign Stand-Alone 1**\n\n"
            "Triggered when **gnomAD allele frequency > 5%** in any population.\n"
            "A single BA1 overrides all pathogenic evidence and classifies the variant as **Benign**.\n\n"
            "This reflects that variants common in the general population are unlikely to cause rare disease."
        )
    if re.search(r'\bps1\b', msg):
        return "**PS1** — Same amino acid change as a previously established pathogenic variant (different nucleotide). Pathogenic Strong evidence."
    if re.search(r'\bps2\b', msg):
        return "**PS2** — De novo variant (confirmed paternity/maternity) in a patient with the disease and no family history. Pathogenic Strong evidence."
    if re.search(r'\bps3\b', msg):
        return "**PS3** — Well-established functional studies show damaging effect on gene/protein function. Pathogenic Strong evidence."
    if re.search(r'\bps4\b', msg):
        return "**PS4** — Variant prevalence significantly increased in affected individuals vs controls. Pathogenic Strong evidence."
    if re.search(r'\bpm1\b', msg):
        return "**PM1** — Located in a mutational hotspot or critical functional domain without benign variation. Pathogenic Moderate."
    if re.search(r'\bpm2\b', msg):
        return "**PM2** — Absent or extremely low frequency in gnomAD (for recessive: <0.001, dominant: absent). Pathogenic Moderate."
    if re.search(r'\bpm4\b', msg):
        return "**PM4** — Protein length change due to in-frame deletions/insertions or stop-loss. Pathogenic Moderate."
    if re.search(r'\bpm5\b', msg):
        return "**PM5** — Novel missense at same position as known pathogenic missense (different AA change). Pathogenic Moderate."
    if re.search(r'\bpp2\b', msg):
        return "**PP2** — Missense variant in gene with low rate of benign missense variation where missense is common mechanism. Pathogenic Supporting."
    if re.search(r'\bpp3\b', msg):
        return "**PP3** — Multiple computational tools predict damaging effect (SIFT, PolyPhen, CADD, etc.). Pathogenic Supporting."
    if re.search(r'\bbs1\b', msg):
        return "**BS1** — Allele frequency greater than expected for the disorder in gnomAD. Benign Strong."
    if re.search(r'\bbs2\b', msg):
        return "**BS2** — Observed in healthy adult with full penetrance expected. Benign Strong."
    if re.search(r'\bbp1\b', msg):
        return "**BP1** — Missense variant in a gene where only truncating variants cause disease. Benign Supporting."
    if re.search(r'\bbp4\b', msg):
        return "**BP4** — Multiple computational tools predict benign effect. Benign Supporting."
    if re.search(r'\bbp7\b', msg):
        return "**BP7** — Synonymous variant with no predicted splice effect and not conserved. Benign Supporting."
    if re.search(r'\b(ps[1-4]|pm[1-6]|pp[1-5]|bs[1-4]|bp[1-7])\b', msg):
        return (
            "**ACMG/AMP 2015 Evidence Codes:**\n\n"
            "**Pathogenic evidence:**\n"
            "• PVS1 — Very Strong (null variant in LOF gene)\n"
            "• PS1-4 — Strong (same AA change, de novo, functional, prevalence)\n"
            "• PM1-6 — Moderate (hotspot, absent in gnomAD, protein length change, etc.)\n"
            "• PP1-5 — Supporting (cosegregation, missense gene, computational, etc.)\n\n"
            "**Benign evidence:**\n"
            "• BA1 — Stand-Alone (gnomAD AF > 5%)\n"
            "• BS1-4 — Strong (high frequency, healthy adult, functional, family)\n"
            "• BP1-7 — Supporting (missense in truncating gene, computational, synonymous, etc.)"
        )

    # ── Classification tiers ───────────────────────────────────────────────────
    if re.search(r'\blikely\s+pathogenic\b', msg):
        return (
            "**Likely Pathogenic** — >90% probability of being disease-causing.\n\n"
            "**Combining rules that give Likely Pathogenic:**\n"
            "• 1× PVS1 alone\n"
            "• 1× PS + 1-2× PM\n"
            "• 1× PS + 2× PP\n"
            "• 3× PM\n"
            "• 2× PM + 2× PP\n"
            "• 1× PM + ≥4× PP\n\n"
            "Our database contains a small number of Likely Pathogenic variants (~4 confirmed in this dataset)."
        )
    if re.search(r'\blikely\s+benign\b', msg):
        return (
            "**Likely Benign** — >90% probability of NOT being disease-causing.\n\n"
            "**Combining rules that give Likely Benign:**\n"
            "• 1× BS + 1× BP\n"
            "• ≥2× BP\n\n"
            "Variants in this tier are generally considered clinically benign but not proven with the "
            "same certainty as the Benign tier."
        )
    if re.search(r'\b(pathogenic|what.*pathogenic|pathogenic.*mean)\b', msg) and "likely" not in msg:
        return (
            "**Pathogenic** — Strong evidence of disease causation.\n\n"
            "**Combining rules that give Pathogenic:**\n"
            "• 1× PVS1 + ≥1× PS\n"
            "• 1× PVS1 + ≥2× PM\n"
            "• 1× PVS1 + 1× PM + 1× PP\n"
            "• 1× PVS1 + ≥2× PP\n"
            "• ≥2× PS\n"
            "• 1× PS + ≥3× PM\n"
            "• 1× PS + 2× PM + ≥2× PP\n"
            "• 1× PS + ≥4× PP\n\n"
            "This dataset is a common-variant reference set — most Pathogenic variants in this DB are rare edge cases."
        )
    if re.search(r'\bbenign\b', msg) and "likely" not in msg:
        return (
            "**Benign** — Not disease-causing.\n\n"
            "**Combining rules that give Benign:**\n"
            "• 1× BA1 (stand-alone — gnomAD AF > 5%)\n"
            "• ≥2× BS\n\n"
            "Common variants in the general population are unlikely to cause rare Mendelian disease."
        )
    if re.search(r'\b(vus|uncertain significance|uncertain)\b', msg):
        return (
            "**VUS — Variant of Uncertain Significance**\n\n"
            "Insufficient evidence to classify as pathogenic or benign. VUS is assigned when:\n"
            "• Conflicting evidence exists (both pathogenic and benign)\n"
            "• Evidence is limited or ambiguous\n"
            "• The variant is novel with no functional data\n\n"
            "Our database has ~661K VUS variants (14% of total 4.8M). "
            "These are the most clinically challenging variants.\n\n"
            "VUS variants require additional evidence (functional studies, family studies, "
            "population data) to be reclassified."
        )
    if re.search(r'\b(acmg|amp 2015|classification|5.tier|five tier|classify|variant classification)\b', msg):
        return (
            "**ACMG/AMP 2015 Variant Classification Guidelines**\n\n"
            "Variants are classified into 5 tiers:\n"
            "• **Pathogenic** — strong evidence of disease causation (~120K in our DB)\n"
            "• **Likely Pathogenic** — >90% probability of disease causation (~500K)\n"
            "• **Uncertain Significance (VUS)** — insufficient evidence (~661K, 14%)\n"
            "• **Likely Benign** — probably not disease-causing\n"
            "• **Benign** — not disease-causing\n\n"
            "Classification uses 28 evidence codes (PVS1, PS1-4, PM1-6, PP1-5, BA1, BS1-4, BP1-7) "
            "which combine by specific rules to reach a final tier."
        )

    # ── Variant types ──────────────────────────────────────────────────────────
    if re.search(r'\b(missense|missense snv)\b', msg):
        return (
            "**Missense variant** — a single nucleotide change that results in a different amino acid.\n\n"
            "• Most common variant type in coding regions\n"
            "• Pathogenicity depends on: location (hotspot?), conservation, functional impact\n"
            "• Key scores: SIFT, PolyPhen, CADD, MetaSVM\n"
            "• ACMG: PP3 (computational tools predict damaging), PM1 (hotspot), PS3 (functional)"
        )
    if re.search(r'\b(frameshift|frameshift deletion|frameshift insertion)\b', msg):
        return (
            "**Frameshift variant** — insertion/deletion that shifts the reading frame.\n\n"
            "• Creates a premature stop codon downstream\n"
            "• Usually triggers **PVS1** (very strong pathogenic) in LOF genes\n"
            "• Results in truncated or degraded protein via nonsense-mediated decay (NMD)\n"
            "• Stored as 'frameshift deletion' or 'frameshift insertion' in exonic_func column"
        )
    if re.search(r'\b(stopgain|nonsense|stop.gain|premature stop)\b', msg):
        return (
            "**Stopgain (nonsense) variant** — single nucleotide change creating a premature stop codon.\n\n"
            "• Triggers **PVS1** in loss-of-function genes\n"
            "• Leads to truncated protein or NMD (nonsense-mediated decay)\n"
            "• Very high CADD scores typically (>30)\n"
            "• Stored as 'stopgain' in the exonic_func column"
        )
    if re.search(r'\b(synonymous|silent|synonymous snv)\b', msg):
        return (
            "**Synonymous (silent) variant** — nucleotide change that does NOT alter the amino acid.\n\n"
            "• Generally considered benign (BP7 if no predicted splice effect)\n"
            "• Can still affect splicing if near exon-intron boundary\n"
            "• Lowest CADD scores among coding variants on average\n"
            "• Stored as 'synonymous SNV' in the exonic_func column"
        )
    if re.search(r'\b(splice|splicing|splice site)\b', msg):
        return (
            "**Splicing variant** — affects pre-mRNA splicing at exon-intron boundaries.\n\n"
            "• Canonical splice sites (GT/AG at ±1,2 bp) trigger **PVS1**\n"
            "• Deep intronic variants may create cryptic splice sites\n"
            "• Tools: SpliceSiteFinder, MaxEntScan, NNSPLICE used for prediction\n"
            "• Stored as 'splicing' in exonic_func or func_region columns"
        )
    if re.search(r'\b(in.?frame|nonframeshift)\b', msg):
        return (
            "**In-frame variant** — insertion/deletion that does NOT shift the reading frame.\n\n"
            "• Inserts/deletes amino acids without disrupting downstream sequence\n"
            "• Less likely to trigger PVS1 vs frameshift\n"
            "• ACMG: PM4 (protein length change), pathogenicity depends on functional domain\n"
            "• Stored as 'nonframeshift deletion' or 'nonframeshift insertion' in exonic_func"
        )

    # ── Scores ─────────────────────────────────────────────────────────────────
    if "cadd" in msg:
        return (
            "**CADD — Combined Annotation-Dependent Depletion**\n\n"
            "Scores variant deleteriousness on a Phred-scaled score:\n"
            "• CADD ≥ 10 → top 10% most deleterious variants\n"
            "• CADD ≥ 20 → top 1% (likely damaging)\n"
            "• CADD ≥ 30 → top 0.1% (highly damaging)\n"
            "• CADD ≥ 40 → extremely deleterious\n\n"
            "CADD integrates 60+ genomic annotations. Used for ACMG PP3 (supporting pathogenic) "
            "when CADD > 25, and BP4 (supporting benign) when CADD < 10."
        )
    if "sift" in msg:
        return (
            "**SIFT — Sorting Intolerant From Tolerant**\n\n"
            "Predicts amino acid substitution effect based on sequence conservation:\n"
            "• SIFT < 0.05 → **Damaging** (supports PP3)\n"
            "• SIFT ≥ 0.05 → **Tolerated** (supports BP4)\n\n"
            "SIFT scores range from 0 (most damaging) to 1 (most tolerated). "
            "Works only for missense variants."
        )
    if re.search(r'\b(polyphen|polyphen2|polyphen-2)\b', msg):
        return (
            "**PolyPhen-2** — Polymorphism Phenotyping v2\n\n"
            "Predicts functional effect of missense variants:\n"
            "• 0.909-1.0 → **Probably Damaging**\n"
            "• 0.447-0.909 → **Possibly Damaging**\n"
            "• 0.0-0.447 → **Benign**\n\n"
            "Uses structural and evolutionary information. Combined with SIFT and CADD for PP3/BP4."
        )
    if re.search(r'\b(metasvm|meta.svm)\b', msg):
        return (
            "**MetaSVM** — Meta-predictor for missense variant pathogenicity.\n\n"
            "Combines 10 deleteriousness scores (SIFT, PolyPhen, CADD, MutationAssessor, etc.):\n"
            "• Score > 0 → **Damaging**\n"
            "• Score ≤ 0 → **Tolerated**\n\n"
            "One of the most reliable single predictors for missense variant classification."
        )
    if re.search(r'\b(gnomad|gnomad af|population frequency)\b', msg):
        return (
            "**gnomAD — Genome Aggregation Database**\n\n"
            "Aggregates genetic variants from 125,748 exomes and 15,708 genomes:\n"
            "• Freq_gnomAD_genome_ALL — overall allele frequency\n"
            "• Population-specific: AFR, AMR, EAS, NFE, FIN, ASJ (Freq_gnomAD_genome_POPs)\n\n"
            "**ACMG thresholds:**\n"
            "• AF > 5% → BA1 (stand-alone benign)\n"
            "• AF > 1% → BS1 (strong benign)\n"
            "• AF absent or near 0 → PM2 (moderate pathogenic)\n\n"
            "NULL in gnomAD = not observed in the population (supports pathogenicity)."
        )
    if re.search(r'\b(gerp|gerp\+\+|conservation)\b', msg):
        return (
            "**GERP++ — Genomic Evolutionary Rate Profiling**\n\n"
            "Measures evolutionary conservation at each genomic position:\n"
            "• GERP > 2 → conserved position (supports pathogenicity)\n"
            "• GERP > 4 → highly conserved\n"
            "• GERP ≤ 0 → not conserved (supports benign)\n\n"
            "Higher conservation means the position is under stronger purifying selection."
        )
    if re.search(r'\b(phylop|phylop46|conservation score)\b', msg):
        return (
            "**PhyloP — Phylogenetic P-value**\n\n"
            "Measures evolutionary conservation across 46 vertebrate species:\n"
            "• PhyloP > 2 → conserved (under selection, supports pathogenicity)\n"
            "• PhyloP ≈ 0 → neutral evolution\n"
            "• PhyloP < -2 → accelerated evolution (less likely to be disease-causing)\n\n"
            "Stored as phylop46way_placental in the database. Used alongside GERP for PP3/BP4."
        )
    # A2 category: ClinVar vs InterVar conflict explanation
    if re.search(r'\b(clinvar|intervar).{0,40}(different|differ|conflict|disagree|mismatch|versus|vs|why|reliable)\b'
                 r'|\b(reliable|trust|accurate).{0,40}(clinvar|intervar)\b'
                 r'|\bclinvar.{0,20}(vus|uncertain).{0,20}intervar.{0,20}(pathogenic|likely)\b'
                 r'|\bintervar.{0,20}(pathogenic|likely).{0,20}clinvar.{0,20}(vus|uncertain)\b',
                 msg):
        return (
            "**Why ClinVar and InterVar can disagree**\n\n"
            "**ClinVar** (National Institutes of Health):\n"
            "• Collects submissions from clinical laboratories, hospitals, and research groups\n"
            "• Each submission reflects that lab's expert interpretation\n"
            "• May be outdated — a VUS from 5 years ago may be reclassified now\n"
            "• 'Conflicting interpretations' = multiple labs disagree\n"
            "• Strength depends on number of submissions and review status\n\n"
            "**InterVar** (algorithmic ACMG/AMP 2015 rules):\n"
            "• Applies the 28 ACMG evidence codes (PVS1, PS, PM, PP, BA1, BS, BP) automatically\n"
            "• Uses gnomAD frequency, CADD scores, splice predictions, OMIM data\n"
            "• Consistent and reproducible — same rules for every variant\n"
            "• May miss novel gene-disease associations not yet in OMIM\n\n"
            "**When they conflict:**\n"
            "• ClinVar VUS + InterVar Pathogenic → InterVar may have captured recent criteria "
            "(PM2, PP3, PVS1) that the ClinVar submission predates\n"
            "• ClinVar Pathogenic + InterVar VUS → ClinVar may reflect strong functional evidence "
            "not captured by automated rules\n\n"
            "**Which is more reliable?** Both — they are complementary. Highest confidence = "
            "agreement between both (ClinVar Pathogenic AND InterVar Pathogenic)."
        )
    if re.search(r'\b(clinvar|clinical significance)\b', msg):
        return (
            "**ClinVar — Clinical Significance Database**\n\n"
            "NCBI database of genetic variants and their clinical interpretations:\n"
            "• Submitted by clinical labs, research groups\n"
            "• Significance tiers: Pathogenic, Likely Pathogenic, VUS, Likely Benign, Benign, Conflicting\n"
            "• Available in our DB as the `\"clinvar: Clinvar\"` column\n"
            "• ClinVar Pathogenic + InterVar Pathogenic = high-confidence call\n\n"
            "ClinVar differs from InterVar: ClinVar collects lab submissions, "
            "InterVar applies ACMG/AMP 2015 rules algorithmically."
        )
    if re.search(r'\b(allele frequency|af|carrier|prevalence)\b', msg) and not re.search(r'\bgnomad\b', msg):
        return (
            "**Allele Frequency** — proportion of a specific allele in a population.\n\n"
            "• AF = (count of alternate alleles) / (total alleles in population)\n"
            "• AF close to 0 → rare variant → more likely pathogenic\n"
            "• AF > 0.01 (1%) → common variant → less likely to cause rare disease\n\n"
            "**ACMG thresholds:**\n"
            "• AF > 5% → BA1 (Benign Stand-Alone)\n"
            "• AF > 1% → BS1 (Benign Strong)\n"
            "• AF absent/very low → PM2 (Pathogenic Moderate)\n\n"
            "Our database stores gnomAD AF in Freq_gnomAD_genome_ALL (overall) and Freq_gnomAD_genome_POPs (per-population)."
        )

    if re.search(r'\b(de novo|denovo|new mutation|new variant)\b', msg):
        return (
            "**De novo variant** — new mutation present in the proband but NOT in either parent.\n\n"
            "• Confirmed de novo = **PS2** (Strong Pathogenic) in ACMG\n"
            "• Assumed de novo (parents not tested) = **PM6** (Moderate Pathogenic)\n"
            "• Common mechanism for dominant developmental disorders (e.g., ASD, intellectual disability)\n"
            "• Rate: ~1-2 de novo coding variants per person per generation"
        )
    # E1 / E2: Inheritance and passing to children
    if re.search(r'\b(will|can|might|could).{0,20}(children|kids|child|son|daughter|offspring|family).{0,30}(inherit|get|have|pass)\b'
                 r'|\b(pass|inherit).{0,20}(children|kids|child)\b'
                 r'|\b(children|kids|family).{0,20}(inherit|risk|tested|check)\b', msg):
        return (
            "**Will my children inherit this?**\n\n"
            "Whether a variant is passed to children depends on the inheritance pattern:\n\n"
            "• **Autosomal Dominant (AD)** — each child has a **50% chance** of inheriting it.\n"
            "  If the variant is pathogenic with AD inheritance, each child is at risk.\n\n"
            "• **Autosomal Recessive (AR)** — if you are a carrier (heterozygous), each child "
            "has a 25% chance of being affected (if your partner is also a carrier) or a 50% "
            "chance of being a carrier themselves.\n\n"
            "• **X-linked** — depends on sex of parent and child. Males with X-linked dominant "
            "variants pass to all daughters (not sons). Female carriers of X-linked recessive "
            "pass a 50% carrier risk to daughters and 50% affected risk to sons.\n\n"
            "⚠️ **This requires genetic counselling** — a genetic counsellor can calculate the "
            "exact risk for your family based on your specific variant and inheritance pattern.\n\n"
            "You can check the Orpha column in your report for the inheritance pattern of specific "
            "disease-gene associations."
        )
    if re.search(r'\b(inheritance|autosomal|dominant|recessive|x.linked)\b', msg):
        return (
            "**Inheritance Patterns in Mendelian Disease**\n\n"
            "• **Autosomal Dominant (AD)** — one pathogenic copy sufficient for disease. "
            "De novo variants common (new mutation, not inherited).\n"
            "• **Autosomal Recessive (AR)** — two pathogenic copies required. "
            "Carriers are unaffected. Consanguinity increases risk.\n"
            "• **X-linked Dominant** — one copy on X chromosome sufficient; both sexes affected.\n"
            "• **X-linked Recessive** — males (XY) affected with one copy; females (XX) carriers.\n"
            "• **De novo** — new variant not present in either parent.\n\n"
            "ACMG PS2 criterion = confirmed de novo in affected individual (Strong pathogenic)."
        )
    if re.search(r'\b(penetrance|expressivity|incomplete penetrance)\b', msg):
        return (
            "**Penetrance and Expressivity**\n\n"
            "• **Penetrance** — proportion of individuals with a genotype who show the phenotype.\n"
            "• **Complete penetrance** — 100% of carriers develop the disease (e.g., HD).\n"
            "• **Incomplete penetrance** — not all carriers develop disease (e.g., BRCA1 ~65-72%).\n"
            "• **Expressivity** — variable severity of disease among carriers.\n\n"
            "Reduced penetrance complicates ACMG classification — a variant may be truly "
            "pathogenic but appear with lower frequency in healthy controls."
        )
    if re.search(r'\b(loss of function|lof|haploinsufficiency|gain of function)\b', msg):
        return (
            "**Loss-of-Function (LOF) variants**\n\n"
            "• Eliminate or severely reduce protein function\n"
            "• Types: stopgain, frameshift, canonical splice site, large deletions\n"
            "• Trigger **PVS1** when the gene is LOF-intolerant (haploinsufficient)\n"
            "• pLI score (gnomAD) ≥ 0.9 → gene is haploinsufficient\n\n"
            "**Gain-of-Function (GOF)** — variant increases or alters protein activity. "
            "Common in dominant negative mechanisms. Does NOT trigger PVS1."
        )

    # F1: Carrier status for recessive conditions
    if re.search(r'\b(am i a carrier|carrier.{0,30}recessive|carry.{0,30}(disease|condition|recessive)|'
                 r'silent.{0,20}disease|carrier.{0,20}status|heterozygous.{0,30}recessive)\b', msg):
        return (
            "**Carrier Status for Autosomal Recessive Conditions**\n\n"
            "A **carrier** is a person with one pathogenic copy of a recessive gene — "
            "they are generally unaffected but can pass the condition to children.\n\n"
            "To check for carrier status in your report, look for:\n"
            "• Heterozygous variants (Otherinfo = 'het') in recessive disease genes\n"
            "• ClinVar or InterVar classification of Pathogenic or Likely Pathogenic\n\n"
            "**Common recessive disease genes to check:**\n"
            "• **CFTR** — Cystic Fibrosis (~1/25 carriers in Europeans)\n"
            "• **HBB** — Sickle Cell / Beta-Thalassemia\n"
            "• **HEXA** — Tay-Sachs (1/30 in Ashkenazi Jewish)\n"
            "• **BRCA2** — Hereditary Breast/Ovarian Cancer (also AD)\n"
            "• **SERPINA1** — Alpha-1 Antitrypsin Deficiency\n\n"
            "You can ask: *'Show heterozygous pathogenic variants'* to identify potential carriers."
        )

    # F2: Secondary / incidental findings (ACMG 59 gene list)
    if re.search(r'\b(secondary finding|incidental finding|acmg list|acmg 59|actionable.{0,20}gene|'
                 r'acmg secondary|acmg.{0,20}list|are there incidental|acmg.{0,20}finding)\b', msg):
        return (
            "**ACMG Secondary Findings (SF v3.2 — 81 genes)**\n\n"
            "The American College of Medical Genetics recommends reporting pathogenic variants "
            "in 81 specific 'actionable' genes — even when not the reason for testing.\n\n"
            "**Key gene categories on the ACMG list:**\n"
            "• **Hereditary Cancer:** BRCA1, BRCA2, MLH1, MSH2, MSH6, PMS2, TP53, STK11, PALB2\n"
            "• **Cardiac:** RYR2, KCNQ1, SCN5A, MYBPC3, MYH7, MYH11, SMAD3, TGFBR1, TGFBR2\n"
            "• **Aortic Disease:** FBN1 (Marfan), FBN2, ACTA2, SLC2A10\n"
            "• **Metabolic:** LDLR, APOB, PCSK9 (familial hypercholesterolaemia)\n"
            "• **Others:** MUTYH, APC, PTEN, RET, VHL, SDHB, SDHC, SDHD\n\n"
            "To check secondary findings in your report, ask:\n"
            "*'Show pathogenic variants in BRCA1'* or *'Do I have variants in ACMG genes?'*\n\n"
            "⚠️ Review of secondary findings should be done with a certified genetic counsellor."
        )

    # ── Genes ──────────────────────────────────────────────────────────────────
    if re.search(r'\b(brca1|brca2)\b', msg):
        return (
            "**BRCA1 / BRCA2 — Breast Cancer genes**\n\n"
            "• Tumor suppressor genes involved in DNA double-strand break repair\n"
            "• Pathogenic variants cause **Hereditary Breast and Ovarian Cancer (HBOC)**\n"
            "• Loss-of-function variants trigger **PVS1**\n"
            "• BRCA1 lifetime risk: ~65-72% breast cancer, ~44% ovarian cancer\n"
            "• BRCA2 lifetime risk: ~45-69% breast cancer, ~17% ovarian cancer\n"
            "• Both are on the ACMG secondary findings list for clinical reporting"
        )
    if re.search(r'\btp53\b', msg):
        return (
            "**TP53 — Tumor Protein p53**\n\n"
            "• The most commonly mutated gene in human cancers (~50% of all cancers)\n"
            "• 'Guardian of the genome' — regulates cell cycle, apoptosis, DNA repair\n"
            "• Germline pathogenic variants cause **Li-Fraumeni Syndrome**\n"
            "• Both missense and LOF variants cause disease\n"
            "• Hotspot codons: R175, G245, R248, R249, R273, R282"
        )
    if re.search(r'\bcftr\b', msg):
        return (
            "**CFTR — Cystic Fibrosis Transmembrane Conductance Regulator**\n\n"
            "• Encodes a chloride channel expressed in epithelial cells\n"
            "• Pathogenic variants cause **Cystic Fibrosis (CF)**\n"
            "• Most common: F508del (frameshift, ~70% of CF alleles)\n"
            "• Autosomal recessive — requires 2 pathogenic alleles for CF\n"
            "• Carrier frequency ~1/25 in European populations"
        )

    # ── Database / system info ─────────────────────────────────────────────────
    if re.search(r'\b(intervar|database|how many|total variant|dataset)\b', msg):
        return (
            "**InterVar Genomic Variant Database**\n\n"
            "• **4,863,751 total variants** annotated using InterVar (ACMG/AMP 2015)\n"
            "• **24,383 unique genes** covered\n"
            "• Classification breakdown:\n"
            "  - Benign: ~4.2M (87%)\n"
            "  - VUS (Uncertain significance): ~661K (14%)\n"
            "  - Likely Benign: ~1,850\n"
            "  - Likely Pathogenic: ~4\n"
            "  - Pathogenic: ~1\n\n"
            "This dataset is a common-variant reference set — most variants are benign population variants. "
            "Rare pathogenic variants are very few.\n\n"
            "**Available scores:** CADD, SIFT, MetaSVM, GERP, PhyloP, gnomAD AF\n"
            "**External data:** ClinVar significance, rsID, HGVS notation"
        )
    if re.search(r'\b(rsid|rs\d+|dbsnp|snp id)\b', msg):
        return (
            "**rsID — Reference SNP ID**\n\n"
            "Unique identifier from the dbSNP database for known genetic variants.\n"
            "Format: rs followed by a number (e.g., rs189107123)\n\n"
            "To look up a specific variant: ask 'Look up rs189107123'\n"
            "To find HGVS notation: ask 'Give me HGVS for rs189107123'"
        )
    if re.search(r'\b(hgvs|c\.\d|p\.\w+|coding change|protein change)\b', msg):
        return (
            "**HGVS Nomenclature** — Human Genome Variation Society standard notation:\n\n"
            "• **c.** (coding DNA): e.g., c.1521_1523delCTT (F508del)\n"
            "• **p.** (protein): e.g., p.Phe508del\n"
            "• **g.** (genomic): e.g., g.117548628T>C\n\n"
            "HGVS notation is stored in the \"AAChange.refGene\", \"AAChange.ensGene\", "
            "and \"AAChange.knownGene\" columns in the database."
        )

    # ── Fallback ───────────────────────────────────────────────────────────────
    return (
        "I'm the **Genomic Q&A Engine** — powered by InterVar (ACMG/AMP 2015).\n\n"
        "**I can answer questions about:**\n"
        "• ACMG classification criteria (PVS1, PS1-4, PM1-6, PP1-5, BA1, BS1-4, BP1-7)\n"
        "• Classification tiers (Pathogenic, Likely Pathogenic, VUS, Likely Benign, Benign)\n"
        "• Variant types (missense, frameshift, stopgain, splice, synonymous)\n"
        "• Scores (CADD, SIFT, gnomAD, MetaSVM, PolyPhen, GERP)\n"
        "• Genes (BRCA1, BRCA2, TP53, CFTR and 24K more)\n"
        "• Database queries (4.8M variants)\n\n"
        "**Try asking:**\n"
        "• 'What are the likely pathogenic combining rules?'\n"
        "• 'Explain PVS1'\n"
        "• 'What does CADD score mean?'\n"
        "• 'Show missense variants in TP53'\n"
        "• 'Look up rs189107123'"
    )


# ── LLM plain-English explanation ──────────────────────────────────────────────

def _explain_results(question: str, rows: list, row_count: int,
                     history: List[ChatMessage] = None,
                     hpo_context: dict = None) -> Optional[str]:
    """Pass SQL results to LLM for a plain-English explanation. Returns None if unavailable."""
    try:
        from app.ai.llm_config import get_llm
        llm = get_llm()
        if llm is None or not hasattr(llm, "explain"):
            return None
        hist_dicts = [{"role": m.role, "content": m.content} for m in (history or [])]
        return llm.explain(question, rows, row_count,
                           history=hist_dicts, hpo_context=hpo_context)
    except Exception as e:
        logger.warning(f"LLM explain failed: {e}")
        return None


# ── External API helpers ────────────────────────────────────────────────────────

_GENE_UPPER_RE = re.compile(r'\b([A-Z][A-Z0-9]{2,15})\b')

def _extract_gene_rsid(question: str):
    """Return (gene, rsid) extracted from the question text."""
    rs = re.search(r'\brs\d+\b', question, re.IGNORECASE)
    rsid = rs.group(0) if rs else None

    from app.ai.text_to_sql import _KNOWN_GENES
    gm = _KNOWN_GENES.search(question)
    gene = gm.group(1) if gm else None
    if not gene:
        gm2 = _GENE_UPPER_RE.search(question)
        if gm2 and gm2.group(1) not in {"VUS", "SQL", "DB", "DNA", "RNA", "PCR", "LOF",
                                          "AF", "PM", "PP", "PS", "BA", "BS", "BP", "PVS",
                                          "CADD", "SIFT", "SNV", "UTR", "LOH", "CDS"}:
            gene = gm2.group(1)
    return gene, rsid


def _run_external_sync(question: str) -> dict:
    """Fetch external evidence using synchronous httpx (safe in any thread context)."""
    gene, rsid = _extract_gene_rsid(question)
    if not gene and not rsid:
        return {}

    NCBI = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
    results: dict = {}

    try:
        import httpx
    except ImportError:
        return {}

    def _ncbi_get(url: str, params: dict) -> dict:
        try:
            r = httpx.get(url, params=params, timeout=10.0)
            r.raise_for_status()
            return r.json()
        except Exception:
            return {}

    # ── ClinVar ───────────────────────────────────────────────────────────────
    if rsid:
        try:
            search = _ncbi_get(f"{NCBI}/esearch.fcgi",
                               {"db": "clinvar", "term": f"{rsid}[rs]",
                                "retmax": 5, "retmode": "json"})
            ids = search.get("esearchresult", {}).get("idlist", [])
            if ids:
                summ = _ncbi_get(f"{NCBI}/esummary.fcgi",
                                 {"db": "clinvar", "id": ",".join(ids[:5]),
                                  "retmode": "json"})
                raw = summ.get("result", {})
                records = []
                for uid in ids:
                    rec = raw.get(uid, {})
                    if rec:
                        sig = rec.get("clinical_significance", {})
                        records.append({
                            "clinvar_id": uid,
                            "title": rec.get("title", ""),
                            "clinical_sig": sig.get("description", "") if isinstance(sig, dict) else str(sig),
                            "review_status": rec.get("review_status", ""),
                            "gene_sort": rec.get("gene_sort", ""),
                            "clinvar_url": f"https://www.ncbi.nlm.nih.gov/clinvar/variation/{uid}/",
                        })
                results["clinvar"] = {"rsid": rsid, "records": records}
            else:
                results["clinvar"] = {"rsid": rsid, "records": [], "message": "No ClinVar records found"}
        except Exception as e:
            results["clinvar"] = {"error": str(e)}

    # ── PubMed ────────────────────────────────────────────────────────────────
    try:
        term_parts = []
        if rsid:
            term_parts.append(f'"{rsid}"')
        if gene:
            term_parts.append(f'"{gene}"[Gene Name]')
        term = " AND ".join(term_parts) if term_parts else question[:100]
        search = _ncbi_get(f"{NCBI}/esearch.fcgi",
                           {"db": "pubmed", "term": term, "retmax": 5,
                            "retmode": "json", "sort": "relevance"})
        ids = search.get("esearchresult", {}).get("idlist", [])
        total = int(search.get("esearchresult", {}).get("count", 0))
        articles = []
        if ids:
            summ = _ncbi_get(f"{NCBI}/esummary.fcgi",
                             {"db": "pubmed", "id": ",".join(ids), "retmode": "json"})
            raw = summ.get("result", {})
            for pmid in ids:
                art = raw.get(pmid, {})
                if art:
                    articles.append({
                        "pmid": pmid,
                        "title": art.get("title", ""),
                        "journal": art.get("source", ""),
                        "year": art.get("pubdate", "")[:4],
                        "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
                    })
        results["pubmed"] = {"total": total, "articles": articles}
    except Exception as e:
        results["pubmed"] = {"error": str(e)}

    return results


def _format_external_for_llm(external: dict, question: str) -> Optional[str]:
    """Format external API results into a prompt for the LLM."""
    parts = []

    clinvar = external.get("clinvar", {})
    if clinvar.get("records"):
        lines = ["ClinVar records:"]
        for rec in clinvar["records"][:3]:
            sig = rec.get("clinical_sig", "")
            gene = rec.get("gene_sort", "")
            title = rec.get("title", "")[:120]
            review = rec.get("review_status", "")
            lines.append(f"  - {title} | Significance: {sig} | Review: {review} | Gene: {gene}")
        parts.append("\n".join(lines))

    clingen = external.get("clingen", {})
    if clingen.get("assertions"):
        lines = ["ClinGen expert panel assertions:"]
        for a in clingen["assertions"][:3]:
            lines.append(f"  - Panel: {a.get('panel','')} | Classification: {a.get('classification','')} "
                         f"| Condition: {a.get('condition','')} | Date: {a.get('date','')}")
        parts.append("\n".join(lines))

    pubmed = external.get("pubmed", {})
    if pubmed.get("articles"):
        lines = [f"PubMed literature ({pubmed.get('total',0)} total results, top {len(pubmed['articles'])}):"]
        for art in pubmed["articles"][:3]:
            lines.append(f"  - {art.get('title','')[:120]} ({art.get('year','')}) - {art.get('journal','')}")
        parts.append("\n".join(lines))

    if not parts:
        return None

    return (
        f"No matching variants found in the local genomic database for: \"{question}\"\n\n"
        f"External evidence retrieved:\n\n" + "\n\n".join(parts) + "\n\n"
        "Based on this external evidence, please provide a comprehensive answer."
    )


def _format_external_direct(external: dict, question: str) -> Optional[str]:
    """Return external evidence as a formatted plain-text answer (no LLM needed)."""
    gene, rsid = _extract_gene_rsid(question)
    lines = [f"No variants found in local database for: **{question}**\n"]
    lines.append("External evidence from public databases:\n")

    clinvar = external.get("clinvar", {})
    if clinvar.get("records"):
        lines.append("**ClinVar records:**")
        for rec in clinvar["records"][:5]:
            lines.append(f"• {rec.get('title','')}")
            lines.append(f"  Clinical significance: {rec.get('clinical_sig','N/A')}")
            lines.append(f"  Review status: {rec.get('review_status','N/A')}")
            lines.append(f"  URL: {rec.get('clinvar_url','')}")
        lines.append("")
    elif clinvar and not clinvar.get("error"):
        lines.append(f"**ClinVar:** No records found for {rsid or gene}")
        lines.append("")

    clingen = external.get("clingen", {})
    if clingen.get("assertions"):
        lines.append("**ClinGen Expert Panel assertions:**")
        for a in clingen["assertions"][:3]:
            lines.append(f"• Panel: {a.get('panel','')} | Classification: {a.get('classification','')}")
            lines.append(f"  Condition: {a.get('condition','')} | Date: {a.get('date','')}")
        lines.append("")

    pubmed = external.get("pubmed", {})
    if pubmed.get("articles"):
        lines.append(f"**PubMed literature** ({pubmed.get('total',0)} total results):")
        for art in pubmed["articles"][:5]:
            lines.append(f"• {art.get('title','')} ({art.get('year','')})")
            lines.append(f"  Journal: {art.get('journal','')} | URL: {art.get('url','')}")
        lines.append("")

    if len(lines) <= 3:
        return None
    return "\n".join(lines)


def _format_api_supplement(external: dict) -> Optional[str]:
    """Format external ClinVar/PubMed results as a brief supplement appended to DB results."""
    if not external:
        return None
    parts = []

    clinvar = external.get("clinvar", {})
    if clinvar.get("records"):
        lines = ["**External sources — ClinVar** *(National Center for Biotechnology Information)*"]
        for rec in clinvar["records"][:3]:
            sig = rec.get("clinical_sig", "N/A")
            title = rec.get("title", "")[:100]
            review = rec.get("review_status", "")
            url = rec.get("clinvar_url", "")
            lines.append(f"• {title}")
            lines.append(f"  Significance: **{sig}** | Review: {review}")
            if url:
                lines.append(f"  Source: [ClinVar — {rec.get('clinvar_id','')}]({url})")
        lines.append("  *ClinVar: https://www.ncbi.nlm.nih.gov/clinvar/*")
        parts.append("\n".join(lines))

    pubmed = external.get("pubmed", {})
    if pubmed.get("articles"):
        total = pubmed.get("total", 0)
        lines = [f"**External sources — PubMed** *(National Library of Medicine — {total} publication(s) found)*"]
        for art in pubmed["articles"][:3]:
            title = art.get("title", "")[:100]
            year = art.get("year", "")
            journal = art.get("journal", "")
            url = art.get("url", "")
            pmid = art.get("pmid", "")
            lines.append(f"• {title} ({year}) — {journal}")
            if url:
                lines.append(f"  Source: [PubMed PMID:{pmid}]({url})")
        lines.append("  *PubMed: https://pubmed.ncbi.nlm.nih.gov/*")
        parts.append("\n".join(lines))

    if not parts:
        return None
    return "---\n" + "\n\n".join(parts)


# ── Session HPO profiles (in-memory, per session_id) ──────────────────────────
# key = session_id (str); value = {"hpo_terms": [...], "candidate_genes": [...]}
# For the Gradio / non-session API, we use a single shared "default" profile.
_SESSION_PROFILES: dict = {}


def _get_session_profile(session_id: str = "default") -> dict:
    if session_id not in _SESSION_PROFILES:
        _SESSION_PROFILES[session_id] = {"hpo_terms": [], "candidate_genes": []}
    return _SESSION_PROFILES[session_id]


# ── New 8-intent router pipeline ───────────────────────────────────────────────

def _run_intervar_pipeline(
    req: ChatRequest,
    db: Any,
    llm: Any,
    t0: float,
    session_id: str = "default",
) -> Optional[ChatResponse]:
    """8-intent agentic pipeline (interval_02 architecture).

    Returns a ChatResponse when successfully handled, or None to signal
    the caller to fall back to the legacy regex pipeline.
    """
    from app.ai.intervar_router import (
        route_and_execute, build_answer_user_message,
        apply_safety_tag, render_executor,
        ExecutorResult,
    )
    from app.ai.validator import validate_answer

    session_profile = _get_session_profile(session_id)
    history_dicts = [{"role": m.role, "content": m.content} for m in req.history]

    # Stage 1 + 1.5 + 2: route → HPO resolve → execute
    decision, executor, resolution_summary = route_and_execute(
        req.message, db, session_profile, history_dicts
    )

    # If router returned "other" with no entities AND LLM is available for
    # general answers, let the legacy pipeline handle it so we preserve the
    # rich static KB and the pronoun-expansion logic.
    if (
        decision.intent == "other"
        and not (decision.gene or decision.rsid or decision.chr is not None
                 or decision.disease_term or decision.symptoms)
    ):
        return None  # signal: fall through to legacy pipeline

    # Empty result short-circuit (no LLM call needed)
    if executor.kind == "empty":
        has_specific_filter = bool(
            decision.gene or decision.rsid or decision.chr is not None
            or decision.disease_term
        )
        hpo_all_unresolved = (
            decision.intent == "hpo_symptom"
            and not resolution_summary.get("added")
            and resolution_summary.get("unresolved")
        )
        if has_specific_filter or hpo_all_unresolved:
            empty_reply = _render_empty_router_answer(decision, executor, resolution_summary)
            empty_reply = apply_safety_tag(empty_reply, decision.intent)
            return ChatResponse(
                response=empty_reply,
                type=decision.intent,
                execution_time_ms=(time.time() - t0) * 1000,
            )
        # Broad question with no filter and empty result → fall back
        return None

    # Schema lookup — no LLM needed when definition found
    if executor.kind == "schema":
        defn = executor.schema_definition or ""
        # If no definition found in _COLUMN_REFERENCE, fall back to legacy
        # pipeline which has a rich static KB (CADD, VUS, ACMG tiers, etc.)
        if defn.startswith("(no definition"):
            return None  # signal: let legacy _static_answer handle it
        resp = (
            f"**{executor.schema_column}** — {defn}\n\n"
            "This is the definition from the InterVar genomic schema reference."
        )
        resp = apply_safety_tag(resp, decision.intent)
        return ChatResponse(
            response=resp,
            type="schema_lookup",
            execution_time_ms=(time.time() - t0) * 1000,
        )

    # Stage 3: LLM answer grounded on executor result
    user_msg = build_answer_user_message(
        req.message, executor, session_profile, decision, resolution_summary
    )

    answer = ""
    if hasattr(llm, "answer"):
        try:
            answer = llm.answer(user_msg, history=history_dicts[-4:])
        except Exception as e:
            logger.warning(f"InterVar answer LLM failed: {e}")

    if not answer:
        # Deterministic fallback — render the executor output directly
        answer = render_executor(executor)

    # Stage 3.5: HGVS validator
    answer, fabricated = validate_answer(answer, executor.rows)
    if fabricated:
        logger.info("validator stripped %d fabricated HGVS token(s)", len(fabricated))

    # Stage 4: safety tag
    answer = apply_safety_tag(answer, decision.intent)

    # Enrich top rows with disease info (gene_enricher)
    rows_display = executor.rows[:10]
    try:
        from app.ai.gene_enricher import enrich_rows_with_diseases
        rows_display = enrich_rows_with_diseases(rows_display, db)
    except Exception:
        pass

    try:
        db.add(QueryLog(
            query_type=f"intervar_{decision.intent}",
            query_text=req.message,
            result_count=executor.total_matched,
            execution_time_ms=(time.time() - t0) * 1000,
            executed_at=datetime.now(),
        ))
        db.commit()
    except Exception:
        pass

    return ChatResponse(
        response=answer,
        type=decision.intent,
        data=rows_display if rows_display else None,
        row_count=executor.total_matched,
        execution_time_ms=(time.time() - t0) * 1000,
    )


def _render_empty_router_answer(decision: Any, executor: Any, resolution_summary: dict) -> str:
    """Deterministic 0-match reply for the new router pipeline."""
    from app.ai.intervar_router import RouterDecision, ExecutorResult
    intent = decision.intent
    universe_note = ""  # we don't have a universe count in our executor yet

    if intent == "biofilter" and decision.gene:
        # Build a specific message that includes any verdict filter applied
        verdict_str = ""
        if decision.intervar_verdict:
            verdict_str = f" classified as **{decision.intervar_verdict}** (InterVar)"
        exonic_str = ""
        if decision.exonic_func:
            exonic_str = f" matching **{decision.exonic_func}**"
        return (
            f"Your report contains **0 {decision.gene} variants{verdict_str}{exonic_str}**.\n"
            "This was a complete scan across all annotated rows."
        )
    if intent == "coord_lookup" and decision.rsid:
        return (
            f"No variant with rsID **{decision.rsid}** was found in your report."
        )
    if intent == "coord_lookup" and decision.chr is not None:
        loc = f"chr{decision.chr}:{decision.start}" if decision.start else f"chr{decision.chr}"
        return f"No variant at **{loc}** was found in your report."
    if intent == "hpo_symptom":
        added = resolution_summary.get("added") or []
        unresolved = resolution_summary.get("unresolved") or []
        if not added and unresolved:
            unr = ", ".join(f'"{s}"' for s in unresolved)
            return (
                f"I couldn't map {unr} to a recognised clinical symptom (HPO). "
                "Could you rephrase with more specific clinical language? For example: "
                "*muscle weakness*, *hearing loss*, *seizures*, *abdominal pain*, *fatigue*."
            )
        resolved_names = [a.get("name") for a in added if a.get("name")]
        sym_str = ", ".join(resolved_names) if resolved_names else "those symptoms"
        msg = (
            f"Your report contains **0 variants** in any gene currently associated with "
            f"{sym_str} (via HPO). This is a meaningful clinical finding — "
            "the report does not show variants in the canonical genes linked to those symptoms."
        )
        if unresolved:
            unr = ", ".join(f'"{s}"' for s in unresolved)
            msg += (
                f"\n\n*Note: I couldn't map {unr} to an HPO term — "
                "try rephrasing with a more specific symptom name.*"
            )
        return msg
    if intent == "disease_link" and decision.disease_term:
        return (
            f"Your report contains **0 variants** with an Orphanet/OMIM annotation "
            f"that includes the exact term **\"{decision.disease_term}\"**.\n\n"
            f"This does not mean you have no risk — many disease-gene associations "
            f"are indexed under different names. For example:\n"
            f"- **Diabetes** → try *MODY*, *Wolfram syndrome*, *diabetes mellitus*, *DIDMOAD*\n"
            f"- **Lung cancer** → try *lung disease*, *pulmonary*, or specific gene names\n"
            f"- **Heart disease** → try *cardiomyopathy*, *long QT syndrome*, *Marfan*\n\n"
            f"You can also ask directly: *\"Show variants in [gene name]\"* — "
            f"for example, *\"Show variants in HNF1A\"* or *\"Show variants in BRCA1\"*."
        )
    if intent == "acmg_clinvar":
        bits = []
        if decision.clinvar_includes: bits.append(f"ClinVar~'{decision.clinvar_includes}'")
        if decision.intervar_verdict: bits.append(f"InterVar='{decision.intervar_verdict}'")
        if decision.acmg_flag:
            v = decision.acmg_flag_value if decision.acmg_flag_value is not None else 1
            bits.append(f"{decision.acmg_flag}={v}")
        flt = " and ".join(bits) if bits else "this filter"
        return f"Your report contains **0 variants** matching {flt}."
    # Generic catch-all
    return (
        "No variants matched that query in your report. "
        "Try rephrasing or narrowing the filter.\n\n"
        "You can ask things like:\n"
        "- *Do I have any pathogenic variants?*\n"
        "- *Show variants in BRCA1*\n"
        "- *I have [symptom] — any related variants?*"
    )


# ── Main chat worker — 8-intent (primary) + 6-layer legacy (fallback) ──────────

def _run_chat(req: ChatRequest) -> ChatResponse:
    from app.ai.text_to_sql import get_engine
    t0 = time.time()

    # ── LAYER 0: Safety refuse (before anything else) ────────────────────────
    safety_response = _safety_refuse(req.message)
    if safety_response:
        return ChatResponse(
            response=safety_response,
            type="safety_refuse",
            execution_time_ms=(time.time() - t0) * 1000,
        )

    db = SessionLocal()
    try:
        # ── PRIMARY: 8-intent router pipeline (interval_02 architecture) ─────
        from app.ai.llm_config import get_llm as _get_llm_primary
        _llm_primary = _get_llm_primary()
        if _llm_primary is not None and hasattr(_llm_primary, "route"):
            try:
                result = _run_intervar_pipeline(req, db, _llm_primary, t0)
                if result is not None:
                    return result
            except Exception as _pipe_err:
                logger.warning(
                    f"InterVar pipeline raised: {_pipe_err} — falling back to legacy"
                )

        # ── FALLBACK: Original 6-layer regex pipeline ─────────────────────────
        return _run_legacy_chat(req, db, t0)

    finally:
        db.close()


def _run_legacy_chat(req: ChatRequest, db: Any, t0: float) -> ChatResponse:
    """Original 6-layer pipeline — preserved as fallback."""
    from app.ai.text_to_sql import get_engine

    # ── LAYER 1: Intent classification (with history for anaphora) ───────────
    intent = _classify_intent(req.message, req.history)
    hpo_context: Optional[dict] = None
    query_text = req.message.strip()
    sql_used: Optional[str] = None
    sql_source_used: Optional[str] = None

    # ── LAYER 1.5: Opportunistic HPO enrichment ───────────────────────────
    # Mirror genelio_backend: HPO runs whenever symptom trigger words appear,
    # even when primary intent is data_query.
    # e.g. "I have seizures — show me pathogenic variants" → HPO + variant lookup
    if intent == "data_query" and _SYMPTOM_QUERY_RE.search(req.message):
        try:
            _opp_hpo = _process_hpo(req.message, req.history)
            if _opp_hpo.get("genes") and not _opp_hpo.get("needs_clarification"):
                intent = "hpo_query"
                hpo_context = _opp_hpo
        except Exception as _hpo_err:
            logger.debug(f"Opportunistic HPO failed: {_hpo_err}")

    # Note: db is already open (passed in from _run_chat)
    if True:
        # ── LAYER 2: HPO resolution ──────────────────────────────────────────
        if intent == "hpo_query":
            # Use pre-computed context from opportunistic enrichment if available
            hpo_result = hpo_context or _process_hpo(req.message, req.history)

            # Single clarification question if nothing resolved
            if hpo_result.get("needs_clarification"):
                response = _apply_disclaimer(
                    hpo_result["clarification_question"], "hpo_clarification"
                )
                return ChatResponse(
                    response=response,
                    type="hpo_clarification",
                    execution_time_ms=(time.time() - t0) * 1000,
                )

            genes = hpo_result.get("genes", [])
            hpo_context = hpo_result

            if genes:
                # Build HPO pattern SQL: gene list + pathogenic filter + CADD sort
                gene_list_sql = ", ".join(f"'{g}'" for g in genes)
                query_text = (
                    f"Symptoms query: find pathogenic variants in these genes: "
                    f"{', '.join(genes[:10])}{'...' if len(genes) > 10 else ''}"
                )
                # Directly build and run the HPO SQL (bypass text_to_sql engine)
                hpo_sql = (
                    f'SELECT "Ref.Gene", Chr, Start, Ref, Alt, '
                    f'"ExonicFunc.refGene", "AAChange.refGene", '
                    f'"InterVar: InterVar and Evidence", "clinvar: Clinvar", '
                    f'CADD_phred, Freq_gnomAD_genome_ALL, Otherinfo '
                    f'FROM variants '
                    f'WHERE "Ref.Gene" IN ({gene_list_sql}) '
                    f'AND ('
                    f'"clinvar: Clinvar" LIKE \'clinvar: Pathogenic%\' '
                    f'  AND "clinvar: Clinvar" NOT LIKE \'clinvar: Conflicting%\' '
                    f'OR "clinvar: Clinvar" LIKE \'clinvar: Likely_pathogenic%\' '
                    f'OR "clinvar: Clinvar" LIKE \'clinvar: Pathogenic/Likely_pathogenic%\' '
                    f'OR "InterVar: InterVar and Evidence" LIKE \'InterVar: Pathogenic%\' '
                    f'OR "InterVar: InterVar and Evidence" LIKE \'InterVar: Likely pathogenic%\''
                    f') '
                    f'ORDER BY CADD_phred DESC '
                    f'LIMIT 50;'
                )
                ok, err = _deterministic_validate(hpo_sql)
                if ok:
                    from app.ai.text_to_sql import _run_sql
                    hpo_db_result = _run_sql(hpo_sql, db, max_rows=50)
                    if hpo_db_result.get("success") and hpo_db_result.get("row_count", 0) > 0:
                        rows_all = hpo_db_result.get("rows", [])
                        total = hpo_db_result.get("row_count", 0)
                        # Enrich with disease associations (DB columns + HPO API)
                        try:
                            from app.ai.gene_enricher import enrich_rows_with_diseases
                            rows_all = enrich_rows_with_diseases(rows_all, db)
                        except Exception as _ge:
                            logger.debug(f"HPO gene enrichment failed: {_ge}")
                        # Layer 4: top-10 cap
                        rows, cap_note = _apply_top10_cap(rows_all, total)
                        # Layer 5: LLM answer with history + HPO context
                        explanation = _explain_results(
                            req.message, rows, total,
                            history=req.history, hpo_context=hpo_result
                        )
                        hpo_header = (
                            f"**Symptom analysis:**\n{hpo_result['hpo_summary']}\n\n"
                            f"**Matching variants (pathogenic/likely pathogenic):**\n"
                        )
                        response_text = hpo_header + (explanation or f"Found {total} matching variant(s).")
                        if cap_note:
                            response_text += f"\n\n{cap_note}"
                        # Layer 6: disclaimer
                        response_text = _apply_disclaimer(response_text, "hpo_query")
                        try:
                            db.add(QueryLog(
                                query_type="hpo_query",
                                query_text=req.message,
                                result_count=total,
                                execution_time_ms=(time.time() - t0) * 1000,
                                executed_at=datetime.now(),
                            ))
                            db.commit()
                        except Exception:
                            pass
                        return ChatResponse(
                            response=response_text,
                            type="hpo_query",
                            sql=hpo_sql if req.include_sql else None,
                            sql_source="hpo_pattern",
                            data=rows,
                            row_count=total,
                            execution_time_ms=(time.time() - t0) * 1000,
                        )

            # HPO resolved genes but 0 DB matches — fall through to general with context
            no_match_msg = (
                f"**Symptom analysis:**\n{hpo_result.get('hpo_summary','')}\n\n"
                "No **Pathogenic** or **Likely Pathogenic** variants were found in your "
                "report for the genes associated with these symptoms. This is a meaningful "
                "finding — it doesn't mean the symptoms aren't real, only that no "
                "high-confidence pathogenic variants in canonical disease genes were detected."
            )
            response_text = _apply_disclaimer(no_match_msg, "hpo_query")
            return ChatResponse(
                response=response_text,
                type="hpo_query",
                sql=hpo_sql if (genes and req.include_sql) else None,
                sql_source="hpo_pattern",
                data=[], row_count=0,
                execution_time_ms=(time.time() - t0) * 1000,
            )

        # ── Anaphora expansion ───────────────────────────────────────────────
        if intent == "anaphora_query":
            query_text = _maybe_expand_with_history(req.message.strip(), req.history)

        # ── LAYER 3: SQL engine (data_query / anaphora_query / hybrid) ───────
        if intent in ("data_query", "anaphora_query", "hybrid"):
            engine = get_engine()
            result = engine.query(query_text, db, req.max_rows)
            sql_used = result.get("sql")
            sql_source_used = result.get("sql_source")

            # Stage A returned rows
            if result.get("success") and result.get("row_count", 0) > 0:
                rows_all = result.get("rows", [])
                total = result.get("row_count", 0)

                # Enrich with disease associations from DB columns + HPO API
                # Prevents LLM from using training-knowledge disease names
                try:
                    from app.ai.gene_enricher import enrich_rows_with_diseases
                    rows_all = enrich_rows_with_diseases(rows_all, db)
                except Exception as _ge:
                    logger.debug(f"Gene enrichment failed: {_ge}")

                # Layer 4: top-10 cap (always)
                rows, cap_note = _apply_top10_cap(rows_all, total)

                # ClinVar/PubMed supplement — only for rsID or single-variant lookups
                api_supplement = ""
                if total <= 3 or re.search(r'\brs\d+\b', req.message, re.IGNORECASE):
                    ext = _run_external_sync(req.message)
                    api_supplement = _format_api_supplement(ext) or ""

                # Layer 5: LLM answer with history
                nl_explanation = _explain_results(
                    req.message, rows, total, history=req.history
                )
                response_text = nl_explanation if nl_explanation else result["response"]
                if cap_note:
                    response_text += f"\n\n{cap_note}"
                if api_supplement:
                    response_text += f"\n\n{api_supplement}"

                # Layer 6: disclaimer
                response_text = _apply_disclaimer(response_text, intent)

                try:
                    db.add(QueryLog(
                        query_type="chat_data",
                        query_text=req.message,
                        result_count=total,
                        execution_time_ms=(time.time() - t0) * 1000,
                        executed_at=datetime.now(),
                    ))
                    db.commit()
                except Exception:
                    pass

                return ChatResponse(
                    response=response_text,
                    type="data_query",
                    sql=sql_used if req.include_sql else None,
                    sql_source=sql_source_used,
                    data=rows,
                    row_count=total,
                    execution_time_ms=(time.time() - t0) * 1000,
                )

            # Stage A returned 0 rows — DB error or empty
            raw_err = result.get("error", "") or result.get("response", "")
            if any(s in str(raw_err).lower() for s in
                   ("no such table", "unable to open", "database is locked")):
                return ChatResponse(
                    response=(
                        "The variant database is still loading. "
                        "Please try again in a few minutes.\n\n"
                        "I can still answer general genomics questions — try: "
                        "\"What is PVS1?\" or \"Explain ACMG classification\"."
                    ),
                    type="data_query",
                    sql=sql_used if req.include_sql else None,
                    sql_source=sql_source_used,
                    data=[], row_count=0,
                    execution_time_ms=(time.time() - t0) * 1000,
                )

            # 0 rows — try external APIs (only for rsID or single gene, not broad queries)
            from app.ai.llm_config import get_llm as _get_llm
            _llm = _get_llm()
            gene, rsid = _extract_gene_rsid(req.message)
            if rsid or (gene and not hpo_context):
                external = _run_external_sync(req.message)
                ext_prompt = _format_external_for_llm(external, req.message)
                if ext_prompt:
                    if _llm and hasattr(_llm, "answer_general"):
                        try:
                            hist_dicts = [{"role": m.role, "content": m.content}
                                          for m in req.history]
                            ext_answer = _llm.answer_general(ext_prompt, history=hist_dicts)
                            if ext_answer:
                                response_text = _apply_disclaimer(ext_answer, intent)
                                return ChatResponse(
                                    response=response_text,
                                    type="data_query",
                                    sql=sql_used if req.include_sql else None,
                                    sql_source="external_api",
                                    data=[], row_count=0,
                                    execution_time_ms=(time.time() - t0) * 1000,
                                )
                        except Exception as e:
                            logger.warning(f"External API LLM answer failed: {e}")
                    ext_direct = _format_external_direct(external, req.message)
                    if ext_direct:
                        return ChatResponse(
                            response=_apply_disclaimer(ext_direct, intent),
                            type="data_query",
                            sql=sql_used if req.include_sql else None,
                            sql_source="external_api",
                            data=[], row_count=0,
                            execution_time_ms=(time.time() - t0) * 1000,
                        )

            # Final fallback — LLM general with history
            if _llm and hasattr(_llm, "answer_general"):
                try:
                    hist_dicts = [{"role": m.role, "content": m.content}
                                  for m in req.history]
                    gen_answer = _llm.answer_general(req.message, history=hist_dicts)
                    if gen_answer:
                        return ChatResponse(
                            response=_apply_disclaimer(gen_answer, "general"),
                            type="general",
                            sql=sql_used if req.include_sql else None,
                            sql_source=sql_source_used,
                            data=[], row_count=0,
                            execution_time_ms=(time.time() - t0) * 1000,
                        )
                except Exception as e:
                    logger.warning(f"LLM general fallback failed: {e}")

            msg = result["response"] if result.get("success") else raw_err or "No results found."
            return ChatResponse(
                response=_apply_disclaimer(msg, intent),
                type="data_query",
                sql=sql_used if req.include_sql else None,
                sql_source=sql_source_used,
                data=[], row_count=0,
                execution_time_ms=(time.time() - t0) * 1000,
            )

        # ── LAYER 5 + 6: General path with history + disclaimer ──────────────
        from app.ai.llm_config import get_llm
        answer = _answer_general(req.message, req.history, get_llm())
        answer = _apply_disclaimer(answer, "general")
        return ChatResponse(
            response=answer,
            type="general",
            execution_time_ms=(time.time() - t0) * 1000,
        )


# ── Endpoints ──────────────────────────────────────────────────────────────────

@router.post("/chat", response_model=ChatResponse,
             summary="Conversational AI — general questions + database queries")
async def chat(request: ChatRequest):
    """
    Multi-turn conversational interface. Ask **any** question:

    **Database queries (auto-converted to SQL):**
    - "Show pathogenic variants in BRCA1"
    - "How many VUS variants are there?"
    - "Find rare frameshift mutations in TP53"
    - "Look up rs80357906"

    **General genomics questions:**
    - "What is PVS1?"
    - "Explain the ACMG classification system"
    - "What does a CADD score of 30 mean?"
    - "What genes cause hereditary breast cancer?"

    Supports multi-turn conversation via the `history` field.
    Uses Qwen3-8B when HF_TOKEN is set, otherwise falls back to pattern SQL + built-in answers.
    """
    if not request.message or len(request.message.strip()) < 2:
        raise HTTPException(status_code=400, detail="Message too short")
    from fastapi.concurrency import run_in_threadpool
    return await run_in_threadpool(_run_chat, request)


@router.post("/query", response_model=NLQueryResponse,
             summary="Natural language → SQL (structured output)")
async def natural_language_query(request: NLQueryRequest):
    """
    Convert a natural language question directly to SQL and execute it.
    Returns structured rows + the SQL used.

    For conversational use, prefer **POST /api/ai/chat** instead.
    """
    if not request.question or len(request.question.strip()) < 3:
        raise HTTPException(status_code=400, detail="Question too short")
    from fastapi.concurrency import run_in_threadpool
    return await run_in_threadpool(_run_nl_query, request)


def _run_nl_query(request: NLQueryRequest) -> NLQueryResponse:
    from app.ai.text_to_sql import get_engine
    db = SessionLocal()
    try:
        engine = get_engine()
        result = engine.query(request.question.strip(), db, request.max_rows)
        try:
            db.add(QueryLog(
                query_type="nl_text_to_sql",
                query_text=request.question,
                result_count=result.get("row_count", 0),
                execution_time_ms=result.get("execution_time_ms", 0),
                executed_at=datetime.now(),
            ))
            db.commit()
        except Exception:
            pass
        return NLQueryResponse(
            success=result.get("success", False),
            question=result["question"],
            response=result["response"],
            sql=result.get("sql") if request.include_sql else None,
            sql_source=result.get("sql_source"),
            row_count=result.get("row_count", 0),
            execution_time_ms=result.get("execution_time_ms", 0),
            error=result.get("error"),
        )
    finally:
        db.close()


@router.get("/status", summary="LLM backend status")
async def llm_status():
    """Check Qwen3 / HuggingFace LLM availability."""
    from app.ai.llm_config import check_llm_status
    status = check_llm_status()
    return {
        "status": "ready" if status.get("llm_ready") else "pattern_fallback_only",
        **status,
        "setup_instructions": {
            "hf_api": (
                "1. Go to https://huggingface.co/settings/tokens\n"
                "2. Click 'New token' → choose 'Read' type\n"
                "3. Enable 'Make calls to the Serverless Inference API' checkbox\n"
                "4. Copy token: set HF_TOKEN=hf_...\n"
                "5. Restart server"
            ),
            "hf_local": (
                "1. pip install transformers torch\n"
                "2. set LLM_BACKEND=hf_local\n"
                "3. Restart — downloads Qwen3-1.7B (~2GB)"
            ),
        },
    }


@router.get("/examples", summary="Example questions for /api/ai/chat")
async def query_examples():
    """Return example questions for both data queries and general chat."""
    return {
        "data_queries": [
            {"question": "Show VUS variants in BRCA1",                  "category": "VUS review",    "note": "Returns 6 VUS BRCA1 variants"},
            {"question": "Show benign variants in BRCA1",               "category": "Gene filter",   "note": "Returns 13 benign BRCA1 variants"},
            {"question": "Look up rs397857709",                         "category": "rsID lookup",   "note": "Known BRCA1 benign variant"},
            {"question": "Show missense variants in TP53",              "category": "Variant type",  "note": "Returns TP53 missense SNVs"},
            {"question": "Find variants in CSMD1",                      "category": "Large gene",    "note": "CSMD1 has 15,542 variants"},
            {"question": "Average CADD score for stopgain vs synonymous variants", "category": "Statistics", "note": "Aggregate query"},
            {"question": "Show variants with CADD score above 30",      "category": "Scores",        "note": "High-impact variants"},
            {"question": "How many VUS variants are there?",            "category": "Count",         "note": "Returns total VUS count"},
        ],
        "general_questions": [
            {"question": "What is the ACMG classification system?",     "category": "Concepts"},
            {"question": "Explain PVS1 criterion",                      "category": "ACMG criteria"},
            {"question": "What does a CADD score of 30 mean?",          "category": "Scores"},
            {"question": "What is a VUS?",                              "category": "Concepts"},
            {"question": "What is gnomAD allele frequency?",            "category": "Population"},
            {"question": "Explain BA1 benign stand-alone criterion",    "category": "ACMG criteria"},
            {"question": "What is a missense variant?",                 "category": "Variant types"},
            {"question": "What is de novo variant?",                    "category": "Genetics"},
        ],
    }


@router.post("/reconnect", summary="Re-initialize LLM connection (reconnect to Ollama)")
async def reconnect_llm():
    """
    Clear the LLM failure cache and attempt to reconnect to Ollama.
    Call this if the LLM was not ready when the server started.
    """
    from app.ai import llm_config
    llm_config._cache.clear()
    llm_config._failed_backends.clear()
    llm = llm_config.get_llm()
    if llm is not None:
        return {"status": "connected", "backend": llm_config.LLM_BACKEND,
                "model": llm_config.VLLM_MODEL, "message": "LLM connected successfully"}
    return {"status": "unavailable", "backend": llm_config.LLM_BACKEND,
            "message": "Could not connect to Ollama — ensure it is running on port 11434",
            "hint": f"Check: curl http://localhost:11434/v1/models"}


@router.get("/schema", summary="Database schema context")
async def get_schema():
    """Return the schema context injected into LLM prompts."""
    db = SessionLocal()
    try:
        from app.ai.schema_injector import get_schema_context, get_examples
        return {
            "schema": get_schema_context(db),
            "examples": get_examples(),
        }
    finally:
        db.close()
