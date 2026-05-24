#!/bin/bash
# InterVar New DB Deployment Script
# Run from local Windows terminal (Git Bash or WSL) when server comes back online
# Usage: bash deploy_to_server.sh
# Working directory: C:\Users\Admin\Desktop\intervar\

set -e
KEY="C:/Users/Admin/Desktop/intervar/ubuntu_.pem"
REMOTE="ubuntu@62.169.159.252"
WORKDIR="/home/ubuntu/intervar"
PYTHON="/ephemeral/conda_envs/intervar/bin/python"

echo "=== InterVar Deployment: New 34-column DB ==="
echo ""
echo "Step 1: Uploading files..."

# Upload ingestion script
scp -i "$KEY" -o StrictHostKeyChecking=no \
    ingest_new_db.py \
    "$REMOTE:$WORKDIR/"

# Upload data file (rename to remove space and '(2)')
scp -i "$KEY" -o StrictHostKeyChecking=no \
    "intervar_MG_100.filtered (2).txt" \
    "$REMOTE:$WORKDIR/intervar_MG_100.filtered.txt"

# Upload updated app files
scp -i "$KEY" -o StrictHostKeyChecking=no \
    app/ai/schema_injector.py \
    app/ai/text_to_sql.py \
    app/ai/llm_config.py \
    "$REMOTE:$WORKDIR/app/ai/"

scp -i "$KEY" -o StrictHostKeyChecking=no \
    app/api/ai_endpoints.py \
    "$REMOTE:$WORKDIR/app/api/"

scp -i "$KEY" -o StrictHostKeyChecking=no \
    app/config.py \
    "$REMOTE:$WORKDIR/app/"

scp -i "$KEY" -o StrictHostKeyChecking=no \
    start_servers.sh \
    "$REMOTE:$WORKDIR/"

echo ""
echo "Step 2: Running ingestion on remote (~5-10 min for 76K rows)..."
ssh -i "$KEY" -o StrictHostKeyChecking=no "$REMOTE" "
  cd $WORKDIR
  $PYTHON ingest_new_db.py intervar_MG_100.filtered.txt patient_variants.db
  echo 'Ingestion complete.'
  ls -lh patient_variants.db
"

echo ""
echo "Step 3: Restarting backend only (not Ollama, not Gradio)..."
ssh -i "$KEY" -o StrictHostKeyChecking=no "$REMOTE" "
  cd $WORKDIR
  pkill -f 'uvicorn main:app' 2>/dev/null || true
  sleep 3
  export TMPDIR=/ephemeral/tmp
  export HF_HOME=/ephemeral/hf
  export LLM_BACKEND=vllm_api
  export VLLM_API_URL=http://localhost:11434
  export VLLM_MODEL=qwen3:32b
  export SQLALCHEMY_DATABASE_URL=sqlite:///patient_variants.db
  nohup $PYTHON -m uvicorn main:app --host 0.0.0.0 --port 8000 --log-level info > backend.log 2>&1 &
  sleep 8
  echo 'Health check:'
  curl -s http://localhost:8000/api/health
  echo ''
  echo 'Reconnecting LLM:'
  curl -s -X POST http://localhost:8000/api/ai/reconnect
  echo ''
"

echo ""
echo "=== Deployment Complete ==="
echo "Test with: curl -s -X POST http://62.169.159.252:8000/api/ai/chat"
echo "           -H 'Content-Type: application/json'"
echo "           -d '{\"message\": \"Show pathogenic variants in BRCA1\"}'"
