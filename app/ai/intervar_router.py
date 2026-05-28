"""8-intent LLM router + deterministic SQL executor for InterVar patient DB.

Adapted from interval_02/chatbot/agentic/intervar_orchestrator.py and
intervar_prompts.py.  Key differences from the original Django version:
  - Queries SQLite via SQLAlchemy (patient_variants.db), NOT in-memory rows
  - Uses our column names: "Ref.Gene", "clinvar: Clinvar", etc.
  - No Django models — session HPO profile lives in a caller-supplied dict
  - LLM backend accessed via app.ai.llm_config.get_llm()

Public entry: route_and_execute(message, db, session_profile, history)
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Optional

from sqlalchemy import text as sa_text
from sqlalchemy.orm import Session

from app.ai.pathogenicity import (
    Bucket, BUCKET_LABEL, BUCKET_PRIORITY,
    pathogenicity_bucket, rank_rows,
)

log = logging.getLogger(__name__)


# ── Router system prompt ───────────────────────────────────────────────────────
# Mirrors interval_02/chatbot/agentic/intervar_prompts.py ROUTER_SYSTEM_PROMPT
# adapted for our SQLite column names.

ROUTER_SYSTEM_PROMPT = """\
/no_think
You are an intent classifier for an InterVar variant-Q&A system.
Your ONLY output is a JSON object — no prose, no markdown fences, no commentary. \
Stick to this exact schema:

{
  "intent": "coord_lookup" | "biofilter" | "acmg_clinvar" |
            "disease_link" | "aggregate" | "hpo_symptom" |
            "schema_lookup" | "summary" | "other",
  "needs_hpo": true | false,
  "symptoms": [string, ...],

  "chr": string | null,
  "start": integer | null,
  "end": integer | null,
  "rsid": string | null,
  "gene": string | null,

  "func": string | null,
  "exonic_func": string | null,
  "zygosity": "het" | "hom" | "hemi" | null,
  "in_repeat": true | false | null,

  "clinvar_includes": string | null,
  "intervar_verdict": string | null,
  "acmg_flag": "PVS1" | "PS" | "PM" | "PP" | "BA1" | "BS" | "BP" | null,
  "acmg_flag_value": 1 | 0 | null,

  "cadd_min": number | null,
  "cadd_max": number | null,
  "gnomad_max": number | null,
  "gnomad_min": number | null,
  "sift_max": number | null,
  "metasvm_min": number | null,

  "disease_term": string | null,

  "group_by": "gene" | "exonic_func" | "clinvar" | "intervar" | "chr" | null,
  "agg_func": "count" | "avg_cadd" | null,
  "limit": integer | null,

  "wants_schema": true | false,
  "target_column": string | null
}

Mapping rules:

- **coord_lookup** — user names a chromosome + position OR a dbSNP ID \
("rsXXXXXX"). Populate chr/start (and optionally end) or rsid.
- **biofilter** — user filters by variant type / gene / threshold. \
Populate exonic_func, func, zygosity, in_repeat, gene, cadd_min, \
gnomad_max, sift_max, metasvm_min, etc. Common lay terms map as:
    "missense"           → exonic_func = "nonsynonymous SNV"
    "synonymous"/"silent"→ exonic_func = "synonymous SNV"
    "stop-gain"/"nonsense"→ exonic_func = "stopgain"
    "stop-loss"          → exonic_func = "stoploss"
    "frameshift"         → exonic_func = "frameshift deletion" + "frameshift insertion"
    "in-frame del"       → exonic_func = "nonframeshift deletion"
    "in-frame ins"       → exonic_func = "nonframeshift insertion"
    "splicing"           → func = "splicing"
    "exonic"             → func = "exonic"
    "intronic"           → func = "intronic"
    "heterozygous"/"het" → zygosity = "het"
    "homozygous"/"hom"   → zygosity = "hom"
    "hemizygous"         → zygosity = "hemi"
    "NOT in a repeat region" → in_repeat = false
    "in a repeat"        → in_repeat = true
    GENE NAME QUERIES — if the user asks about a specific gene (e.g. SERPINA1, \
BRCA2, TP53), use biofilter with gene = that gene symbol. \
disease_link is for disease/syndrome names only, NOT gene names.
- **acmg_clinvar** — user filters by ClinVar significance, InterVar \
verdict, or a specific ACMG criterion. Populate clinvar_includes (the \
substring to match, case-insensitive — e.g. "Pathogenic"), \
intervar_verdict ("Pathogenic" / "Likely pathogenic" / "Uncertain \
significance" / "Likely benign" / "Benign"), and acmg_flag/acmg_flag_value.
    IMPORTANT MAPPINGS for intervar_verdict:
    "VUS" / "variant of uncertain significance" / "uncertain" → intervar_verdict = "Uncertain significance"
    When a gene name is also present, combine biofilter gene + acmg_clinvar intervar_verdict: \
use intent="biofilter" with both gene AND intervar_verdict populated.
- **disease_link** — user names a DISEASE or SYNDROME ("Rett syndrome", \
"Wilson disease", "muscular dystrophy"). Populate disease_term. \
IMPORTANT: gene names (SERPINA1, BRCA1, TP53) are NOT diseases — route those to biofilter.
- **aggregate** — user wants counts / averages / top-N. Populate \
group_by, agg_func, limit.
- **hpo_symptom** — user describes lay symptoms ("I have headaches", \
"I feel feverish", "I have fatigue"). Set needs_hpo=true, populate \
symptoms with atomic phrases (one phenotype per entry, no severity adverbs).
- **schema_lookup** — user asks what a column / score / term means. Set \
wants_schema=true and target_column to the exact term they asked about.
    IMPORTANT: these knowledge questions use schema_lookup:
    "What is PVS1?" → target_column = "PVS1"
    "What does VUS mean?" → target_column = "VUS"
    "Explain CADD" → target_column = "CADD"
    "What are ACMG classification tiers?" → target_column = "ACMG classification tiers"
    "What is BA1?" → target_column = "BA1"
    "Explain gnomAD" → target_column = "Freq_gnomAD_genome_ALL"
- **summary** — user asks for an overview/summary of the report.
- **other** — anything else.

Examples:

INPUT: "What is the gene and ref/alt for the variant at chromosome 1, position 10611?"
OUTPUT: {"intent":"coord_lookup","needs_hpo":false,"symptoms":[],"chr":"1","start":10611,"end":null,"rsid":null,"gene":null,"func":null,"exonic_func":null,"zygosity":null,"in_repeat":null,"clinvar_includes":null,"intervar_verdict":null,"acmg_flag":null,"acmg_flag_value":null,"cadd_min":null,"cadd_max":null,"gnomad_max":null,"gnomad_min":null,"sift_max":null,"metasvm_min":null,"disease_term":null,"group_by":null,"agg_func":null,"limit":null,"wants_schema":false,"target_column":null}

INPUT: "Show HGVS for dbSNP rs189107123."
OUTPUT: {"intent":"coord_lookup","needs_hpo":false,"symptoms":[],"chr":null,"start":null,"end":null,"rsid":"rs189107123","gene":null,"func":null,"exonic_func":null,"zygosity":null,"in_repeat":null,"clinvar_includes":null,"intervar_verdict":null,"acmg_flag":null,"acmg_flag_value":null,"cadd_min":null,"cadd_max":null,"gnomad_max":null,"gnomad_min":null,"sift_max":null,"metasvm_min":null,"disease_term":null,"group_by":null,"agg_func":null,"limit":null,"wants_schema":false,"target_column":null}

INPUT: "Find all heterozygous missense variants in DDX11L2 with CADD > 20."
OUTPUT: {"intent":"biofilter","needs_hpo":false,"symptoms":[],"chr":null,"start":null,"end":null,"rsid":null,"gene":"DDX11L2","func":null,"exonic_func":"nonsynonymous SNV","zygosity":"het","in_repeat":null,"clinvar_includes":null,"intervar_verdict":null,"acmg_flag":null,"acmg_flag_value":null,"cadd_min":20,"cadd_max":null,"gnomad_max":null,"gnomad_min":null,"sift_max":null,"metasvm_min":null,"disease_term":null,"group_by":null,"agg_func":null,"limit":null,"wants_schema":false,"target_column":null}

INPUT: "List all in-frame deletions that are NOT in a repeat region."
OUTPUT: {"intent":"biofilter","needs_hpo":false,"symptoms":[],"chr":null,"start":null,"end":null,"rsid":null,"gene":null,"func":null,"exonic_func":"nonframeshift deletion","zygosity":null,"in_repeat":false,"clinvar_includes":null,"intervar_verdict":null,"acmg_flag":null,"acmg_flag_value":null,"cadd_min":null,"cadd_max":null,"gnomad_max":null,"gnomad_min":null,"sift_max":null,"metasvm_min":null,"disease_term":null,"group_by":null,"agg_func":null,"limit":null,"wants_schema":false,"target_column":null}

INPUT: "What variants do I have in SERPINA1?"
OUTPUT: {"intent":"biofilter","needs_hpo":false,"symptoms":[],"chr":null,"start":null,"end":null,"rsid":null,"gene":"SERPINA1","func":null,"exonic_func":null,"zygosity":null,"in_repeat":null,"clinvar_includes":null,"intervar_verdict":null,"acmg_flag":null,"acmg_flag_value":null,"cadd_min":null,"cadd_max":null,"gnomad_max":null,"gnomad_min":null,"sift_max":null,"metasvm_min":null,"disease_term":null,"group_by":null,"agg_func":null,"limit":null,"wants_schema":false,"target_column":null}

INPUT: "Show VUS variants in BRCA1"
OUTPUT: {"intent":"biofilter","needs_hpo":false,"symptoms":[],"chr":null,"start":null,"end":null,"rsid":null,"gene":"BRCA1","func":null,"exonic_func":null,"zygosity":null,"in_repeat":null,"clinvar_includes":null,"intervar_verdict":"Uncertain significance","acmg_flag":null,"acmg_flag_value":null,"cadd_min":null,"cadd_max":null,"gnomad_max":null,"gnomad_min":null,"sift_max":null,"metasvm_min":null,"disease_term":null,"group_by":null,"agg_func":null,"limit":null,"wants_schema":false,"target_column":null}

INPUT: "Find uncertain significance variants in TP53"
OUTPUT: {"intent":"biofilter","needs_hpo":false,"symptoms":[],"chr":null,"start":null,"end":null,"rsid":null,"gene":"TP53","func":null,"exonic_func":null,"zygosity":null,"in_repeat":null,"clinvar_includes":null,"intervar_verdict":"Uncertain significance","acmg_flag":null,"acmg_flag_value":null,"cadd_min":null,"cadd_max":null,"gnomad_max":null,"gnomad_min":null,"sift_max":null,"metasvm_min":null,"disease_term":null,"group_by":null,"agg_func":null,"limit":null,"wants_schema":false,"target_column":null}

INPUT: "Which variants are Pathogenic in ClinVar but have gnomAD > 1%?"
OUTPUT: {"intent":"acmg_clinvar","needs_hpo":false,"symptoms":[],"chr":null,"start":null,"end":null,"rsid":null,"gene":null,"func":null,"exonic_func":null,"zygosity":null,"in_repeat":null,"clinvar_includes":"Pathogenic","intervar_verdict":null,"acmg_flag":null,"acmg_flag_value":null,"cadd_min":null,"cadd_max":null,"gnomad_max":null,"gnomad_min":0.01,"sift_max":null,"metasvm_min":null,"disease_term":null,"group_by":null,"agg_func":null,"limit":null,"wants_schema":false,"target_column":null}

INPUT: "Which variants have PVS1 met?"
OUTPUT: {"intent":"acmg_clinvar","needs_hpo":false,"symptoms":[],"chr":null,"start":null,"end":null,"rsid":null,"gene":null,"func":null,"exonic_func":null,"zygosity":null,"in_repeat":null,"clinvar_includes":null,"intervar_verdict":null,"acmg_flag":"PVS1","acmg_flag_value":1,"cadd_min":null,"cadd_max":null,"gnomad_max":null,"gnomad_min":null,"sift_max":null,"metasvm_min":null,"disease_term":null,"group_by":null,"agg_func":null,"limit":null,"wants_schema":false,"target_column":null}

INPUT: "Find variants associated with Rett syndrome that InterVar calls Benign."
OUTPUT: {"intent":"disease_link","needs_hpo":false,"symptoms":[],"chr":null,"start":null,"end":null,"rsid":null,"gene":null,"func":null,"exonic_func":null,"zygosity":null,"in_repeat":null,"clinvar_includes":null,"intervar_verdict":"Benign","acmg_flag":null,"acmg_flag_value":null,"cadd_min":null,"cadd_max":null,"gnomad_max":null,"gnomad_min":null,"sift_max":null,"metasvm_min":null,"disease_term":"Rett syndrome","group_by":null,"agg_func":null,"limit":null,"wants_schema":false,"target_column":null}

INPUT: "Count Pathogenic variants per gene; top 5 genes."
OUTPUT: {"intent":"aggregate","needs_hpo":false,"symptoms":[],"chr":null,"start":null,"end":null,"rsid":null,"gene":null,"func":null,"exonic_func":null,"zygosity":null,"in_repeat":null,"clinvar_includes":null,"intervar_verdict":"Pathogenic","acmg_flag":null,"acmg_flag_value":null,"cadd_min":null,"cadd_max":null,"gnomad_max":null,"gnomad_min":null,"sift_max":null,"metasvm_min":null,"disease_term":null,"group_by":"gene","agg_func":"count","limit":5,"wants_schema":false,"target_column":null}

INPUT: "Average CADD for stopgain vs synonymous SNV?"
OUTPUT: {"intent":"aggregate","needs_hpo":false,"symptoms":[],"chr":null,"start":null,"end":null,"rsid":null,"gene":null,"func":null,"exonic_func":null,"zygosity":null,"in_repeat":null,"clinvar_includes":null,"intervar_verdict":null,"acmg_flag":null,"acmg_flag_value":null,"cadd_min":null,"cadd_max":null,"gnomad_max":null,"gnomad_min":null,"sift_max":null,"metasvm_min":null,"disease_term":null,"group_by":"exonic_func","agg_func":"avg_cadd","limit":null,"wants_schema":false,"target_column":null}

INPUT: "I have terrible headaches and easy bruising — any variant explain that?"
OUTPUT: {"intent":"hpo_symptom","needs_hpo":true,"symptoms":["headaches","easy bruising"],"chr":null,"start":null,"end":null,"rsid":null,"gene":null,"func":null,"exonic_func":null,"zygosity":null,"in_repeat":null,"clinvar_includes":null,"intervar_verdict":null,"acmg_flag":null,"acmg_flag_value":null,"cadd_min":null,"cadd_max":null,"gnomad_max":null,"gnomad_min":null,"sift_max":null,"metasvm_min":null,"disease_term":null,"group_by":null,"agg_func":null,"limit":null,"wants_schema":false,"target_column":null}

INPUT: "I have fever and fatigue, what genes are associated?"
OUTPUT: {"intent":"hpo_symptom","needs_hpo":true,"symptoms":["fever","fatigue"],"chr":null,"start":null,"end":null,"rsid":null,"gene":null,"func":null,"exonic_func":null,"zygosity":null,"in_repeat":null,"clinvar_includes":null,"intervar_verdict":null,"acmg_flag":null,"acmg_flag_value":null,"cadd_min":null,"cadd_max":null,"gnomad_max":null,"gnomad_min":null,"sift_max":null,"metasvm_min":null,"disease_term":null,"group_by":null,"agg_func":null,"limit":null,"wants_schema":false,"target_column":null}

INPUT: "What does CADD_phred mean?"
OUTPUT: {"intent":"schema_lookup","needs_hpo":false,"symptoms":[],"chr":null,"start":null,"end":null,"rsid":null,"gene":null,"func":null,"exonic_func":null,"zygosity":null,"in_repeat":null,"clinvar_includes":null,"intervar_verdict":null,"acmg_flag":null,"acmg_flag_value":null,"cadd_min":null,"cadd_max":null,"gnomad_max":null,"gnomad_min":null,"sift_max":null,"metasvm_min":null,"disease_term":null,"group_by":null,"agg_func":null,"limit":null,"wants_schema":true,"target_column":"CADD_phred"}

INPUT: "Explain CADD scores"
OUTPUT: {"intent":"schema_lookup","needs_hpo":false,"symptoms":[],"chr":null,"start":null,"end":null,"rsid":null,"gene":null,"func":null,"exonic_func":null,"zygosity":null,"in_repeat":null,"clinvar_includes":null,"intervar_verdict":null,"acmg_flag":null,"acmg_flag_value":null,"cadd_min":null,"cadd_max":null,"gnomad_max":null,"gnomad_min":null,"sift_max":null,"metasvm_min":null,"disease_term":null,"group_by":null,"agg_func":null,"limit":null,"wants_schema":true,"target_column":"CADD"}

INPUT: "What does VUS mean?"
OUTPUT: {"intent":"schema_lookup","needs_hpo":false,"symptoms":[],"chr":null,"start":null,"end":null,"rsid":null,"gene":null,"func":null,"exonic_func":null,"zygosity":null,"in_repeat":null,"clinvar_includes":null,"intervar_verdict":null,"acmg_flag":null,"acmg_flag_value":null,"cadd_min":null,"cadd_max":null,"gnomad_max":null,"gnomad_min":null,"sift_max":null,"metasvm_min":null,"disease_term":null,"group_by":null,"agg_func":null,"limit":null,"wants_schema":true,"target_column":"VUS"}

INPUT: "What are the ACMG classification tiers?"
OUTPUT: {"intent":"schema_lookup","needs_hpo":false,"symptoms":[],"chr":null,"start":null,"end":null,"rsid":null,"gene":null,"func":null,"exonic_func":null,"zygosity":null,"in_repeat":null,"clinvar_includes":null,"intervar_verdict":null,"acmg_flag":null,"acmg_flag_value":null,"cadd_min":null,"cadd_max":null,"gnomad_max":null,"gnomad_min":null,"sift_max":null,"metasvm_min":null,"disease_term":null,"group_by":null,"agg_func":null,"limit":null,"wants_schema":true,"target_column":"ACMG classification tiers"}

INPUT: "What is PVS1 in ACMG criteria?"
OUTPUT: {"intent":"schema_lookup","needs_hpo":false,"symptoms":[],"chr":null,"start":null,"end":null,"rsid":null,"gene":null,"func":null,"exonic_func":null,"zygosity":null,"in_repeat":null,"clinvar_includes":null,"intervar_verdict":null,"acmg_flag":null,"acmg_flag_value":null,"cadd_min":null,"cadd_max":null,"gnomad_max":null,"gnomad_min":null,"sift_max":null,"metasvm_min":null,"disease_term":null,"group_by":null,"agg_func":null,"limit":null,"wants_schema":true,"target_column":"PVS1"}

INPUT: "Are there incidental findings in my report?"
OUTPUT: {"intent":"schema_lookup","needs_hpo":false,"symptoms":[],"chr":null,"start":null,"end":null,"rsid":null,"gene":null,"func":null,"exonic_func":null,"zygosity":null,"in_repeat":null,"clinvar_includes":null,"intervar_verdict":null,"acmg_flag":null,"acmg_flag_value":null,"cadd_min":null,"cadd_max":null,"gnomad_max":null,"gnomad_min":null,"sift_max":null,"metasvm_min":null,"disease_term":null,"group_by":null,"agg_func":null,"limit":null,"wants_schema":true,"target_column":"incidental findings"}

INPUT: "Do I have anything on the ACMG secondary findings list?"
OUTPUT: {"intent":"schema_lookup","needs_hpo":false,"symptoms":[],"chr":null,"start":null,"end":null,"rsid":null,"gene":null,"func":null,"exonic_func":null,"zygosity":null,"in_repeat":null,"clinvar_includes":null,"intervar_verdict":null,"acmg_flag":null,"acmg_flag_value":null,"cadd_min":null,"cadd_max":null,"gnomad_max":null,"gnomad_min":null,"sift_max":null,"metasvm_min":null,"disease_term":null,"group_by":null,"agg_func":null,"limit":null,"wants_schema":true,"target_column":"ACMG secondary findings"}

INPUT: "Summarize my report."
OUTPUT: {"intent":"summary","needs_hpo":false,"symptoms":[],"chr":null,"start":null,"end":null,"rsid":null,"gene":null,"func":null,"exonic_func":null,"zygosity":null,"in_repeat":null,"clinvar_includes":null,"intervar_verdict":null,"acmg_flag":null,"acmg_flag_value":null,"cadd_min":null,"cadd_max":null,"gnomad_max":null,"gnomad_min":null,"sift_max":null,"metasvm_min":null,"disease_term":null,"group_by":null,"agg_func":null,"limit":null,"wants_schema":false,"target_column":null}

Return ONLY the JSON object.
"""


# Answer-generation system prompt — exact interval_02 ANSWER_SYSTEM_PROMPT
ANSWER_SYSTEM_PROMPT = """\
⛔️ ABSOLUTE OUTPUT FORMAT RULE — READ THIS FIRST:
You are outputting to a PATIENT-FACING chat interface.
You MUST ONLY output: plain English sentences, markdown tables (| col |), bullet points, bold (**text**).
You MUST NEVER output:
  - Python code: lung_genes = [...], query = f\"\"\"...\"\"\", import, def, for, while, class
  - SQL queries: SELECT ... FROM ... WHERE ...
  - Code blocks: ```python ... ``` or ```sql ... ``` or ``` ... ```
  - Variable assignments: anything = [...] or anything = {…}
  - Programming syntax of any kind
If you start writing code → STOP immediately → rewrite as plain English sentences.
─────────────────────────────────────────────────────────────────────────────

You are **Genelio**, a clinical-genomics assistant interpreting an \
InterVar-annotated variant report (76,000+ rows annotated per patient — \
most are common population variants).

You receive a user message that contains EITHER (a) a MATCHED RECORDS \
block from a deterministic filter, OR (b) a QUERY CONTEXT block telling \
you to answer from STRUCTURED ANALYSIS instead. Read which one the \
message gives you and follow the rules for that case.

Blocks you may see:

1. **MATCHED RECORDS** — the output of a deterministic Python query \
that the system already ran for the user's question. THIS IS YOUR \
SOURCE OF TRUTH when present. The query handled the filtering / \
aggregation / counting; your job is to render the result in plain \
language and add clinical context.
1b. **QUERY CONTEXT** — appears INSTEAD of MATCHED RECORDS when the \
user's question was too broad for a single deterministic filter \
(e.g. "any disease-causing variants?"). When you see this block, your \
source of truth is the STRUCTURED ANALYSIS block — quote its verdict \
counts and headline-variant list verbatim. **Do NOT say "0 matches" or \
"no variants" in this case** — the report has whatever STRUCTURED \
ANALYSIS shows it has.
2. **SCHEMA REFERENCE** — column definitions; quote verbatim when the \
user asked what a column means.
3. **STRUCTURED ANALYSIS** — whole-report summary (verdict counts, \
ACMG-flag totals, headline-variant list). Always present; primary \
source when QUERY CONTEXT is set.
4. **SESSION HPO PROFILE** — symptoms the user has revealed across \
the chat, resolved to canonical HPO terms.
5. **USER QUESTION** — what the patient actually asked.

Operating rules — non-negotiable:

0. **NEVER fabricate variants, transcripts, or HGVS notations.** The \
MATCHED RECORDS block (when present) is the complete answer to \
filter/lookup questions. If it is empty AND no QUERY CONTEXT block is \
present, say "0 variants" plainly — do NOT invent rows from your \
training prior. The system has 76k rows; if 0 matches are returned and \
the filter was specific, the patient genuinely has 0 matches.

0a. **When QUERY CONTEXT says "no concrete filter — use STRUCTURED \
ANALYSIS":** Answer from STRUCTURED ANALYSIS, not from MATCHED RECORDS \
(it is absent). For "any disease-causing variants?" → quote the \
"Likely pathogenic: N" count and list the headline LP variants. \
Never reply "0 matches" or "no disease-causing variants found" in this \
case — that contradicts the report.

1. **MATCHED RECORDS is the ground truth** (when present). When it \
lists 3 variants, you report EXACTLY those 3 variants — every single \
one, by name. Use a markdown table when there are more than 3. \
For each variant ALWAYS include: gene name, HGVS notation \
(AAChange.refGene field), ClinVar verdict, InterVar verdict, CADD score, \
and zygosity. Do not skip any variant from MATCHED RECORDS.

1a. **Never name the prompt scaffold in your reply.** Do NOT say \
"MATCHED RECORDS shows..." or "the EXECUTOR RESULT..." or "the FILTER \
APPLIED...". Write naturally: "Your report contains 52 variants with \
PVS1 = 1." or "Two variants in your report are classified Pathogenic \
by ClinVar." The patient is not reading the scaffold.

2. **Quote BOTH ClinVar AND InterVar verdicts verbatim for each variant.** \
Every variant entry MUST state the ClinVar column value AND the InterVar \
column value separately. "Conflicting_interpretations_of_pathogenicity" \
must be reported as such, not paraphrased as "Pathogenic". \
Never mention only one classification and ignore the other.

3. **Explain ACMG evidence explicitly** (PVS1, PS, PM, PP, BA1, BS, BP) \
using the SCHEMA REFERENCE. PVS1=1 is a "Very Strong" pathogenic signal \
(null variant in LOF-known gene); BA1=1 is "Stand-Alone Benign" \
(allele freq >5%).

4. **Disease-link questions:** if the executor returned matches with \
OMIM / Orpha annotation, surface those identifiers and the disease names.

5. **HPO-symptom questions:** explain WHICH HPO term mapped to the \
user's symptom, WHICH genes are associated, and which (if any) the \
patient has variants in. List the specific gene names and their variants. \
If the patient has no variants in the relevant genes, say so — \
that's a meaningful clinical finding.

6. **NEVER diagnose, prescribe, or recommend specific treatments.** \
Always close with a recommendation to discuss findings with a physician \
or certified genetic counselor.

7. **ABSOLUTELY NO CODE OUTPUT.** Never write Python, SQL, JavaScript, \
or any programming language. No variable assignments, no lists of gene \
names in code format, no query strings. Output ONLY plain English \
and markdown tables. This is the most important rule.

Formatting:
- Markdown tables when listing >3 variants (columns: Gene, HGVS, ClinVar, InterVar, CADD, Zygosity).
- Bullets for ≤3 variants.
- 🧬 variant · ⚠️ pathogenic · 📝 carrier · 💊 pharmacogenomic.
- Plain-language gloss after every technical term on first use.
"""


# ── Router decision dataclass ──────────────────────────────────────────────────

@dataclass
class RouterDecision:
    intent: str = "other"
    needs_hpo: bool = False
    symptoms: list[str] = field(default_factory=list)

    chr: Optional[str] = None
    start: Optional[int] = None
    end: Optional[int] = None          # genomic range end (coord_lookup)
    rsid: Optional[str] = None
    gene: Optional[str] = None

    func: Optional[str] = None
    exonic_func: Optional[str] = None
    zygosity: Optional[str] = None
    in_repeat: Optional[bool] = None   # True=in repeat, False=NOT in repeat

    clinvar_includes: Optional[str] = None
    intervar_verdict: Optional[str] = None
    acmg_flag: Optional[str] = None
    acmg_flag_value: Optional[int] = None

    cadd_min: Optional[float] = None
    cadd_max: Optional[float] = None   # CADD upper bound
    gnomad_max: Optional[float] = None
    gnomad_min: Optional[float] = None  # gnomAD lower bound (Pathogenic + high freq)
    sift_max: Optional[float] = None    # SIFT score upper bound (lower = more damaging)
    metasvm_min: Optional[float] = None # MetaSVM score lower bound
    disease_term: Optional[str] = None

    group_by: Optional[str] = None
    agg_func: Optional[str] = None
    limit: Optional[int] = None

    wants_schema: bool = False
    target_column: Optional[str] = None

    raw: dict = field(default_factory=dict)


# ── ExecutorResult dataclass ───────────────────────────────────────────────────

@dataclass
class ExecutorResult:
    kind: str                                    # "rows"|"aggregate"|"schema"|"summary"|"empty"
    intent: str
    rows: list[dict[str, Any]] = field(default_factory=list)
    total_matched: int = 0
    aggregate_table: list[dict[str, Any]] = field(default_factory=list)
    aggregate_caption: str = ""
    description: str = ""
    schema_definition: Optional[str] = None
    schema_column: Optional[str] = None


# ── Columns to SELECT for variant rows ───────────────────────────────────────

_VARIANT_COLS = (
    '"Ref.Gene"', "Chr", "Start", "End", "Ref", "Alt",
    '"Func.refGene"', '"ExonicFunc.refGene"', '"AAChange.refGene"',
    '"clinvar: Clinvar"', '"InterVar: InterVar and Evidence"',
    "CADD_phred", "Freq_gnomAD_genome_ALL",
    "Freq_esp6500siv2_all", "Freq_1000g2015aug_all",
    "avsnp147", "Otherinfo",
    "Orpha", "OMIM", "Phenotype_MIM",
)
_SELECT = ", ".join(_VARIANT_COLS)

_PATHOGENIC_WHERE = (
    "("
    "\"clinvar: Clinvar\" LIKE 'clinvar: Pathogenic%' "
    "AND \"clinvar: Clinvar\" NOT LIKE 'clinvar: Conflicting%' "
    "OR \"clinvar: Clinvar\" LIKE 'clinvar: Likely_pathogenic%' "
    "OR \"clinvar: Clinvar\" LIKE 'clinvar: Pathogenic/Likely_pathogenic%' "
    "OR \"InterVar: InterVar and Evidence\" LIKE 'InterVar: Pathogenic%' "
    "OR \"InterVar: InterVar and Evidence\" LIKE 'InterVar: Likely pathogenic%'"
    ")"
)


# ── JSON helper ────────────────────────────────────────────────────────────────

_JSON_BLOCK_RE = re.compile(r"\{[\s\S]*\}")


def _safe_json(text: str) -> dict:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```\s*$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = _JSON_BLOCK_RE.search(text)
        if not m:
            return {}
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            return {}


# ── Valid intents ─────────────────────────────────────────────────────────────

_VALID_INTENTS = frozenset({
    "coord_lookup", "biofilter", "acmg_clinvar", "disease_link",
    "aggregate", "hpo_symptom", "schema_lookup", "summary", "other",
})

# Symptom salvage — "I feel X", "I get X" etc. patterns
_SYMPTOM_SALVAGE_RE = re.compile(
    r"^\s*(?:i\s+(?:sometimes\s+|usually\s+|often\s+|always\s+)?"
    r"(?:feel|am\s+feeling|get|got|experience|experienced|suffer\s+from)"
    r"|i'?m\s+feeling|i'?ve\s+been\s+feeling|feeling)\s+"
    r"((?:[a-z\- ]{3,60}?))(?:[\.,;\?\!]|$| and\b| but\b)",
    re.IGNORECASE,
)
_LOOKUP_DISQ_RE = re.compile(
    r"\b(?:variant|mutation|gene|allele|rs\d{3,}|chr\d|exon|intron|c\.\w|"
    r"p\.\w|ACMG|PVS1|PS\d|PM\d|PP\d|BA1|BS\d|BP\d|CADD|ClinVar|InterVar|"
    r"OMIM|Orpha|pathogenic|benign|likely)\b",
    re.IGNORECASE,
)


def _salvage_symptom(message: str) -> Optional[str]:
    if not message or _LOOKUP_DISQ_RE.search(message):
        return None
    m = _SYMPTOM_SALVAGE_RE.match(message)
    if not m:
        return None
    chunk = m.group(1).strip()
    if not (3 <= len(chunk) <= 60):
        return None
    first = chunk.split()[0] if chunk.split() else ""
    if first and first[0].isupper() and first.isalnum() and len(first) <= 10:
        return None
    return chunk


# Compound symptom splitter
_SYMPTOM_SPLIT_RE = re.compile(
    r"\s*(?:,|;|/| and | or | plus | also | with )\s*", re.IGNORECASE,
)
_LEADING_CONJ_RE = re.compile(r"^(?:and|or|plus|also|with)\s+", re.IGNORECASE)


def _split_symptoms(symptoms: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in symptoms:
        if not raw:
            continue
        for piece in _SYMPTOM_SPLIT_RE.split(raw):
            p = _LEADING_CONJ_RE.sub("", piece.strip().rstrip(".,!?;:").strip())
            if len(p) < 3:
                continue
            key = p.lower()
            if key not in seen:
                seen.add(key)
                out.append(p)
    return out


# ── Stage 1: LLM router ────────────────────────────────────────────────────────

def _call_router_llm(message: str) -> Optional[dict]:
    """Call the LLM with ROUTER_SYSTEM_PROMPT and return parsed JSON or None."""
    try:
        from app.ai.llm_config import get_llm
        llm = get_llm()
        if llm is None or not hasattr(llm, "route"):
            return None
        raw = llm.route(message)
        if not raw:
            return None
        parsed = _safe_json(raw)
        if not parsed:
            return None
        return parsed
    except Exception as e:
        log.warning("Router LLM call failed: %s", e)
        return None


def _parse_decision(parsed: dict) -> RouterDecision:
    def _os(v): return str(v).strip() or None if v is not None else None
    def _oi(v):
        if v is None: return None
        try: return int(v)
        except: return None
    def _of(v):
        if v is None: return None
        try: return float(v)
        except: return None

    # in_repeat is a tri-state: True, False, or None
    _in_repeat = parsed.get("in_repeat")
    if _in_repeat is not None:
        _in_repeat = bool(_in_repeat)

    return RouterDecision(
        intent=_os(parsed.get("intent")) or "other",
        needs_hpo=bool(parsed.get("needs_hpo")),
        symptoms=[s.strip() for s in (parsed.get("symptoms") or [])
                  if isinstance(s, str) and s.strip()],
        chr=_os(parsed.get("chr")),
        start=_oi(parsed.get("start")),
        end=_oi(parsed.get("end")),
        rsid=_os(parsed.get("rsid")),
        gene=_os(parsed.get("gene")),
        func=_os(parsed.get("func")),
        exonic_func=_os(parsed.get("exonic_func")),
        zygosity=_os(parsed.get("zygosity")),
        in_repeat=_in_repeat,
        clinvar_includes=_os(parsed.get("clinvar_includes")),
        intervar_verdict=_os(parsed.get("intervar_verdict")),
        acmg_flag=_os(parsed.get("acmg_flag")),
        acmg_flag_value=_oi(parsed.get("acmg_flag_value")),
        cadd_min=_of(parsed.get("cadd_min")),
        cadd_max=_of(parsed.get("cadd_max")),
        gnomad_max=_of(parsed.get("gnomad_max")),
        gnomad_min=_of(parsed.get("gnomad_min")),
        sift_max=_of(parsed.get("sift_max")),
        metasvm_min=_of(parsed.get("metasvm_min")),
        disease_term=_os(parsed.get("disease_term")),
        group_by=_os(parsed.get("group_by")),
        agg_func=_os(parsed.get("agg_func")),
        limit=_oi(parsed.get("limit")),
        wants_schema=bool(parsed.get("wants_schema")),
        target_column=_os(parsed.get("target_column")),
        raw=parsed,
    )


def _normalize_decision(d: RouterDecision, message: str) -> RouterDecision:
    """Remap invalid / fuzzy intents and apply symptom salvage."""
    # Symptom salvage when router missed "I feel X"
    if not d.symptoms and message:
        salvaged = _salvage_symptom(message)
        if salvaged:
            log.warning("router: salvaged symptom %r from message", salvaged)
            d.symptoms = [salvaged]
            d.intent = "hpo_symptom"
            d.needs_hpo = True
            return d

    if d.intent not in _VALID_INTENTS:
        if d.rsid or d.chr is not None:
            d.intent = "coord_lookup"
        elif d.symptoms:
            d.intent = "hpo_symptom"; d.needs_hpo = True
        elif d.gene:
            d.intent = "biofilter"
        else:
            d.intent = "other"
    elif d.intent == "other":
        if d.rsid or d.chr is not None:
            d.intent = "coord_lookup"
        elif d.symptoms:
            d.intent = "hpo_symptom"; d.needs_hpo = True
        elif d.gene:
            d.intent = "biofilter"

    return d


def classify(message: str) -> RouterDecision:
    """Stage 1 — LLM router → RouterDecision."""
    parsed = _call_router_llm(message) or {}
    d = _parse_decision(parsed)
    d = _normalize_decision(d, message)
    log.info("router: intent=%s gene=%r rsid=%r symptoms=%r",
             d.intent, d.gene, d.rsid, d.symptoms)
    return d


# ── Stage 1.5: HPO resolution ─────────────────────────────────────────────────

def resolve_hpo(symptoms: list[str], session_profile: dict) -> dict:
    """Resolve symptoms → HPO terms → candidate genes; merge into session profile.

    Strategy:
      1. Local HPO TSV data (fast, works offline)
      2. HPO API client fallback for organ/body-system terms that the TSV misses

    session_profile is a plain dict (persisted by the caller):
      {"hpo_terms": [...], "candidate_genes": [...]}

    Returns a summary dict: {added, unresolved, total_terms, total_candidate_genes}
    """
    from app.hpo.resolver import resolve as hpo_resolve

    symptoms = _split_symptoms(symptoms)
    seen = {t.get("input_text", "").lower()
            for t in (session_profile.get("hpo_terms") or [])}

    added_this_turn: list[dict] = []
    unresolved: list[str] = []

    for phrase in symptoms:
        key = phrase.lower().strip()
        if key in seen:
            continue
        seen.add(key)

        match = hpo_resolve(phrase)
        if not match.hpo_id:
            # ── Fallback: try HPO API client (organ/body-system terms) ────────
            genes_from_api: list[str] = []
            api_terms: list[tuple] = []
            try:
                from app.hpo.api_client import resolve_organ_query
                genes_from_api, api_terms = resolve_organ_query(phrase)
            except Exception as _api_err:
                log.debug("HPO API client failed for %r: %s", phrase, _api_err)

            if genes_from_api and api_terms:
                hp_id, term_name, gene_count = api_terms[0]
                term_entry = {
                    "input_text":  phrase,
                    "hpo_id":      hp_id,
                    "name":        term_name,
                    "confidence":  0.75,
                    "matched_via": "hpo_api",
                    "gene_count":  gene_count,
                }
                added_this_turn.append(term_entry)
                session_profile.setdefault("hpo_terms", []).append(term_entry)
                existing = set(session_profile.get("candidate_genes") or [])
                existing.update(genes_from_api)
                session_profile["candidate_genes"] = sorted(existing)
                log.info("HPO API resolved %r → %s (%d genes)", phrase, term_name, len(genes_from_api))
                continue
            # Still unresolved
            unresolved.append(phrase)
            continue

        term_entry = {
            "input_text":  phrase,
            "hpo_id":      match.hpo_id,
            "name":        match.name,
            "confidence":  match.confidence,
            "matched_via": match.matched_via,
            "gene_count":  len(match.genes),
        }
        added_this_turn.append(term_entry)
        session_profile.setdefault("hpo_terms", []).append(term_entry)
        existing = set(session_profile.get("candidate_genes") or [])
        existing.update(match.genes)
        session_profile["candidate_genes"] = sorted(existing)

    return {
        "added":                  added_this_turn,
        "unresolved":             unresolved,
        "total_terms":            len(session_profile.get("hpo_terms") or []),
        "total_candidate_genes":  len(session_profile.get("candidate_genes") or []),
    }


# ── Stage 2: Deterministic SQL executor ──────────────────────────────────────

_MAX_ROWS = 30     # cap on rows passed to the answer LLM


def _run_sql(sql: str, db: Session, limit: int = _MAX_ROWS) -> list[dict[str, Any]]:
    """Execute a SQL SELECT and return rows as list of dicts."""
    try:
        result = db.execute(sa_text(sql))
        keys = list(result.keys())
        rows = [dict(zip(keys, row)) for row in result.fetchmany(limit)]
        return rows
    except Exception as e:
        log.warning("executor SQL failed: %s\nSQL: %s", e, sql[:300])
        return []


def _build_intervar_verdict_clause(verdict: str) -> str:
    v = verdict.strip().lower()
    col = '"InterVar: InterVar and Evidence"'
    if v in ("pathogenic",):
        return (f"{col} LIKE 'InterVar: Pathogenic%' "
                f"AND {col} NOT LIKE 'InterVar: Likely pathogenic%'")
    if v in ("likely pathogenic", "likely_pathogenic"):
        return f"{col} LIKE 'InterVar: Likely pathogenic%'"
    if v in ("uncertain significance", "uncertain_significance", "vus"):
        return f"{col} LIKE 'InterVar: Uncertain%'"
    if v in ("likely benign", "likely_benign"):
        return f"{col} LIKE 'InterVar: Likely benign%'"
    if v in ("benign",):
        return (f"{col} LIKE 'InterVar: Benign%' "
                f"AND {col} NOT LIKE 'InterVar: Likely benign%'")
    # Default: substring match
    return f"{col} LIKE '%{verdict}%'"


def _build_clinvar_clause(includes: str) -> str:
    col = '"clinvar: Clinvar"'
    s = includes.strip().lower()
    if s in ("pathogenic",):
        return (f"{col} LIKE 'clinvar: Pathogenic%' "
                f"AND {col} NOT LIKE 'clinvar: Conflicting%' "
                f"AND {col} NOT LIKE 'clinvar: Pathogenic/Likely_pathogenic%'")
    if s in ("likely pathogenic", "likely_pathogenic"):
        return (f"({col} LIKE 'clinvar: Likely_pathogenic%' "
                f"OR {col} LIKE 'clinvar: Pathogenic/Likely_pathogenic%')")
    if s in ("benign",):
        return (f"{col} LIKE 'clinvar: Benign%' "
                f"AND {col} NOT LIKE 'clinvar: Likely_benign%'")
    if s in ("likely benign", "likely_benign"):
        return f"{col} LIKE 'clinvar: Likely_benign%'"
    if s in ("conflicting",):
        return f"{col} LIKE 'clinvar: Conflicting%'"
    # Substring fallback
    return f"LOWER({col}) LIKE LOWER('%{includes}%')"


def execute(decision: RouterDecision, db: Session, session_profile: dict) -> ExecutorResult:
    """Stage 2 — map RouterDecision to SQL, execute, apply pathogenicity buckets."""
    intent = decision.intent
    cap = min(decision.limit or _MAX_ROWS, 100)

    # ── coord_lookup ──────────────────────────────────────────────────────────
    if intent == "coord_lookup":
        if decision.rsid:
            sql = (f"SELECT {_SELECT} FROM variants "
                   f"WHERE avsnp147 = '{decision.rsid.strip()}' LIMIT 20;")
            desc = f"rsID = {decision.rsid}"
        elif decision.chr is not None and decision.start is not None:
            if decision.end is not None and decision.end != decision.start:
                # Genomic range query
                sql = (f"SELECT {_SELECT} FROM variants "
                       f"WHERE Chr = '{decision.chr}' "
                       f"AND Start >= {decision.start} AND End <= {decision.end} "
                       f"LIMIT 50;")
                desc = f"chr{decision.chr}:{decision.start}-{decision.end}"
            else:
                sql = (f"SELECT {_SELECT} FROM variants "
                       f"WHERE Chr = '{decision.chr}' AND Start = {decision.start} "
                       f"LIMIT 20;")
                desc = f"chr{decision.chr}:{decision.start}"
        else:
            return ExecutorResult(kind="empty", intent=intent,
                                  description="coord_lookup with no rsid/chr/pos")
        rows = _run_sql(sql, db)
        return ExecutorResult(
            kind="rows" if rows else "empty",
            intent=intent, rows=rows,
            total_matched=len(rows), description=desc,
        )

    # ── aggregate ─────────────────────────────────────────────────────────────
    if intent == "aggregate":
        gb = decision.group_by or "gene"
        agg = decision.agg_func or "count"

        gb_col = {
            "gene": '"Ref.Gene"',
            "exonic_func": '"ExonicFunc.refGene"',
            "clinvar": '"clinvar: Clinvar"',
            "intervar": '"InterVar: InterVar and Evidence"',
            "chr": "Chr",
        }.get(gb, '"Ref.Gene"')

        where_parts: list[str] = []
        if decision.clinvar_includes:
            where_parts.append(_build_clinvar_clause(decision.clinvar_includes))
        if decision.intervar_verdict:
            where_parts.append(_build_intervar_verdict_clause(decision.intervar_verdict))
        if decision.gene:
            where_parts.append(f'"Ref.Gene" = \'{decision.gene}\'')
        if decision.cadd_min is not None:
            where_parts.append(f"CADD_phred > {decision.cadd_min}")

        where = f"WHERE {' AND '.join(where_parts)}" if where_parts else ""

        if agg == "avg_cadd":
            # Need to add CADD IS NOT NULL filter; use AND if WHERE already present
            cadd_null_clause = (
                "AND CADD_phred IS NOT NULL" if where else "WHERE CADD_phred IS NOT NULL"
            )
            sql = (f"SELECT {gb_col} AS label, "
                   f"ROUND(AVG(CADD_phred), 2) AS avg_cadd, COUNT(*) AS n "
                   f"FROM variants {where} {cadd_null_clause} "
                   f"GROUP BY {gb_col} ORDER BY avg_cadd DESC LIMIT {cap};")
            caption = f"average CADD_phred grouped by {gb}"
        else:
            sql = (f"SELECT {gb_col} AS label, COUNT(*) AS count "
                   f"FROM variants {where} "
                   f"GROUP BY {gb_col} ORDER BY count DESC LIMIT {cap};")
            caption = f"count grouped by {gb}"

        rows = _run_sql(sql, db, cap)
        return ExecutorResult(
            kind="aggregate" if rows else "empty",
            intent=intent,
            aggregate_table=rows,
            aggregate_caption=caption,
            total_matched=len(rows),
            description=caption,
        )

    # ── summary ───────────────────────────────────────────────────────────────
    if intent == "summary":
        # Build a compact summary: verdict distribution + top pathogenic
        sql_counts = (
            'SELECT SUBSTR("InterVar: InterVar and Evidence", 1, 50) AS verdict, '
            'COUNT(*) AS n FROM variants '
            'GROUP BY SUBSTR("InterVar: InterVar and Evidence", 1, 50) '
            'ORDER BY n DESC LIMIT 10;'
        )
        verdict_rows = _run_sql(sql_counts, db, 10)

        sql_path = (
            f'SELECT {_SELECT} FROM variants '
            f'WHERE {_PATHOGENIC_WHERE} '
            f'ORDER BY CADD_phred DESC LIMIT 10;'
        )
        path_rows = _run_sql(sql_path, db, 10)
        path_rows = rank_rows(path_rows, 10)

        agg_table = verdict_rows
        rows = path_rows
        return ExecutorResult(
            kind="summary",
            intent=intent,
            rows=rows,
            aggregate_table=agg_table,
            aggregate_caption="InterVar verdict distribution (top 10 categories)",
            total_matched=sum(r.get("n", 0) for r in verdict_rows),
            description="whole-report summary",
        )

    # ── schema_lookup ─────────────────────────────────────────────────────────
    if intent == "schema_lookup":
        col = decision.target_column or ""
        defn = _COLUMN_REFERENCE.get(col)
        if defn is None:
            for k, v in _COLUMN_REFERENCE.items():
                if k.lower() == col.lower():
                    defn = v
                    col = k
                    break
        return ExecutorResult(
            kind="schema",
            intent=intent,
            schema_column=col,
            schema_definition=defn or f"(no definition found for column '{col}')",
        )

    # ── biofilter / acmg_clinvar / disease_link ────────────────────────────────
    if intent in ("biofilter", "acmg_clinvar", "disease_link"):
        where_parts: list[str] = []

        if decision.gene:
            where_parts.append(f'"Ref.Gene" = \'{decision.gene}\'')
        if decision.func:
            where_parts.append(f'"Func.refGene" = \'{decision.func}\'')
        if decision.exonic_func:
            where_parts.append(
                f'"ExonicFunc.refGene" LIKE \'%{decision.exonic_func}%\''
            )
        if decision.zygosity:
            # Our DB stores zygosity in Otherinfo column (het/hom)
            zyg_map = {"het": "het", "hom": "hom", "hemi": "hemi"}
            z = zyg_map.get(decision.zygosity.lower(), decision.zygosity)
            where_parts.append(f"Otherinfo = '{z}'")
        if decision.cadd_min is not None:
            where_parts.append(f"CADD_phred > {decision.cadd_min}")
        if decision.cadd_max is not None:
            where_parts.append(f"CADD_phred < {decision.cadd_max}")
        if decision.gnomad_max is not None:
            where_parts.append(
                f"(Freq_gnomAD_genome_ALL IS NULL "
                f"OR Freq_gnomAD_genome_ALL <= {decision.gnomad_max})"
            )
        if decision.gnomad_min is not None:
            # gnomad_min: variant must be present in gnomAD AND above threshold
            where_parts.append(
                f"(Freq_gnomAD_genome_ALL IS NOT NULL "
                f"AND Freq_gnomAD_genome_ALL >= {decision.gnomad_min})"
            )
        # in_repeat filter — uses rmsk column if present in the DB schema.
        # The column is silently ignored if it doesn't exist (graceful degradation).
        # We attach the filter and let _run_sql catch the SQLite "no such column" error,
        # then fall back to a query without the in_repeat constraint.
        _in_repeat_clause: Optional[str] = None
        if decision.in_repeat is not None:
            if decision.in_repeat is False:
                _in_repeat_clause = "(rmsk IS NULL OR rmsk = '' OR rmsk = '.')"
            else:
                _in_repeat_clause = "(rmsk IS NOT NULL AND rmsk != '' AND rmsk != '.')"
        if decision.clinvar_includes:
            cv_clause = _build_clinvar_clause(decision.clinvar_includes)
            # For acmg_clinvar with pathogenic filter and no explicit intervar_verdict,
            # also include InterVar-pathogenic variants (OR logic) so we don't miss
            # variants that are Pathogenic/LP in InterVar but absent/conflicting in ClinVar.
            if (intent == "acmg_clinvar"
                    and not decision.intervar_verdict
                    and "pathogenic" in decision.clinvar_includes.lower()):
                _cv_lower = decision.clinvar_includes.lower()
                _iv_equiv = "Likely pathogenic" if "likely" in _cv_lower else "Pathogenic"
                iv_clause = _build_intervar_verdict_clause(_iv_equiv)
                where_parts.append(f"({cv_clause} OR {iv_clause})")
            else:
                where_parts.append(cv_clause)
        if decision.intervar_verdict:
            where_parts.append(_build_intervar_verdict_clause(decision.intervar_verdict))
        if decision.acmg_flag:
            flag = decision.acmg_flag
            val = decision.acmg_flag_value if decision.acmg_flag_value is not None else 1
            if flag in ("PVS1", "BA1", "PM1", "PM2", "PP3"):
                flag_val = "YES" if val == 1 else "NO"
                where_parts.append(f'"{flag}" = \'{flag_val}\'')
            else:
                # For PS/PM/PP/BS/BP groups — find any sub-criterion fired
                flag_val = "YES" if val == 1 else "NO"
                where_parts.append(f'"{flag}" = \'{flag_val}\'')
        if decision.disease_term:
            t = decision.disease_term.replace("'", "''")
            where_parts.append(
                f"(Orpha LIKE '%{t}%' OR OMIM LIKE '%{t}%' "
                f"OR Phenotype_MIM LIKE '%{t}%')"
            )

        if not where_parts and _in_repeat_clause is None:
            # No filters → show top pathogenic
            where_parts.append(_PATHOGENIC_WHERE)

        # Build the main query (possibly with in_repeat clause)
        effective_parts = where_parts[:]
        if _in_repeat_clause:
            effective_parts.append(_in_repeat_clause)

        where = "WHERE " + " AND ".join(effective_parts)
        sql = (f"SELECT {_SELECT} FROM variants "
               f"{where} ORDER BY CADD_phred DESC LIMIT {cap};")

        rows = _run_sql(sql, db, cap)
        # If in_repeat filter caused a SQL error (column absent), retry without it
        if not rows and _in_repeat_clause and where_parts:
            log.info("in_repeat filter failed or no rows — retrying without repeat constraint")
            where_fallback = "WHERE " + " AND ".join(where_parts)
            sql_fallback = (f"SELECT {_SELECT} FROM variants "
                            f"{where_fallback} ORDER BY CADD_phred DESC LIMIT {cap};")
            rows = _run_sql(sql_fallback, db, cap)

        # Apply pathogenicity filter for acmg_clinvar only.
        # disease_link intentionally shows ALL variants (Benign included) so the
        # user can see which genes they have variants in — even if all are Benign,
        # that's a meaningful clinical finding (e.g. "you have variants in MODY
        # genes but none are pathogenic").
        explicit_non_path = bool(
            decision.intervar_verdict and decision.intervar_verdict.lower() in
            ("benign", "likely benign", "uncertain significance", "uncertain")
        ) or bool(
            decision.clinvar_includes and decision.clinvar_includes.lower() in
            ("benign", "likely_benign")
        )
        if intent == "acmg_clinvar" and not explicit_non_path:
            from app.ai.pathogenicity import is_clinically_interesting
            rows = [r for r in rows if is_clinically_interesting(r)]

        rows = rank_rows(rows, min(cap, _MAX_ROWS))
        desc = _describe_filter(decision)
        return ExecutorResult(
            kind="rows" if rows else "empty",
            intent=intent, rows=rows,
            total_matched=len(rows), description=desc,
        )

    # ── hpo_symptom ───────────────────────────────────────────────────────────
    if intent == "hpo_symptom":
        genes = session_profile.get("candidate_genes") or []
        if not genes:
            return ExecutorResult(kind="empty", intent=intent,
                                  description="HPO resolved 0 candidate genes")
        cap_genes = genes[:200]
        gene_list = ", ".join(f"'{g}'" for g in cap_genes)
        sql = (
            f"SELECT {_SELECT} FROM variants "
            f"WHERE \"Ref.Gene\" IN ({gene_list}) "
            f"AND {_PATHOGENIC_WHERE} "
            f"ORDER BY CADD_phred DESC LIMIT 50;"
        )
        rows = _run_sql(sql, db, 50)
        rows = rank_rows(rows, _MAX_ROWS)
        return ExecutorResult(
            kind="rows" if rows else "empty",
            intent=intent, rows=rows,
            total_matched=len(rows),
            description=(
                f"HPO-derived genes ({len(genes)}) "
                "intersected with pathogenic variants"
            ),
        )

    # ── other ─────────────────────────────────────────────────────────────────
    return ExecutorResult(kind="empty", intent=intent,
                          description="intent=other — no filter applied")


def _describe_filter(d: RouterDecision) -> str:
    parts = []
    if d.gene: parts.append(f"gene={d.gene}")
    if d.func: parts.append(f"func={d.func}")
    if d.exonic_func: parts.append(f"exonic_func={d.exonic_func}")
    if d.zygosity: parts.append(f"zygosity={d.zygosity}")
    if d.in_repeat is not None:
        parts.append(f"in_repeat={'yes' if d.in_repeat else 'no'}")
    if d.clinvar_includes: parts.append(f"clinvar~'{d.clinvar_includes}'")
    if d.intervar_verdict: parts.append(f"intervar='{d.intervar_verdict}'")
    if d.acmg_flag:
        parts.append(f"{d.acmg_flag}={d.acmg_flag_value or 1}")
    if d.cadd_min is not None: parts.append(f"CADD>{d.cadd_min}")
    if d.cadd_max is not None: parts.append(f"CADD<{d.cadd_max}")
    if d.gnomad_max is not None: parts.append(f"gnomAD<={d.gnomad_max}")
    if d.gnomad_min is not None: parts.append(f"gnomAD>={d.gnomad_min}")
    if d.sift_max is not None: parts.append(f"SIFT<={d.sift_max}")
    if d.metasvm_min is not None: parts.append(f"MetaSVM>={d.metasvm_min}")
    if d.disease_term: parts.append(f"disease~'{d.disease_term}'")
    return ", ".join(parts) or "no filter"


# ── Stage 3: Answer renderer ──────────────────────────────────────────────────

def render_executor(result: ExecutorResult) -> str:
    """Convert ExecutorResult to a compact text block for the answer LLM."""
    if result.kind == "empty":
        return (
            f"MATCHED RECORDS — 0 variants found.\n"
            f"Filter applied: {result.description}\n"
        )
    if result.kind == "rows":
        head = (
            f"MATCHED RECORDS — {result.total_matched} variant(s) "
            f"(showing top {len(result.rows)} ranked by evidence tier then CADD).\n"
            f"Filter: {result.description}\n\n"
        )
        lines = []
        for row in result.rows:
            bucket_label = row.get("_bucket_label", "")
            gene = row.get("Ref.Gene", "?")
            aac  = (row.get("AAChange.refGene") or "").split(",")[0].strip()
            func = row.get("ExonicFunc.refGene") or row.get("Func.refGene") or "?"
            lines.append(
                f"🧬 **{gene}** · {aac or '(no HGVS)'} · {func}\n"
                f"   Evidence tier: {bucket_label}\n"
                f"   chr{row.get('Chr')}:{row.get('Start')} {row.get('Ref')}>({row.get('Alt')})\n"
                f"   ClinVar: {row.get('clinvar: Clinvar', 'N/A')}\n"
                f"   InterVar: {(row.get('InterVar: InterVar and Evidence') or 'N/A')[:60]}\n"
                f"   CADD: {row.get('CADD_phred', 'N/A')} | "
                f"gnomAD: {row.get('Freq_gnomAD_genome_ALL', 'N/A')} | "
                f"rsID: {row.get('avsnp147', 'N/A')}\n"
                + (f"   Disease: {row.get('Orpha') or row.get('Phenotype_MIM') or row.get('OMIM') or ''}\n"
                   if (row.get('Orpha') or row.get('Phenotype_MIM') or row.get('OMIM')) else "")
            )
        return head + "\n".join(lines)

    if result.kind == "aggregate":
        head = f"MATCHED RECORDS — {result.aggregate_caption}.\n"
        if not result.aggregate_table:
            return head + "(no rows after filter)"
        cols = list(result.aggregate_table[0].keys())
        tbl = ["| " + " | ".join(cols) + " |",
               "| " + " | ".join(["---"] * len(cols)) + " |"]
        for row in result.aggregate_table:
            tbl.append("| " + " | ".join(str(row.get(c, "")) for c in cols) + " |")
        return head + "\n".join(tbl)

    if result.kind == "schema":
        return (
            f"MATCHED RECORDS — schema lookup for **{result.schema_column}**:\n"
            f"{result.schema_definition}\n"
        )
    if result.kind == "summary":
        # Render verdict distribution + top pathogenic rows
        head = "MATCHED RECORDS — whole-report summary requested.\n\n"
        verdict_text = ""
        if result.aggregate_table:
            verdict_text = "InterVar verdict distribution:\n"
            for row in result.aggregate_table[:10]:
                verdict_text += f"  {row.get('verdict', '?')}: {row.get('n', 0)}\n"
            verdict_text += "\n"
        top_text = ""
        if result.rows:
            top_text = f"Top {len(result.rows)} high-priority variants:\n"
            for row in result.rows:
                gene = row.get("Ref.Gene", "?")
                cv = row.get("clinvar: Clinvar", "N/A")
                iv = (row.get("InterVar: InterVar and Evidence") or "N/A")[:50]
                cadd = row.get("CADD_phred", "N/A")
                bucket_label = row.get("_bucket_label", "")
                top_text += (
                    f"  🧬 **{gene}** · ClinVar: {cv} · InterVar: {iv}\n"
                    f"       Evidence tier: {bucket_label} · CADD: {cadd}\n"
                )
        return head + verdict_text + top_text

    return f"MATCHED RECORDS — kind={result.kind}"


# ── Safety tags ───────────────────────────────────────────────────────────────

_SAFETY_TAG: dict[str, str] = {
    "coord_lookup":  "general_disclaimer",
    "biofilter":     "counselor_referral",
    "acmg_clinvar":  "counselor_referral",
    "disease_link":  "counselor_referral",
    "aggregate":     "general_disclaimer",
    "hpo_symptom":   "counselor_referral",
    "schema_lookup": "general_disclaimer",
    "summary":       "general_disclaimer",
    "other":         "general_disclaimer",
}

_DISCLAIMERS: dict[str, str] = {
    "counselor_referral": (
        "\n\n— *These findings are educational, not a diagnosis. Please "
        "discuss with a certified genetic counselor or your physician "
        "before acting on any of them.*"
    ),
    "general_disclaimer": (
        "\n\n— *Genomic information should be interpreted by a qualified "
        "clinician. This response is for educational purposes only.*"
    ),
}


def apply_safety_tag(answer: str, intent: str) -> str:
    tag = _SAFETY_TAG.get(intent, "general_disclaimer")
    if any(p in answer.lower() for p in (
        "genetic counselor", "discuss with a", "consult your",
        "discuss these findings", "educational only",
    )):
        return answer
    return answer + _DISCLAIMERS[tag]


# ── Schema reference (subset) ─────────────────────────────────────────────────

_COLUMN_REFERENCE: dict[str, str] = {
    "Ref.Gene": "HGNC gene symbol — the gene the variant falls in.",
    "Chr": "Chromosome (no 'chr' prefix: '1', 'X', 'Y', 'MT').",
    "Start": "1-based genomic start position.",
    "End": "1-based genomic end position.",
    "Ref": "Reference allele.",
    "Alt": "Alternate (variant) allele.",
    "Func.refGene": "Functional region: exonic, intronic, UTR3, UTR5, splicing, intergenic, etc.",
    "ExonicFunc.refGene": (
        "Exonic function: nonsynonymous SNV (missense), synonymous SNV (silent), "
        "stopgain, stoploss, frameshift deletion, frameshift insertion, "
        "nonframeshift deletion, nonframeshift insertion, splicing."
    ),
    "AAChange.refGene": "HGVS notation: GENE:NM_XXXX:exon:c.XX:p.XX.",
    "clinvar: Clinvar": (
        "ClinVar clinical significance. Prefix 'clinvar: '. "
        "Values: 'clinvar: Pathogenic', 'clinvar: Likely_pathogenic', "
        "'clinvar: Conflicting_interpretations_of_pathogenicity', 'clinvar: Benign', "
        "'clinvar: UNK', etc."
    ),
    "InterVar: InterVar and Evidence": (
        "InterVar algorithmic ACMG/AMP 2015 classification. Prefix 'InterVar: '. "
        "Values: 'InterVar: Pathogenic PVS1=1, ...', 'InterVar: Likely pathogenic ...', "
        "'InterVar: Uncertain significance ...', 'InterVar: Likely benign ...', 'InterVar: Benign ...'."
    ),
    "CADD_phred": (
        "CADD Phred-scaled deleteriousness score. "
        "≥10 = top 10% most deleterious; ≥20 = top 1%; ≥30 = top 0.1%."
    ),
    "Freq_gnomAD_genome_ALL": (
        "gnomAD allele frequency across all populations. "
        "NULL = not observed in gnomAD (supports pathogenicity via PM2). "
        ">0.05 → BA1 (stand-alone benign)."
    ),
    "avsnp147": "dbSNP rsID (e.g. rs189107123). NULL = novel variant.",
    "Otherinfo": "Zygosity: 'het' (heterozygous) or 'hom' (homozygous).",
    "PVS1": (
        "**PVS1 — ACMG Pathogenic Very Strong 1**\n\n"
        "Triggered for **null (loss-of-function) variants** in genes where LOF is a known disease mechanism:\n"
        "• Stopgain (nonsense) mutations — premature stop codon\n"
        "• Frameshift deletions/insertions — reading-frame shift\n"
        "• Canonical splice-site disruptions (±1, ±2 bp from exon boundary)\n\n"
        "PVS1 combining rules:\n"
        "• PVS1 alone → Likely Pathogenic\n"
        "• PVS1 + ≥1 PS criterion → Pathogenic\n"
        "• PVS1 + ≥2 PM criteria → Pathogenic\n"
        "• PVS1 + 1 PM + 1 PP → Pathogenic\n"
        "• PVS1 + ≥2 PP → Pathogenic\n\n"
        "PVS1 is the strongest single pathogenic criterion in the ACMG/AMP 2015 guidelines."
    ),
    "PM1": "ACMG PM1: Moderate pathogenic — located in mutational hotspot or critical functional domain with no benign variation.",
    "PM2": "ACMG PM2: Moderate pathogenic — absent or extremely low frequency in gnomAD (recessive: <0.001, dominant: absent).",
    "PP3": "ACMG PP3: Supporting pathogenic — multiple computational predictors (CADD, SIFT, PolyPhen, MetaSVM) agree the variant is damaging.",
    "BA1": (
        "**BA1 — ACMG Benign Stand-Alone 1**\n\n"
        "Triggered when **gnomAD allele frequency > 5%** in any population.\n"
        "A single BA1 flag overrides ALL pathogenic evidence and classifies the variant as **Benign**.\n\n"
        "This reflects that variants common in the general population are extremely unlikely to cause rare Mendelian disease."
    ),
    "BS1": "ACMG BS1: Benign Strong — allele frequency greater than expected for the disorder (1–5% in gnomAD).",
    "BP4": "ACMG BP4: Benign Supporting — multiple computational tools predict benign effect (CADD < 10, SIFT tolerated, PolyPhen benign).",
    "VUS": (
        "**VUS — Variant of Uncertain Significance**\n\n"
        "A genetic variant that cannot yet be classified as either pathogenic or benign.\n"
        "VUS is assigned when:\n"
        "• Evidence is conflicting (some pathogenic, some benign signals)\n"
        "• Data are too limited to determine pathogenicity\n"
        "• The variant is novel with no published functional or clinical data\n\n"
        "VUS variants require additional evidence to be reclassified:\n"
        "• Functional studies (cell/animal models)\n"
        "• Family segregation studies\n"
        "• Accumulation of cases in affected populations\n\n"
        "Stored as 'InterVar: Uncertain significance' in the InterVar column."
    ),
    "CADD": (
        "**CADD — Combined Annotation-Dependent Depletion**\n\n"
        "Phred-scaled deleteriousness score integrating 60+ genomic annotations:\n"
        "• CADD ≥ 10 → top 10% most deleterious variants\n"
        "• CADD ≥ 20 → top 1% (likely damaging) — supports ACMG PP3\n"
        "• CADD ≥ 30 → top 0.1% (highly damaging)\n"
        "• CADD ≥ 40 → extremely deleterious\n"
        "• CADD < 10 → supports ACMG BP4 (benign supporting)\n\n"
        "CADD scores both coding and non-coding variants. Stored as CADD_phred in the database."
    ),
    "ACMG classification tiers": (
        "**ACMG/AMP 2015 Variant Classification — 5 Tiers**\n\n"
        "1. **Pathogenic** — strong evidence of disease causation. Requires specific combinations of "
        "PVS1, PS, PM, PP criteria (e.g., PVS1 + ≥1 PS; or ≥2 PS).\n\n"
        "2. **Likely Pathogenic** — >90% probability of disease causation. Requires e.g. PVS1 alone; "
        "or 1 PS + 1–2 PM; or ≥3 PM.\n\n"
        "3. **Uncertain Significance (VUS)** — insufficient or conflicting evidence. Cannot classify "
        "as pathogenic or benign. Most clinically challenging tier.\n\n"
        "4. **Likely Benign** — >90% probability of NOT being disease-causing. Requires e.g. "
        "1 BS + 1 BP; or ≥2 BP.\n\n"
        "5. **Benign** — not disease-causing. Requires BA1 (stand-alone, gnomAD AF > 5%) or ≥2 BS.\n\n"
        "Classification uses 28 evidence codes: PVS1 (Very Strong Pathogenic), PS1-4 (Strong Pathogenic), "
        "PM1-6 (Moderate Pathogenic), PP1-5 (Supporting Pathogenic), BA1 (Stand-Alone Benign), "
        "BS1-4 (Strong Benign), BP1-7 (Supporting Benign)."
    ),
    "ACMG": (
        "**ACMG/AMP 2015 Guidelines** — American College of Medical Genetics variant classification framework.\n\n"
        "5 classification tiers: Pathogenic | Likely Pathogenic | Uncertain Significance (VUS) | Likely Benign | Benign\n\n"
        "28 evidence codes:\n"
        "• **Pathogenic:** PVS1 (Very Strong), PS1-4 (Strong), PM1-6 (Moderate), PP1-5 (Supporting)\n"
        "• **Benign:** BA1 (Stand-Alone), BS1-4 (Strong), BP1-7 (Supporting)\n\n"
        "Key criteria:\n"
        "• PVS1 — null variant (stopgain, frameshift, splice) in LOF gene\n"
        "• BA1 — gnomAD AF > 5% (stand-alone benign)\n"
        "• PM2 — absent from gnomAD (moderate pathogenic)\n"
        "• PP3 — multiple computational tools predict damaging\n\n"
        "The InterVar algorithm applies these rules automatically to each variant."
    ),
    "Orpha": "Orphanet rare-disease annotation: OrphaNum|DiseaseName|Prevalence|Inheritance|Onset|OMIM.",
    "OMIM": "OMIM gene ID(s).",
    "Phenotype_MIM": "OMIM phenotype MIM number(s) for the gene.",

    # Secondary / incidental findings
    "secondary findings": (
        "**ACMG Secondary Findings (SF v3.2 — 81 genes)**\n\n"
        "The ACMG recommends reporting pathogenic/likely pathogenic variants in 81 actionable genes "
        "even when not the primary reason for testing.\n\n"
        "**Key gene categories:**\n"
        "• **Hereditary Cancer:** BRCA1, BRCA2, MLH1, MSH2, MSH6, PMS2, TP53, STK11, PALB2\n"
        "• **Cardiac arrhythmia:** KCNQ1, KCNH2, SCN5A, RYR2, CACNA1S\n"
        "• **Cardiomyopathy:** MYBPC3, MYH7, MYH11, TNNT2, TPM1, LMNA\n"
        "• **Aortic disease/Marfan:** FBN1, FBN2, TGFBR1, TGFBR2, ACTA2, MYH11, SMAD3\n"
        "• **Familial hypercholesterolaemia:** LDLR, APOB, PCSK9\n"
        "• **Others:** MUTYH, APC, PTEN, RET, VHL, SDHB, SDHC, SDHD\n\n"
        "To check in your report: ask *'Show pathogenic variants in BRCA1'* or "
        "*'Show pathogenic variants in TP53'*.\n\n"
        "⚠️ Secondary findings review should be done with a certified genetic counsellor."
    ),
    "incidental findings": (
        "**Incidental / Secondary Findings**\n\n"
        "Findings unrelated to the primary reason for genomic testing. "
        "The ACMG recommends reporting pathogenic variants in 81 medically actionable genes (SF v3.2).\n\n"
        "**Key reportable gene categories:**\n"
        "• Hereditary cancer (BRCA1, BRCA2, TP53, MLH1, MSH2, MSH6)\n"
        "• Cardiovascular (RYR2, KCNQ1, SCN5A, MYBPC3, FBN1 for Marfan)\n"
        "• Metabolic (LDLR, PCSK9 for familial hypercholesterolaemia)\n\n"
        "To check: ask *'Show pathogenic variants in BRCA1'* or *'Do I have variants in ACMG genes?'*\n\n"
        "⚠️ These should be reviewed with a genetic counsellor."
    ),
    "ACMG secondary findings": (
        "**ACMG Secondary Findings v3.2 (81 genes)**\n\n"
        "Medically actionable genes where ACMG recommends reporting pathogenic/likely pathogenic variants.\n\n"
        "Categories: Hereditary cancer, cardiac arrhythmia, cardiomyopathy, aortic disease, "
        "familial hypercholesterolaemia, and other rare conditions.\n\n"
        "Notable genes: BRCA1, BRCA2, TP53, MLH1, MSH2, RYR2, FBN1, LDLR, and 73 more.\n\n"
        "You can ask: *'Show pathogenic variants in BRCA1'* or *'Show pathogenic variants in RYR2'*"
    ),
    "heterozygous": (
        "**Heterozygous** — having two different alleles at a gene locus.\n\n"
        "• One allele from mother, one from father — they are different\n"
        "• Stored as 'het' in the Otherinfo column\n"
        "• For dominant diseases: one pathogenic copy may be enough to cause disease\n"
        "• For recessive diseases: heterozygous = carrier (generally unaffected)\n\n"
        "Opposite of homozygous (two identical alleles)."
    ),
    "homozygous": (
        "**Homozygous** — having two identical alleles at a gene locus.\n\n"
        "• Both alleles inherited from parents are the same\n"
        "• Stored as 'hom' in the Otherinfo column\n"
        "• For recessive diseases: homozygous pathogenic = affected\n"
        "• For dominant diseases: homozygous may cause more severe disease\n\n"
        "Homozygous rare variants are often more clinically significant."
    ),
}


# ── Public entry ──────────────────────────────────────────────────────────────

def route_and_execute(
    message: str,
    db: Session,
    session_profile: dict,
    history: list[dict],
) -> tuple[RouterDecision, ExecutorResult, dict]:
    """Full Stages 1-2 for one chat turn.

    Returns (decision, executor_result, resolution_summary).
    resolution_summary is non-empty only when HPO resolution ran.
    """
    # Stage 1: LLM router
    decision = classify(message)

    # Stage 1.5: HPO resolution
    resolution_summary: dict = {
        "added": [], "unresolved": [], "total_terms": 0, "total_candidate_genes": 0
    }
    if decision.needs_hpo and decision.symptoms:
        resolution_summary = resolve_hpo(decision.symptoms, session_profile)
        log.info("HPO resolved: added=%d unresolved=%d genes=%d",
                 len(resolution_summary["added"]),
                 len(resolution_summary["unresolved"]),
                 resolution_summary["total_candidate_genes"])

    # Stage 2: Deterministic SQL executor
    result = execute(decision, db, session_profile)
    log.info("executor: kind=%s matched=%d intent=%s",
             result.kind, result.total_matched, result.intent)

    return decision, result, resolution_summary


def build_answer_user_message(
    user_message: str,
    executor: ExecutorResult,
    session_profile: dict,
    decision: RouterDecision,
    resolution_summary: dict,
) -> str:
    """Assemble the user-turn message passed to the answer LLM."""
    parts: list[str] = []

    # Executor block
    parts.append(render_executor(executor))

    # HPO profile
    hpo_terms = session_profile.get("hpo_terms") or []
    if hpo_terms:
        lines = ["SESSION HPO PROFILE (symptoms reported so far):"]
        for t in hpo_terms:
            if not t.get("hpo_id"):
                continue
            lines.append(
                f"  • {t['hpo_id']} {t['name']} "
                f"(matched from {t.get('input_text','?')!r}, "
                f"confidence={t.get('confidence', 0):.2f}, "
                f"associated genes: {t.get('gene_count',0)})"
            )
        parts.append("\n".join(lines))

    # New HPO resolutions this turn
    if resolution_summary.get("added"):
        lines = ["NEW SYMPTOMS RESOLVED THIS TURN:"]
        for a in resolution_summary["added"]:
            lines.append(
                f"  • {a['input_text']!r} → {a['hpo_id']} {a['name']!r} "
                f"({a['gene_count']} associated genes)"
            )
        parts.append("\n".join(lines))
    if resolution_summary.get("unresolved"):
        parts.append(
            "UNRESOLVED SYMPTOMS (do NOT fabricate HPO mappings — ask "
            "the user to clarify):\n  "
            + ", ".join(f"{s!r}" for s in resolution_summary["unresolved"])
        )

    parts.append(f"USER QUESTION: {user_message}")
    return "\n\n".join(parts)
