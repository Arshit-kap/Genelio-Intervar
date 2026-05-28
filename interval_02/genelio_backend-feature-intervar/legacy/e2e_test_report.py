"""
End-to-end test harness: uploads each PDF, asks a standard battery of questions,
captures every response, and prints a structured report.
"""

import os, sys, time, textwrap

os.environ["GRADIO_ANALYTICS_ENABLED"] = "False"

# ── import the app functions directly ──────────────────────────────────────
sys.path.insert(0, os.path.dirname(__file__))
from app import process_upload, chat_respond, _session

# ── PDFs to test ──────────────────────────────────────────────────────────
PDFS = [
    ("/Users/user/Downloads/GM AL C17 MR.pdf",           "GM AL C17 MR"),
    ("/Users/user/Downloads/GM AL C18 MS.pdf",           "GM AL C18 MS"),
    ("/Users/user/Downloads/Mail from Arshit Arora.pdf", "Mail from Arshit Arora"),
]

# ── Standard question battery ─────────────────────────────────────────────
QUESTIONS = [
    "Is my gut microbiome healthy overall? Give me a quick summary.",
    "What is my Shannon Diversity score and what does it mean?",
    "Tell me about my F/B ratio — is it normal?",
    "Which keystone species am I missing and what foods can help?",
    "Tell me about my depression markers — are any out of range?",
    "What about my obesity markers?",
    "Do I have any IBD or inflammatory bowel markers that are flagged?",
    "Do I have any pathogens detected?",
    "Tell me about my fungi, archaea, and virus findings.",
    "What are the top 5 things I should do to improve my gut health based on this report?",
]

SEP = "=" * 90

def truncate(text, max_chars=600):
    """Show first max_chars of a long response."""
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + f"\n  ... [truncated, total {len(text)} chars]"

def run_test(pdf_path, label):
    """Upload one PDF and ask all standard questions."""
    print(f"\n{SEP}")
    print(f"  PDF: {label}")
    print(f"  Path: {pdf_path}")
    print(SEP)

    # ── Upload ─────────────────────────────────────────────────────────
    t0 = time.time()
    upload_result = process_upload(pdf_path)
    upload_time = time.time() - t0
    print(f"\n📄 UPLOAD RESULT ({upload_time:.1f}s):\n")
    print(textwrap.indent(upload_result, "  "))

    report = _session.get("report")
    if report is None:
        print("\n  ⚠️  STRUCTURED REPORT IS NONE — skipping questions.\n")
        return {"label": label, "upload": upload_result, "answers": {}, "error": "No report parsed"}

    # Quick structured data sanity check
    print(f"\n  → Patient: {report.get('patient', {}).get('Name', 'N/A')}")
    print(f"  → Diversity: {report.get('diversity', {})}")
    print(f"  → F/B Ratio: {report.get('fb_ratio', {})}")
    print(f"  → Keystone present: {len(report.get('keystone_present', []))}")
    print(f"  → Keystone missing: {len(report.get('keystone_missing', []))}")
    print(f"  → Conditions: {len(report.get('conditions', {}))}")
    print(f"  → Pathogens: {len(report.get('pathogens', []))}")
    print(f"  → Fungi: {len(report.get('fungi', []))}")
    print(f"  → Archaea: {len(report.get('archaea', []))}")
    print(f"  → Viruses: {len(report.get('viruses', []))}")

    # ── Ask questions ──────────────────────────────────────────────────
    answers = {}
    for i, q in enumerate(QUESTIONS, 1):
        print(f"\n{'─' * 80}")
        print(f"  Q{i}: {q}")
        print(f"{'─' * 80}")

        history = [{"role": "user", "content": q}]
        t0 = time.time()
        try:
            result = chat_respond(history)
            elapsed = time.time() - t0

            # Last assistant message
            assistant_msgs = [m for m in result if m.get("role") == "assistant"]
            answer = assistant_msgs[-1]["content"] if assistant_msgs else "(no answer)"

            print(f"  ⏱ {elapsed:.1f}s  |  {len(answer)} chars")
            print()
            print(textwrap.indent(truncate(answer, 800), "  "))
            answers[q] = {"answer": answer, "time": elapsed, "chars": len(answer)}
        except Exception as e:
            print(f"  ❌ ERROR: {e}")
            answers[q] = {"answer": f"ERROR: {e}", "time": 0, "chars": 0}

    return {"label": label, "upload": upload_result, "answers": answers, "error": None}


# ── Main ──────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    all_results = []
    for pdf_path, label in PDFS:
        result = run_test(pdf_path, label)
        all_results.append(result)

    # ── Summary ────────────────────────────────────────────────────────
    print(f"\n\n{'█' * 90}")
    print(f"  SUMMARY ACROSS ALL PDFs")
    print(f"{'█' * 90}")

    for r in all_results:
        label = r["label"]
        print(f"\n  📋 {label}:")
        if r["error"]:
            print(f"    ⚠️  Error: {r['error']}")
            continue
        for q, a in r["answers"].items():
            status = "✅" if a["chars"] > 100 else "⚠️ SHORT"
            print(f"    {status} [{a['time']:.1f}s, {a['chars']}c] {q[:60]}")

    print(f"\n{'█' * 90}")
    print("  DONE")
    print(f"{'█' * 90}\n")
