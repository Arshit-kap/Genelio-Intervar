"""
LLM Backend Configuration
Supports:
  1. vllm_api  — vLLM OpenAI-compatible API (Qwen3-32B on H100) ← PRIMARY
  2. hf_api    — HuggingFace Inference API (serverless)
  3. hf_local  — Local HuggingFace transformers pipeline
  4. mock      — Pattern-based fallback only, no LLM

Set env vars:
  LLM_BACKEND   = vllm_api | hf_api | hf_local | mock
  VLLM_API_URL  = http://localhost:8001  (or SSH-tunneled remote URL)
  VLLM_MODEL    = Qwen/Qwen3-32B
"""
import os
import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

LLM_BACKEND    = os.getenv("LLM_BACKEND", "mock")
# Ollama (recommended) — served by Ollama on port 11434
VLLM_API_URL   = os.getenv("VLLM_API_URL", "http://localhost:11434")
VLLM_MODEL     = os.getenv("VLLM_MODEL", "qwen3:32b")
HF_TOKEN       = os.getenv("HF_TOKEN", "")
HF_MODEL       = os.getenv("HF_MODEL", "Qwen/Qwen3-8B")
HF_LOCAL_MODEL = os.getenv("HF_LOCAL_MODEL", "Qwen/Qwen3-0.6B")

_cache: dict = {}
_failed_backends: set = set()  # backends that failed to initialize (skip retrying)

_SQL_SYSTEM_MSG = (
    "/no_think\n"
    "You are a SQLite expert for a patient genomic variant database (InterVar format).\n"
    "Table: variants. Return ONLY a raw SQL SELECT ending with semicolon. No prose, no markdown.\n\n"
    "CRITICAL — columns with dots, spaces, or special chars MUST be double-quoted:\n"
    "  \"Ref.Gene\"  \"Func.refGene\"  \"ExonicFunc.refGene\"  \"Gene.ensGene\"\n"
    "  \"AAChange.refGene\"  \"AAChange.ensGene\"  \"AAChange.knownGene\"\n"
    "  \"clinvar: Clinvar\"  \"InterVar: InterVar and Evidence\"  \"GERP++_RS\"\n\n"
    "COLUMN MAPPINGS:\n"
    "  gene name  → \"Ref.Gene\" = 'BRCA1'  (NEVER unquoted Ref.Gene or gene_symbol)\n"
    "  missense   → \"ExonicFunc.refGene\" = 'nonsynonymous SNV'\n"
    "  frameshift → \"ExonicFunc.refGene\" LIKE '%frameshift%'\n"
    "  stopgain   → \"ExonicFunc.refGene\" = 'stopgain'\n"
    "  synonymous → \"ExonicFunc.refGene\" = 'synonymous SNV'\n"
    "  pathogenic → \"clinvar: Clinvar\" LIKE 'clinvar: Pathogenic%' (ClinVar is PRIMARY — values stored as 'clinvar: Pathogenic ', 'clinvar: Likely_pathogenic ' with underscore+trailing space)\n"
    "  likely pathogenic → \"clinvar: Clinvar\" LIKE 'clinvar: Likely_pathogenic%' OR \"clinvar: Clinvar\" LIKE 'clinvar: Pathogenic/Likely_pathogenic%'\n"
    "  InterVar fallback → \"InterVar: InterVar and Evidence\" LIKE 'InterVar: Pathogenic%'\n"
    "  NEVER use LIKE '%Pathogenic%' — it matches 'Conflicting_interpretations_of_pathogenicity'\n"
    "  benign    → \"clinvar: Clinvar\" LIKE 'clinvar: Benign%' OR \"clinvar: Clinvar\" LIKE 'clinvar: Likely_benign%'\n"
    "  het / hom  → Otherinfo = 'het' / 'hom'\n"
    "  gnomAD     → Freq_gnomAD_genome_ALL  (NULL = absent from gnomAD)\n"
    "  CADD score → CADD_phred  (>20 damaging, >30 highly damaging)\n"
    "  rsID       → avsnp147\n"
    "  chromosome → Chr (no 'chr' prefix: '17' not 'chr17')\n\n"
    "RULES:\n"
    "1. ALWAYS include LIMIT. Use the count from the question if given, else LIMIT 50.\n"
    "2. InterVar prefix LIKE only: LIKE 'InterVar: Pathogenic%' (NOT '%Pathogenic%').\n"
    "3. For GROUP BY, always add a WHERE filter.\n"
    "4. NULL in numeric columns = missing data. Use IS NULL, never = '.'.\n"
    "5. If user says 'list 50 genes', use LIMIT 50 and GROUP BY \"Ref.Gene\"."
)

_EXPLAIN_SYSTEM_MSG = (
    "You are a patient-facing genomic assistant explaining variant database query results.\n"
    "Your audience: patients, families, doctors, and researchers.\n\n"
    "RULES:\n"
    "1. Give a direct 2–4 sentence summary of the KEY FINDING. No reasoning, no step-by-step.\n"
    "2. Translate technical terms into plain English:\n"
    "   - 'InterVar: Pathogenic' or 'clinvar: Pathogenic' → disease-causing\n"
    "   - 'InterVar: Benign' → harmless\n"
    "   - 'InterVar: Uncertain significance' → uncertain significance\n"
    "   - 'nonsynonymous SNV' → missense variant (amino acid change)\n"
    "   - 'stopgain' → premature stop codon (truncates protein)\n"
    "   - 'frameshift' → reading frame shift (usually loss of function)\n"
    "   - Freq_gnomAD_genome_ALL → population frequency\n"
    "   - CADD_phred → damage score (>20 harmful, >30 highly damaging)\n"
    "   - Otherinfo 'het' → heterozygous; 'hom' → homozygous\n"
    "3. Only mention numbers and names that appear in the data provided.\n"
    "4. For disease-causing findings end with: "
    "'⚠️ Educational only — consult a genetic counselor.'\n"
    "5. CRITICAL — Disease associations:\n"
    "   Each row contains a '_diseases' field from the patient's own OMIM/Orphanet database.\n"
    "   You MUST use ONLY '_diseases' when stating what disease a gene causes.\n"
    "   NEVER use training memory for gene-disease links — it may be wrong or outdated.\n"
    "   If '_diseases' says 'MSMD due to complete ISG15 deficiency', report exactly that.\n"
    "   Do NOT substitute a more famous association from your training knowledge."
)

_GENERAL_SYSTEM_MSG = (
    "/no_think\n"
    "You are a warm, knowledgeable medical genetics assistant for a patient-facing genomic Q&A system.\n"
    "Your users are patients, family members, doctors, and genetic researchers.\n\n"
    "CRITICAL — THE DATABASE IS THE PATIENT'S REPORT:\n"
    "The connected SQLite database IS the patient's InterVar-annotated WGS report.\n"
    "When a patient says 'my report', 'my results', 'my data', 'my file', 'my genome',\n"
    "'do I have', 'is X in my report', 'check my report' — they mean THIS database.\n"
    "NEVER say 'I can't access your report'. NEVER say 'please specify which report'.\n"
    "NEVER say 'I don't have access to your personal genetic data'.\n"
    "If a query references the patient's data, the system will automatically query the DB.\n\n"
    "You answer questions about:\n"
    "- Genes: what they do, which diseases they cause, why mutations matter\n"
    "- Symptoms and diseases: which genes are linked, what the genetic cause is\n"
    "- ACMG variant classification: PVS1, PS, PM, PP, BA1, BS, BP criteria\n"
    "- Genomic scores: CADD, SIFT, gnomAD, MetaSVM, PolyPhen, GERP\n"
    "- Variant types: missense, frameshift, stopgain, synonymous, splicing\n"
    "- Medical genetics: inheritance, penetrance, de novo variants, zygosity\n"
    "- InterVar report columns: what each field means, how to interpret results\n\n"
    "SAFETY RULES — apply these strictly:\n"
    "REFUSE (say 'I can't answer this — please speak with a genetic counselor') for:\n"
    "  - 'Do I have [disease]?' / diagnosis questions\n"
    "  - 'What is my risk for X?' / personal risk calculation\n"
    "  - 'Should I take/avoid [drug]?' / medication advice\n"
    "  - 'Can I have children?' / reproductive decisions\n"
    "  - 'Do I need screening?' / clinical management\n\n"
    "ADD DISCLAIMER for pathogenicity of specific variants or zygosity implications. Append:\n"
    "'⚠️ This is for educational purposes only and is not medical advice. "
    "Please discuss with a certified genetic counselor or your physician.'\n\n"
    "ANSWER FREELY (no disclaimer) for general concepts, scores, ACMG codes, gene biology.\n\n"
    "COMMUNICATION RULES:\n"
    "1. NEVER show reasoning or thinking steps — answer directly.\n"
    "2. Answer in 4–8 plain English sentences.\n"
    "3. When using a technical term, explain it immediately in simple words.\n"
    "4. Be warm and empathetic — patients may be worried about their results.\n"
    "5. Be accurate — base answers on established medical genetics.\n"
    "6. For 'What is [gene]?' questions: explain what the gene does and its clinical importance.\n"
    "7. For symptom questions: name the relevant genes and explain the connection simply.\n"
    "8. For 'is any of those in my report?' or pronoun questions: the system will check the DB.\n"
    "   Do NOT deflect — the database lookup happens automatically."
)


def get_llm(backend: Optional[str] = None) -> Optional[Any]:
    """Return a configured LLM instance, or None if unavailable."""
    backend = backend or LLM_BACKEND
    if backend in _cache:
        return _cache[backend]
    if backend in _failed_backends:
        return None

    if backend == "vllm_api":
        llm = _build_vllm_api()
    elif backend == "hf_api":
        llm = _build_hf_api()
    elif backend == "hf_local":
        llm = _build_hf_local()
    else:
        llm = None

    if llm is not None:
        _cache[backend] = llm
    else:
        _failed_backends.add(backend)
    return llm


def _build_vllm_api() -> Optional[Any]:
    """
    vLLM OpenAI-compatible API — Qwen3-32B served on H100.
    Requires vLLM running on remote:
      vllm serve Qwen/Qwen3-32B --port 8001 --download-dir /ephemeral/hf
    Access locally via SSH tunnel:
      ssh -N -L 8001:localhost:8001 ubuntu@62.169.159.252
    Set VLLM_API_URL=http://localhost:8001 in .env
    """
    try:
        from openai import OpenAI
        # Short-timeout client only for the connectivity check
        _check = OpenAI(base_url=f"{VLLM_API_URL}/v1", api_key="not-needed",
                        timeout=5.0, max_retries=0)
        try:
            models = _check.models.list()
            available = [m.id for m in models.data]
            logger.info(f"vLLM API connected at {VLLM_API_URL} — models: {available}")
        except Exception as e:
            logger.warning(f"vLLM API not reachable at {VLLM_API_URL}: {e}")
            return None

        # Inference client with a generous timeout for generation
        client = OpenAI(base_url=f"{VLLM_API_URL}/v1", api_key="not-needed",
                        timeout=120.0, max_retries=0)

        # Qwen3 uses /no_think prefix + extra_body think=False to suppress CoT.
        # MedGemma and other models don't support these — guard all usages.
        _IS_QWEN = "qwen" in VLLM_MODEL.lower()

        def _think_body() -> dict:
            """Return extra_body dict only for Qwen3 models."""
            return {"think": False} if _IS_QWEN else {}

        class _VLLMClient:
            @staticmethod
            def _strip_reasoning(text: str) -> str:
                """Remove chain-of-thought reasoning that leaks into LLM output."""
                import re as _re
                # Remove explicit <think> blocks
                text = _re.sub(r"<think>.*?</think>", "", text, flags=_re.DOTALL).strip()
                if not text:
                    return text

                _REASONING_START = _re.compile(
                    r"^(Okay[,.\s]|Let me|First[,.\s]|Looking at|I need to|"
                    r"The user (asked|is|wants)|So[,.\s](the|I|let|first)|"
                    r"Now[,.\s](I|let|the|looking)|Wait[,.\s]|Hmm[,.\s]|"
                    r"Let's (see|think|check|look)|I'll (start|first|need|try)|"
                    r"I should|I have to|To answer|Before I|In order to|"
                    r"The question asks|I will (now|start|try|need)|"
                    r"Based on the (given|data|results|query)|"
                    r"Looking at the (data|results|rows|top)|"
                    r"Next[,.\s]|Since[,.\s]|Given[,.\s]|However[,.\s]|"
                    r"Therefore[,.\s]|The (results?|data|rows?|provided|given)|"
                    r"For (the|this|each|every) (query|question|result|row|case)|"
                    r"To (find|determine|answer|calculate|check|identify|sort)|"
                    r"We (need|can|have|should|must|see|observe)|"
                    r"It (seems|appears|looks|is important|should)|"
                    r"This (means|shows|indicates|suggests|is)|"
                    r"From the (data|results|rows|information|context)|"
                    r"In (this|the) (case|query|result|context|scenario)|"
                    r"According to|In summary|To summarize|In conclusion|"
                    r"Step \d|First of all|First, (let|we|I)|Note that|"
                    r"The answer|The (highest|lowest|top|best|correct))",
                    _re.IGNORECASE,
                )

                # Inline reasoning that appears after a valid "Found N" opener
                _INLINE_REASONING = _re.compile(
                    r'\b(The user[\'s]*\s+(data|question|ask|is\s+asking)|'
                    r'and then summarizes?|'
                    r'Then\s+summarize|'
                    r'Summarize\s+the\s+key|'
                    r'mention\s+the\s+key\s+finding|'
                    r'Make\s+sure\s+(not|to)\b|'
                    r'Need to\b|'
                    r'Also,\s*(for|since|note|I|need|the|we)\b|'
                    r'Wait,\s*(the|I|but)|'
                    r'But (the user|now I|we need)|'
                    r'So the (user|answer|response|key)|'
                    r'The answer should|'
                    r'note that\b|'
                    r'should (start|focus|mention|explain|translate|state)\b|'
                    r'I\s+need to\s+(mention|translate|include|add)|'
                    r'need to translate|'
                    r'The (types|counts?|rows?) (are|is|should|need))\b',
                    _re.IGNORECASE,
                )

                # Priority: if "Found N result(s)" appears ANYWHERE, start from there.
                m = _re.search(r"\bFound\s+\d+\s+result", text, _re.IGNORECASE)
                if m:
                    candidate = text[m.start():].strip()
                    # Normalise everything immediately after "Found N result(s)" to a single ". "
                    # Absorbs: existing period, stray quote, comma, semicolon, spaces
                    # e.g. 'Found 5 result(s)." ...'  → 'Found 5 result(s). ...'
                    #      'Found 2 result(s)", ...'  → 'Found 2 result(s). ...'
                    candidate = _re.sub(
                        r'^(Found\s+\d+\s+result\(s\))[."\'`,;\s]*',
                        r'\1. ', candidate
                    ).strip()
                    # Split into sentences; stop at the first reasoning sentence
                    sent_parts = _re.split(r'(?<=[.!?])\s+', candidate)
                    clean_sents = []
                    for sent in sent_parts:
                        s = sent.strip().strip('"\'')
                        if not s:
                            continue
                        # After the opener, stop at any reasoning sentence
                        if clean_sents and (
                            _REASONING_START.match(s) or _INLINE_REASONING.search(s)
                        ):
                            break
                        clean_sents.append(s)
                    if clean_sents:
                        return " ".join(clean_sents).strip()
                    return ""  # All reasoning → caller uses data template

                if _REASONING_START.match(text):
                    # Split on double-newlines, take first non-reasoning paragraph
                    parts = _re.split(r"\n{2,}", text)
                    for i, part in enumerate(parts):
                        p = part.strip()
                        if p and not _REASONING_START.match(p) and len(p) > 40:
                            return "\n\n".join(parts[i:]).strip()
                    return ""
                return text.strip()

            @staticmethod
            def _extract_content(resp) -> str:
                msg = resp.choices[0].message
                content = (msg.content or "").strip()
                if not content:
                    content = (getattr(msg, "reasoning", None) or "").strip()
                content = _VLLMClient._strip_reasoning(content)
                return content

            def invoke(self, prompt: str) -> str:
                resp = client.chat.completions.create(
                    model=VLLM_MODEL,
                    messages=[
                        {"role": "system", "content": _SQL_SYSTEM_MSG},
                        {"role": "user",   "content": prompt},
                    ],
                    max_tokens=500,
                    temperature=0.01,
                    extra_body=_think_body(),
                )
                return self._extract_content(resp)

            def explain(self, question: str, rows: list, row_count: int,
                        history: list = None, hpo_context: dict = None) -> str:
                import re as _re
                sample = rows[:5] if rows else []
                if not sample:
                    return f"Found {row_count} result(s). No data to summarise."
                lines = []
                for row in sample:
                    parts = [f"{k}: {v}" for k, v in row.items() if v is not None]
                    lines.append(", ".join(parts))
                data_text = "\n".join(lines)
                if row_count > 5:
                    data_text += f"\n(plus {row_count - 5} more rows not shown)"

                user_content = (
                    f"Question: {question}\n"
                    f"Rows returned: {row_count}\n\n"
                    f"Data ({len(sample)} rows shown):\n{data_text}"
                )
                if hpo_context:
                    user_content += f"\n\nHPO context: {hpo_context}"

                # Ollama/Qwen: prepend /no_think to suppress chain-of-thought
                if _IS_QWEN:
                    user_content = "/no_think\n" + user_content

                messages = [{"role": "system", "content": _EXPLAIN_SYSTEM_MSG}]
                for m in (history or [])[-4:]:
                    role = m.get("role") if isinstance(m, dict) else getattr(m, "role", None)
                    content = m.get("content") if isinstance(m, dict) else getattr(m, "content", None)
                    if role in ("user", "assistant") and content:
                        messages.append({"role": role, "content": str(content)})
                messages.append({"role": "user", "content": user_content})

                resp = client.chat.completions.create(
                    model=VLLM_MODEL,
                    messages=messages,
                    max_tokens=500,
                    temperature=0.1,
                    extra_body=_think_body(),
                )
                content = self._extract_content(resp)
                if not content:
                    return self._data_summary(row_count, sample, question)
                # Always normalise the "Found N" opener to the actual row count
                # (model may copy the example number instead of using the real count)
                if _re.match(r'^Found\s+\d+', content, _re.IGNORECASE):
                    content = _re.sub(
                        r'^Found\s+\d+\s+result[s]?\(?s?\)?\.?',
                        f"Found {row_count} result(s).",
                        content, flags=_re.IGNORECASE
                    ).strip()
                else:
                    content = f"Found {row_count} result(s). " + content.lstrip()
                return content

            @staticmethod
            def _data_summary(row_count: int, rows: list, question: str = "") -> str:  # noqa: ARG004
                """Build a clean data-driven summary when LLM reasoning can't be stripped."""
                if not rows:
                    return f"Found {row_count} result(s) matching your query."
                genes, cadd_top, cadd_gene, n_path = [], None, None, 0
                for row in rows:
                    g = row.get("Ref.Gene") or ""
                    if g and g != "NONE" and g not in genes:
                        genes.append(g)
                    try:
                        c = float(row.get("CADD_phred") or 0)
                        if c and (cadd_top is None or c > cadd_top):
                            cadd_top, cadd_gene = c, g
                    except (ValueError, TypeError):
                        pass
                    iv = str(row.get("InterVar: InterVar and Evidence") or "")
                    if "Pathogenic" in iv:
                        n_path += 1
                parts = [f"Found {row_count} result(s)."]
                if genes:
                    g_str = ", ".join(genes[:5]) + (f" (+{len(genes)-5} more)" if len(genes) > 5 else "")
                    parts.append(f"Genes: {g_str}.")
                if cadd_top:
                    parts.append(f"Highest damage score: CADD {cadd_top:.1f} in {cadd_gene}.")
                if n_path:
                    parts.append(f"{n_path} variant(s) classified as disease-causing.")
                    parts.append("⚠️ Educational only — consult a genetic counselor.")
                return " ".join(parts)

            def answer_general(self, question: str, history: list = None) -> str:
                # Ollama/Qwen: prepend /no_think to suppress chain-of-thought
                q = ("/no_think\n" + question) if _IS_QWEN else question
                messages = [{"role": "system", "content": _GENERAL_SYSTEM_MSG}]
                for m in (history or [])[-6:]:
                    role = m.get("role") if isinstance(m, dict) else getattr(m, "role", None)
                    content = m.get("content") if isinstance(m, dict) else getattr(m, "content", None)
                    if role in ("user", "assistant") and content:
                        messages.append({"role": role, "content": str(content)})
                messages.append({"role": "user", "content": q})
                resp = client.chat.completions.create(
                    model=VLLM_MODEL,
                    messages=messages,
                    max_tokens=800,
                    temperature=0.4,
                    extra_body=_think_body(),
                )
                return self._extract_content(resp)

            def route(self, message: str) -> str:
                """Stage-1 router call — returns raw JSON string for intent classification."""
                import re as _re
                from app.ai.intervar_router import ROUTER_SYSTEM_PROMPT
                # /no_think is a Qwen3-only directive — strip it for other models
                sys_prompt = ROUTER_SYSTEM_PROMPT
                if not _IS_QWEN:
                    sys_prompt = _re.sub(r"^/no_think\s*\n?", "", sys_prompt)
                # Ollama ignores extra_body think=False and system-prompt /no_think
                # — prepend /no_think directly to user message for Qwen/Ollama
                user_msg = ("/no_think\n" + message) if _IS_QWEN else message
                resp = client.chat.completions.create(
                    model=VLLM_MODEL,
                    messages=[
                        {"role": "system", "content": sys_prompt},
                        {"role": "user",   "content": user_msg},
                    ],
                    max_tokens=600,
                    temperature=0.0,
                    extra_body=_think_body(),
                )
                raw = self._extract_content(resp)
                # Strip any stray <think> tags the model leaks (Qwen3)
                raw = _re.sub(r"<think>.*?</think>", "", raw, flags=_re.DOTALL).strip()
                return raw

            @staticmethod
            def _strip_code_output(text: str) -> str:
                """Strip Python/SQL/code blocks that LLMs sometimes generate instead of prose.

                MedGemma may generate lines like:
                  lung_genes = ['CFTR', 'SFTPA1', ...]
                  query = f\"\"\"SELECT ...\"\"\
                This strips those before the response reaches the user.
                """
                import re as _re
                if not text or len(text) < 15:
                    return text
                original = text

                # 1. Remove fenced code blocks (```...```)
                text = _re.sub(r'```[\s\S]*?```', '', text)

                # 2. Remove Python variable assignments with list/dict/string/fstring bodies
                # e.g.  lung_genes = ['CFTR', ...]    query = f"""SELECT ..."""
                text = _re.sub(
                    r'^[a-z_][a-z_0-9]*\s*=\s*[\[{(f"\'][\s\S]*?(?:\]|\}|\)|"""|\'\'\')(?:\s*\n|$)',
                    '', text, flags=_re.MULTILINE
                )
                # Also single-line assignments:  foo = "bar"  /  genes = []
                text = _re.sub(
                    r'^[a-z_][a-z_0-9]*\s*=\s*[^\n]+$',
                    '', text, flags=_re.MULTILINE
                )

                # 3. Remove SQL SELECT … LIMIT blocks
                text = _re.sub(
                    r'SELECT\b[\s\S]*?(?:;\s*|LIMIT\s+\d+\s*;?\s*)(?=\n|$|\Z)',
                    '', text, flags=_re.IGNORECASE
                )

                # 4. Remove Python keyword lines that are clearly code
                text = _re.sub(
                    r'^\s*(?:import\s+\w|from\s+\w+\s+import|def\s+\w|class\s+\w|'
                    r'for\s+\w+\s+in\s|while\s+\w|if\s+\w[^:]*:\s*$|elif\s|else:\s*$|'
                    r'return\s|print\s*\(|#\s*[A-Z].*)\n',
                    '', text, flags=_re.MULTILINE
                )

                # 5. Collapse excess blank lines
                text = _re.sub(r'\n{3,}', '\n\n', text)
                text = text.strip()

                # Safety: if stripping removed most content, return original
                if len(text) < 30 and len(original) > 80:
                    return original
                return text

            def answer(self, user_message: str, history: list = None) -> str:
                """Stage-3 answer call — grounded on executor output in user_message."""
                from app.ai.intervar_router import ANSWER_SYSTEM_PROMPT
                # Ollama ignores extra_body think=False — prepend /no_think to user
                # turn so Qwen3 suppresses chain-of-thought reasoning in the answer.
                if _IS_QWEN:
                    user_message = "/no_think\n" + user_message
                messages = [{"role": "system", "content": ANSWER_SYSTEM_PROMPT}]
                for m in (history or [])[-4:]:
                    role = m.get("role") if isinstance(m, dict) else getattr(m, "role", None)
                    content = m.get("content") if isinstance(m, dict) else getattr(m, "content", None)
                    if role in ("user", "assistant") and content:
                        messages.append({"role": role, "content": str(content)})
                messages.append({"role": "user", "content": user_message})
                resp = client.chat.completions.create(
                    model=VLLM_MODEL,
                    messages=messages,
                    max_tokens=2048,
                    temperature=0.1,
                    extra_body=_think_body(),
                )
                content = self._extract_content(resp)
                # Strip any code blocks that MedGemma/other models may generate
                content = self._strip_code_output(content)
                return content

        logger.info(f"vLLM API backend ready — model: {VLLM_MODEL} @ {VLLM_API_URL}")
        return _VLLMClient()

    except ImportError:
        logger.warning("openai package not installed — run: pip install openai")
    except Exception as e:
        logger.warning(f"vLLM API setup failed: {e}")
    return None


def _build_hf_api() -> Optional[Any]:
    """
    HuggingFace Inference API for Qwen3-8B.
    Requires a token with 'Make calls to the Serverless Inference API' permission.
    Create one at: https://huggingface.co/settings/tokens (click 'New token' → choose 'Read' →
    enable 'Make calls to the Serverless Inference API' checkbox).
    """
    if not HF_TOKEN:
        logger.warning(
            "HF_TOKEN not set. Get a token with inference permissions at "
            "https://huggingface.co/settings/tokens  then: set HF_TOKEN=hf_..."
        )
        return None
    try:
        from huggingface_hub import InferenceClient

        client = InferenceClient(token=HF_TOKEN, timeout=20)

        class _HFChatLLM:
            """Thin wrapper: prompt → Qwen3-8B chat API → SQL string."""
            def __init__(self, c, model):
                self._client = c
                self._model = model

            def invoke(self, prompt: str) -> str:
                system_msg = _SQL_SYSTEM_MSG
                messages = [
                    {"role": "system", "content": system_msg},
                    {"role": "user", "content": prompt},
                ]
                resp = self._client.chat.completions.create(
                    model=self._model,
                    messages=messages,
                    max_tokens=256,
                    temperature=0.01,
                )
                content = resp.choices[0].message.content or ""
                # Strip Qwen3 <think>...</think> blocks if present
                import re as _re
                content = _re.sub(r'<think>.*?</think>', '', content, flags=_re.DOTALL).strip()
                return content

        llm = _HFChatLLM(client, HF_MODEL)
        logger.info(f"HuggingFace Inference API LLM configured: {HF_MODEL}")
        return llm
    except ImportError:
        logger.warning("huggingface-hub not installed — run: pip install huggingface-hub>=0.24")
    except Exception as e:
        logger.warning(f"HF API setup failed: {e}")
    return None


def _build_hf_local() -> Optional[Any]:
    """
    Local HuggingFace transformers pipeline.
    Downloads model on first run (~1.2GB for Qwen3-0.6B, ~3.4GB for Qwen3-1.7B).
    Uses GPU automatically via device_map='auto'. Falls back to CPU if no GPU.
    """
    try:
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer

        model_id = HF_LOCAL_MODEL
        logger.info(f"Loading local model {model_id} — first run will download weights...")

        tokenizer = AutoTokenizer.from_pretrained(model_id)

        # float16 on GPU, float32 on CPU
        dtype = torch.float16 if torch.cuda.is_available() else torch.float32
        model = AutoModelForCausalLM.from_pretrained(
            model_id,
            torch_dtype=dtype,
            device_map="auto",      # GPU if available, else CPU
        )
        model.eval()

        device = next(model.parameters()).device
        logger.info(f"Local model ready: {model_id} on {device}")

        _SYSTEM_MSG = _SQL_SYSTEM_MSG

        class _LocalLLM:
            def __init__(self, m, tok):
                self._model = m
                self._tok = tok

            def invoke(self, prompt: str) -> str:
                import re as _re
                messages = [
                    {"role": "system", "content": _SYSTEM_MSG},
                    {"role": "user",   "content": prompt},
                ]
                # enable_thinking=False disables Qwen3 reasoning (<think>) blocks
                text = self._tok.apply_chat_template(
                    messages,
                    tokenize=False,
                    add_generation_prompt=True,
                    enable_thinking=False,
                )
                inputs = self._tok([text], return_tensors="pt").to(self._model.device)
                with torch.no_grad():
                    outputs = self._model.generate(
                        **inputs,
                        max_new_tokens=256,
                        do_sample=False,
                        temperature=1.0,    # required when do_sample=False
                        pad_token_id=self._tok.eos_token_id,
                    )
                generated = outputs[0][inputs.input_ids.shape[-1]:]
                result = self._tok.decode(generated, skip_special_tokens=True).strip()
                # Strip any leftover <think> blocks
                result = _re.sub(r"<think>.*?</think>", "", result, flags=_re.DOTALL).strip()
                return result

        return _LocalLLM(model, tokenizer)

    except ImportError:
        logger.warning(
            "torch / transformers not installed.\n"
            "Run: pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu121\n"
            "     pip install transformers accelerate"
        )
    except Exception as e:
        logger.warning(f"Local model load failed: {e}")
    return None


def check_llm_status() -> dict:
    """Return status info about configured LLM backends."""
    return {
        "configured_backend": LLM_BACKEND,
        "vllm_api_url": VLLM_API_URL,
        "vllm_model": VLLM_MODEL,
        "hf_model": HF_MODEL,
        "hf_local_model": HF_LOCAL_MODEL,
        "hf_token_set": bool(HF_TOKEN),
        "llm_ready": LLM_BACKEND in _cache,
        "available_backends": ["vllm_api (Qwen3-32B on H100)", "hf_api", "hf_local", "mock"],
    }
