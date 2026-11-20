@echo off
rem Windows: double-click this file to open Fees Tracker.
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (py -3 -m feestracker) else (python -m feestracker)
pause
