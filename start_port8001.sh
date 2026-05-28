#!/bin/bash
# Server: port 8001 — Qwen3-32B backend (Ollama at localhost:11434)

cd /home/ubuntu/intervar
LOG=/home/ubuntu/intervar/server_8001.log
echo "[START] port 8001 (Qwen3) at $(date)" >> $LOG

# Kill any existing instance
pkill -f 'uvicorn main:app.*port 8001' 2>/dev/null || true
sleep 1

# Start with Qwen3 env overrides (overrides .env MedGemma settings)
nohup env LLM_BACKEND=vllm_api           VLLM_API_URL=http://localhost:11434           VLLM_MODEL=qwen3:32b   /ephemeral/conda_envs/intervar/bin/python   -m uvicorn main:app   --host 0.0.0.0   --port 8001   >> $LOG 2>&1 &
echo "Port 8001 started (PID $!)"
