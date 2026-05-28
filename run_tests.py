# -*- coding: utf-8 -*-
"""
Performance test script — runs 10 questions against the live API
and prints a summary table with pass/fail, timing, row count, SQL source.
Run: python run_tests.py
Backend must be running at http://localhost:8000
"""
import requests
import json
import time

BACKEND = "http://localhost:8000"

TESTS = [
    # (question, expected_type)
    ("Show VUS variants in BRCA1",                                              "data_query"),
    ("What is PVS1 in ACMG criteria?",                                          "general"),
    ("Find missense variants in TP53 with CADD > 25",                           "data_query"),
    ("Look up rs189107123",                                                     "data_query"),
    ("What does VUS mean?",                                                     "general"),
    ("Average CADD score for stopgain vs synonymous variants",                  "data_query"),
    ("List all in-frame deletions not in a repeat region",                      "data_query"),
    ("Which variants are Pathogenic in ClinVar with gnomAD frequency > 1%?",   "data_query"),
    ("What is the gene at chromosome 1 position 10611?",                        "data_query"),
    ("Show frameshift variants in CFTR",                                        "data_query"),
]

LINE = "-" * 90

def run_tests():
    print(LINE)
    print(f"  InterVar AI Performance Test  |  Backend: {BACKEND}")
    print(LINE)

    # Health check
    try:
        h = requests.get(f"{BACKEND}/api/health", timeout=5)
        data = h.json()
        count = data.get("variant_count") or data.get("total_variants", "")
        suffix = f"  |  Variants: {int(count):,}" if count else ""
        print(f"  Backend: OK{suffix}")
    except Exception as e:
        print(f"  ERROR: Cannot reach backend — {e}")
        return

    # LLM status
    try:
        s = requests.get(f"{BACKEND}/api/ai/status", timeout=5).json()
        print(f"  LLM:     {s.get('backend','?')}  |  Model: {s.get('model','?')}  |  Ready: {s.get('available','?')}")
    except Exception:
        print("  LLM:     status unavailable")

    print(LINE)
    print(f"  {'#':<3} {'STATUS':<8} {'TYPE':<12} {'ROWS':<6} {'MS':<7} {'SRC':<12}  QUESTION")
    print(LINE)

    results = []
    total_ms = 0

    for i, (question, expected_type) in enumerate(TESTS, 1):
        try:
            t0 = time.time()
            resp = requests.post(
                f"{BACKEND}/api/ai/chat",
                json={"message": question, "history": [], "max_rows": 20, "include_sql": True},
                timeout=120,
            )
            elapsed = int((time.time() - t0) * 1000)

            data = resp.json()
            q_type    = data.get("type", "?")
            row_count = data.get("row_count", 0)
            sql_src   = data.get("sql_source") or "none"
            error     = data.get("error")

            if error:
                status = "ERROR"
            elif q_type == "data_query" and row_count == 0:
                status = "NO_DATA"
            else:
                status = "PASS"

            type_ok = "OK" if q_type == expected_type else "WRONG_TYPE"
            display_status = status if type_ok == "OK" else f"{status}*"

        except requests.Timeout:
            elapsed = 120000
            q_type, row_count, sql_src = "timeout", 0, "-"
            display_status = "TIMEOUT"
        except Exception as e:
            elapsed = 0
            q_type, row_count, sql_src = "error", 0, "-"
            display_status = f"ERR"

        total_ms += elapsed
        results.append(display_status)

        q_short = question[:55] + "..." if len(question) > 55 else question
        print(f"  {i:<3} {display_status:<8} {q_type:<12} {row_count:<6} {elapsed:<7} {sql_src:<12}  {q_short}")

    print(LINE)
    passed   = sum(1 for r in results if r == "PASS")
    no_data  = sum(1 for r in results if "NO_DATA" in r)
    timeouts = sum(1 for r in results if "TIMEOUT" in r)
    errors   = sum(1 for r in results if r in ("ERR", "ERROR"))

    print(f"  PASS: {passed}  |  NO_DATA: {no_data}  |  TIMEOUT: {timeouts}  |  ERROR: {errors}  |  Total time: {total_ms/1000:.1f}s")
    print(LINE)

    # Save results
    out_file = "test_results_v3.json"
    with open(out_file, "w") as f:
        json.dump({"summary": {"pass": passed, "no_data": no_data, "timeout": timeouts, "error": errors},
                   "total_ms": total_ms}, f, indent=2)
    print(f"  Results saved to {out_file}")
    print(LINE)


if __name__ == "__main__":
    run_tests()
