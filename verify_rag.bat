@echo off
cd /d c:\Users\Admin\Desktop\intervar
"%USERPROFILE%\Miniconda3\python.exe" verify_rag_setup.py > rag_setup_result.txt 2>&1
echo RAG setup verification complete. Results saved to rag_setup_result.txt
pause
