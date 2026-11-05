@echo off
setlocal
cd /d "%~dp0"

rem Nao precisa de administrador. Se o Windows bloquear este arquivo com
rem "politica de Controle de Aplicativo", desbloqueie o .zip antes de
rem extrair (botao direito > Propriedades > Desbloquear) ou rode
rem Unblock-File. Detalhes no README.md.

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

rem Copia o pacote onedir completo (CentralCameras.exe + _internal).
xcopy /e /i /y /h "dist\CentralCameras" "dist\CentralCameras-windows" >nul
if errorlevel 1 goto :error

copy /y LEIA-ME.txt dist\CentralCameras-windows\ >nul
copy /y COMO_USAR.md dist\CentralCameras-windows\ >nul
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
