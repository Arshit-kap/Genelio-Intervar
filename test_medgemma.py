import urllib.request, json, time

url = "http://localhost:8030/v1/chat/completions"
payload = {
    "model": "medgemma-27b",
    "messages": [
        {"role": "system", "content": "You are a genomic expert. Answer concisely."},
        {"role": "user", "content": "The variant PADI3:NM_016233:exon8:c.C881T:p.A294V is ClinVar Pathogenic. Summarise in 2 sentences."}
    ],
    "max_tokens": 300,
    "temperature": 0.1
}
data = json.dumps(payload).encode()
req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
t0 = time.time()
try:
    with urllib.request.urlopen(req, timeout=60) as r:
        resp = json.loads(r.read())
        elapsed = time.time() - t0
        content = resp["choices"][0]["message"]["content"]
        print(f"Time: {elapsed:.1f}s")
        print(f"Content: {repr(content)}")
        print(f"Finish reason: {resp['choices'][0]['finish_reason']}")
except Exception as e:
    print(f"ERROR: {e}")
