"""
Comprehensive validation for all 5 fixes:
  Fix 1: MedGemma code output → no code in responses
  Fix 2: Explicit variant listing → both ClinVar AND InterVar shown
  Fix 3: Combined ClinVar+InterVar filter in acmg_clinvar
  Fix 4: HPO/symptom queries work
  Fix 5: Qwen reasoning leak fixed
"""
import json
import urllib.request
import time

TESTS = [
    # (port, name, message, checks)
    (8000, "MedGemma: lung disease (was generating Python code)",
     "I have problems with my lungs. Any variants in my report linked to lung disease?",
     {"no_code": True, "has_clinvar": True, "has_intervar": True}),

    (8000, "MedGemma: pathogenic variants (ClinVar+InterVar combined)",
     "Show me pathogenic variants in my report",
     {"no_code": True, "has_clinvar": True, "has_intervar": True}),

    (8000, "MedGemma: SERPINA1 gene query",
     "What variants do I have in SERPINA1?",
     {"no_code": True, "has_clinvar": True, "has_intervar": True, "min_rows": 1}),

    (8000, "MedGemma: symptom query (breathing difficulties)",
     "I have breathing difficulties and shortness of breath. Any variants in my genes?",
     {"no_code": True}),

    (8001, "Qwen: SERPINA1 (explicit variant listing)",
     "What variants do I have in SERPINA1?",
     {"no_code": True, "no_think": True, "has_clinvar": True, "has_intervar": True, "min_rows": 1}),

    (8001, "Qwen: pathogenic variants (InterVar+ClinVar)",
     "Show pathogenic and likely pathogenic variants",
     {"no_code": True, "no_think": True}),

    (8001, "Qwen: HPO organ symptom (lung/breathing)",
     "I have lung problems and difficulty breathing, are there related variants in my report?",
     {"no_code": True, "no_think": True}),
]

PASS = 0
FAIL = 0

for port, name, message, checks in TESTS:
    body = json.dumps({"message": message, "history": []}).encode()
    req = urllib.request.Request(
        f"http://localhost:{port}/api/ai/chat",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            d = json.loads(r.read())

        resp = d.get("response", "")
        rtype = d.get("type", "?")
        rows = d.get("row_count", 0)

        results = []
        failed = []

        code_markers = ["= ['", "= [\n", "import ", "def ", "SELECT ", "```python", "```sql"]
        if checks.get("no_code"):
            has_code = any(m in resp for m in code_markers)
            if has_code:
                failed.append("HAS_CODE")
            else:
                results.append("no_code✓")

        think_markers = ["<think>", "Wait, the rules", "Let me think", "I need to consider"]
        if checks.get("no_think"):
            has_think = any(m in resp for m in think_markers)
            if has_think:
                failed.append("HAS_THINK")
            else:
                results.append("no_think✓")

        if checks.get("has_clinvar"):
            if "ClinVar" in resp or "clinvar" in resp.lower():
                results.append("ClinVar✓")
            else:
                failed.append("NO_CLINVAR")

        if checks.get("has_intervar"):
            if "InterVar" in resp or "intervar" in resp.lower():
                results.append("InterVar✓")
            else:
                failed.append("NO_INTERVAR")

        if checks.get("min_rows"):
            if rows >= checks["min_rows"]:
                results.append(f"rows={rows}✓")
            else:
                failed.append(f"rows={rows}<{checks['min_rows']}")

        status = "PASS" if not failed else "FAIL"
        if status == "PASS":
            PASS += 1
        else:
            FAIL += 1

        print(f"[{status}] {name}")
        print(f"       type={rtype} rows={rows} checks={' '.join(results)} fails={' '.join(failed)}")
        print(f"       resp_start={resp[:150]!r}")
        print()

    except Exception as e:
        FAIL += 1
        print(f"[ERROR] {name}")
        print(f"        {type(e).__name__}: {str(e)[:200]}")
        print()

print(f"{'='*60}")
print(f"RESULTS: {PASS} PASS / {FAIL} FAIL / {PASS+FAIL} TOTAL")
