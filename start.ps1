# Genomic Q&A Engine — Full Startup Script
# Run this AFTER starting the SSH tunnel in a separate terminal:
#   ssh -N -i "C:\Users\Admin\Desktop\intervar\ubuntu_.pem" -L 11434:localhost:11434 ubuntu@62.169.159.252

Set-Location "C:\Users\Admin\Desktop\intervar"

Write-Host ""
Write-Host "=== Genomic Q&A Engine Startup ===" -ForegroundColor Cyan
Write-Host ""

# Step 1: Kill any old processes on ports 8000 and 7860
Write-Host "[1/4] Stopping any old server processes..." -ForegroundColor Yellow
Get-Process -Name "uvicorn" -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Get-NetTCPConnection -LocalPort 7860 -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }
Start-Sleep -Seconds 2

# Step 2: Start FastAPI backend
Write-Host "[2/4] Starting FastAPI backend on port 8000..." -ForegroundColor Yellow
Remove-Item "server.log","server_err.log" -ErrorAction SilentlyContinue
Start-Process -FilePath ".\.venv\Scripts\uvicorn.exe" -ArgumentList "main:app","--host","0.0.0.0","--port","8000" -WindowStyle Hidden -RedirectStandardOutput "server.log" -RedirectStandardError "server_err.log"
Start-Sleep -Seconds 6

# Step 3: Connect LLM (Qwen3-32B via SSH tunnel)
Write-Host "[3/4] Connecting Qwen3-32B model..." -ForegroundColor Yellow
$reconnect = Invoke-RestMethod -Uri "http://localhost:8000/api/ai/reconnect" -Method Post -ErrorAction SilentlyContinue
if ($reconnect.status -eq "connected") {
    Write-Host "      Model connected: $($reconnect.model)" -ForegroundColor Green
} else {
    Write-Host "      WARNING: Model not connected. Is the SSH tunnel running?" -ForegroundColor Red
    Write-Host "      Run first: ssh -N -i 'C:\Users\Admin\Desktop\intervar\ubuntu_.pem' -L 11434:localhost:11434 ubuntu@62.169.159.252" -ForegroundColor Red
}

# Step 4: Start Gradio UI
Write-Host "[4/4] Starting Gradio UI on port 7860..." -ForegroundColor Yellow
Start-Process -FilePath ".\.venv\Scripts\python.exe" -ArgumentList "gradio_app.py" -WindowStyle Hidden -RedirectStandardOutput "gradio.log" -RedirectStandardError "gradio_err.log"
Start-Sleep -Seconds 6

Write-Host ""
Write-Host "=== All services started ===" -ForegroundColor Green
Write-Host ""
Write-Host "  Gradio UI  :  http://localhost:7860" -ForegroundColor Cyan
Write-Host "  API docs   :  http://localhost:8000/docs" -ForegroundColor Cyan
Write-Host "  LLM status :  http://localhost:8000/api/ai/status" -ForegroundColor Cyan
Write-Host ""
