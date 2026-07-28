@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

rem plpipe 설치 스크립트 (윈도우)
rem 이 파일을 더블클릭하면 필요한 걸 확인하고 plpipe 를 설치합니다.

echo.
echo ============================================
echo   plpipe 설치
echo ============================================
echo.

set "PIPE_DIR=%~dp0"
set "PROBLEM=0"

rem ── 1. 파이썬 ────────────────────────────────────────────
python --version >nul 2>&1
if errorlevel 1 (
    echo   [X] 파이썬이 없습니다.
    echo.
    echo       https://www.python.org/downloads/ 에서 받아 설치하세요.
    echo       설치 화면 맨 아래 "Add python.exe to PATH" 를 꼭 체크하세요.
    echo.
    set "PROBLEM=1"
) else (
    for /f "tokens=2" %%v in ('python --version 2^>^&1') do set "PYVER=%%v"
    echo   [O] 파이썬 !PYVER!

    rem 3.11 이상인지 확인
    python -c "import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)" >nul 2>&1
    if errorlevel 1 (
        echo       버전이 낮습니다. 3.11 이상이 필요합니다.
        echo       https://www.python.org/downloads/ 에서 최신 버전을 받으세요.
        set "PROBLEM=1"
    )
)

rem ── 2. ffmpeg ────────────────────────────────────────────
where ffmpeg >nul 2>&1
if errorlevel 1 (
    echo   [X] ffmpeg 이 없습니다.
    echo.
    echo       관리자 권한 명령 프롬프트에서 아래를 실행하세요:
    echo           winget install Gyan.FFmpeg
    echo       설치 후 창을 닫았다 다시 열어야 인식됩니다.
    echo.
    set "PROBLEM=1"
) else (
    echo   [O] ffmpeg
)

rem ── 3. After Effects ─────────────────────────────────────
set "AE_FOUND=0"
for /d %%d in ("C:\Program Files\Adobe\Adobe After Effects *") do (
    if exist "%%d\Support Files\AfterFX.exe" set "AE_FOUND=1"
)
if "%AE_FOUND%"=="1" (
    echo   [O] After Effects
) else (
    echo   [!] After Effects 를 기본 위치에서 못 찾았습니다.
    echo       설치되어 있다면 나중에 plpipe check 가 알려줍니다.
)

echo.

if "%PROBLEM%"=="1" (
    echo 위의 [X] 항목을 먼저 해결한 뒤 이 파일을 다시 실행하세요.
    echo.
    pause
    exit /b 1
)

rem ── 4. 설치 ──────────────────────────────────────────────
echo plpipe 를 설치하는 중...
echo.
python -m pip install --quiet --upgrade pip
python -m pip install --quiet -e "%PIPE_DIR%"
if errorlevel 1 (
    echo.
    echo 설치에 실패했습니다. 위의 메시지를 그대로 복사해서 알려주세요.
    echo.
    pause
    exit /b 1
)

plpipe --help >nul 2>&1
if errorlevel 1 (
    echo.
    echo 설치는 됐지만 plpipe 명령을 찾지 못합니다.
    echo 이 창을 닫고 새 명령 프롬프트를 연 뒤 다시 시도하세요.
    echo.
    pause
    exit /b 1
)

echo   [O] 설치 완료
echo.
echo ============================================
echo   다음 순서
echo ============================================
echo.
echo   1. 작업 폴더로 이동           cd /d D:\warmtapesociety
echo   2. AE 템플릿(.aep)을 그 폴더에 두세요
echo   3. 설정 만들기                plpipe setup
echo   4. 설정 확인                  plpipe check
echo.
pause
