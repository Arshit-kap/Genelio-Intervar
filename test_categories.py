"""
Comprehensive category test — A1-F2 conversational patterns from interval_02.
Tests both MedGemma (8000) and Qwen (8001).
"""
import json, urllib.request, time

TESTS = [
    # ── Category A: Pathogenicity / Classification ────────────────────────────
    ("A1-harmful",   8000, "Are any of my variants harmful?",
     ["pathogenic","harmful","disease","variant","ClinVar","InterVar"]),
    ("A1-pathogen",  8000, "Do I have pathogenic variants?",
     ["pathogenic","variant"]),
    ("A1-dangerous", 8000, "Show me my dangerous mutations",
     ["pathogenic","variant","mutation"]),
    ("A1-actionable",8000, "What is actionable in my report?",
     ["pathogenic","variant","report","actionable"]),
    ("A2-conflict",  8000, "ClinVar says VUS but InterVar says pathogenic — why?",
     ["ClinVar","InterVar","uncertain","pathogenic","conflict","classification"]),

    # ── Category B: Column Definitions ────────────────────────────────────────
    ("B1-cadd",      8000, "Explain what CADD score is",
     ["CADD","score","deleteriousness","damaging"]),
    ("B1-ba1",       8000, "What is BA1?",
     ["BA1","benign","gnomAD","frequency"]),
    ("B1-het",       8000, "What does heterozygous mean?",
     ["heterozygous","het","allele","copy"]),
    ("B1-vus",       8000, "What does VUS mean?",
     ["VUS","uncertain","significance","variant"]),

    # ── Category C: Symptom-Driven / HPO ─────────────────────────────────────
    ("C1-lung",      8000, "Do I have any variants related to lung disease?",
     ["variant","lung","gene","HPO","report","result"]),
    ("C1-heart",     8000, "Anything related to my heart?",
     ["variant","heart","cardiac","gene"]),
    ("C1-kidney",    8000, "Are there kidney-related issues in my genes?",
     ["kidney","renal","variant","gene"]),
    ("C2-multi",     8000, "I have weak muscles and trouble seeing at night — what could it be?",
     ["variant","gene","muscle","vision","HP","associated"]),
    ("C2-fatigue",   8000, "I am tired, bruise easily, and have joint pain",
     ["variant","gene","fatigue","joint","HP"]),

    # ── Category D: Disease-Driven ─────────────────────────────────────────────
    ("D1-marfan",    8000, "Do I have anything related to Marfan syndrome?",
     ["variant","Marfan","FBN1","gene","report"]),
    ("D2-gene",      8000, "What conditions are linked to BRCA1?",
     ["BRCA1","breast","cancer","ovarian","hereditary","pathogenic"]),

    # ── Category E: Inheritance ────────────────────────────────────────────────
    ("E1-inherit",   8000, "How is this condition inherited?",
     ["inheritance","dominant","recessive","autosomal","X-linked"]),
    ("E2-children",  8000, "Will my children inherit this?",
     ["children","inherit","pass","risk","carrier","recessive"]),

    # ── Category F: Carrier / Secondary ───────────────────────────────────────
    ("F1-carrier",   8000, "Am I a carrier for any recessive conditions?",
     ["carrier","recessive","variant","gene","condition"]),
    ("F2-secondary", 8000, "Are there incidental findings in my report?",
     ["finding","variant","ACMG","secondary","incidental","report"]),

    # ── Same key categories on Qwen ───────────────────────────────────────────
    ("A1-q",  8001, "Are any of my variants harmful?",
     ["pathogenic","harmful","disease","variant"]),
    ("B1-q",  8001, "What does VUS mean?",
     ["VUS","uncertain","significance"]),
    ("C1-q",  8001, "Do I have any variants related to lung disease?",
     ["variant","lung","gene"]),
    ("A2-q",  8001, "ClinVar says VUS but InterVar says pathogenic — why?",
     ["ClinVar","InterVar","classification"]),
]

PASS = FAIL = 0

for tag, port, q, expected_any in TESTS:
    body = json.dumps({"message": q, "history": []}).encode()
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

        code_markers = ["= ['", "= [\n", "import ", "def ", "SELECT ", "```python"]
        has_code = any(m in resp for m in code_markers)
        think_markers = ["<think>", "Wait, the rules", "Let me think"]
        has_think = any(m in resp for m in think_markers)

        content_ok = any(kw.lower() in resp.lower() for kw in expected_any)

        failed = []
        if has_code: failed.append("HAS_CODE")
        if has_think: failed.append("HAS_THINK")
        if not content_ok: failed.append(f"MISSING_CONTENT(need:{expected_any[:3]})")

        status = "PASS" if not failed else "FAIL"
        if status == "PASS":
            PASS += 1
        else:
            FAIL += 1

        print(f"[{status}] {tag}|P{port} type={rtype} rows={rows}")
        if failed:
            print(f"       FAILS: {' '.join(failed)}")
        print(f"       Q: {q[:65]}")
        print(f"       R: {resp[:200]!r}")
        print()

    except Exception as e:
        FAIL += 1
        print(f"[ERROR] {tag}|P{port}")
        print(f"        {type(e).__name__}: {str(e)[:150]}")
        print()

    time.sleep(0.3)

print(f"{'='*60}")
print(f"RESULTS: {PASS} PASS / {FAIL} FAIL / {PASS+FAIL} TOTAL")
