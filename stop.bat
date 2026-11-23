@echo off
setlocal
cd /d "%~dp0"
rem Frees the GrowthCompass ports so a restart never leaves an old server answering.
rem Main ports come from config.toml; 8011/5186/5196 are the preview "alt" servers in .claude/launch.json.

for /f "usebackq tokens=1,2 delims=|" %%a in (`python -c "import tomllib;c=tomllib.load(open('config.toml','rb'));print(c['server']['backend_port'],c['server']['frontend_port'],sep='|')"`) do (
  set BACKEND_PORT=%%a
  set FRONTEND_PORT=%%b
)
if not defined BACKEND_PORT set BACKEND_PORT=8001
if not defined FRONTEND_PORT set FRONTEND_PORT=5176

set FREED=0
for %%P in (%BACKEND_PORT% %FRONTEND_PORT% 8011 5186 5196) do call :free %%P
if "%FREED%"=="0" echo [ok] no GrowthCompass servers were running.
goto :eof

:free
for /f "tokens=5" %%p in ('netstat -ano ^| findstr /r /c:"127\.0\.0\.1:%1 .*LISTENING"') do (
  echo [stop] port %1 - killing PID %%p
  taskkill /PID %%p /T /F >nul 2>&1
  set FREED=1
)
goto :eof
