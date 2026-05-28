"""Test Qwen3-8B via legacy HF Inference API (no special permissions needed)."""
import os, time, requests
TOKEN = os.environ.get("HF_TOKEN", "")  # set HF_TOKEN env var before running
MODEL = "Qwen/Qwen3-8B"

headers = {
    "Authorization": f"Bearer {TOKEN}",
    "Content-Type": "application/json"
}

payload = {
    "inputs": "SELECT variant_key FROM variants WHERE gene_symbol = 'BRCA1' LIMIT 5;",
    "parameters": {"max_new_tokens": 100, "return_full_text": False}
}

url = f"https://api-inference.huggingface.co/models/{MODEL}"
print(f"Testing legacy API: {url}")
t0 = time.time()
r = requests.post(url, headers=headers, json=payload, timeout=60)
print(f"Status: {r.status_code} in {time.time()-t0:.1f}s")
print(f"Response: {r.text[:300]}")

# Also try chat completions with messages format
print("\nTesting messages format...")
url2 = f"https://api-inference.huggingface.co/models/{MODEL}/v1/chat/completions"
payload2 = {
    "model": MODEL,
    "messages": [{"role": "user", "content": "Write SQL to show variants in BRCA1: SELECT"}],
    "max_tokens": 100
}
t0 = time.time()
r2 = requests.post(url2, headers=headers, json=payload2, timeout=60)
print(f"Chat Status: {r2.status_code} in {time.time()-t0:.1f}s")
print(f"Response: {r2.text[:300]}")
