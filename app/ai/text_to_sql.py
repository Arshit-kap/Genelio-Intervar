"""
Text-to-SQL Engine — Patient Variant DB (34-column InterVar format)
Pipeline: natural language → pattern SQL (fast) → LLM fallback → validate → execute → format
"""
import re
import logging
import time
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session
from sqlalchemy import text

from app.ai.schema_injector import get_schema_context, get_examples
from app.ai.llm_config import get_llm

logger = logging.getLogger(__name__)

_SELECT_RE = re.compile(r'^\s*SELECT\b', re.IGNORECASE)
_DANGER_RE = re.compile(
    r'\b(DROP|DELETE|UPDATE|INSERT|ALTER|TRUNCATE|EXEC|EXECUTE|GRANT|REVOKE)\b',
    re.IGNORECASE
)

# ── SQL helpers ────────────────────────────────────────────────────────────────

_PROSE_RE = re.compile(
    r'\.\s+[A-Z]|[?!]|\bSince\b|\bBut\b|\bHowever\b|\bNote\b|\bThis\b|\bLet me\b',
    re.IGNORECASE
)


def _extract_sql(raw: str) -> str:
    raw = re.sub(r'<think>.*?</think>', '', raw, flags=re.DOTALL).strip()
    m = re.search(r'```(?:sql)?\s*\n?(.*?)```', raw, re.DOTALL | re.IGNORECASE)
    if m:
        return m.group(1).strip()
    m = re.search(r'SQL:\s*(SELECT.*?)(?:;|$)', raw, re.DOTALL | re.IGNORECASE)
    if m:
        return m.group(1).strip() + ";"
    matches = list(re.finditer(r'(SELECT\b[^;]+;)', raw, re.DOTALL | re.IGNORECASE))
    for m in reversed(matches):
        candidate = m.group(1).strip()
        if not _PROSE_RE.search(candidate):
            return candidate
    return raw.strip()


def _validate_sql(sql: str) -> Tuple[bool, str]:
    if not _SELECT_RE.match(sql):
        return False, "Only SELECT queries are permitted"
    if _DANGER_RE.search(sql):
        return False, "Query contains unsafe operations"
    return True, ""


def _ensure_limit(sql: str, max_rows: int = 100) -> str:
    if "LIMIT" not in sql.upper():
        return sql.rstrip("; \n") + f" LIMIT {max_rows};"
    return sql


def _run_sql(sql: str, db: Session, max_rows: int = 100) -> Dict[str, Any]:
    try:
        final = _ensure_limit(sql, max_rows)
        result = db.execute(text(final))
        cols = list(result.keys())
        rows = [dict(zip(cols, r)) for r in result.fetchall()]
        return {"success": True, "columns": cols, "rows": rows,
                "row_count": len(rows), "sql": final}
    except Exception as e:
        logger.error(f"SQL execution error: {e}")
        return {"success": False, "error": str(e), "sql": sql,
                "columns": [], "rows": [], "row_count": 0}


# ── Result formatter ───────────────────────────────────────────────────────────

# Trim the long InterVar evidence string to just the classification tier
_INTERVAR_TRIM_RE = re.compile(
    r'^(InterVar:\s*\S[\w\s]+?)(?:\s+PVS1=|\s+PS=|$)', re.IGNORECASE
)


def _trim_intervar(v: str) -> str:
    m = _INTERVAR_TRIM_RE.match(str(v))
    return m.group(1).strip() if m else str(v)


_PRIORITY_KEYS = [
    "Chr", "Start", "Ref", "Alt", "Ref.Gene", "Gene.ensGene",
    "ExonicFunc.refGene", "Func.refGene", "AAChange.refGene",
    "InterVar: InterVar and Evidence", "clinvar: Clinvar",
    "Freq_gnomAD_genome_ALL", "CADD_phred", "SIFT_score",
    "avsnp147", "Otherinfo", "Orpha", "OMIM", "Phenotype_MIM",
    "Interpro_domain", "rmsk",
    "count", "variant_count", "pathogenic_count", "gene_count",
    "avg_cadd", "classification",
]


def _format_rows(results: Dict, requested_count: int = 0) -> str:
    if not results.get("success"):
        return f"Query error: {results.get('error')}"
    rows = results.get("rows", [])
    if not rows:
        return "No results found matching the query."

    total = results["row_count"]
    # Determine display limit based on what user asked for
    display_limit = max(requested_count, total) if requested_count > 0 else total

    lines = [f"Found {total} result(s):\n"]

    # Use table format for > 10 results
    if total > 10:
        return _format_table(rows, total, requested_count)

    # Full detail for ≤ 10 results
    for i, row in enumerate(rows[:display_limit], 1):
        parts = []
        shown = set()
        for k in _PRIORITY_KEYS:
            if k in row and row[k] is not None:
                v = row[k]
                if k == "InterVar: InterVar and Evidence":
                    parts.append(f"classification: {_trim_intervar(v)}")
                elif isinstance(v, float) and k in ("Freq_gnomAD_genome_ALL",
                                                     "Freq_esp6500siv2_all"):
                    parts.append(f"{k}: {v:.6f}")
                elif isinstance(v, float):
                    parts.append(f"{k}: {v:.2f}")
                else:
                    parts.append(f"{k}: {v}")
                shown.add(k)
        for k, v in row.items():
            if k not in shown and v is not None:
                parts.append(f"{k}: {v:.2f}" if isinstance(v, float) else f"{k}: {v}")
        lines.append(f"  {i}. " + "  |  ".join(parts) if parts else "  (empty row)")

    return "\n".join(lines)


def _format_table(rows: list, total: int, requested_count: int = 0) -> str:
    """Format results as a markdown table — used for > 10 rows."""
    if not rows:
        return "No results found."

    # Key columns to show in the table (subset for readability)
    TABLE_COLS = [
        "Ref.Gene", "Chr", "Start", "ExonicFunc.refGene", "AAChange.refGene",
        "InterVar: InterVar and Evidence", "clinvar: Clinvar",
        "Freq_gnomAD_genome_ALL", "CADD_phred", "Otherinfo",
        # Aggregate columns
        "count", "variant_count", "pathogenic_count", "avg_cadd", "classification",
        "Orpha", "OMIM",
    ]
    available = [c for c in TABLE_COLS if c in rows[0]]
    if not available:
        available = list(rows[0].keys())[:8]

    def _cell(v, col):
        if v is None:
            return "—"
        if col == "InterVar: InterVar and Evidence":
            return _trim_intervar(str(v))
        if isinstance(v, float) and col in ("Freq_gnomAD_genome_ALL",):
            return f"{v:.5f}"
        if isinstance(v, float):
            return f"{v:.2f}"
        s = str(v)
        return s[:40] + "…" if len(s) > 40 else s

    # Header
    header = " | ".join(f"**{c}**" for c in available)
    separator = " | ".join("---" for _ in available)
    data_rows = []
    for row in rows:
        data_rows.append(" | ".join(_cell(row.get(c), c) for c in available))

    note = f"\n\n*Showing all {total} results.*"
    if requested_count > 0 and total < requested_count:
        note = f"\n\n*Requested {requested_count}, found {total} results.*"

    return (
        f"Found {total} result(s):\n\n"
        f"| {header} |\n"
        f"| {separator} |\n"
        + "\n".join(f"| {r} |" for r in data_rows)
        + note
    )


# ── LLM SQL generation ─────────────────────────────────────────────────────────

def _llm_generate_sql(question: str, schema: str, llm: Any) -> Optional[str]:
    try:
        prompt = (
            "You are an expert SQL assistant for a patient genomic variants SQLite database.\n"
            "Generate ONE SQL SELECT query for the question. Return ONLY the SQL, nothing else.\n\n"
            f"DATABASE SCHEMA:\n{schema}\n\n"
            f"{get_examples()}\n\n"
            f"QUESTION: {question}\n\n"
            "SQL:"
        )
        resp = llm.invoke(prompt)
        if hasattr(resp, "content"):
            resp = resp.content
        if not resp:
            return None
        sql = _extract_sql(str(resp))
        if not _SELECT_RE.match(sql):
            logger.warning(f"LLM returned non-SELECT output: {sql[:80]}")
            return None
        return sql
    except Exception as e:
        logger.error(f"LLM SQL generation error: {e}")
        return None


# ── Pattern SQL helpers ────────────────────────────────────────────────────────

# New schema uses quoted column names for dots/spaces
_COLS = (
    'Chr, Start, Ref, Alt, "Ref.Gene", "ExonicFunc.refGene", '
    '"AAChange.refGene", "InterVar: InterVar and Evidence", '
    '"clinvar: Clinvar", Freq_gnomAD_genome_ALL, CADD_phred'
)

_COLS_FULL = (
    'Chr, Start, Ref, Alt, "Ref.Gene", "Func.refGene", '
    '"ExonicFunc.refGene", "AAChange.refGene", avsnp147, '
    '"InterVar: InterVar and Evidence", "clinvar: Clinvar", '
    'Freq_gnomAD_genome_ALL, CADD_phred, Otherinfo'
)


def _clf(tier: str) -> str:
    """InterVar classification LIKE clause (prefix match, indexed)."""
    return f'"InterVar: InterVar and Evidence" LIKE \'InterVar: {tier}%\''


def _parse_limit(question: str) -> Optional[int]:
    """Extract explicit count from question: 'list 50 genes' → 50."""
    m = re.search(
        r'\b(list|show|find|give|display|get)\s+(?:me\s+)?(\d+)\b|'
        r'\btop\s+(\d+)\b|'
        r'\bfirst\s+(\d+)\b|'
        r'\b(\d+)\s+(genes?|variants?|results?|rows?|entries|items?)\b',
        question, re.IGNORECASE
    )
    if m:
        for g in m.groups():
            if g and g.isdigit():
                n = int(g)
                if 1 <= n <= 500:
                    return n
    return None


_KNOWN_GENES = re.compile(
    r'\b(BRCA[12]|TP53|CFTR|MLH1|MSH[26]|PMS2|APC|MUTYH|PTEN|STK11|CDH1|'
    r'PALB2|CHEK2|ATM|BRIP1|RAD51[CD]|NF[12]|TSC[12]|VHL|RB1|BMPR1A|SMAD4|'
    r'EPCAM|BARD1|NBN|MRE11A|RAD50|FANCC|MEN1|RET|SDHB|SDHD|MECP2|HNF1A|'
    r'GCK|RYR2|CSMD1|HERC2|OBSCN|AHNAK2|SYNE1|ABCA4|USH2A|RPE65|RPGR|'
    r'LMNA|SCN5A|KCNQ1|KCNH2|RYR1|DMD|FMR1|HTT|ATXN[123]|AR|DMPK|'
    r'OR4F5|SAMD11|OR4F[0-9]+)\b'
)


def _find_all_genes_known(question: str) -> List[str]:
    """Return all _KNOWN_GENES matches from the question (deduped, up to 12)."""
    found: List[str] = []
    for m in _KNOWN_GENES.finditer(question):
        g = m.group(1)
        if g not in found:
            found.append(g)
    return found[:12]


def _gene_filter(question: str) -> Optional[str]:
    """Return SQL WHERE clause for gene name if found."""
    gm = _KNOWN_GENES.search(question)
    if gm:
        return f'"Ref.Gene" = \'{gm.group(1)}\''
    # Fallback: upper-case word that looks like a gene (2-10 chars, starts with letter)
    gm2 = re.search(r'\b([A-Z][A-Z0-9]{1,9})\b', question)
    if gm2:
        candidate = gm2.group(1)
        # Skip common non-gene abbreviations
        skip = {"VUS", "SQL", "DB", "DNA", "RNA", "PCR", "LOF", "AF", "PM", "PP",
                "PS", "BA", "BS", "BP", "PVS", "CADD", "SIFT", "SNV", "UTR",
                "LOH", "CDS", "NGS", "WGS", "MRI", "CT", "ID", "OR", "IS",
                "ALL", "NOT", "AND", "THE", "FOR", "TOP", "WITH", "FROM",
                "SHOW", "FIND", "LIST", "GET", "GIVE", "NONE", "NULL", "NA",
                "ACMG", "AMP", "LOD", "NMD", "GOF", "GNO", "MAD", "EVE"}
        if candidate not in skip:
            return f'"Ref.Gene" = \'{candidate}\''
    return None


# Disease → gene mapping for common Mendelian conditions
_DISEASE_GENE_MAP = {
    "rett syndrome": "MECP2", "rett": "MECP2",
    "cystic fibrosis": "CFTR", "cf ": "CFTR",
    "lynch syndrome": "MLH1",
    "cowden syndrome": "PTEN",
    "hereditary breast": "BRCA1",
    "li-fraumeni": "TP53",
    "neurofibromatosis type 1": "NF1", "neurofibromatosis": "NF1",
    "tuberous sclerosis": "TSC1",
    "retinitis pigmentosa": "RPGR",
    "duchenne muscular dystrophy": "DMD", "duchenne": "DMD",
    "huntington": "HTT",
    "marfan": "FBN1",
    "familial hypercholesterolemia": "LDLR",
}


_COLS_FULL_CLINICAL = (
    'Chr, Start, Ref, Alt, "Ref.Gene", "ExonicFunc.refGene", '
    '"AAChange.refGene", "InterVar: InterVar and Evidence", "clinvar: Clinvar", '
    'Freq_gnomAD_genome_ALL, CADD_phred, Otherinfo, Orpha, OMIM'
)

_DISEASE_CAUSING_FILTER = (
    '("InterVar: InterVar and Evidence" LIKE \'InterVar: Pathogenic%\' '
    'OR "InterVar: InterVar and Evidence" LIKE \'InterVar: Likely pathogenic%\' '
    'OR "clinvar: Clinvar" LIKE \'%Pathogenic%\' '
    'OR "clinvar: Clinvar" LIKE \'%Likely_pathogenic%\')'
)


def _extract_disease_term(question: str) -> Optional[str]:
    """Extract a disease/symptom term from free-text question."""
    patterns = [
        r'linked\s+to\s+([a-z][\w\s]+?)(?:\?|$|,|\.)',
        r'associated\s+with\s+([a-z][\w\s]+?)(?:\?|$|,|\.)',
        r'related\s+to\s+([a-z][\w\s]+?)(?:\?|$|,|\.)',
        r'symptoms?\s+(?:are|is|include[s]?|like)\s+([a-z][\w\s]+?)(?:\?|$|—|,)',
        r'(?:condition|disease|disorder|syndrome)\s+(?:called|named|like|of)?\s*([a-z][\w\s]+?)(?:\?|$|,|\.)',
        r'behind\s+my\s+([a-z][\w\s]+?)(?:\?|$|,|\.)',
        r'cause[sd]?\s+(?:by|of)?\s+([a-z][\w\s]+?)(?:\?|$|,|\.)',
    ]
    for pat in patterns:
        m = re.search(pat, question.lower())
        if m:
            term = m.group(1).strip()
            term = re.sub(
                r'\b(gene|genes|variant|variants|mutation|mutations|my|the|a|an|'
                r'report|results?|data|file|database|genome|condition|patient|in|'
                r'please|check|look|find|show|list|any|there)\b',
                '', term
            ).strip()
            term = re.sub(r'\s{2,}', ' ', term).strip()
            # Filter out noise-only terms
            _NOISE = {"in", "report", "my", "data", "file", "results", "db", "check"}
            if len(term) > 3 and term.lower() not in _NOISE:
                return re.sub(r"['\";%\\]", "", term)[:60]
    return None


def _pattern_sql(question: str) -> Optional[str]:
    q = question.lower()
    conds: List[str] = []
    order = ""

    # ── Explicit count from user request ──────────────────────────────────────
    limit_n = _parse_limit(question) or 50

    # ── Multi-gene history lookup (context carryover) ─────────────────────────
    # Triggered by _maybe_expand_with_history() — message starts with
    # "Show variants for these specific genes: GENE1, GENE2, ..."
    multi_gene_m = re.match(
        r'Show\s+variants\s+for\s+these\s+specific\s+genes?:\s+([A-Z0-9,\s]+)',
        question, re.IGNORECASE
    )
    if multi_gene_m:
        gene_names = [g.strip() for g in multi_gene_m.group(1).split(",") if g.strip()]
        if gene_names:
            gene_list = ", ".join(f"'{g}'" for g in gene_names[:12])
            return (
                f'SELECT {_COLS_FULL_CLINICAL} FROM variants '
                f'WHERE "Ref.Gene" IN ({gene_list}) '
                f'ORDER BY CADD_phred DESC NULLS LAST LIMIT {limit_n};'
            )

    # ── Disease-causing / harmful variants (dual ClinVar + InterVar) ───────────
    if re.search(
        r'disease[- ]caus|\bcausing disease\b|\bany disease\b|'
        r'\bharmful variant\b|\bbad variant\b|\bdangerous variant\b|'
        r'\bcause disease\b|\bdisease variant\b|\bwhat.*wrong\b|'
        r'\bare there.*pathogen\b|\bfind.*pathogen\b|\bany.*pathogen\b', q
    ) or (re.search(r'\bpathogen\b', q) and re.search(r'\bmy\b|\breport\b|\bpatient\b', q)):
        return (
            f'SELECT {_COLS_FULL_CLINICAL} FROM variants '
            f'WHERE {_DISEASE_CAUSING_FILTER} '
            f'ORDER BY CADD_phred DESC NULLS LAST LIMIT {limit_n};'
        )

    # ── Symptom / condition → gene → pathogenic variant search ────────────────
    symptom_triggers = re.search(
        r'\b(my\s+symptoms?\s+(are|is|include)|symptom[s]?\s+like|'
        r'what\s+gene\s+(should|do)\s+i\s+(look|check)|find\s+(me\s+)?the\s+gene|'
        r'gene\s+behind\s+my|which\s+gene\s+causes?|gene\s+for\s+my|'
        r'gene\s+responsible\s+for)\b', q
    )
    if symptom_triggers:
        term = _extract_disease_term(question)
        if term:
            # Also try splitting compound symptoms (e.g., "hearing loss and muscle weakness")
            # and search for the first meaningful term
            first_term = re.split(r'\s+and\s+', term)[0].strip()
            search_term = first_term if len(first_term) > 3 else term
            return (
                f'SELECT DISTINCT "Ref.Gene", Orpha, OMIM, Phenotype_MIM, '
                f'COUNT(*) AS variant_count, '
                f'SUM(CASE WHEN {_DISEASE_CAUSING_FILTER} THEN 1 ELSE 0 END) AS pathogenic_count '
                f'FROM variants '
                f'WHERE (Orpha LIKE \'%{search_term}%\' OR Phenotype_MIM LIKE \'%{search_term}%\') '
                f'AND "Ref.Gene" != \'NONE\' '
                f'GROUP BY "Ref.Gene" ORDER BY pathogenic_count DESC, variant_count DESC '
                f'LIMIT 20;'
            )
        # No extractable term — show top disease-causing genes from this patient's data
        return (
            f'SELECT DISTINCT "Ref.Gene", Orpha, OMIM, '
            f'COUNT(*) AS variant_count, '
            f'SUM(CASE WHEN {_DISEASE_CAUSING_FILTER} THEN 1 ELSE 0 END) AS pathogenic_count '
            f'FROM variants '
            f'WHERE "Ref.Gene" != \'NONE\' AND Orpha IS NOT NULL AND Orpha != \'\' '
            f'GROUP BY "Ref.Gene" HAVING pathogenic_count > 0 '
            f'ORDER BY pathogenic_count DESC LIMIT 20;'
        )

    # ── Variant type distribution: "how many types of variant / what variant types" ──
    # vari[ae]nt matches both "variant" and "varient" (common misspelling)
    if re.search(r'\btype[s]?\s+of\s+(?:the\s+)?vari[ae]nt\b|\bvari[ae]nt\s+type[s]?\b', q):
        return (
            'SELECT "ExonicFunc.refGene" AS variant_type, COUNT(*) AS count '
            'FROM variants '
            'WHERE "ExonicFunc.refGene" IS NOT NULL AND "ExonicFunc.refGene" != \'\' '
            'GROUP BY "ExonicFunc.refGene" ORDER BY count DESC;'
        )

    # ── Aggregate: count per classification / overview ─────────────────────────
    if any(w in q for w in ("how many", "count", "total", "statistics", "overview",
                             "summary", "breakdown")):
        gf = _gene_filter(question)

        # Plain "how many variants are in the report/database?" → total COUNT(*)
        if re.search(r'\bhow\s+many\s+(?:total\s+)?variants?\b', q) and not gf:
            if not any(c in q for c in ('pathogenic', 'benign', 'vus', 'uncertain',
                                         'missense', 'frameshift', 'stopgain',
                                         'splice', 'synonymous', 'clinvar')):
                return "SELECT COUNT(*) AS total_variants FROM variants;"

        if gf and "pathogenic" in q:
            return (
                f"SELECT COUNT(*) AS pathogenic_count FROM variants "
                f"WHERE {gf} AND ({_clf('Pathogenic')} OR {_clf('Likely pathogenic')});"
            )
        if gf:
            return (
                f"SELECT COUNT(*) AS total_count, "
                f"SUM(CASE WHEN {_clf('Pathogenic')} THEN 1 ELSE 0 END) AS pathogenic, "
                f"SUM(CASE WHEN {_clf('Benign')} THEN 1 ELSE 0 END) AS benign, "
                f"SUM(CASE WHEN {_clf('Uncertain')} THEN 1 ELSE 0 END) AS vus "
                f"FROM variants WHERE {gf};"
            )
        if "pathogenic" in q and "likely" not in q:
            return f"SELECT COUNT(*) AS pathogenic_count FROM variants WHERE {_clf('Pathogenic')};"
        if "likely pathogenic" in q:
            return f"SELECT COUNT(*) AS count FROM variants WHERE {_clf('Likely pathogenic')};"
        if "uncertain" in q or "vus" in q:
            return f"SELECT COUNT(*) AS vus_count FROM variants WHERE {_clf('Uncertain')};"
        if "benign" in q and "likely" not in q:
            return f"SELECT COUNT(*) AS count FROM variants WHERE {_clf('Benign')};"
        # Overall breakdown
        return (
            'SELECT SUBSTR("InterVar: InterVar and Evidence", 1, 35) AS classification, '
            "COUNT(*) AS count FROM variants "
            'GROUP BY SUBSTR("InterVar: InterVar and Evidence", 1, 35) '
            "ORDER BY count DESC;"
        )

    # ── Top N genes — only when "genes" is the direct object of "top N" ──────────
    # Avoids matching "top 10 variants ... which gene?" queries
    top_m = re.search(r'top\s+(\d+)', q)
    if top_m and re.search(r'\btop\s+\d+\s+genes?\b', q):
        n = top_m.group(1)
        if "pathogenic" in q:
            return (
                f'SELECT "Ref.Gene", COUNT(*) AS pathogenic_count FROM variants '
                f'WHERE {_clf("Pathogenic")} AND "Ref.Gene" != \'NONE\' '
                f'GROUP BY "Ref.Gene" ORDER BY pathogenic_count DESC LIMIT {n};'
            )
        return (
            f'SELECT "Ref.Gene", COUNT(*) AS variant_count FROM variants '
            f'WHERE "Ref.Gene" != \'NONE\' '
            f'GROUP BY "Ref.Gene" ORDER BY variant_count DESC LIMIT {n};'
        )

    # ── List N genes of a type ─────────────────────────────────────────────────
    # e.g. "list 50 genes with missense variants"
    if re.search(r'\b(list|show|find|give)\b.{0,30}\bgenes?\b', q) and limit_n:
        type_sql = ""
        if "missense" in q or "nonsynonymous" in q:
            type_sql = "\"ExonicFunc.refGene\" = 'nonsynonymous SNV'"
        elif "frameshift" in q:
            type_sql = "\"ExonicFunc.refGene\" LIKE '%frameshift%'"
        elif "stopgain" in q or "nonsense" in q:
            type_sql = "\"ExonicFunc.refGene\" = 'stopgain'"
        elif "pathogenic" in q:
            type_sql = _clf("Pathogenic")
        if type_sql:
            return (
                f'SELECT DISTINCT "Ref.Gene", COUNT(*) as variant_count '
                f'FROM variants WHERE {type_sql} AND "Ref.Gene" != \'NONE\' '
                f'GROUP BY "Ref.Gene" ORDER BY variant_count DESC LIMIT {limit_n};'
            )

    # ── Average CADD ──────────────────────────────────────────────────────────
    if re.search(r'\b(average|avg|mean)\b', q) and "cadd" in q:
        type_map = {
            "stopgain": "stopgain", "nonsense": "stopgain",
            "missense": "nonsynonymous SNV", "synonymous": "synonymous SNV",
            "frameshift": "frameshift deletion",
            "splice": "splicing",
        }
        mentioned = list({v for k, v in type_map.items() if k in q})
        if mentioned:
            unions = []
            for t in mentioned:
                unions.append(
                    f"SELECT * FROM (SELECT \"{t}\" AS extype, CADD_phred "
                    f"FROM variants WHERE \"ExonicFunc.refGene\" = '{t}' "
                    f"AND CADD_phred IS NOT NULL LIMIT 50000)"
                )
            inner = " UNION ALL ".join(unions)
            return (
                f"SELECT extype AS \"ExonicFunc.refGene\", "
                f"ROUND(AVG(CADD_phred), 2) AS avg_cadd, COUNT(*) AS count "
                f"FROM ({inner}) GROUP BY extype ORDER BY avg_cadd DESC;"
            )
        return (
            'SELECT "ExonicFunc.refGene", ROUND(AVG(CADD_phred), 2) AS avg_cadd, '
            "COUNT(*) AS count "
            "FROM (SELECT \"ExonicFunc.refGene\", CADD_phred FROM variants "
            "WHERE CADD_phred IS NOT NULL AND \"ExonicFunc.refGene\" IS NOT NULL LIMIT 50000) "
            "GROUP BY \"ExonicFunc.refGene\" ORDER BY avg_cadd DESC LIMIT 10;"
        )

    # ── Disease-name to gene mapping ──────────────────────────────────────────
    for disease, gene in _DISEASE_GENE_MAP.items():
        if disease in q:
            gf = f'"Ref.Gene" = \'{gene}\''
            if "pathogenic" in q:
                return (
                    f'SELECT {_COLS_FULL} FROM variants '
                    f'WHERE {gf} AND ({_clf("Pathogenic")} OR {_clf("Likely pathogenic")}) '
                    f'LIMIT {limit_n};'
                )
            return (
                f'SELECT {_COLS_FULL} FROM variants '
                f'WHERE {gf} LIMIT {limit_n};'
            )

    # ── rsID lookup ───────────────────────────────────────────────────────────
    rs = re.search(r'\brs\d+\b', question, re.IGNORECASE)
    if rs:
        if any(x in q for x in ("hgvs", "notation", "coding", "protein", "amino")):
            return (
                f'SELECT avsnp147, "Ref.Gene", "AAChange.refGene", "AAChange.ensGene", '
                f'"ExonicFunc.refGene", "InterVar: InterVar and Evidence", '
                f'"clinvar: Clinvar" FROM variants WHERE avsnp147 = \'{rs.group(0)}\';'
            )
        return (
            f'SELECT Chr, Start, Ref, Alt, "Ref.Gene", avsnp147, "ExonicFunc.refGene", '
            f'"AAChange.refGene", "InterVar: InterVar and Evidence", "clinvar: Clinvar", '
            f'Freq_gnomAD_genome_ALL, CADD_phred, Otherinfo '
            f'FROM variants WHERE avsnp147 = \'{rs.group(0)}\';'
        )

    # ── Chromosome + position lookup ──────────────────────────────────────────
    chr_m = re.search(r'\bchr(?:omosome)?\s*([0-9]{1,2}|X|Y|MT)\b', q)
    pos_m = re.search(r'\bpos(?:ition)?\s*[:=]?\s*(\d+)\b|\bstart\s+(\d+)\b', q)
    if chr_m and pos_m:
        pos = pos_m.group(1) or pos_m.group(2)
        return (
            f'SELECT Chr, Start, Ref, Alt, "Ref.Gene", "ExonicFunc.refGene", '
            f'"AAChange.refGene", avsnp147, "InterVar: InterVar and Evidence", '
            f'"clinvar: Clinvar", Freq_gnomAD_genome_ALL, CADD_phred, Otherinfo '
            f'FROM variants WHERE Chr = \'{chr_m.group(1).upper()}\' AND Start = {pos};'
        )

    # ── Build conditions from query ───────────────────────────────────────────

    # Gene filter
    gf = _gene_filter(question)
    if gf:
        conds.append(gf)

    # InterVar classification — skip if user explicitly references ClinVar classification
    # e.g. "Pathogenic in ClinVar" should only add ClinVar filter, not InterVar filter
    _clinvar_clf_ref = "clinvar" in q and any(
        x in q for x in ("pathogenic", "benign", "likely")
    )
    if not _clinvar_clf_ref:
        if "likely pathogenic" in q:
            conds.append(_clf("Likely pathogenic"))
        elif "pathogenic" in q and "not" not in q:
            conds.append(_clf("Pathogenic"))
        elif "likely benign" in q:
            conds.append(_clf("Likely benign"))
        elif "benign" in q and "not" not in q:
            conds.append(_clf("Benign"))
        elif any(x in q for x in ("vus", "uncertain significance", "uncertain")):
            conds.append(_clf("Uncertain"))

    # ClinVar filter
    if "clinvar pathogenic" in q or ("clinvar" in q and "pathogenic" in q):
        conds.append('"clinvar: Clinvar" LIKE \'%Pathogenic%\'')
    elif "clinvar" in q and "benign" in q:
        conds.append('"clinvar: Clinvar" LIKE \'%Benign%\'')

    # Variant type
    if "frameshift" in q:
        conds.append('"ExonicFunc.refGene" LIKE \'%frameshift%\'')
    elif "stopgain" in q or "nonsense" in q:
        conds.append('"ExonicFunc.refGene" = \'stopgain\'')
    elif "missense" in q or "nonsynonymous" in q:
        conds.append('"ExonicFunc.refGene" = \'nonsynonymous SNV\'')
    elif "synonymous" in q and "non" not in q:
        conds.append('"ExonicFunc.refGene" = \'synonymous SNV\'')
    elif re.search(r'\bsplice\b|\bsplicing\b', q):
        conds.append('"Func.refGene" = \'splicing\'')
    elif re.search(r'\bin.?frame\b', q) or "nonframeshift" in q:
        conds.append(
            '"ExonicFunc.refGene" IN (\'nonframeshift deletion\',\'nonframeshift insertion\')'
        )

    # Functional region
    if re.search(r'\bexonic\b|\bcoding region\b|\bin exon\b', q) and not conds:
        conds.append('"Func.refGene" = \'exonic\'')
    elif re.search(r'\bintronic\b|\bin intron\b', q) and not conds:
        conds.append('"Func.refGene" = \'intronic\'')

    # Zygosity
    if re.search(r'\bhetero(?:zygous)?\b', q):
        conds.append("Otherinfo = 'het'")
    elif re.search(r'\bhomo(?:zygous)?\b', q):
        conds.append("Otherinfo = 'hom'")
    elif re.search(r'\bhemi(?:zygous)?\b', q):
        conds.append("Otherinfo = 'hemi'")

    # Frequency filters
    if re.search(r'\bnot\s+(?:found\s+)?in\s+gnomad\b|\bgnomad\s+absent\b|\babsent\s+(?:from|in)\s+gnomad\b|\babsent\b|\bnovel\b', q):
        conds.append("(Freq_gnomAD_genome_ALL IS NULL OR Freq_gnomAD_genome_ALL = 0)")
    elif re.search(r'\brave\b', q):
        conds.append("(Freq_gnomAD_genome_ALL IS NULL OR Freq_gnomAD_genome_ALL < 0.01)")
    elif re.search(r'\bcommon\b', q):
        conds.append("Freq_gnomAD_genome_ALL > 0.05")
    else:
        # Handle both fractions (0.01) and percentages (1%) — convert % to fraction
        m2 = re.search(
            r'gnomad.{0,20}(?:>|greater\s+than|above)\s*([\d.]+)\s*(%|percent)?', q)
        if m2:
            val = float(m2.group(1))
            if m2.group(2):  # "%" or "percent" present → convert to fraction
                val = val / 100
            conds.append(f"Freq_gnomAD_genome_ALL > {val}")
        m2 = re.search(
            r'gnomad.{0,20}(?:<|less\s+than|below)\s*([\d.]+)\s*(%|percent)?', q)
        if m2:
            val = float(m2.group(1))
            if m2.group(2):
                val = val / 100
            conds.append(f"Freq_gnomAD_genome_ALL < {val}")

    # CADD sort — "sorted by CADD", "highest CADD/damage/score", "by CADD score"
    if re.search(r'\bsort(?:ed)?\s+by\s+cadd\b|\bhighest\s+(?:cadd|damage|score)\b'
                 r'|\bby\s+cadd\s+score\b|\brank(?:ed)?\s+by\s+(?:cadd|damage)\b', q):
        order = "ORDER BY CADD_phred DESC NULLS LAST"

    # CADD filter (threshold)
    cadd_m = re.search(
        r'cadd.{0,20}(?:>|greater\s+than|above|score\s+(?:above|>))\s*(\d+(?:\.\d+)?)', q)
    if cadd_m:
        conds.append(f"CADD_phred > {cadd_m.group(1)}")
        if not order:
            order = "ORDER BY CADD_phred DESC NULLS LAST"
    elif re.search(r'\bhigh\s+(?:cadd|impact|damage)\b|\bdeleterious\b|\bdamaging\b', q):
        conds.append("CADD_phred > 20")
        order = "ORDER BY CADD_phred DESC"

    # Repeat masker
    if re.search(r'\bnot\s+in\s+repeat\b|\bno\s+repeat\b', q):
        conds.append("rmsk IS NULL")
    elif re.search(r'\brepeat\s+region\b|\brepeat\s+element\b', q):
        conds.append("rmsk IS NOT NULL")

    # ── Multi-gene direct lookup ───────────────────────────────────────────────
    # MUST be before pheno_m: prevents gene names being parsed as disease terms.
    # Fires only when 2+ known genes appear and no non-gene conditions are built.
    # e.g. "Find variants in DMD, TP53 and BRCA1 in my report"
    all_known_genes = _find_all_genes_known(question)
    if len(all_known_genes) >= 2:
        non_gene_conds = [c for c in conds if '"Ref.Gene"' not in c]
        if not non_gene_conds:
            gene_list = ", ".join(f"'{g}'" for g in all_known_genes[:12])
            return (
                f'SELECT {_COLS_FULL_CLINICAL} FROM variants '
                f'WHERE "Ref.Gene" IN ({gene_list}) '
                f'ORDER BY CADD_phred DESC NULLS LAST LIMIT {limit_n};'
            )

    # OMIM/Orpha phenotype search — "variants/genes linked to deafness/cancer/etc."
    pheno_m = re.search(
        r'(?:linked|associated|related|caused?|behind|responsible)\s+(?:to|by|with|for)\s+(.+?)(?:\?|$)',
        question, re.IGNORECASE
    )
    if not pheno_m:
        pheno_m = re.search(
            r'(?:variants?|genes?|mutations?)\s+(?:for|in|causing)\s+(.+?)(?:\?|$)',
            question, re.IGNORECASE
        )
    if pheno_m:
        term = re.sub(r"['\";%_\\]", "", pheno_m.group(1).strip())[:60]
        term = re.sub(r'\b(gene|genes|variant|variants|mutation|my|the|a|an|report|patient)\b',
                      '', term, flags=re.IGNORECASE).strip()
        if len(term) > 3:
            return (
                f'SELECT DISTINCT "Ref.Gene", Orpha, Phenotype_MIM, OMIM, '
                f'COUNT(*) AS variant_count, '
                f'SUM(CASE WHEN {_DISEASE_CAUSING_FILTER} THEN 1 ELSE 0 END) AS pathogenic_count '
                f'FROM variants '
                f'WHERE (Orpha LIKE \'%{term}%\' OR Phenotype_MIM LIKE \'%{term}%\') '
                f'AND "Ref.Gene" != \'NONE\' '
                f'GROUP BY "Ref.Gene" ORDER BY pathogenic_count DESC, variant_count DESC '
                f'LIMIT 20;'
            )

    if not conds:
        return None  # No recognised pattern — let LLM handle it

    sql = f"SELECT {_COLS_FULL} FROM variants"
    if conds:
        sql += " WHERE " + " AND ".join(conds)
    if order:
        sql += f" {order}"
    sql += f" LIMIT {limit_n};"
    return sql


# ── LLM narration ──────────────────────────────────────────────────────────────

def _llm_generate_sql_with_schema(question: str, schema: str, llm: Any) -> Optional[str]:
    return _llm_generate_sql(question, schema, llm)


# ── Main Engine ────────────────────────────────────────────────────────────────

class TextToSQLEngine:
    """Patient variant Q&A text-to-SQL engine using Qwen3 via Ollama."""

    @property
    def llm(self) -> Optional[Any]:
        return get_llm()

    def query(self, question: str, db: Session, max_rows: int = 100) -> Dict[str, Any]:
        t0 = time.time()
        schema = get_schema_context(db)

        # Extract explicit count from question for LIMIT and display
        requested_count = _parse_limit(question) or 0

        # 1. Pattern SQL first (fast, index-optimised)
        sql: Optional[str] = None
        sql_source = "pattern"

        sql = _pattern_sql(question)

        # 2. LLM fallback
        if not sql and self.llm:
            sql = _llm_generate_sql(question, schema, self.llm)
            if sql:
                sql_source = "qwen3"

        if not sql:
            return self._error("Could not generate SQL for this question.", question)

        # 3. Validate
        valid, err = _validate_sql(sql)
        if not valid:
            return self._error(f"SQL validation failed: {err}", question, sql=sql)

        # 4. Execute — use requested_count as max_rows if explicitly given
        effective_max = max(max_rows, requested_count) if requested_count > 0 else max_rows
        results = _run_sql(sql, db, effective_max)

        # 5. Format — pass requested_count so display shows all rows
        response = _format_rows(results, requested_count)

        elapsed = (time.time() - t0) * 1000
        return {
            "success": results.get("success", False),
            "question": question,
            "sql": sql,
            "sql_source": sql_source,
            "response": response,
            "rows": results.get("rows", []),
            "columns": results.get("columns", []),
            "row_count": results.get("row_count", 0),
            "execution_time_ms": round(elapsed, 1),
            "error": results.get("error"),
        }

    @staticmethod
    def _error(msg: str, question: str, sql: Optional[str] = None) -> Dict:
        return {
            "success": False, "question": question, "sql": sql,
            "sql_source": None, "response": msg, "rows": [],
            "columns": [], "row_count": 0,
            "execution_time_ms": 0, "error": msg,
        }

    def get_status(self) -> Dict[str, Any]:
        from app.ai.llm_config import check_llm_status
        return {
            "llm_available": self.llm is not None,
            "backend_status": check_llm_status(),
        }


# Singleton
_ENGINE: Optional[TextToSQLEngine] = None


def get_engine() -> TextToSQLEngine:
    global _ENGINE
    if _ENGINE is None:
        _ENGINE = TextToSQLEngine()
    return _ENGINE
