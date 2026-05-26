"""
Stream-compress genomic_variants.db through SSH to remote server.
Uses gzip level-1 (fastest) to reduce data in transit.
"""
import subprocess
import gzip
import sys
import time
import os

KEY = r"C:\Users\Admin\Desktop\intervar\ubuntu_.pem"
REMOTE = "ubuntu@62.169.159.252"
DEST = "/ephemeral/ubuntu/intervar/genomic_variants.db"
LOCAL = r"C:\Users\Admin\Desktop\intervar\genomic_variants.db"

total = os.path.getsize(LOCAL)
print(f"Source: {total/1024**3:.1f} GB", flush=True)
print("Starting compressed SSH transfer...", flush=True)

ssh_cmd = [
    "ssh", "-i", KEY,
    "-o", "StrictHostKeyChecking=no",
    "-o", "ServerAliveInterval=60",
    "-o", "ServerAliveCountMax=20",
    REMOTE,
    f"gunzip -c > {DEST}"
]

t0 = time.time()
proc = subprocess.Popen(ssh_cmd, stdin=subprocess.PIPE)

CHUNK = 256 * 1024  # 256 KB chunks
sent = 0

with open(LOCAL, "rb") as f_in:
    with gzip.GzipFile(fileobj=proc.stdin, mode="wb", compresslevel=1) as gz:
        while True:
            chunk = f_in.read(CHUNK)
            if not chunk:
                break
            gz.write(chunk)
            sent += len(chunk)
            if sent % (128 * 1024 * 1024) < CHUNK:  # log every 128 MB
                elapsed = time.time() - t0
                rate = sent / elapsed / 1024 / 1024
                pct = sent / total * 100
                eta = (total - sent) / (sent / elapsed) / 3600
                print(f"  {pct:.1f}%  {sent/1024**3:.2f}/{total/1024**3:.1f} GB  "
                      f"{rate:.1f} MB/s  ETA {eta:.1f}h", flush=True)

proc.stdin.close()
rc = proc.wait()
elapsed = time.time() - t0
print(f"Done. Exit={rc}  Time={elapsed/3600:.2f}h", flush=True)
