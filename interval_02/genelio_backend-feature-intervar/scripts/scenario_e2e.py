"""End-to-end scenario runner — REAL router LLM included.

Drives the full Stage 1→6 pipeline for every PDF question category
against the synthetic clinical_csv from chatbot.test_scenarios. Unlike
the SimpleTestCase harness (which builds RouterDecision objects
directly), this script calls ``orchestrator.classify(message)`` —
the actual LLM router — and verifies it correctly identifies the
intent + extracts entities.

Run:
    DJANGO_SETTINGS_MODULE=genelio.settings \\
    DATABASE_URL=sqlite:///./test.sqlite3 \\
    DJANGO_SECRET_KEY=test-only \\
    DJANGO_DEBUG=True DJANGO_ALLOWED_HOSTS='*' \\
    python scripts/scenario_e2e.py
"""
from __future__ import annotations

import os
import sys
import textwrap


def _bootstrap():
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "genelio.settings")
    os.environ.setdefault("DATABASE_URL", "sqlite:///./test.sqlite3")
    os.environ.setdefault("DJANGO_SECRET_KEY", "test-only-key")
    os.environ.setdefault("DJANGO_DEBUG", "True")
    os.environ.setdefault("DJANGO_ALLOWED_HOSTS", "*")
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    import django
    django.setup()


_bootstrap()


from chatbot.agentic import hpo, orchestrator  # noqa: E402
from chatbot.test_scenarios import _build_synthetic_parsed_data  # noqa: E402


# ---------------------------------------------------------------------------
# PDF question matrix — multiple paraphrases per category. Each entry:
#   (label, expected_category, expected_entity_key, expected_entity_value,
#    [paraphrases])
# ---------------------------------------------------------------------------

MATRIX = [
    ("A1 — harmful variants",
     "A1", None, None,
     [
         "Are any of my variants harmful?",
         "Do I have pathogenic variants?",
         "Which variants are disease-causing?",
         "Show me my dangerous mutations.",
     ]),
    ("A2 — ClinVar vs InterVar disagreement",
     "A2", "target_gene", "TP53",
     [
         "Why does ClinVar say one thing and InterVar another for my TP53 variant?",
         "ClinVar says VUS for TP53 but InterVar says pathogenic — why?",
     ]),
    ("B1 — column lookup",
     "B1", "target_column", "CADD_phred",
     [
         "What does CADD_phred mean?",
         "Explain the CADD_phred score in my report.",
     ]),
    ("C1 — body system",
     "C1", "target_body_system", "lungs",
     [
         "Do I have anything that could affect my lungs?",
         "Anything related to my lungs in this report?",
         "Are there any lung-related variants?",
     ]),
    ("C2 — multi-symptom",
     "C2", None, None,
     [
         "I have weak muscles and trouble seeing at night — what could it be?",
         "I'm tired, bruise easily, and have joint pain.",
     ]),
    ("D1 — disease-named (Marfan)",
     "D1", "target_disease", "Marfan",
     [
         "Do I have anything related to Marfan syndrome?",
         "Am I at risk for Marfan?",
     ]),
    ("D2 — gene-to-disease",
     "D2", "target_gene", "BRCA1",
     [
         "Tell me about the disease BRCA1 causes.",
         "What conditions are linked to BRCA1?",
     ]),
    ("E1 — inheritance pattern",
     "E1", "target_gene", "CFTR",
     [
         "How is the CFTR condition inherited?",
         "Is the CFTR variant dominant or recessive?",
     ]),
    ("E2 — children inherit",
     "E2", "target_gene", "BRCA1",
     [
         "Will my children inherit this BRCA1 variant?",
         "Can I pass my BRCA1 variant to my kids?",
     ]),
    ("F1 — carrier",
     "F1", None, None,
     [
         "Am I a carrier for any recessive conditions?",
         "Do I carry any silent diseases?",
     ]),
    ("F2 — secondary findings",
     "F2", None, None,
     [
         "What secondary findings do I have?",
         "Are there incidental findings?",
         "Do I have anything from the ACMG list?",
     ]),
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def dispatch_with_router(parsed, decision):
    """Replicate orchestrator dispatch but pre-build the resolution_summary
    for C2 from the router's extracted symptoms (since we don't have a
    SessionPhenotypeProfile in this script)."""
    handler = orchestrator._DISPATCH.get(decision.intent_category)
    if handler is None:
        return None
    if decision.intent_category == "C2":
        added = []
        for sym in decision.symptoms:
            m = hpo.resolve(sym)
            if m.hpo_id:
                added.append({
                    "input_text": sym, "hpo_id": m.hpo_id,
                    "name": m.name, "confidence": m.confidence,
                    "matched_via": m.matched_via,
                    "gene_count": len(m.genes),
                })
        return handler(parsed, decision, None,
                       {"added": added, "unresolved": []})
    return handler(parsed, decision)


def evaluate(decision, expected_cat, expected_key, expected_value):
    """Return (pass: bool, reason: str)."""
    reasons = []
    if decision.intent_category != expected_cat:
        reasons.append(
            f"router classified as {decision.intent_category!r}, "
            f"expected {expected_cat!r}"
        )
    if expected_key:
        got = getattr(decision, expected_key, None)
        if not got or expected_value.lower() not in str(got).lower():
            reasons.append(
                f"{expected_key}={got!r}, expected to contain "
                f"{expected_value!r}"
            )
    return (not reasons, "; ".join(reasons) or "OK")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def main():
    parsed = _build_synthetic_parsed_data()
    print(f"Loaded synthetic report — {parsed['report']['counts']['total_variants']} variants\n")

    total = 0
    routed_ok = 0
    rendered_ok = 0
    failures = []

    for label, expected_cat, exp_key, exp_value, prompts in MATRIX:
        print(f"\n{'='*100}")
        print(f"  {label}  (expect: {expected_cat}"
              + (f", {exp_key}~{exp_value!r}" if exp_key else "")
              + ")")
        print(f"{'='*100}")
        for prompt in prompts:
            total += 1
            print(f"\n  USER: {prompt}")
            try:
                decision = orchestrator.classify(prompt)
            except Exception as exc:
                print(f"  [router ERROR] {exc}")
                failures.append((label, prompt, f"router error: {exc}"))
                continue
            ok_route, reason = evaluate(decision, expected_cat,
                                        exp_key, exp_value)
            marker = "✓" if ok_route else "✗"
            print(f"  {marker} ROUTER: category={decision.intent_category}"
                  f" gene={decision.target_gene}"
                  f" disease={decision.target_disease}"
                  f" body_system={decision.target_body_system}"
                  f" column={decision.target_column}"
                  f" symptoms={decision.symptoms}"
                  f" intersect={decision.intersect_symptoms}")
            if not ok_route:
                print(f"    ROUTER FAIL — {reason}")
                failures.append((label, prompt, reason))
                continue
            routed_ok += 1

            response = dispatch_with_router(parsed, decision)
            if response is None:
                print("  ✗ RENDER returned None — fell through to LLM fallback")
                failures.append((label, prompt, "renderer returned None"))
                continue
            rendered_ok += 1
            # Print response, indented and truncated for terminal sanity
            for line in textwrap.dedent(response).splitlines()[:14]:
                print(f"      {line}")
            if len(response.splitlines()) > 14:
                print(f"      … ({len(response.splitlines()) - 14} more lines)")

    print(f"\n{'='*100}")
    print(f"  SUMMARY: {routed_ok}/{total} routed correctly · "
          f"{rendered_ok}/{total} rendered deterministically · "
          f"{len(failures)} failures")
    print(f"{'='*100}")
    if failures:
        print("\n  Failures:")
        for label, prompt, reason in failures:
            print(f"    [{label}] {prompt!r}  →  {reason}")
        sys.exit(1)


if __name__ == "__main__":
    main()
