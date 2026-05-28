import requests, json, sqlite3, subprocess, os

print("=" * 50)
print("REMOTE SERVER FULL STATUS CHECK")
print("=" * 50)

# 1. Backend health
try:
    r = requests.get("http://localhost:8000/api/health", timeout=5)
    d = r.json()
    print(f"[1] BACKEND     : {d['status'].upper()}")
except Exception as e:
    print(f"[1] BACKEND     : DOWN - {e}")

# 2. LLM / Model status
try:
    r = requests.get("http://localhost:8000/api/ai/status", timeout=10)
    d = r.json()
    print(f"[2] LLM MODEL   : {d['status'].upper()} | {d['vllm_model']} via {d['configured_backend']}")
except Exception as e:
    print(f"[2] LLM MODEL   : ERROR - {e}")

# 3. Database
try:
    c = sqlite3.connect("/ephemeral/ubuntu/intervar/genomic_variants.db", timeout=10)
    cur = c.cursor()
    cur.execute("SELECT COUNT(*) FROM variants")
    variants = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM genes")
    genes = cur.fetchone()[0]
    cur.execute("SELECT MAX(variant_id) FROM variants")
    max_id = cur.fetchone()[0]
    c.close()
    print(f"[3] DATABASE    : HEALTHY | {variants:,} variants | {genes:,} genes")
except Exception as e:
    print(f"[3] DATABASE    : ERROR - {e}")

# 4. Ollama / Model server
try:
    r = requests.get("http://localhost:11434/api/tags", timeout=5)
    models = [m["name"] for m in r.json().get("models", [])]
    print(f"[4] OLLAMA      : RUNNING | Models: {', '.join(models)}")
except Exception as e:
    print(f"[4] OLLAMA      : ERROR - {e}")

# 5. Gradio process
result = subprocess.run(["pgrep", "-fa", "gradio_app"], capture_output=True, text=True)
if result.stdout.strip():
    print(f"[5] GRADIO      : RUNNING | PID {result.stdout.strip().split()[0]}")
else:
    print(f"[5] GRADIO      : DOWN")

# 6. Live query test
try:
    r = requests.post("http://localhost:8000/api/ai/chat",
        json={"message": "Show VUS variants in BRCA1", "history": [], "max_rows": 5},
        timeout=60)
    d = r.json()
    print(f"[6] LIVE QUERY  : OK | type={d['type']} rows={d['row_count']} sql_src={d.get('sql_source','')}")
    print(f"    SQL: {d.get('sql','')[:80]}")
except Exception as e:
    print(f"[6] LIVE QUERY  : ERROR - {e}")

# 7. Gradio URL
try:
    with open("/home/ubuntu/intervar/gradio.log") as f:
        for line in f:
            if "gradio.live" in line:
                url = [x for x in line.split() if "gradio.live" in x]
                if url:
                    print(f"[7] PUBLIC URL  : {url[-1]}")
except:
    print("[7] PUBLIC URL  : Check gradio.log")

print("=" * 50)
