@echo off
cd /d "%~dp0"

echo Starting KB4Trade Bot and Streamlit Dashboard...

:: Detect Python executable (prefer configured Python path, fallback to system PATH)
set "PYTHON_EXE=C:\Users\krono\AppData\Local\Programs\Python\Python311\python.exe"
if not exist "%PYTHON_EXE%" (
    set "PYTHON_EXE=python"
)

:: Start Trading Engine in a new window
start "KB4Trade Bot" cmd /k ""%PYTHON_EXE%" -m app.main"

:: Start Streamlit Dashboard in a new window
start "KB4Trade Dashboard" cmd /k ""%PYTHON_EXE%" -m streamlit run dashboard.py --server.port 8501"

echo Bot and Dashboard launched!
