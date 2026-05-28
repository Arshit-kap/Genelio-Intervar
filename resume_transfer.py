"""
Resumable DB transfer using paramiko SFTP.
Continues from where previous SCP left off.
"""
import os
import sys
import time
import paramiko

LOCAL_FILE  = r"C:\Users\Admin\Desktop\intervar\genomic_variants.db"
REMOTE_FILE = "/ephemeral/data/genomic_variants.db"
HOST        = "62.169.159.252"
USER        = "ubuntu"
KEY_FILE    = r"C:\Users\Admin\Desktop\intervar\ubuntu_.pem"
CHUNK_SIZE  = 32 * 1024 * 1024  # 32 MB chunks

def human(n):
    return f"{n/1073741824:.2f} GB"

def transfer():
    local_size  = os.path.getsize(LOCAL_FILE)
    print(f"Local file : {human(local_size)}")

    key = paramiko.RSAKey.from_private_key_file(KEY_FILE)
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(HOST, username=USER, pkey=key, timeout=30,
                   banner_timeout=30, auth_timeout=30)
    client.get_transport().set_keepalive(20)

    sftp = client.open_sftp()

    # Check remote file size for resume
    try:
        remote_size = sftp.stat(REMOTE_FILE).st_size
        print(f"Remote file: {human(remote_size)} — resuming from here")
    except FileNotFoundError:
        remote_size = 0
        print("Remote file not found — starting fresh")

    if remote_size >= local_size:
        print("Already complete!")
        sftp.close(); client.close()
        return

    # Open files for resume
    with open(LOCAL_FILE, "rb") as lf:
        lf.seek(remote_size)
        with sftp.open(REMOTE_FILE, "ab") as rf:
            rf.set_pipelined(True)
            transferred = remote_size
            t0 = time.time()
            t_last = t0

            while True:
                chunk = lf.read(CHUNK_SIZE)
                if not chunk:
                    break
                rf.write(chunk)
                transferred += len(chunk)

                now = time.time()
                if now - t_last >= 15:
                    pct  = transferred / local_size * 100
                    rate = (transferred - remote_size) / (now - t0) / 1024 / 1024
                    eta  = (local_size - transferred) / ((transferred - remote_size) / (now - t0)) if transferred > remote_size else 0
                    print(f"  {human(transferred)} / {human(local_size)}  ({pct:.1f}%)  {rate:.1f} MB/s  ETA: {eta/60:.0f} min")
                    sys.stdout.flush()
                    t_last = now

    print(f"\nTransfer complete: {human(local_size)}")
    sftp.close()
    client.close()

if __name__ == "__main__":
    try:
        transfer()
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)
