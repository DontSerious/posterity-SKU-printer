@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

rem Keep this file pure ASCII. cmd.exe parses .bat with the OEM codepage,
rem so non-ASCII bytes here desync the parser. All Chinese output is done
rem from app.py, which forces UTF-8 on stdout.

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

echo [2/2] Starting server on http://127.0.0.1:5000
"%UV%" run python app.py

pause
exit /b 0

:find_uv
rem winget installs uv either as a Links shim or straight into Packages\<name+hash>\,
rem and a freshly-changed PATH is not visible to already-open shells. Try them all.
set "UV="
where uv >nul 2>nul && set "UV=uv" && goto :eof
for %%D in (
  "%LOCALAPPDATA%\Microsoft\WinGet\Links\uv.exe"
  "%LOCALAPPDATA%\Programs\uv\uv.exe"
  "%USERPROFILE%\.local\bin\uv.exe"
) do if not defined UV if exist "%%~D" set "UV=%%~D"
if not defined UV for /f "delims=" %%D in ('dir /b /s "%LOCALAPPDATA%\Microsoft\WinGet\Packages\uv.exe" 2^>nul') do if not defined UV set "UV=%%D"
goto :eof
