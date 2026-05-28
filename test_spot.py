import json, urllib.request

def t(port, q, label):
    body = json.dumps({"message": q, "history": []}).encode()
    req = urllib.request.Request(
        f"http://localhost:{port}/api/ai/chat",
        data=body, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            d = json.loads(r.read())
        resp = d.get("response", "")
        code = any(m in resp for m in ["import ", "def ", "SELECT ", "```python"])
        think = any(m in resp for m in ["<think>", "Let me think"])
        print(f"{label} | P{port} | type={d['type']} | code={code} | think={think}")
        print(f"  {resp[:200]}")
        print()
    except Exception as e:
        print(f"{label} ERROR: {e}")

t(8000, "Are there incidental findings in my report?", "F2-secondary")
t(8000, "Am I a carrier for any recessive conditions?", "F1-carrier-check")
t(8000, "Will my children inherit this?", "E2-children")
t(8001, "Are there incidental findings in my report?", "F2-qwen")
t(8000, "ClinVar says VUS but InterVar says pathogenic why?", "A2-conflict")
