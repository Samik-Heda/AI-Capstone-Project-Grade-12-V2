@echo off
setlocal
cd /d "%~dp0"
echo === GrowthCompass v2 ===

rem Ports and URLs come from config.toml (single source of truth).
for /f "usebackq tokens=1,2,3,4 delims=|" %%a in (`python -c "import tomllib;c=tomllib.load(open('config.toml','rb'));print(c['server']['backend_host'],c['server']['backend_port'],c['server']['frontend_port'],c['ollama']['base_url'],sep='|')"`) do (
  set BACKEND_HOST=%%a
  set BACKEND_PORT=%%b
  set FRONTEND_PORT=%%c
  set OLLAMA_URL=%%d
)
if not defined BACKEND_PORT (
  echo [error] could not read config.toml - is Python 3.12 on PATH?
  exit /b 1
)

python -c "import fastapi, uvicorn, numpy, sklearn, rapidfuzz, shapely, httpx" 2>nul || (
  echo [setup] installing Python requirements...
  python -m pip install -q -r requirements.txt || exit /b 1
)

curl -s -m 2 %OLLAMA_URL%/api/tags >nul 2>&1 && (
  echo [ok] Ollama reachable at %OLLAMA_URL%
) || (
  echo [warn] Ollama not reachable at %OLLAMA_URL% - the app still works with templated replies.
)

if not exist frontend\public\map.json (
  echo [setup] building map asset...
  python pipeline\build_map.py || exit /b 1
)
if not exist data\gc.db (
  echo [setup] seeding database...
  python pipeline\seed.py || exit /b 1
)
if not exist frontend\node_modules (
  echo [setup] installing frontend packages...
  pushd frontend && call npm install --silent && popd
)

rem An old server still holding a port makes the new one fail silently while the stale one keeps answering.
call stop.bat

echo [run] backend  http://%BACKEND_HOST%:%BACKEND_PORT%
start "GrowthCompass backend" cmd /k python -m backend.serve
echo [run] frontend http://127.0.0.1:%FRONTEND_PORT%
start "GrowthCompass frontend" cmd /k "cd frontend && npm run dev"

rem Wait for the backend, then open the browser.
for /l %%i in (1,1,60) do (
  curl -s -m 1 http://%BACKEND_HOST%:%BACKEND_PORT%/api/health >nul 2>&1 && goto :ready
  ping -n 2 127.0.0.1 >nul
)
echo [warn] backend did not answer within 60 s - check the backend window.
goto :eof

:ready
start "" http://127.0.0.1:%FRONTEND_PORT%
echo [ok] GrowthCompass is up.
