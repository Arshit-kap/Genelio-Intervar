"""
12-question acceptance test for both MedGemma (8000) and Qwen (8001).
Tests routing, content quality, and answer completeness.
"""
import json, urllib.request, sys, time

QUESTIONS = [
    ("Show VUS variants in BRCA1",
     {"intent_hint": "acmg_clinvar", "check_gene": "BRCA1", "check_any": ["VUS","Uncertain","uncertain"]}),

    ("What is PVS1 in ACMG criteria?",
     {"knowledge": True, "check_any": ["PVS1","Very Strong","loss of function","LOF","null variant","truncat","pathogenic"]}),

    ("Find rare missense variants in TP53 with CADD > 25",
     {"check_gene": "TP53", "check_any": ["missense","nonsynonymous","CADD","TP53"]}),

    ("Look up rs189107123",
     {"check_any": ["rs189107123","chr","gene","variant"]}),

    ("What does VUS mean?",
     {"knowledge": True, "check_any": ["Uncertain","uncertain significance","VUS","not enough","classification"]}),

    ("Average CADD score for stopgain vs synonymous variants",
     {"check_any": ["CADD","stopgain","synonymous","average","avg"]}),

    ("List all in-frame deletions not in a repeat region",
     {"check_any": ["deletion","in-frame","nonframeshift","variant","gene"]}),

    ("Which variants are Pathogenic in ClinVar but have gnomAD frequency > 1%?",
     {"check_any": ["Pathogenic","pathogenic","ClinVar","gnomAD","frequency","variant"]}),

    ("What is the gene at chromosome 1 position 10611?",
     {"check_any": ["gene","chr","position","1","10611","variant"]}),

    ("Show frameshift variants in CFTR",
     {"check_gene": "CFTR", "check_any": ["frameshift","CFTR","variant","deletion","insertion"]}),

    ("Explain CADD scores",
     {"knowledge": True, "check_any": ["CADD","deleteriousness","score","damaging","pathogenic","harmful"]}),

    ("What are the ACMG classification tiers?",
     {"knowledge": True, "check_any": ["Pathogenic","Benign","Uncertain","ACMG","classification","tier","category"]}),
]

def run_test(port, q_text, checks, q_num):
    body = json.dumps({"message": q_text, "history": []}).encode()
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

        # Check for code contamination
        code_markers = ["= ['", "= [\n", "import ", "def ", "SELECT ", "```python", "```sql"]
        has_code = any(m in resp for m in code_markers)
        # Check for think leak
        think_markers = ["<think>", "Wait, the rules", "Let me think", "I need to consider"]
        has_think = any(m in resp for m in think_markers)

        failed = []
        passed = []

        if has_code:
            failed.append("HAS_CODE")
        else:
            passed.append("no_code✓")

        if has_think:
            failed.append("HAS_THINK")
        else:
            passed.append("no_think✓")

        check_any = checks.get("check_any", [])
        if check_any:
            if any(c in resp for c in check_any):
                passed.append("content✓")
            else:
                failed.append(f"MISSING_CONTENT(expected one of: {check_any[:3]}...)")

        check_gene = checks.get("check_gene")
        if check_gene:
            if check_gene in resp:
                passed.append(f"{check_gene}✓")
            else:
                failed.append(f"NO_{check_gene}_IN_RESP")

        status = "PASS" if not failed else "FAIL"
        print(f"[{status}] Q{q_num:02d}|P{port} {q_text[:60]}")
        print(f"       type={rtype} rows={rows} {' '.join(passed)} {' '.join(failed)}")
        print(f"       resp={resp[:200]!r}")
        print()
        return status == "PASS"
    except Exception as e:
        print(f"[ERROR] Q{q_num:02d}|P{port} {q_text[:60]}")
        print(f"        {type(e).__name__}: {str(e)[:200]}")
        print()
        return False

total_pass = 0
total_fail = 0

for port in [8000, 8001]:
    print(f"\n{'='*60}")
    print(f"PORT {port} ({'MedGemma' if port==8000 else 'Qwen'})")
    print(f"{'='*60}\n")
    for i, (q, checks) in enumerate(QUESTIONS, 1):
        ok = run_test(port, q, checks, i)
        if ok:
            total_pass += 1
        else:
            total_fail += 1
        time.sleep(0.5)

print(f"\n{'='*60}")
print(f"TOTAL: {total_pass} PASS / {total_fail} FAIL / {total_pass+total_fail} TOTAL")
