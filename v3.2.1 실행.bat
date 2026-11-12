@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "BASKET_PYTHON=%USERPROFILE%\miniconda3\envs\streamlit_env\python.exe"
if not exist "%BASKET_PYTHON%" set "BASKET_PYTHON=python"
if exist "%~dp0.venv\Scripts\python.exe" set "BASKET_PYTHON=%~dp0.venv\Scripts\python.exe"
set "PYTHONUTF8=1"
"%BASKET_PYTHON%" -m streamlit run streamlit_app.py --server.address 127.0.0.1 --server.port 8512 --browser.gatherUsageStats false
pause
