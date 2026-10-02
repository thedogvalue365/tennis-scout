@echo off
title Tennis Scout
cd /d "%~dp0"
chcp 65001 >nul
:loop
python bot.py
if errorlevel 3 if not errorlevel 4 goto end
echo.
echo El bot se ha parado. Se reinicia en 30 segundos (cierra esta ventana para pararlo del todo)...
timeout /t 30 >nul
goto loop
:end
timeout /t 5 >nul
