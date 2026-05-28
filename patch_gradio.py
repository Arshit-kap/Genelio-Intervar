with open("/home/ubuntu/intervar/gradio_app_qwen.py", "r") as f:
    content = f.read()

content = content.replace(
    'BACKEND_URL = "http://localhost:8000"',
    'BACKEND_URL = "http://localhost:8001"'
)
content = content.replace(
    'title="InterVar Genomic AI"',
    'title="InterVar Genomic AI - Qwen3-32B"'
)

with open("/home/ubuntu/intervar/gradio_app_qwen.py", "w") as f:
    f.write(content)

print("patched OK")
# verify
import re
m1 = re.search(r'BACKEND_URL = .+', content)
m2 = re.search(r'title=.+', content)
m3 = re.search(r'server_port=\d+', content)
print("BACKEND_URL:", m1.group() if m1 else "not found")
print("title:", m2.group() if m2 else "not found")
print("port:", m3.group() if m3 else "not found")
