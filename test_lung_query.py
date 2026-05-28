import json
import urllib.request
import sys

def test_query(port, message):
    body = json.dumps({
        "message": message,
        "history": []
    }).encode()
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
        code_markers = ["= [", "import ", "def ", "for x in", "SELECT "]
        has_code = any(m in resp for m in code_markers)
        print(f"PORT={port} TYPE={d.get('type')} ROWS={d.get('row_count')} HAS_CODE={has_code}")
        print(f"RESP_FIRST_300: {resp[:300]}")
        print("---")
    except Exception as e:
        print(f"PORT={port} ERROR: {type(e).__name__}: {str(e)[:200]}")

# Test 1: Lung disease (was generating Python code)
test_query(8000, "I have problems with my lungs. Any variants in my report linked to lung disease?")

# Test 2: Pathogenic variants (InterVar + ClinVar combined)
test_query(8000, "Show me pathogenic variants in my report")

# Test 3: SERPINA1 gene query
test_query(8000, "What variants do I have in SERPINA1?")
