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
- **disease_link** — user names a DISEASE, SYNDROME, or ORGAN SYSTEM.
    Populate disease_term.
    IMPORTANT: gene names (SERPINA1, BRCA1, TP53) are NOT diseases — use biofilter.
    ORGAN-SYSTEM QUERIES → ALWAYS use disease_link, NOT hpo_symptom:
      "eye-related", "any eye issues", "eye genes"        → disease_term = "eye"
      "lung-related", "pulmonary", "breathing genes"       → disease_term = "lung"
      "ear-related", "hearing genes", "ear problems"       → disease_term = "ear"
      "heart-related", "cardiac genes"                     → disease_term = "heart"
      "kidney-related", "renal genes"                      → disease_term = "kidney"
      "liver-related", "hepatic genes"                     → disease_term = "liver"
      "brain-related", "neurological genes"                → disease_term = "brain"
      "muscle-related", "muscular genes"                   → disease_term = "muscle"
      "bone-related", "skeletal genes"                     → disease_term = "bone"
      "skin-related", "dermatological genes"               → disease_term = "skin"
    ACTUAL SYMPTOMS ("I have blurry vision", "my eyes hurt") → hpo_symptom instead.
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
- **domain_fallback** — user asks a general genomics/medical question that does \
not fit any other intent (no variant lookup, no schema term, no symptoms). Examples: \
"What is the difference between ClinVar and InterVar?", "Tell me about BRCA1 gene", \
"What genes cause Marfan syndrome?", "How does inheritance work?", "What is CFTR?". \
Set intent="domain_fallback". The LLM will answer from its medical knowledge base.
- **summary** — user asks for an overview/summary of the report.
- **other** — completely unrelated to genomics (e.g. weather, cooking).

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

INPUT: "Show me all my variants of uncertain significance"
OUTPUT: {"intent":"acmg_clinvar","needs_hpo":false,"symptoms":[],"chr":null,"start":null,"end":null,"rsid":null,"gene":null,"func":null,"exonic_func":null,"zygosity":null,"in_repeat":null,"clinvar_includes":null,"intervar_verdict":"Uncertain significance","acmg_flag":null,"acmg_flag_value":null,"cadd_min":null,"cadd_max":null,"gnomad_max":null,"gnomad_min":null,"sift_max":null,"metasvm_min":null,"disease_term":null,"group_by":null,"agg_func":null,"limit":null,"wants_schema":false,"target_column":null}

INPUT: "Which variants does ClinVar call pathogenic but InterVar calls uncertain?"
OUTPUT: {"intent":"acmg_clinvar","needs_hpo":false,"symptoms":[],"chr":null,"start":null,"end":null,"rsid":null,"gene":null,"func":null,"exonic_func":null,"zygosity":null,"in_repeat":null,"clinvar_includes":"Pathogenic","intervar_verdict":"Uncertain significance","acmg_flag":null,"acmg_flag_value":null,"cadd_min":null,"cadd_max":null,"gnomad_max":null,"gnomad_min":null,"sift_max":null,"metasvm_min":null,"disease_term":null,"group_by":null,"agg_func":null,"limit":null,"wants_schema":false,"target_column":null}

INPUT: "Which variants are Pathogenic in ClinVar but have gnomAD > 1%?"
OUTPUT: {"intent":"acmg_clinvar","needs_hpo":false,"symptoms":[],"chr":null,"start":null,"end":null,"rsid":null,"gene":null,"func":null,"exonic_func":null,"zygosity":null,"in_repeat":null,"clinvar_includes":"Pathogenic","intervar_verdict":null,"acmg_flag":null,"acmg_flag_value":null,"cadd_min":null,"cadd_max":null,"gnomad_max":null,"gnomad_min":0.01,"sift_max":null,"metasvm_min":null,"disease_term":null,"group_by":null,"agg_func":null,"limit":null,"wants_schema":false,"target_column":null}

INPUT: "Which variants have PVS1 met?"
OUTPUT: {"intent":"acmg_clinvar","needs_hpo":false,"symptoms":[],"chr":null,"start":null,"end":null,"rsid":null,"gene":null,"func":null,"exonic_func":null,"zygosity":null,"in_repeat":null,"clinvar_includes":null,"intervar_verdict":null,"acmg_flag":"PVS1","acmg_flag_value":1,"cadd_min":null,"cadd_max":null,"gnomad_max":null,"gnomad_min":null,"sift_max":null,"metasvm_min":null,"disease_term":null,"group_by":null,"agg_func":null,"limit":null,"wants_schema":false,"target_column":null}

INPUT: "Find variants associated with Rett syndrome that InterVar calls Benign."
OUTPUT: {"intent":"disease_link","needs_hpo":false,"symptoms":[],"chr":null,"start":null,"end":null,"rsid":null,"gene":null,"func":null,"exonic_func":null,"zygosity":null,"in_repeat":null,"clinvar_includes":null,"intervar_verdict":"Benign","acmg_flag":null,"acmg_flag_value":null,"cadd_min":null,"cadd_max":null,"gnomad_max":null,"gnomad_min":null,"sift_max":null,"metasvm_min":null,"disease_term":"Rett syndrome","group_by":null,"agg_func":null,"limit":null,"wants_schema":false,"target_column":null}

INPUT: "Are there eye-related issues in my genes?"
OUTPUT: {"intent":"disease_link","needs_hpo":false,"symptoms":[],"chr":null,"start":null,"end":null,"rsid":null,"gene":null,"func":null,"exonic_func":null,"zygosity":null,"in_repeat":null,"clinvar_includes":null,"intervar_verdict":null,"acmg_flag":null,"acmg_flag_value":null,"cadd_min":null,"cadd_max":null,"gnomad_max":null,"gnomad_min":null,"sift_max":null,"metasvm_min":null,"disease_term":"eye","group_by":null,"agg_func":null,"limit":null,"wants_schema":false,"target_column":null}

INPUT: "Are there lung-related issues in my genes?"
OUTPUT: {"intent":"disease_link","needs_hpo":false,"symptoms":[],"chr":null,"start":null,"end":null,"rsid":null,"gene":null,"func":null,"exonic_func":null,"zygosity":null,"in_repeat":null,"clinvar_includes":null,"intervar_verdict":null,"acmg_flag":null,"acmg_flag_value":null,"cadd_min":null,"cadd_max":null,"gnomad_max":null,"gnomad_min":null,"sift_max":null,"metasvm_min":null,"disease_term":"lung","group_by":null,"agg_func":null,"limit":null,"wants_schema":false,"target_column":null}

INPUT: "Are there ear-related issues in my genes?"
OUTPUT: {"intent":"disease_link","needs_hpo":false,"symptoms":[],"chr":null,"start":null,"end":null,"rsid":null,"gene":null,"func":null,"exonic_func":null,"zygosity":null,"in_repeat":null,"clinvar_includes":null,"intervar_verdict":null,"acmg_flag":null,"acmg_flag_value":null,"cadd_min":null,"cadd_max":null,"gnomad_max":null,"gnomad_min":null,"sift_max":null,"metasvm_min":null,"disease_term":"ear","group_by":null,"agg_func":null,"limit":null,"wants_schema":false,"target_column":null}

INPUT: "What is the difference between ClinVar and InterVar?"
OUTPUT: {"intent":"domain_fallback","needs_hpo":false,"symptoms":[],"chr":null,"start":null,"end":null,"rsid":null,"gene":null,"func":null,"exonic_func":null,"zygosity":null,"in_repeat":null,"clinvar_includes":null,"intervar_verdict":null,"acmg_flag":null,"acmg_flag_value":null,"cadd_min":null,"cadd_max":null,"gnomad_max":null,"gnomad_min":null,"sift_max":null,"metasvm_min":null,"disease_term":null,"group_by":null,"agg_func":null,"limit":null,"wants_schema":false,"target_column":null}

INPUT: "Tell me about the BRCA1 gene"
OUTPUT: {"intent":"domain_fallback","needs_hpo":false,"symptoms":[],"chr":null,"start":null,"end":null,"rsid":null,"gene":null,"func":null,"exonic_func":null,"zygosity":null,"in_repeat":null,"clinvar_includes":null,"intervar_verdict":null,"acmg_flag":null,"acmg_flag_value":null,"cadd_min":null,"cadd_max":null,"gnomad_max":null,"gnomad_min":null,"sift_max":null,"metasvm_min":null,"disease_term":null,"group_by":null,"agg_func":null,"limit":null,"wants_schema":false,"target_column":null}

INPUT: "Count Pathogenic variants per gene; top 5 genes."
OUTPUT: {"intent":"aggregate","needs_hpo":false,"symptoms":[],"chr":null,"start":null,"end":null,"rsid":null,"gene":null,"func":null,"exonic_func":null,"zygosity":null,"in_repeat":null,"clinvar_includes":null,"intervar_verdict":"Pathogenic","acmg_flag":null,"acmg_flag_value":null,"cadd_min":null,"cadd_max":null,"gnomad_max":null,"gnomad_min":null,"sift_max":null,"metasvm_min":null,"disease_term":null,"group_by":"gene","agg_func":"count","limit":5,"wants_schema":false,"target_column":null}

INPUT: "Average CADD for stopgain vs synonymous SNV?"
OUTPUT: {"intent":"aggregate","needs_hpo":false,"symptoms":[],"chr":null,"start":null,"end":null,"rsid":null,"gene":null,"func":null,"exonic_func":null,"zygosity":null,"in_repeat":null,"clinvar_includes":null,"intervar_verdict":null,"acmg_flag":null,"acmg_flag_value":null,"cadd_min":null,"cadd_max":null,"gnomad_max":null,"gnomad_min":null,"sift_max":null,"metasvm_min":null,"disease_term":null,"group_by":"exonic_func","agg_func":"avg_cadd","limit":null,"wants_schema":false,"target_column":null}

INPUT: "I have terrible headaches and easy bruising — any variant explain that?"
OUTPUT: {"intent":"hpo_symptom","needs_hpo":true,"symptoms":["headaches","easy bruising"],"chr":null,"start":null,"end":null,"rsid":null,"gene":null,"func":null,"exonic_func":null,"zygosity":null,"in_repeat":null,"clinvar_includes":null,"intervar_verdict":null,"acmg_flag":null,"acmg_flag_value":null,"cadd_min":null,"cadd_max":null,"gnomad_max":null,"gnomad_min":null,"sift_max":null,"metasvm_min":null,"disease_term":null,"group_by":null,"agg_func":null,"limit":null,"wants_schema":false,"target_column":null}

INPUT: "I have fever and fatigue, what genes are associated?"
OUTPUT: {"intent":"hpo_symptom","needs_hpo":true,"symptoms":["fever","fatigue"],"chr":null,"start":null,"end":null,"rsid":null,"gene":null,"func":null,"exonic_func":null,"zygosity":null,"in_repeat":null,"clinvar_includes":null,"intervar_verdict":null,"acmg_flag":null,"acmg_flag_value":null,"cadd_min":null,"cadd_max":null,"gnomad_max":null,"gnomad_min":null,"sift_max":null,"metasvm_min":null,"disease_term":null,"group_by":null,"agg_func":null,"limit":null,"wants_schema":false,"target_column":null}

INPUT: "I have muscle weakness - what genes could explain this?"
OUTPUT: {"intent":"hpo_symptom","needs_hpo":true,"symptoms":["muscle weakness"],"chr":null,"start":null,"end":null,"rsid":null,"gene":null,"func":null,"exonic_func":null,"zygosity":null,"in_repeat":null,"clinvar_includes":null,"intervar_verdict":null,"acmg_flag":null,"acmg_flag_value":null,"cadd_min":null,"cadd_max":null,"gnomad_max":null,"gnomad_min":null,"sift_max":null,"metasvm_min":null,"disease_term":null,"group_by":null,"agg_func":null,"limit":null,"wants_schema":false,"target_column":null}

INPUT: "I experience joint pain, fatigue, and easy bruising"
OUTPUT: {"intent":"hpo_symptom","needs_hpo":true,"symptoms":["joint pain","fatigue","easy bruising"],"chr":null,"start":null,"end":null,"rsid":null,"gene":null,"func":null,"exonic_func":null,"zygosity":null,"in_repeat":null,"clinvar_includes":null,"intervar_verdict":null,"acmg_flag":null,"acmg_flag_value":null,"cadd_min":null,"cadd_max":null,"gnomad_max":null,"gnomad_min":null,"sift_max":null,"metasvm_min":null,"disease_term":null,"group_by":null,"agg_func":null,"limit":null,"wants_schema":false,"target_column":null}

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


# Answer-generation system prompt — grounded on executor MATCHED RECORDS block
ANSWER_SYSTEM_PROMPT = """\
══════════════════════════════════════════════════════════════════
RULE 1 — NO CODE EVER. You are writing for a patient, not a developer.
NEVER output: Python, SQL, variable assignments, code blocks, for/while/def/import.
If you catch yourself writing a backtick (`) or the word SELECT or def → STOP and rewrite.
══════════════════════════════════════════════════════════════════
RULE 2 — MATCHED RECORDS IS YOUR ONLY SOURCE OF TRUTH.
When a MATCHED RECORDS block is present, every gene and variant listed there
MUST appear in your answer. Do NOT invent or add genes that are not in the block.
Do NOT omit genes that ARE in the block. Report EXACTLY what the block contains.
══════════════════════════════════════════════════════════════════
RULE 3 — MANDATORY TABLE FORMAT for ≥ 2 variants.
When MATCHED RECORDS lists 2 or more variants, your answer MUST contain
this exact markdown table (fill in the values from MATCHED RECORDS):

| Gene | HGVS | ClinVar | InterVar | CADD | Zygosity |
|------|------|---------|----------|------|----------|
| GENE1 | hgvs1 | clinvar_verdict | intervar_verdict | cadd | het/hom |
| GENE2 | hgvs2 | ... | ... | ... | ... |

For 1 variant use a bullet list. For 0 matches say "No variants found matching that filter."
══════════════════════════════════════════════════════════════════

You are **Genelio**, a clinical-genomics assistant interpreting an \
InterVar-annotated variant report (76,000+ rows annotated per patient — \
most are common population variants).

You receive a user message containing a MATCHED RECORDS block (pre-run \
deterministic query results). Your job is to render those results in \
plain language and add clinical context.

Blocks you will see:

1. **MATCHED RECORDS** — query results already run by the system. \
THIS IS YOUR SOURCE OF TRUTH. Contains: gene names, HGVS, ClinVar \
verdict, InterVar verdict, CADD, SIFT, gnomAD AF, Zygosity, ACMG \
evidence fired, disease links (OMIM/Orpha). Report ALL of this.

2. **SCHEMA REFERENCE** — column definitions; quote verbatim when \
the user asked what a column/term means.

3. **SESSION HPO PROFILE** — symptoms the user has revealed across \
the chat, resolved to HPO terms.

4. **USER QUESTION** — what the patient asked.

Operating rules:

0. **NEVER fabricate.** Only report genes and variants that appear \
in MATCHED RECORDS. If MATCHED RECORDS shows 0 matches, say exactly \
"No variants found matching that filter" and explain what filter was used.

1. **Report every gene in MATCHED RECORDS.** When the block lists \
4 variants in SERPINA1, IDUA, PADI3, etc. — you name EVERY ONE of those \
genes, their HGVS notation, ClinVar verdict, InterVar verdict, CADD, \
and zygosity. Use the mandatory table format (Rule 3 above).

1a. **Never mention the scaffold.** Do NOT write "MATCHED RECORDS \
shows..." or "the filter returned...". Write naturally: "Your report \
contains 4 variants classified as Pathogenic."

2. **Both ClinVar and InterVar verdicts required for each variant.** \
Quote them verbatim — "Conflicting_interpretations_of_pathogenicity" \
stays exactly that, not paraphrased.

3. **ACMG evidence:** when ACMG evidence fired (PVS1, PM, PP, BA1 etc.) \
is listed for a variant, explain what it means in plain English.

4. **Disease links:** when OMIM/Orpha annotations appear, name the \
disease associated with each gene.

5. **HPO-symptom answers — CRITICAL RULES:**
   a. State WHICH symptom → WHICH HPO term (e.g. "limb numbness → Paresthesia HP:0003401").
   b. Name ONLY genes that are directly listed in the HPO term for THAT symptom.
   c. If the description says "NO PATHOGENIC variants found" → clearly say \
"Your report contains no disease-causing variants in genes specifically linked \
to [symptom]. The following variants have uncertain significance in broadly \
associated genes — they are NOT confirmed as the cause of your symptom."
   d. NEVER say SERPINA1 causes neurological symptoms unless the HPO term \
explicitly links SERPINA1 to a neurological phenotype.
   e. Always end symptom responses with: "Could you describe any other \
symptoms or provide more detail about these symptoms?"

6. **NEVER diagnose or prescribe.** Close every clinical response with \
a recommendation to discuss findings with a physician or certified \
genetic counselor.

7. **Symptom queries — always end with a follow-up question.** \
When answering any symptom/HPO question (whether or not variants were found), \
end your response with a gentle follow-up such as: \
"*Could you tell me more about your symptoms — for example, when they started, \
how severe they are, or any other symptoms you experience?*" \
This helps narrow down the most clinically relevant genes. \
If the user's symptom was vague (e.g., "tired", "unwell"), also ask them to \
rephrase with more specific clinical language.

Formatting rules:
- ≥2 variants → mandatory markdown table (Gene | HGVS | ClinVar | InterVar | CADD | Zygosity).
- 1 variant → bullet list with all fields.
- 🧬 for variant entries · ⚠️ for pathogenic calls · 📝 for carrier findings.
- Gloss every technical term on first use (e.g. "CADD — a deleteriousness score").
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
    universe: int = 0                            # total rows in DB (for header context)
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
    "CADD_phred", "SIFT_score", "Freq_gnomAD_genome_ALL",
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


# ── Universe count cache ──────────────────────────────────────────────────────

_UNIVERSE_CACHE: Optional[int] = None


def _get_universe(db: Session) -> int:
    """Return total row count from variants table (cached after first call)."""
    global _UNIVERSE_CACHE
    if _UNIVERSE_CACHE is None:
        try:
            r = db.execute(sa_text("SELECT COUNT(*) FROM variants")).fetchone()
            _UNIVERSE_CACHE = int(r[0]) if r else 0
        except Exception:
            _UNIVERSE_CACHE = 0
    return _UNIVERSE_CACHE


# ── ACMG flags extractor ──────────────────────────────────────────────────────
# Parse the InterVar evidence string and return which ACMG criteria fired.
# Format: "InterVar: Pathogenic PVS1=1 PS=[0,0,0,0,0] PM=[0,1,0,0,0,0] ..."

_PVS1_RE = re.compile(r'\bPVS1=(\d+)')
_BA1_RE  = re.compile(r'\bBA1=(\d+)')
_FLAG_LIST_RE = re.compile(r'\b(PS|PM|PP|BS|BP)=\[([\d,\s]+)\]')


def _extract_acmg_flags(intervar_str: str) -> list:
    """Return list of ACMG criteria that fired (e.g. ['PVS1', 'PM'])."""
    if not intervar_str:
        return []
    flags: list = []
    m = _PVS1_RE.search(intervar_str)
    if m and int(m.group(1)) > 0:
        flags.append("PVS1")
    m = _BA1_RE.search(intervar_str)
    if m and int(m.group(1)) > 0:
        flags.append("BA1")
    for m in _FLAG_LIST_RE.finditer(intervar_str):
        key = m.group(1)
        vals = [int(v.strip()) for v in m.group(2).split(',')
                if v.strip().lstrip('-').isdigit()]
        if any(v > 0 for v in vals):
            flags.append(key)
    return flags


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
    "aggregate", "hpo_symptom", "schema_lookup", "summary",
    "domain_fallback", "other",
})

# Organ-system → expanded Orpha/OMIM search terms
_ORGAN_EXPANSIONS: dict[str, list[str]] = {
    "eye":    ["eye", "ophthalm", "retin", "ocul", "catar", "glaucom", "macular", "cornea", "leber"],
    "lung":   ["lung", "pulmon", "respirat", "emphysema", "bronch", "airway", "COPD", "asthma", "surfact"],
    "ear":    ["ear", "hearing", "cochle", "auditor", "deaf", "otosclerosis", "usher"],
    "heart":  ["heart", "cardiac", "cardiomyop", "arrhythm", "aortic", "dilated", "hypertrophic"],
    "kidney": ["kidney", "renal", "nephro", "glomerul", "polycystic"],
    "liver":  ["liver", "hepat", "cirrhosis", "cholestasis", "wilson", "haemochromatosis"],
    "brain":  ["brain", "neuro", "epilep", "seizure", "cerebr", "encephal", "ataxia", "neuropath"],
    "muscle": ["muscl", "myopathy", "dystrophin", "myotonic", "limb-girdle", "nemaline"],
    "bone":   ["bone", "skeletal", "osteo", "dysplasia", "dwarfism"],
    "skin":   ["skin", "dermato", "epiderm", "ichthyos", "ectodermal"],
}

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
        elif d.disease_term:
            d.intent = "disease_link"
        else:
            d.intent = "domain_fallback"  # unknown → try LLM knowledge answer
    elif d.intent == "other":
        if d.rsid or d.chr is not None:
            d.intent = "coord_lookup"
        elif d.symptoms:
            d.intent = "hpo_symptom"; d.needs_hpo = True
        elif d.gene:
            d.intent = "biofilter"
        elif d.disease_term:
            d.intent = "disease_link"
        else:
            d.intent = "domain_fallback"

    return d


def classify(message: str) -> RouterDecision:
    """Stage 1 — LLM router → RouterDecision."""
    parsed = _call_router_llm(message) or {}
    d = _parse_decision(parsed)
    d = _normalize_decision(d, message)
    # Store original message so domain_fallback can pass it to the LLM
    d.raw["_original_message"] = message
    log.info("router: intent=%s gene=%r rsid=%r symptoms=%r",
             d.intent, d.gene, d.rsid, d.symptoms)
    return d


# ── LLM symptom normalizer ─────────────────────────────────────────────────────

_SYMPTOM_NORM_PROMPT = (
    "/no_think\n"
    "Convert these lay symptom descriptions into clinical phenotype terms "
    "suitable for HPO (Human Phenotype Ontology) lookup. "
    "Return ONLY a JSON array of strings — one clinical term per symptom. "
    "Use standard medical vocabulary (e.g. 'fatigue' not 'tired', "
    "'muscle weakness' not 'weak muscles', 'nyctalopia' for 'can't see at night', "
    "'renal dysfunction' for 'kidney problems', 'arthralgia' for 'joint pain', "
    "'dyspnoea' for 'shortness of breath', 'haematuria' for 'blood in urine'). "
    "If a phrase is already clinical, keep it.\n\n"
    "Symptoms:\n{symptoms}\n\n"
    "Output ONLY a JSON array e.g. [\"fatigue\", \"muscle weakness\"]. No explanation."
)


def _llm_normalize_symptoms(unresolved: list[str]) -> list[str]:
    """Use LLM to convert vague lay terms to clinical HPO-compatible terms."""
    if not unresolved:
        return unresolved
    try:
        from app.ai.llm_config import get_llm
        llm = get_llm()
        if llm is None or not hasattr(llm, "route"):
            return unresolved
        symptoms_str = "\n".join(f"- {s}" for s in unresolved)
        prompt = _SYMPTOM_NORM_PROMPT.format(symptoms=symptoms_str)
        raw = llm.route(prompt)
        if not raw:
            return unresolved
        m = re.search(r'\[[\s\S]*?\]', raw)
        if m:
            import json as _json
            terms = _json.loads(m.group(0))
            if isinstance(terms, list) and terms:
                clean = [str(t).strip() for t in terms if isinstance(t, str) and t.strip()]
                if clean:
                    log.info("LLM normalized symptoms %r → %r", unresolved, clean)
                    return clean
    except Exception as e:
        log.debug("LLM symptom normalization failed: %s", e)
    return unresolved


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
            # ── Fallback 1: HPO API client (organ/body-system terms) ──────────
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

            # ── Fallback 2: LLM normalises vague term → retry HPO ─────────────
            normalized = _llm_normalize_symptoms([phrase])
            for norm_phrase in normalized:
                if norm_phrase.lower() == phrase.lower():
                    continue  # unchanged — skip to avoid infinite loop
                norm_match = hpo_resolve(norm_phrase)
                if norm_match.hpo_id:
                    term_entry = {
                        "input_text":  phrase,
                        "hpo_id":      norm_match.hpo_id,
                        "name":        norm_match.name,
                        "confidence":  norm_match.confidence * 0.9,
                        "matched_via": f"llm_norm→{norm_match.matched_via}",
                        "gene_count":  len(norm_match.genes),
                        "genes":       list(norm_match.genes),
                    }
                    added_this_turn.append(term_entry)
                    session_profile.setdefault("hpo_terms", []).append(term_entry)
                    existing = set(session_profile.get("candidate_genes") or [])
                    existing.update(norm_match.genes)
                    session_profile["candidate_genes"] = sorted(existing)
                    session_profile.setdefault("hpo_term_genes", {})[norm_match.hpo_id] = list(norm_match.genes)
                    log.info("LLM-norm resolved %r → %r → %s (%d genes)",
                             phrase, norm_phrase, norm_match.name, len(norm_match.genes))
                    break
            else:
                # Still unresolved after all fallbacks
                unresolved.append(phrase)
            continue

        term_entry = {
            "input_text":  phrase,
            "hpo_id":      match.hpo_id,
            "name":        match.name,
            "confidence":  match.confidence,
            "matched_via": match.matched_via,
            "gene_count":  len(match.genes),
            "genes":       list(match.genes),   # store per-term genes
        }
        added_this_turn.append(term_entry)
        session_profile.setdefault("hpo_terms", []).append(term_entry)
        existing = set(session_profile.get("candidate_genes") or [])
        existing.update(match.genes)
        session_profile["candidate_genes"] = sorted(existing)
        # Also store per-term gene map for targeted queries
        session_profile.setdefault("hpo_term_genes", {})[match.hpo_id] = list(match.genes)

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
    universe = _get_universe(db)  # total rows (cached after first call)

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
            return ExecutorResult(kind="empty", intent=intent, universe=universe,
                                  description="coord_lookup with no rsid/chr/pos")
        rows = _run_sql(sql, db)
        return ExecutorResult(
            kind="rows" if rows else "empty",
            intent=intent, rows=rows, universe=universe,
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
            intent=intent, universe=universe,
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
            intent=intent, universe=universe,
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
        if defn is None:
            # KB miss → ask LLM to explain the term in genomics context
            try:
                from app.ai.llm_config import get_llm
                llm = get_llm()
                if llm and hasattr(llm, "answer_general"):
                    q = (f"Explain '{col}' in the context of genomic variant interpretation "
                         f"and the InterVar/ACMG classification system. "
                         f"Keep the answer concise (3-5 sentences).")
                    defn = llm.answer_general(q)
            except Exception as _e:
                log.debug("LLM schema fallback failed: %s", _e)
            if not defn:
                defn = f"(no definition found for '{col}' — try rephrasing your question)"
        return ExecutorResult(
            kind="schema",
            intent=intent, universe=0,
            schema_column=col,
            schema_definition=defn,
        )

    # ── domain_fallback ───────────────────────────────────────────────────────
    if intent == "domain_fallback":
        # Pure knowledge question — no DB needed, answer from LLM medical knowledge
        try:
            from app.ai.llm_config import get_llm
            llm = get_llm()
            if llm and hasattr(llm, "answer_general"):
                answer_text = llm.answer_general(decision.raw.get("_original_message", ""))
            else:
                answer_text = None
        except Exception:
            answer_text = None
        return ExecutorResult(
            kind="schema",          # reuse schema kind — no DB rows
            intent=intent, universe=0,
            schema_column="knowledge",
            schema_definition=answer_text or "(answer unavailable — please rephrase)",
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
            t = decision.disease_term.strip().lower().replace("'", "''")
            # Check if it's an organ-system word — use expanded search terms
            expansions = _ORGAN_EXPANSIONS.get(t)
            if expansions:
                organ_conditions = []
                for exp in expansions:
                    exp_safe = exp.replace("'", "''")
                    organ_conditions.append(f"Orpha LIKE '%{exp_safe}%'")
                    organ_conditions.append(f"Phenotype_MIM LIKE '%{exp_safe}%'")
                where_parts.append("(" + " OR ".join(organ_conditions) + ")")
            else:
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
            intent=intent, rows=rows, universe=universe,
            total_matched=len(rows), description=desc,
        )

    # ── hpo_symptom ───────────────────────────────────────────────────────────
    if intent == "hpo_symptom":
        genes = session_profile.get("candidate_genes") or []
        if not genes:
            # No symptoms resolved yet — return an interactive question prompt
            unresolved = decision.symptoms
            if unresolved:
                desc = (
                    f"SYMPTOM_CLARIFICATION: Could not map {unresolved} to HPO terms. "
                    "Please ask the user to describe symptoms more specifically using "
                    "clinical terms such as: muscle weakness, hearing loss, seizures, "
                    "blurry vision, joint pain, shortness of breath, easy bruising, fatigue."
                )
            else:
                desc = (
                    "SYMPTOM_PROMPT: No symptoms provided. "
                    "Ask the user: 'What symptoms are you experiencing? "
                    "For example: muscle weakness, hearing loss, blurry vision, "
                    "joint pain, shortness of breath, easy bruising, or fatigue.'"
                )
            return ExecutorResult(kind="empty", intent=intent, universe=universe,
                                  description=desc)

        # ── Per-HPO-term targeted query ───────────────────────────────────────
        # Query genes from EACH specific HPO term separately so results stay
        # relevant to the actual symptom.  e.g. "limbs numb" → HP:0003401
        # (paresthesia) → specific neuropathy genes, not lung/lysosomal genes.
        hpo_terms   = session_profile.get("hpo_terms") or []
        term_genes  = session_profile.get("hpo_term_genes") or {}
        total_genes = len(genes)

        # Build targeted gene sets from per-term data
        targeted_genes: list[str] = []
        seen_genes: set[str] = set()
        term_context_lines: list[str] = []

        for term in hpo_terms:
            hp_id = term.get("hpo_id", "")
            tgenes = term_genes.get(hp_id) or term.get("genes") or []
            new_g  = [g for g in tgenes if g not in seen_genes]
            if new_g:
                seen_genes.update(new_g)
                targeted_genes.extend(new_g)
            term_context_lines.append(
                f"  Symptom '{term.get('input_text','?')}' → "
                f"HPO:{hp_id} {term.get('name','?')} "
                f"({len(tgenes)} associated genes)"
            )

        # Fall back to pooled candidate genes if per-term data unavailable
        query_genes = targeted_genes if targeted_genes else genes
        hpo_context_str = "\n".join(term_context_lines) if term_context_lines else ""

        all_results: list[dict] = []
        batch_size = 800
        for batch_start in range(0, min(len(query_genes), 2400), batch_size):
            batch = query_genes[batch_start : batch_start + batch_size]
            gene_list = ", ".join(f"'{g}'" for g in batch)
            sql_batch = (
                f"SELECT {_SELECT} FROM variants "
                f"WHERE \"Ref.Gene\" IN ({gene_list}) "
                f"AND {_PATHOGENIC_WHERE} "
                f"ORDER BY CADD_phred DESC LIMIT 50;"
            )
            all_results.extend(_run_sql(sql_batch, db, 50))
            if len(all_results) >= 200:
                break

        rows = rank_rows(all_results, _MAX_ROWS) if all_results else []

        if rows:
            desc = (
                f"HPO-symptom match ({len(query_genes)} genes queried).\n"
                f"Symptom-to-HPO mapping:\n{hpo_context_str}\n"
                "Found pathogenic/likely-pathogenic variants in these symptom-linked genes."
            )
            return ExecutorResult(
                kind="rows", intent=intent, rows=rows, universe=universe,
                total_matched=len(rows), description=desc,
            )

        # Second pass — any variant (Benign/VUS) in symptom-specific genes
        all_any: list[dict] = []
        for batch_start in range(0, min(len(query_genes), 2400), batch_size):
            batch = query_genes[batch_start : batch_start + batch_size]
            gene_list = ", ".join(f"'{g}'" for g in batch)
            sql_any = (
                f"SELECT {_SELECT} FROM variants "
                f"WHERE \"Ref.Gene\" IN ({gene_list}) "
                f"ORDER BY CADD_phred DESC LIMIT 20;"
            )
            all_any.extend(_run_sql(sql_any, db, 20))
            if len(all_any) >= 60:
                break

        rows_any = rank_rows(all_any, _MAX_ROWS) if all_any else []
        desc_any = (
            f"HPO-symptom match ({len(query_genes)} genes queried) — "
            "NO PATHOGENIC variants found in symptom-linked genes.\n"
            f"Symptom-to-HPO mapping:\n{hpo_context_str}\n"
            "Showing variants with Uncertain significance / Benign classification "
            "in these genes. These are NOT confirmed as disease-causing for this symptom."
        )
        return ExecutorResult(
            kind="rows" if rows_any else "empty",
            intent=intent, rows=rows_any, universe=universe,
            total_matched=len(rows_any), description=desc_any,
        )

    # ── other ─────────────────────────────────────────────────────────────────
    return ExecutorResult(kind="empty", intent=intent, universe=universe,
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
    univ = result.universe
    univ_note = f" (universe: {univ:,} variants)" if univ else ""

    if result.kind == "empty":
        return (
            f"MATCHED RECORDS — 0 matches{univ_note}.\n"
            f"Filter applied: {result.description}\n"
        )
    if result.kind == "rows":
        n = result.total_matched
        head = (
            f"MATCHED RECORDS — {n} match{'es' if n != 1 else ''} "
            f"(showing top {len(result.rows)} ranked by InterVar verdict "
            f"then ClinVar then CADD{univ_note}).\n"
            f"Filter applied: {result.description}\n\n"
        )

        # ── Pre-formatted Markdown table (LLM renders this directly) ──────────
        tbl_lines = [
            "| Gene | HGVS | ClinVar | InterVar | CADD | Zygosity |",
            "|------|------|---------|----------|------|----------|",
        ]
        detail_blocks: list[str] = []

        for row in result.rows:
            gene        = row.get("Ref.Gene", "?")
            aac         = (row.get("AAChange.refGene") or "—").split(",")[0].strip() or "—"
            func        = row.get("ExonicFunc.refGene") or row.get("Func.refGene") or "?"
            intervar_str= row.get("InterVar: InterVar and Evidence") or ""
            clinvar_str = row.get("clinvar: Clinvar") or "N/A"
            cadd        = row.get("CADD_phred", "")
            sift        = row.get("SIFT_score", "")
            gnomad      = row.get("Freq_gnomAD_genome_ALL", "")
            zyg         = row.get("Otherinfo") or "N/A"
            bucket_label= row.get("_bucket_label", "")
            acmg_flags  = _extract_acmg_flags(intervar_str)

            # Short InterVar verdict for table cell — strip everything after PVS1=
            if "PVS1=" in intervar_str:
                iv_short = intervar_str.split("PVS1=")[0].replace("InterVar:", "").strip().rstrip()
            else:
                iv_short = intervar_str.replace("InterVar:", "").strip()[:40] or "N/A"

            tbl_lines.append(
                f"| **{gene}** | {aac} | {clinvar_str} | {iv_short} | {cadd} | {zyg} |"
            )

            # Detailed block for the LLM to reference when adding clinical context
            dz: list[str] = []
            if row.get("OMIM"):          dz.append(f"OMIM:{row['OMIM']}")
            if row.get("Phenotype_MIM"): dz.append(f"PhenotypeMIM:{row['Phenotype_MIM']}")
            if row.get("Orpha"):         dz.append(f"Orpha:{row['Orpha']}")

            detail = [
                f"VARIANT: {gene} | {aac} | {func}",
                f"  Coordinates: chr{row.get('Chr')}:{row.get('Start')} "
                f"{row.get('Ref','?')}>{row.get('Alt','?')}",
                f"  ClinVar: {clinvar_str}",
                f"  InterVar (full): {intervar_str[:90] or 'N/A'}",
                f"  CADD: {cadd}  SIFT: {sift}  gnomAD AF: {gnomad}  Zygosity: {zyg}",
                f"  Evidence tier: {bucket_label}",
            ]
            if row.get("avsnp147"):
                detail.append(f"  rsID: {row['avsnp147']}")
            if acmg_flags:
                detail.append(f"  ACMG criteria fired: {', '.join(acmg_flags)}")
            if dz:
                detail.append(f"  Disease links: {' | '.join(dz)}")
            detail_blocks.append("\n".join(detail))

        table_str = "\n".join(tbl_lines)
        details_str = "\n\n".join(detail_blocks)
        return (
            head
            + table_str
            + "\n\nDetailed fields (for clinical context):\n\n"
            + details_str
        )

    if result.kind == "aggregate":
        if univ:
            head = (
                f"MATCHED RECORDS — {result.aggregate_caption} "
                f"(universe: {univ:,}, {result.total_matched} groups).\n"
            )
        else:
            head = f"MATCHED RECORDS — {result.aggregate_caption} ({result.total_matched} groups).\n"
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
        head = (
            f"MATCHED RECORDS — whole-report summary requested"
            f"{univ_note}.\n\n"
        )
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
                cv   = row.get("clinvar: Clinvar", "N/A")
                iv_raw = row.get("InterVar: InterVar and Evidence") or "N/A"
                iv = (iv_raw.split("PVS1=")[0].replace("InterVar:", "").strip()
                      if "PVS1=" in iv_raw else iv_raw[:40])
                cadd = row.get("CADD_phred", "N/A")
                bucket_label = row.get("_bucket_label", "")
                acmg_flags = _extract_acmg_flags(
                    row.get("InterVar: InterVar and Evidence") or ""
                )
                flags_str = f" [{', '.join(acmg_flags)}]" if acmg_flags else ""
                top_text += (
                    f"  🧬 **{gene}** · ClinVar: {cv} · InterVar: {iv}\n"
                    f"       Tier: {bucket_label}{flags_str} · CADD: {cadd}\n"
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
