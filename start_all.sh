#!/bin/bash
# Start everything: MedGemma tunnel + port 8000 + port 8001
echo "=== Genelio InterVar Backend Startup ==="
echo "Date: $(date)"
echo ""

cd /home/ubuntu/intervar

# 1. MedGemma SSH tunnel (autossh)
echo "[1/3] Starting MedGemma tunnel (autossh)..."
pkill -f 'autossh.*8030\|ssh.*8030.*213.181' 2>/dev/null || true
sleep 1
nohup /home/ubuntu/intervar/tunnel_medgemma.sh >> /home/ubuntu/intervar/tunnel.log 2>&1 &
TUNNEL_PID=$!
sleep 3
if ss -tlnp | grep -q 8030; then
  echo "  ✅ Tunnel active (pid $TUNNEL_PID) — MedGemma at localhost:8030"
else
  echo "  ⚠️  Tunnel may still be connecting..."
fi

# 2. Port 8000 (MedGemma)
echo "[2/3] Starting port 8000 (MedGemma)..."
/home/ubuntu/intervar/start_port8000.sh
sleep 2

# 3. Port 8001 (Qwen3)
echo "[3/3] Starting port 8001 (Qwen3-32B)..."
/home/ubuntu/intervar/start_port8001.sh
sleep 2

echo ""
echo "=== Startup complete. Checking services ==="
ss -tlnp | grep -E '8000|8001|8030' | awk '{print $4}'
