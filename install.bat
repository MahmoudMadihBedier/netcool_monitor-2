@echo off
cd /d "%~dp0"
echo Installing Netcool Shift Monitor requirements...
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
echo.
echo Installing the tool's own browser (Chromium) - it never uses your Edge.
echo (If this step fails on a network without internet, the tool falls back to Edge with a separate profile.)
python -m playwright install --no-shell chromium
echo.
echo Done. Start the tool with run.bat
pause
