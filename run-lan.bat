@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

rem LAN mode: binds 0.0.0.0 so other hosts on the same subnet can print
rem from a browser without installing the CT-428D driver.
rem WARNING: anyone on the subnet can then make this printer feed paper.
rem
rem Keep this file pure ASCII -- see the note in run.bat.

call :find_uv
if not defined UV (
  echo [ERROR] uv not found. Run: winget install --id astral-sh.uv -e
  echo         Then open a NEW terminal window so PATH takes effect.
  pause
  exit /b 1
)

echo [1/2] Syncing dependencies...
"%UV%" sync
if errorlevel 1 (
  echo [ERROR] dependency install failed
  pause
  exit /b 1
)

echo [2/2] Starting server in LAN mode
"%UV%" run python app.py --lan

pause
exit /b 0

:find_uv
set "UV="
where uv >nul 2>nul && set "UV=uv" && goto :eof
for %%D in (
  "%LOCALAPPDATA%\Microsoft\WinGet\Links\uv.exe"
  "%LOCALAPPDATA%\Programs\uv\uv.exe"
  "%USERPROFILE%\.local\bin\uv.exe"
) do if not defined UV if exist "%%~D" set "UV=%%~D"
if not defined UV for /f "delims=" %%D in ('dir /b /s "%LOCALAPPDATA%\Microsoft\WinGet\Packages\uv.exe" 2^>nul') do if not defined UV set "UV=%%D"
goto :eof
