#!/bin/bash
# Server: port 8000 — MedGemma-27B backend
# LLM reads from .env: VLLM_API_URL=localhost:8030, VLLM_MODEL=medgemma-27b

cd /home/ubuntu/intervar
LOG=/home/ubuntu/intervar/server_8000.log
echo "[START] port 8000 (MedGemma) at $(date)" >> $LOG

# Kill any existing instance
pkill -f 'uvicorn main:app.*port 8000' 2>/dev/null || true
sleep 1

# Start with .env (MedGemma settings)
nohup /ephemeral/conda_envs/intervar/bin/python   -m uvicorn main:app   --host 0.0.0.0   --port 8000   >> $LOG 2>&1 &
echo "Port 8000 started (PID $!)"
