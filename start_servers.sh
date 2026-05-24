#!/bin/bash
# InterVar Genomic AI - Remote Server Startup
# Run from: /home/ubuntu/intervar/
# DB lives at: /ephemeral/ubuntu/intervar/genomic_variants.db (symlinked as ~/intervar/genomic_variants.db)

PYTHON=/ephemeral/conda_envs/intervar/bin/python
WORKDIR=/home/ubuntu/intervar

cd $WORKDIR

# Kill any existing processes
pkill -f "uvicorn main:app" 2>/dev/null
pkill -f "gradio_app.py" 2>/dev/null
sleep 2

# Check DB is ready — prefer patient_variants.db (new schema), fall back to genomic_variants.db
if [ -f "$WORKDIR/patient_variants.db" ]; then
    DB_FILE="patient_variants.db"
elif [ -f "$WORKDIR/genomic_variants.db" ]; then
    DB_FILE="genomic_variants.db"
    echo "WARNING: Using legacy genomic_variants.db — run ingest_new_db.py for patient DB"
else
    echo "ERROR: No database found. Run ingest_new_db.py first."
    exit 1
fi

DB_SIZE=$(stat -c%s "$WORKDIR/$DB_FILE" 2>/dev/null || echo 0)
echo "DB: $DB_FILE  (${DB_SIZE} bytes)"

# Set environment
export TMPDIR=/ephemeral/tmp
export HF_HOME=/ephemeral/hf
export LLM_BACKEND=vllm_api
export VLLM_API_URL=http://localhost:11434
export VLLM_MODEL=qwen3:32b
export SQLALCHEMY_DATABASE_URL=sqlite:///$DB_FILE

# Start backend
nohup $PYTHON -m uvicorn main:app --host 0.0.0.0 --port 8000 --log-level info \
    > $WORKDIR/backend.log 2>&1 &
BACKEND_PID=$!
echo "Backend PID: $BACKEND_PID"
sleep 8

# Health check
if curl -s http://localhost:8000/api/health | grep -q healthy; then
    echo "Backend: HEALTHY"
else
    echo "Backend: FAILED - check backend.log"
    exit 1
fi

# Reconnect LLM
curl -s -X POST http://localhost:8000/api/ai/reconnect | python3 -c "import sys,json; d=json.load(sys.stdin); print('LLM:', d.get('status'), d.get('model',''))"

# Start Gradio
nohup $PYTHON -u gradio_app.py > $WORKDIR/gradio.log 2>&1 &
GRADIO_PID=$!
echo "Gradio PID: $GRADIO_PID"
sleep 30

# Get public URL
echo "--- Gradio URL ---"
grep -o "https://[^ ]*gradio.live" $WORKDIR/gradio.log 2>/dev/null || echo "URL still loading, check: tail -f $WORKDIR/gradio.log"

echo ""
echo "=== ALL SERVICES RUNNING ==="
echo "Backend : http://$(curl -s ifconfig.me 2>/dev/null || echo 'SERVER_IP'):8000"
echo "Gradio  : http://$(curl -s ifconfig.me 2>/dev/null || echo 'SERVER_IP'):7860"
echo "LLM     : Qwen3-32B via Ollama on port 11434"
