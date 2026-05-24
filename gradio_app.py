# -*- coding: utf-8 -*-
"""
InterVar Genomic AI Assistant - Gradio Frontend
Run:  python gradio_app.py
Open: http://localhost:7860
Backend must be running at http://localhost:8000
  Start backend: uvicorn main:app --reload --port 8000
"""
import requests
import gradio as gr

BACKEND_URL = "http://localhost:8000"

EXAMPLES = [
    "Show VUS variants in BRCA1",
    "What is PVS1 in ACMG criteria?",
    "Find rare missense variants in TP53 with CADD > 25",
    "Look up rs189107123",
    "What does VUS mean?",
    "Average CADD score for stopgain vs synonymous variants",
    "List all in-frame deletions not in a repeat region",
    "Which variants are Pathogenic in ClinVar but have gnomAD frequency > 1%?",
    "What is the gene at chromosome 1 position 10611?",
    "Show frameshift variants in CFTR",
    "Explain CADD scores",
    "What are the ACMG classification tiers?",
]

CSS = """
/* ---- global ---- */
body, .gradio-container {
    background-color: #343541 !important;
    font-family: 'Segoe UI', ui-sans-serif, system-ui, sans-serif;
}
footer { display: none !important; }

/* ---- header ---- */
#header {
    background: linear-gradient(135deg, #10a37f 0%, #1a7f64 100%);
    border-radius: 12px;
    padding: 18px 26px;
    margin-bottom: 6px;
}
#header h1 {
    color: white !important;
    font-size: 1.5rem !important;
    font-weight: 700 !important;
    margin: 0 !important;
}
#header p {
    color: rgba(255,255,255,0.85) !important;
    margin: 4px 0 0 0 !important;
    font-size: 0.88rem !important;
}

/* ---- status box ---- */
#status-box textarea {
    background: #2a2b32 !important;
    border: 1px solid #565869 !important;
    color: #acacbe !important;
    border-radius: 8px !important;
    font-size: 0.8rem !important;
    text-align: center !important;
}

/* ---- chatbot window ---- */
#chatbot {
    background-color: #343541 !important;
    border: 1px solid #565869 !important;
    border-radius: 12px !important;
}

/* ---- message input ---- */
#msg-box textarea {
    background-color: #40414f !important;
    border: 1px solid #565869 !important;
    color: #ececf1 !important;
    border-radius: 12px !important;
    font-size: 0.97rem !important;
}
#msg-box textarea:focus {
    border-color: #10a37f !important;
    box-shadow: 0 0 0 2px rgba(16,163,127,0.25) !important;
}
#msg-box textarea::placeholder { color: #8e8ea0 !important; }

/* ---- buttons ---- */
#send-btn {
    background: #10a37f !important;
    border: none !important;
    border-radius: 10px !important;
    color: white !important;
    font-weight: 600 !important;
}
#send-btn:hover { background: #0d9268 !important; }

#clear-btn, #refresh-btn {
    background: #40414f !important;
    border: 1px solid #565869 !important;
    border-radius: 10px !important;
    color: #ececf1 !important;
}
#clear-btn:hover, #refresh-btn:hover { background: #565869 !important; }

/* ---- SQL / meta panels ---- */
#sql-panel textarea, #meta-panel textarea {
    background-color: #1e1e2e !important;
    color: #a6e3a1 !important;
    font-family: 'Consolas', 'JetBrains Mono', monospace !important;
    font-size: 0.82rem !important;
    border: 1px solid #565869 !important;
    border-radius: 8px !important;
}
#meta-panel textarea { color: #89dceb !important; }

/* ---- example buttons ---- */
.example-row button {
    background-color: #40414f !important;
    border: 1px solid #565869 !important;
    color: #ececf1 !important;
    border-radius: 20px !important;
    font-size: 0.8rem !important;
    padding: 5px 12px !important;
    margin: 2px !important;
    transition: all 0.18s !important;
}
.example-row button:hover {
    background-color: #10a37f !important;
    border-color: #10a37f !important;
    color: white !important;
}

/* ---- labels ---- */
.section-label {
    color: #8e8ea0;
    font-size: 0.73rem;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    margin: 8px 0 2px 2px;
}

/* ---- slider ---- */
input[type=range] { accent-color: #10a37f !important; }
"""


# ---------------------------------------------------------------------------
# Backend helpers
# ---------------------------------------------------------------------------

def get_backend_status() -> str:
    try:
        r = requests.get(f"{BACKEND_URL}/api/health", timeout=3)
        if r.status_code == 200:
            data = r.json()
            count = data.get("variant_count") or data.get("total_variants", "")
            suffix = f"  |  {int(count):,} variants" if count else ""
            return f"Backend online{suffix}"
        return "Backend error"
    except requests.ConnectionError:
        return "Backend offline - start uvicorn"
    except Exception:
        return "Backend status unknown"


def call_chat_api(message: str, history: list, max_rows: int):
    """
    Call POST /api/ai/chat and return (response_text, sql, meta).
    history is a list of {"role": ..., "content": ...} dicts.
    """
    api_history = []
    for m in history:
        if isinstance(m, dict):
            role = str(m.get("role", "user"))
            content = m.get("content", "")
            if not isinstance(content, str):
                content = str(content) if content is not None else ""
            api_history.append({"role": role, "content": content})

    payload = {
        "message": message.strip(),
        "history": api_history,
        "max_rows": int(max_rows),
        "include_sql": True,
    }

    try:
        resp = requests.post(f"{BACKEND_URL}/api/ai/chat", json=payload, timeout=600)
        resp.raise_for_status()
        data = resp.json()

        response_text = data.get("response") or "No response received."
        sql          = data.get("sql") or ""
        sql_source   = data.get("sql_source") or "pattern"
        row_count    = data.get("row_count", 0)
        exec_time    = data.get("execution_time_ms", 0)
        query_type   = data.get("type", "general")
        error        = data.get("error")

        if error:
            response_text = f"Warning: {error}"

        if query_type == "data_query":
            meta = (
                f"Type   : {query_type}\n"
                f"Rows   : {row_count}\n"
                f"Time   : {exec_time} ms\n"
                f"Source : {sql_source}"
            )
        else:
            meta = (
                f"Type   : {query_type}\n"
                f"Time   : {exec_time} ms"
            )

        return response_text, sql, meta

    except requests.Timeout:
        return (
            "Request timed out (>120s).\n\n"
            "Tip: Add a gene name (e.g. BRCA1) or chromosome to narrow the search.",
            "",
            "Status : TIMEOUT",
        )
    except requests.ConnectionError:
        return (
            f"Cannot reach backend at {BACKEND_URL}.\n\n"
            "Start the server:\n  uvicorn main:app --reload --port 8000",
            "",
            "Status : OFFLINE",
        )
    except Exception as e:
        return f"Error: {e}", "", "Status : ERROR"


# ---------------------------------------------------------------------------
# Gradio event handlers
# ---------------------------------------------------------------------------

def chat(message: str, history: list, max_rows: int):
    if not message.strip():
        return history, "", "", ""

    response_text, sql, meta = call_chat_api(message, history, max_rows)

    history = history + [
        {"role": "user",      "content": message.strip()},
        {"role": "assistant", "content": response_text},
    ]
    return history, sql, meta, ""   # "" clears the input box


def use_example(example: str, history: list, max_rows: int):
    return chat(example, history, max_rows)


def clear_chat():
    return [], "", "", ""


# ---------------------------------------------------------------------------
# Build UI
# ---------------------------------------------------------------------------

with gr.Blocks(title="InterVar Genomic AI") as demo:

    # -- Header --------------------------------------------------------------
    with gr.Row(elem_id="header"):
        with gr.Column(scale=5):
            gr.HTML("""
                <div id='header'>
                    <h1>InterVar Genomic AI Assistant</h1>
                    <p>Natural language queries over 4.8M annotated variants
                    &nbsp;&middot;&nbsp; ACMG 2015
                    &nbsp;&middot;&nbsp; gnomAD
                    &nbsp;&middot;&nbsp; ClinVar</p>
                </div>
            """)
        with gr.Column(scale=1, min_width=220):
            status_box = gr.Textbox(
                value=get_backend_status(),
                interactive=False,
                show_label=False,
                elem_id="status-box",
            )

    # -- Chat window ---------------------------------------------------------
    chatbot = gr.Chatbot(
        value=[],
        height=460,
        show_label=False,
        elem_id="chatbot",
        avatar_images=(None, None),
        render_markdown=True,
        layout="bubble",
    )

    # -- Input row -----------------------------------------------------------
    with gr.Row():
        msg_box = gr.Textbox(
            placeholder="Ask anything -- e.g. 'Show VUS variants in BRCA1' or 'What is PVS1?'",
            show_label=False,
            lines=1,
            max_lines=4,
            scale=8,
            elem_id="msg-box",
        )
        send_btn  = gr.Button("Send", variant="primary", scale=1, elem_id="send-btn")
        clear_btn = gr.Button("Clear", scale=1, elem_id="clear-btn")

    # -- Options row ---------------------------------------------------------
    with gr.Row():
        max_rows = gr.Slider(
            minimum=5, maximum=100, value=20, step=5,
            label="Max rows returned",
            scale=3,
            elem_id="max-rows",
        )
        refresh_btn = gr.Button("Refresh Status", scale=1, size="sm", elem_id="refresh-btn")

    # -- SQL + Metadata panel ------------------------------------------------
    with gr.Row():
        with gr.Column(scale=3):
            gr.HTML("<p class='section-label'>Generated SQL</p>")
            sql_box = gr.Textbox(
                value="",
                show_label=False,
                lines=4,
                max_lines=8,
                interactive=False,
                placeholder="SQL will appear here for data queries...",
                elem_id="sql-panel",
            )
        with gr.Column(scale=1):
            gr.HTML("<p class='section-label'>Query Info</p>")
            meta_box = gr.Textbox(
                value="",
                show_label=False,
                lines=4,
                interactive=False,
                placeholder="Rows / time / source",
                elem_id="meta-panel",
            )

    # -- Example questions ---------------------------------------------------
    gr.HTML("<p class='section-label' style='margin-top:10px'>Quick examples</p>")
    with gr.Row(elem_classes=["example-row"]):
        ex_btns = [gr.Button(ex, size="sm") for ex in EXAMPLES[:6]]
    with gr.Row(elem_classes=["example-row"]):
        ex_btns += [gr.Button(ex, size="sm") for ex in EXAMPLES[6:]]

    for btn in ex_btns:
        btn.click(
            fn=use_example,
            inputs=[btn, chatbot, max_rows],
            outputs=[chatbot, sql_box, meta_box, msg_box],
        )

    # -- Wire events ---------------------------------------------------------
    send_btn.click(
        fn=chat,
        inputs=[msg_box, chatbot, max_rows],
        outputs=[chatbot, sql_box, meta_box, msg_box],
    )
    msg_box.submit(
        fn=chat,
        inputs=[msg_box, chatbot, max_rows],
        outputs=[chatbot, sql_box, meta_box, msg_box],
    )
    clear_btn.click(
        fn=clear_chat,
        outputs=[chatbot, sql_box, meta_box, msg_box],
    )
    refresh_btn.click(
        fn=get_backend_status,
        outputs=status_box,
    )


if __name__ == "__main__":
    import time as _time

    demo.launch(
        server_name="0.0.0.0",
        server_port=7860,
        share=True,
        show_error=True,
        prevent_thread_lock=True,
    )

    # Wait for share tunnel then persist URL
    _time.sleep(30)
    _url = getattr(demo, "share_url", None) or "not available"
    with open("gradio_public_url.txt", "w") as _f:
        _f.write(_url + "\n")
    print(f"\n>>> GRADIO PUBLIC URL: {_url}\n", flush=True)

    demo.block_thread()
