import urllib.request, json
try:
    r = urllib.request.urlopen("http://localhost:11434/api/tags", timeout=5)
    d = json.loads(r.read())
    for m in d.get("models", []):
        size_gb = round(m.get("size", 0) / 1e9, 1)
        print(f"  {m['name']}  ({size_gb} GB)")
except Exception as e:
    print("Ollama tags error:", e)

try:
    r2 = urllib.request.urlopen("http://localhost:11434/v1/models", timeout=5)
    d2 = json.loads(r2.read())
    print("v1/models:", [m["id"] for m in d2.get("data", [])])
except Exception as e2:
    print("v1/models error:", e2)
