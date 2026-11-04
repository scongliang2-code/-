@echo off
chcp 65001 >nul
cd /d "%~dp0"
if errorlevel 1 exit /b 1
python -B -X utf8 python_copy\desktop_app.py
if errorlevel 1 pause
