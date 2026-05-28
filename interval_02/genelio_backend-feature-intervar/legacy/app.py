"""
Genelio Bot — Gut Microbiome Report Assistant
Upload your gut microbiome PDF report and ask questions in plain language.
"""

import os
import re

import chromadb
import gradio as gr
import pdfplumber
from openai import OpenAI
from sentence_transformers import SentenceTransformer

from report_analyzer import (
    extract_all_tables,
    build_full_analysis_context,
    build_structured_summary,
    status_emoji,
)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
VLLM_BASE_URL = os.getenv("VLLM_BASE_URL", "http://localhost:8011/v1")
MODEL_NAME = os.getenv("MODEL_NAME", "qwen3-30b")
EMBED_MODEL = os.getenv("EMBED_MODEL", "nomic-ai/nomic-embed-text-v1.5")
COLLECTION = "gut_report"
CHUNK_SIZE = 1200
CHUNK_OVERLAP = 200

llm = OpenAI(base_url=VLLM_BASE_URL, api_key="not-needed")
embedder = SentenceTransformer(EMBED_MODEL, trust_remote_code=True)
chroma_client = chromadb.Client()

# Module-level session store (single-user for now)
_session = {
    "collection": None,
    "pages": [],
    "report": None,          # structured parsed report
    "analysis_context": "",  # pre-built analysis text
}

# ---------------------------------------------------------------------------
# PDF processing
# ---------------------------------------------------------------------------

def extract_text_from_pdf(pdf_path: str) -> list[dict]:
    """Extract text page-by-page from a PDF."""
    pages = []
    with pdfplumber.open(pdf_path) as pdf:
        for i, page in enumerate(pdf.pages):
            text = page.extract_text()
            if text and text.strip():
                pages.append({"page": i + 1, "text": text.strip()})
    return pages


def chunk_pages(pages: list[dict]) -> list[dict]:
    """Split page texts into overlapping chunks with section headers."""
    chunks = []
    for p in pages:
        text = p["text"]
        first_line = text.split("\n")[0][:120]
        start = 0
        while start < len(text):
            end = start + CHUNK_SIZE
            chunk_text = text[start:end]
            tagged = f"[Section: {first_line}] [Page {p['page']}]\n{chunk_text}"
            chunks.append({
                "id": f"p{p['page']}_c{len(chunks)}",
                "text": chunk_text,
                "tagged_text": tagged,
                "page": p["page"],
            })
            start += CHUNK_SIZE - CHUNK_OVERLAP
    return chunks

# ---------------------------------------------------------------------------
# Vector store
# ---------------------------------------------------------------------------

def build_vector_store(chunks: list[dict]) -> chromadb.Collection:
    """Embed chunks and store in ChromaDB."""
    try:
        chroma_client.delete_collection(COLLECTION)
    except Exception:
        pass
    collection = chroma_client.create_collection(
        name=COLLECTION,
        metadata={"hnsw:space": "cosine"},
    )

    tagged_texts = [c["tagged_text"] for c in chunks]
    raw_texts = [c["text"] for c in chunks]
    ids = [c["id"] for c in chunks]
    metadatas = [{"page": c["page"]} for c in chunks]

    # Nomic v1.5 uses task prefixes for best retrieval quality
    doc_texts = [f"search_document: {t}" for t in tagged_texts]
    embeddings = embedder.encode(doc_texts, show_progress_bar=False).tolist()

    collection.add(
        ids=ids,
        embeddings=embeddings,
        documents=raw_texts,
        metadatas=metadatas,
    )
    return collection


def retrieve(collection: chromadb.Collection, query: str, top_k: int = 8) -> str:
    """Retrieve the most relevant chunks for a query."""
    query_emb = embedder.encode([f"search_query: {query}"]).tolist()
    results = collection.query(query_embeddings=query_emb, n_results=top_k)
    docs = results["documents"][0]
    metas = results["metadatas"][0]
    seen = {}
    for doc, meta in zip(docs, metas):
        page = meta["page"]
        if page not in seen:
            seen[page] = []
        seen[page].append(doc)
    context_parts = []
    for page in sorted(seen.keys()):
        combined = "\n".join(seen[page])
        context_parts.append(f"[Page {page}]\n{combined}")
    return "\n\n---\n\n".join(context_parts)

# ---------------------------------------------------------------------------
# Upload summary builder
# ---------------------------------------------------------------------------

def build_upload_summary(report: dict) -> str:
    """Build the sidebar summary shown after upload."""
    parts = []

    # Patient
    p = report.get("patient", {})
    name = p.get("Name", "Patient")
    parts.append(f"**Patient**: {name}")

    # Diversity
    d = report.get("diversity", {})
    if d:
        emoji = "🟢" if d["status"] == "within_range" else "🔴"
        parts.append(f"**Shannon Diversity**: {d['score']} {emoji} (healthy: {d['range']})")

    # F/B ratio
    fb = report.get("fb_ratio", {})
    if fb:
        emoji = "🟢" if fb["status"] == "within_range" else "🔴"
        parts.append(f"**F/B Ratio**: {fb['ratio']} {emoji} (healthy: {fb['range']})")

    # Keystone species
    present = report.get("keystone_present", [])
    missing = report.get("keystone_missing", [])
    top_organisms = report.get("top_organisms", [])
    if present or missing:
        parts.append(
            f"**Keystone Species**: {len(present)} present, "
            f"{len(missing)} missing"
        )
        if present:
            for org in present:
                parts.append(f"  - {org['name']} {status_emoji(org['status'])}")
        if missing:
            parts.append(f"**Missing**: {', '.join(missing)}")

    # Flagged conditions count
    conditions = report.get("conditions", {})
    flagged = 0
    for cond_data in conditions.values():
        out_of_range = [m for m in cond_data["markers"] if m["status"] in ("above_range", "below_range")]
        if out_of_range:
            flagged += 1
    if flagged:
        parts.append(f"**Conditions with flags**: {flagged} conditions have out-of-range markers")

    return "\n".join(f"- {s}" for s in parts)

# ---------------------------------------------------------------------------
# LLM call
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """\
You are **Genelio**, a friendly and professional gut-health assistant.

You have access to TWO types of context:
1. **STRUCTURED ANALYSIS** — Pre-parsed data from the user's report with exact abundance values, \
healthy reference ranges, and status flags (ABOVE_RANGE, BELOW_RANGE, WITHIN_RANGE, NOT_DETECTED) \
for every biomarker across all conditions.
2. **RAG CONTEXT** — Raw text excerpts retrieved from the report for additional details.

ALWAYS prefer the structured analysis for numerical comparisons. Use the RAG context for \
explanations, significance descriptions, and lifestyle recommendations.

Your job:
1. Help the user understand their gut microbiome report in simple, reassuring language.
2. For EVERY condition the user asks about, list ALL biomarkers checked for that condition \
along with their actual abundance vs healthy reference range, and clearly state if each is \
ABOVE, BELOW, or within normal range.
3. When a marker is out of range, explain what it means and suggest practical dietary or \
lifestyle improvements — but always remind the user to consult their healthcare provider.
4. When asked about keystone species, list ALL present keystone species AND all missing ones. \
Include dietary recommendations for the missing ones.
5. If you don't have enough information, say so honestly.

Formatting guidelines:
- Use tables (markdown) when comparing multiple markers.
- Use 🔴 for above-range, 🟡 for below-range, 🟢 for normal, ⚪ for not-detected.
- Avoid overly technical jargon; explain medical terms in plain language.
- Be empathetic — many users may feel anxious about their results.
- NEVER diagnose diseases. You provide educational information only.
- Keep answers concise but thorough.
"""


def ask_llm(query: str, structured_context: str, rag_context: str, chat_history: list[dict]) -> str:
    """Send the query + both context types to the local LLM."""
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]

    # Include recent chat history (last 6 turns) for continuity
    for msg in chat_history[-6:]:
        messages.append(msg)

    user_msg = (
        f"STRUCTURED ANALYSIS (pre-parsed from report):\n"
        f"```\n{structured_context}\n```\n\n"
        f"RAG CONTEXT (raw text excerpts from report):\n"
        f"```\n{rag_context}\n```\n\n"
        f"USER QUESTION: {query}"
    )
    messages.append({"role": "user", "content": user_msg})

    response = llm.chat.completions.create(
        model=MODEL_NAME,
        messages=messages,
        max_tokens=3072,
        temperature=0.3,
        extra_body={"chat_template_kwargs": {"enable_thinking": False}},
    )
    raw = response.choices[0].message.content

    # Fallback: strip <think>...</think> blocks if model still emits them
    cleaned = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
    return cleaned if cleaned else raw.strip()

# ---------------------------------------------------------------------------
# Gradio app
# ---------------------------------------------------------------------------

def _extract_text(content) -> str:
    """Extract plain text from Gradio 6 message content.
    Handles both plain strings and Gradio 6 list-of-dicts format."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and "text" in item:
                parts.append(item["text"])
            elif isinstance(item, str):
                parts.append(item)
        return " ".join(parts)
    return str(content) if content is not None else ""


def process_upload(file_path):
    """Handle PDF upload: extract, chunk, embed, and parse structured data."""
    if file_path is None:
        return "⚠️ Please upload a PDF file."

    pages = extract_text_from_pdf(file_path)
    if not pages:
        return "⚠️ Could not extract text from this PDF. Is it a scanned image?"

    # Build vector store for RAG
    chunks = chunk_pages(pages)
    collection = build_vector_store(chunks)

    # Parse structured report data
    report = extract_all_tables(file_path)
    analysis_context = build_full_analysis_context(report)

    _session["collection"] = collection
    _session["pages"] = pages
    _session["report"] = report
    _session["analysis_context"] = analysis_context

    upload_summary = build_upload_summary(report)

    return (
        f"✅ **Report processed successfully!**\n\n"
        f"**Quick Overview:**\n{upload_summary}\n\n"
        f"---\n"
        f"- **Pages extracted**: {len(pages)}\n"
        f"- **Text chunks indexed**: {len(chunks)}\n"
        f"- **Conditions analyzed**: {len(report.get('conditions', {}))}\n\n"
        f"Ask me anything about this report! For example:\n"
        f'- *"Is my gut healthy overall?"*\n'
        f'- *"Tell me about my depression markers"*\n'
        f'- *"Which keystone species am I missing?"*\n'
        f'- *"What foods can help improve my gut?"*\n'
        f'- *"Do I have any pathogens?"*'
    )


def chat_respond(history):
    """Handle a chat message: retrieve context, call LLM, return response."""
    if not history:
        history = []

    if _session["collection"] is None:
        history.append({
            "role": "assistant",
            "content": "📄 Please upload your gut microbiome report first using the panel on the left.",
        })
        return history

    # Extract the last user message (handle Gradio 6 content format)
    message = ""
    if history and history[-1].get("role") == "user":
        message = _extract_text(history[-1].get("content", ""))
    if not message.strip():
        return history

    # RAG retrieval for additional raw context
    rag_context = retrieve(_session["collection"], message)

    # Pre-built structured analysis (always included)
    structured_context = _session.get("analysis_context", "")

    # Build LLM history from chat (exclude the last user msg)
    llm_history = []
    for msg in history[:-1]:
        llm_history.append({
            "role": msg.get("role", "user"),
            "content": _extract_text(msg.get("content", "")),
        })

    answer = ask_llm(message, structured_context, rag_context, llm_history)

    history.append({"role": "assistant", "content": answer})
    return history


# ---------------------------------------------------------------------------
# UI Layout
# ---------------------------------------------------------------------------

THEME = gr.themes.Soft(
    primary_hue="teal",
    secondary_hue="emerald",
    neutral_hue="slate",
)

CSS = """
#header { text-align: center; margin-bottom: 0.5em; }
#header h1 { color: #0d9488; margin-bottom: 0; font-size: 2em; }
#header p { color: #64748b; font-size: 0.95em; }
#upload-status { min-height: 120px; }
.disclaimer { font-size: 0.8em; color: #94a3b8; text-align: center; padding: 8px; }
"""

with gr.Blocks(title="Genelio — Gut Health Assistant") as demo:
    gr.HTML(
        """
        <div id="header">
            <h1>🧬 Genelio</h1>
            <p>Your personal gut microbiome report assistant — upload your report and ask anything</p>
        </div>
        """
    )

    with gr.Row():
        # --- Left sidebar ---
        with gr.Column(scale=1, min_width=320):
            gr.Markdown("### 📄 Upload Report")
            file_input = gr.File(
                label="Upload your gut microbiome PDF",
                file_types=[".pdf"],
                type="filepath",
            )
            upload_btn = gr.Button("🔍 Analyze Report", variant="primary", size="lg")
            upload_status = gr.Markdown(
                value="Upload your PDF report to get started.",
                elem_id="upload-status",
            )

            gr.Markdown("---")
            gr.Markdown(
                "### 💡 Example Questions\n"
                "- Is my gut microbiome healthy?\n"
                "- What does my Shannon Diversity score mean?\n"
                "- Which keystone species am I missing?\n"
                "- Tell me about my depression markers\n"
                "- What about my IBD / obesity markers?\n"
                "- What foods should I eat to improve my gut?\n"
                "- Tell me about my F/B ratio\n"
                "- Do I have any pathogens?\n"
            )

        # --- Main chat area ---
        with gr.Column(scale=3):
            chatbot = gr.Chatbot(
                label="Chat with Genelio",
                height=580,
                placeholder="Upload your report and start asking questions...",
            )
            with gr.Row():
                msg_input = gr.Textbox(
                    placeholder="Ask about your gut health report...",
                    show_label=False,
                    scale=9,
                    container=False,
                )
                send_btn = gr.Button("Send", variant="primary", scale=1)

    gr.HTML(
        '<div class="disclaimer">'
        "⚕️ Genelio provides educational information only — not medical advice. "
        "Always consult your healthcare provider before making changes."
        "</div>"
    )

    # --- Event wiring ---
    upload_btn.click(
        fn=process_upload,
        inputs=[file_input],
        outputs=[upload_status],
    )

    def user_message(message, history):
        """Add user message to chat and clear input."""
        if history is None:
            history = []
        if not message or not message.strip():
            return "", history
        history.append({"role": "user", "content": message})
        return "", history

    msg_input.submit(
        fn=user_message,
        inputs=[msg_input, chatbot],
        outputs=[msg_input, chatbot],
    ).then(
        fn=chat_respond,
        inputs=[chatbot],
        outputs=[chatbot],
    )

    send_btn.click(
        fn=user_message,
        inputs=[msg_input, chatbot],
        outputs=[msg_input, chatbot],
    ).then(
        fn=chat_respond,
        inputs=[chatbot],
        outputs=[chatbot],
    )

# ---------------------------------------------------------------------------
# Launch
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    demo.launch(
        server_name="0.0.0.0",
        server_port=7860,
        share=True,
        css=CSS,
        theme=THEME,
        show_error=True,
    )
