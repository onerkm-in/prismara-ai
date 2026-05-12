@echo off
REM ============================================================
REM  Prismara AI Build Script - produces a self-contained PrismaraAI.exe
REM  Build-machine prerequisites (not required by end users):
REM    pip install -r requirements.txt
REM    npm install  (in both frontend/ and server/)
REM ============================================================

setlocal EnableDelayedExpansion

echo.
echo ============================================================
echo  Prismara AI Packager
echo ============================================================
echo.

REM --- 0. Verify tools are available ---------------------------
python --version >nul 2>&1 || (
    echo [ERROR] Python not found. Install Python 3.11+ and add to PATH.
    exit /b 1
)
call npm --version >nul 2>&1 || (
    echo [ERROR] npm not found. Install Node.js 18+ and add to PATH.
    exit /b 1
)
REM --- 1. Install Python dependencies ---------------------------
echo [1/5] Installing Python dependencies...
python -m pip install -r requirements.txt --quiet || (
    echo [ERROR] pip install failed.
    exit /b 1
)
echo       Done.

REM --- 2. Install Node dependencies -----------------------------
echo [2/5] Installing frontend Node dependencies...
pushd frontend
call npm install --silent || (
    echo [ERROR] npm install frontend failed.
    exit /b 1
)
popd

REM --- 3. Build React frontend ----------------------------------
echo [3/5] Building React frontend (npm run build)...
pushd frontend
call npm run build || (
    echo [ERROR] React build failed. Check frontend/src for errors.
    exit /b 1
)
popd
echo       Build output: frontend\dist\

REM --- 4. Run PyInstaller ---------------------------------------
echo [4/5] Running PyInstaller (this takes 1-3 minutes)...
python -m PyInstaller prismara.spec --noconfirm --clean || (
    echo [ERROR] PyInstaller failed.
    exit /b 1
)
echo       Exe produced: dist\PrismaraAI.exe

if not exist "release" mkdir "release"
copy /Y "dist\PrismaraAI.exe" "release\PrismaraAI.exe" >nul || (
    echo [ERROR] Could not create clean release\PrismaraAI.exe.
    exit /b 1
)
python -c "import hashlib, pathlib; p=pathlib.Path('release/PrismaraAI.exe'); pathlib.Path('release/PrismaraAI.exe.sha256').write_text(hashlib.sha256(p.read_bytes()).hexdigest().upper() + chr(10), encoding='ascii')" || (
    echo [WARN] Could not write SHA256 hash file.
)

REM --- 5. Verify output -----------------------------------------
echo [5/5] Verifying output...
if exist "release\PrismaraAI.exe" (
    for %%F in ("release\PrismaraAI.exe") do set SIZE=%%~zF
    set /a SIZE_MB=!SIZE! / 1048576
    echo       PrismaraAI.exe size: ~!SIZE_MB! MB
    if exist "release\PrismaraAI.exe.sha256" (
        set /p SHA256=<"release\PrismaraAI.exe.sha256"
        echo       SHA256: !SHA256!
    )
    if exist "dist\prismara" (
        echo       NOTE: dist\prismara is local runtime data and is NOT part of the deliverable.
    )
    echo.
    echo ============================================================
    echo  BUILD SUCCESSFUL
    echo  Deliverable: release\PrismaraAI.exe
    echo  Ship only release\PrismaraAI.exe. It creates its own prismara folder on first run.
    echo  End users do not need Python, Node, npm, or PyInstaller.
    echo ============================================================
) else (
    echo [ERROR] release\PrismaraAI.exe not found after build.
    exit /b 1
)

endlocal
