import json, urllib.request

def test(port, q):
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
        print(f"[P{port}] TYPE={d.get('type')} ROWS={d.get('row_count',0)}")
        print(f"  Q: {q[:60]}")
        resp = d.get("response", "")
        print(f"  R: {resp[:300]}")
        print()
    except Exception as e:
        print(f"[P{port}] ERROR: {e}")
        print()

# Previously failing knowledge questions
test(8000, "What does VUS mean?")
test(8000, "Explain CADD scores")
test(8000, "What are the ACMG classification tiers?")
test(8000, "What is PVS1 in ACMG criteria?")

# Previously wrong routing (VUS in BRCA1)
test(8000, "Show VUS variants in BRCA1")

# Qwen versions
test(8001, "What does VUS mean?")
test(8001, "What is PVS1 in ACMG criteria?")
test(8001, "Show VUS variants in BRCA1")
