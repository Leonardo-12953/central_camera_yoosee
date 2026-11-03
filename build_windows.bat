@echo off
setlocal
cd /d "%~dp0"

if not exist .build-venv (
    python -m venv .build-venv
    if errorlevel 1 goto :error
)

call .build-venv\Scripts\activate.bat
if errorlevel 1 goto :error

python -m pip install --upgrade pip
if errorlevel 1 goto :error

python -m pip install -r requirements-build.txt
if errorlevel 1 goto :error

python -m PyInstaller --noconfirm --clean central_cameras.spec
if errorlevel 1 goto :error

if exist dist\CentralCameras-windows rmdir /s /q dist\CentralCameras-windows
if exist dist\CentralCameras-windows.zip del /q dist\CentralCameras-windows.zip
mkdir dist\CentralCameras-windows

copy /y dist\CentralCameras.exe dist\CentralCameras-windows\ >nul
copy /y LEIA-ME.txt dist\CentralCameras-windows\ >nul
copy /y .env.example dist\CentralCameras-windows\ >nul
copy /y config.example.json dist\CentralCameras-windows\ >nul

powershell -NoProfile -ExecutionPolicy Bypass -Command "Compress-Archive -Path 'dist\CentralCameras-windows' -DestinationPath 'dist\CentralCameras-windows.zip' -Force"
if errorlevel 1 goto :error

echo.
echo Pronto: dist\CentralCameras-windows.zip
if not "%CI%"=="1" pause
exit /b 0

:error
echo.
echo Falha ao gerar o executavel.
if not "%CI%"=="1" pause
exit /b 1
