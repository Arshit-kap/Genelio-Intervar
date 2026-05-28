"""Run the original legacy/app.py RAG pipeline against the same gut.pdf
with the same questions used by scripts/e2e_django.py, so we can compare
responses side-by-side.

We stub out ``gradio`` before importing ``legacy.app`` (the legacy module
imports gradio at top level for the UI, but we only need the non-UI
functions: extract_text_from_pdf, chunk_pages, build_vector_store,
retrieve, ask_llm, plus the report_analyzer parser).
"""
from __future__ import annotations

import json
import os
import sys
import time
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _stub_gradio() -> None:
    """Install a minimal gradio stub so ``legacy/app.py`` imports cleanly."""
    if "gradio" in sys.modules:
        return
    gr = types.ModuleType("gradio")

    # ``legacy/app.py`` calls: gr.themes.Soft(...), gr.Blocks(...),
    # gr.HTML(...), gr.Row/Column, gr.Markdown, gr.File, gr.Button,
    # gr.Chatbot, gr.Textbox. Everything outside `if __name__ == "__main__"`
    # so we need callables + context managers that no-op.

    class _Any:
        def __init__(self, *a, **kw): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def __call__(self, *a, **kw): return self
        def click(self, *a, **kw): return self
        def submit(self, *a, **kw): return self
        def then(self, *a, **kw): return self

    class _Themes:
        Soft = _Any
    gr.themes = _Themes()
    for name in ("Blocks", "HTML", "Row", "Column", "Markdown", "File",
                 "Button", "Chatbot", "Textbox"):
        setattr(gr, name, _Any)
    sys.modules["gradio"] = gr


def main() -> int:
    pdf = Path(os.getenv("GUT_PDF", str(Path.home() / "Downloads" / "gut.pdf")))
    if not pdf.exists():
        print(f"PDF not found at {pdf}", file=sys.stderr)
        return 2

    # Make ``legacy`` importable as a package.
    sys.path.insert(0, str(ROOT / "legacy"))
    _stub_gradio()
    import app as legacy  # noqa: E402 — after path + gradio stub

    print(f"=== Legacy pipeline on {pdf.name} ({pdf.stat().st_size:,} bytes) ===")

    # Build the same pipeline the Gradio `process_upload` callback builds.
    t0 = time.perf_counter()
    pages = legacy.extract_text_from_pdf(str(pdf))
    chunks = legacy.chunk_pages(pages)
    collection = legacy.build_vector_store(chunks)
    report = legacy.extract_all_tables(str(pdf))
    analysis_context = legacy.build_full_analysis_context(report)
    upload_elapsed = time.perf_counter() - t0
    print(f"Indexed {len(pages)} pages / {len(chunks)} chunks in {upload_elapsed:.1f}s")
    print(f"Parsed report keys: {sorted(report.keys())}")
    print(f"  patient:     {report.get('patient')}")
    print(f"  diversity:   {report.get('diversity')}")
    print(f"  fb_ratio:    {report.get('fb_ratio')}")
    print(f"  keystones:   "
          f"{len(report.get('keystone_present', []))} present / "
          f"{len(report.get('keystone_missing', []))} missing")
    print(f"  conditions:  {len(report.get('conditions', {}))}")

    questions = [
        "Is my gut microbiome healthy overall?",
        "Tell me about my Shannon Diversity score — what does it mean for me?",
        "Which keystone species am I missing, and what foods can help?",
        "Tell me about my depression markers — list every one with its range and status.",
        "Do I have any pathogens flagged in this report?",
    ]

    chat_history: list[dict] = []
    transcript = []
    for i, q in enumerate(questions, 1):
        print(f"\n--- Q{i}: {q} ---")
        t0 = time.perf_counter()
        rag_context = legacy.retrieve(collection, q)
        answer = legacy.ask_llm(q, analysis_context, rag_context, chat_history)
        elapsed = time.perf_counter() - t0
        print(f"[{elapsed:.1f}s] {len(answer)} chars")
        print(answer)
        chat_history.append({"role": "user", "content": q})
        chat_history.append({"role": "assistant", "content": answer})
        transcript.append({"q": q, "a": answer, "elapsed": elapsed})

    out = Path("scripts/_out_legacy.json")
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps({
        "upload_seconds": upload_elapsed,
        "pages": len(pages),
        "chunks": len(chunks),
        "parsed_report": report,
        "transcript": transcript,
    }, indent=2, default=str))
    print(f"\nSaved -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
