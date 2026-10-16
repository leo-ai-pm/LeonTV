@echo off
chcp 65001 >nul 2>&1
setlocal

set "ROOT_DIR=%~dp0"
set "BACKEND_DIR=%ROOT_DIR%backend"
set "FRONTEND_DIR=%ROOT_DIR%frontend"
set "FRONTEND_START_COMMAND=npm start"
set "SKIP_FRONTEND_BUILD=%XYQ_SKIP_FRONTEND_BUILD%"
if defined LEON_TV_SKIP_FRONTEND_BUILD set "SKIP_FRONTEND_BUILD=%LEON_TV_SKIP_FRONTEND_BUILD%"

echo [install] Leon TV one-click install

where python >nul 2>nul
if errorlevel 1 (
  echo [install] Python 3.10+ is required. Please install Python first.
  exit /b 1
)

python -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)"
if errorlevel 1 (
  echo [install] Python 3.10+ is required. Please upgrade Python first.
  exit /b 1
)

where node >nul 2>nul
if errorlevel 1 (
  echo [install] Node.js 20.9+ is required. Please install Node.js first.
  exit /b 1
)

node -e "const [major, minor] = process.versions.node.split('.').map(Number); process.exit(major > 20 || (major === 20 && minor >= 9) ? 0 : 1)"
if errorlevel 1 (
  echo [install] Node.js 20.9+ is required. Please upgrade Node.js first.
  exit /b 1
)

where npm >nul 2>nul
if errorlevel 1 (
  echo [install] npm is required. Please install npm first.
  exit /b 1
)

where ffmpeg >nul 2>nul
if errorlevel 1 (
  echo [install] ffmpeg was not found. Video concatenation and audio/video post-processing will be unavailable.
)

cd /d "%BACKEND_DIR%" || goto :install_failed
if not exist "config.yaml" if exist "config.yaml.example" (
  copy "config.yaml.example" "config.yaml" >nul || goto :install_failed
  echo [install] Created backend config.yaml from config.yaml.example. Fill in API keys before generating videos.
)

where uv >nul 2>nul
if errorlevel 1 (
  echo [install] uv was not found. Installing uv with python -m pip...
  python -m pip install --user uv
  if errorlevel 1 echo [install] Automatic uv installation failed. Trying venv + pip.
)

where uv >nul 2>nul
if errorlevel 1 (
  echo [install] uv is still not on PATH. Falling back to venv + pip.
  python -m venv venv || goto :install_failed
  call venv\Scripts\activate.bat || goto :install_failed
  python -m pip install --upgrade pip || goto :install_failed
  python -m pip install -r requirements.txt || goto :install_failed
) else (
  uv sync || goto :install_failed
)

cd /d "%FRONTEND_DIR%" || goto :install_failed
if exist "package-lock.json" (
  call npm ci || goto :install_failed
) else (
  call npm install || goto :install_failed
)

if "%SKIP_FRONTEND_BUILD%"=="1" (
  echo [install] Skipping frontend build via LEON_TV_SKIP_FRONTEND_BUILD or legacy XYQ_SKIP_FRONTEND_BUILD.
  set "FRONTEND_START_COMMAND=npm run dev"
) else (
  call npm run build || goto :install_failed
)

echo.
echo [install] Leon TV installation complete.
echo.
echo Next steps:
echo   1. Edit backend API keys:
echo      %BACKEND_DIR%\config.yaml
echo.
echo   2. Start backend:
echo      cd /d "%BACKEND_DIR%"
echo      uv run python api_server.py
echo      or: venv\Scripts\activate.bat ^&^& python api_server.py
echo.
echo   3. Start frontend in a new terminal:
echo      cd /d "%FRONTEND_DIR%"
echo      %FRONTEND_START_COMMAND%
echo.
echo URLs:
echo   Backend:  http://localhost:8000/api/health
echo   Frontend: http://localhost:3000

endlocal
exit /b 0

:install_failed
echo [install] Leon TV installation failed. Review the error above before retrying. 1>&2
endlocal
exit /b 1
