import json
import urllib.request

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
        # Check for reasoning leak
        think_markers = ["<think>", "Let me think", "I need to", "First, let"]
        has_think = any(m in resp for m in think_markers)
        print(f"PORT={port} TYPE={d.get('type')} ROWS={d.get('row_count')} HAS_CODE={has_code} HAS_THINK={has_think}")
        print(f"RESP_FIRST_400: {resp[:400]}")
        print("---")
    except Exception as e:
        print(f"PORT={port} ERROR: {type(e).__name__}: {str(e)[:200]}")

# Qwen tests
test_query(8001, "Show me pathogenic and likely pathogenic variants in my report")
test_query(8001, "What variants do I have in SERPINA1?")
test_query(8001, "I have lung problems and breathing difficulties, any related variants?")
