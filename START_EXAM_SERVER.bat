@echo off
title Exam Proctoring System
color 0A
echo.
echo ============================================
echo   ONLINE EXAM PROCTORING SYSTEM
echo   Starting server... please wait
echo ============================================
echo.

:: Check if Python is installed
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python not found. Please install Python from python.org
    pause
    exit
)

:: Install required packages silently
echo [1/3] Installing required packages...
pip install flask opencv-python numpy --quiet --disable-pip-version-check

:: Run setup to create database
echo [2/3] Setting up database...
python setup.py

:: Open browser after 2 second delay
echo [3/3] Starting server...
echo.
echo ============================================
echo   Server running at: http://127.0.0.1:5000
echo   Open your browser and go to that address
echo   Press CTRL+C to stop the server
echo ============================================
echo.

:: Auto-open browser
start "" "http://127.0.0.1:5000"

:: Start Flask app
python app.py

pause
