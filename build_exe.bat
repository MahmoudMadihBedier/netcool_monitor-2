@echo off
REM ==========================================================================
REM  Builds the PORTABLE version:   dist\NetcoolShiftMonitor\NetcoolShiftMonitor.exe
REM                       and zip:  dist\NetcoolShiftMonitor_portable.zip
REM  Run once on any Windows PC with Python 3.9+ ("Add python.exe to PATH") and internet.
REM  Users who receive the zip need NO Python and NO installation.
REM ==========================================================================
setlocal
cd /d "%~dp0"
REM the tool's own Chromium goes INSIDE the build (so it never uses / blocks the user's Edge)
set PLAYWRIGHT_BROWSERS_PATH=0

echo [1/5] Installing build tools and requirements...
python -m pip install --upgrade pip
python -m pip install -r requirements.txt pyinstaller
if errorlevel 1 goto :fail

echo [2/5] Getting the tool's own browser (Chromium)...
python -m playwright install --no-shell chromium
if errorlevel 1 echo WARNING: Chromium download failed - the tool will use Edge with a separate profile instead.

echo [3/5] Running the tests...
python -m unittest discover tests
if errorlevel 1 goto :fail

echo [4/5] Building the portable folder (1-5 minutes)...
python -m PyInstaller --noconfirm --clean NetcoolShiftMonitor.spec
if errorlevel 1 goto :fail

echo [5/5] Zipping...
if exist "dist\NetcoolShiftMonitor_portable.zip" del "dist\NetcoolShiftMonitor_portable.zip"
tar -a -c -f "dist\NetcoolShiftMonitor_portable.zip" -C dist NetcoolShiftMonitor
if errorlevel 1 echo (Could not zip automatically - right-click dist\NetcoolShiftMonitor ^> Send to ^> Compressed folder)

echo.
echo DONE:
echo   folder: %~dp0dist\NetcoolShiftMonitor\NetcoolShiftMonitor.exe
echo   share : %~dp0dist\NetcoolShiftMonitor_portable.zip
explorer "%~dp0dist"
pause
exit /b 0

:fail
echo.
echo BUILD FAILED - see the messages above.
pause
exit /b 1
