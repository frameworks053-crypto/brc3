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

rem %~dp0 는 항상 역슬래시로 끝난다. 그대로 따옴표 안에 넣으면 마지막
rem 역슬래시가 닫는 따옴표를 이스케이프해 버리므로 떼어낸다.
for %%i in ("%~dp0..") do set "WORK_DIR=%%~fi"
set "PIPE_DIR=%~dp0"
if "%PIPE_DIR:~-1%"=="\" set "PIPE_DIR=%PIPE_DIR:~0,-1%"
set "PROBLEM=0"
set "PY_CMD="

rem ── 1. 파이썬 3.11 이상 찾기 ─────────────────────────────
rem  "python" 이 낡은 버전을 가리키는 일이 흔하다. 윈도우 py 런처로
rem  설치된 버전을 직접 골라 쓴다.
call :trypy 3.14
call :trypy 3.13
call :trypy 3.12
call :trypy 3.11

if not defined PY_CMD (
    rem py 런처가 없으면 PATH 의 python 을 확인한다.
    python -c "import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)" >nul 2>&1
    if not errorlevel 1 set "PY_CMD=python"
)

if not defined PY_CMD goto :nopython
for /f "tokens=2" %%v in ('%PY_CMD% --version 2^>^&1') do set "PYVER=%%v"
echo   [O] 파이썬 !PYVER!   ^(%PY_CMD%^)
goto :checkffmpeg

:nopython
echo   [X] 파이썬 3.11 이상을 찾지 못했습니다.
echo.
echo       지금 설치된 파이썬:
python --version 2>nul
if errorlevel 1 echo         (없음)
echo.
echo       새 버전을 설치했는데도 안 잡힌다면, 설치할 때
echo       "Add python.exe to PATH" 를 체크하지 않은 경우입니다.
echo.
echo       해결 방법
echo         파이썬 설치 파일을 다시 실행 - Modify - Next -
echo         "Add Python to environment variables" 체크 - Install
echo.
echo       또는 https://www.python.org/downloads/ 에서 최신 버전을
echo       새로 설치하세요. 설치 후 이 창을 닫고 다시 실행해야 합니다.
echo.
set "PROBLEM=1"

:checkffmpeg

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
%PY_CMD% -m pip install --quiet --upgrade pip
%PY_CMD% -m pip install --quiet -e "%PIPE_DIR%"
if errorlevel 1 (
    echo.
    echo 설치에 실패했습니다. 위의 메시지를 그대로 복사해서 알려주세요.
    echo.
    pause
    exit /b 1
)

rem ── 5. 실행 확인 ─────────────────────────────────────────
rem  파이썬 Scripts 폴더가 PATH 에 없으면 plpipe 명령이 안 잡힌다.
rem  그럴 때를 대비해 작업 폴더에 실행용 배치를 만들어 둔다.
set "LAUNCHER=%WORK_DIR%\plpipe.bat"
> "%LAUNCHER%" echo @echo off
>> "%LAUNCHER%" echo %PY_CMD% -m plpipe %%*

plpipe --help >nul 2>&1
if errorlevel 1 (
    echo   [O] 설치 완료
    echo.
    echo   참고: plpipe 명령이 바로 잡히지 않아 실행용 파일을 만들어 뒀습니다.
    echo         %LAUNCHER%
    echo         작업 폴더에서 plpipe 라고 치면 그대로 동작합니다.
) else (
    echo   [O] 설치 완료
)

echo.
echo ============================================
echo   다음 순서
echo ============================================
echo.
echo   1. AE 템플릿(.aep)을 아래 폴더에 두세요
echo        %WORK_DIR%
echo.
echo   2. 명령 프롬프트에서:
echo        cd /d %WORK_DIR%
echo        plpipe setup
echo        plpipe check
echo.
pause
exit /b 0

rem ── 보조 함수 ────────────────────────────────────────────
:trypy
if defined PY_CMD goto :eof
py -%1 -c "import sys" >nul 2>&1
if errorlevel 1 goto :eof
set "PY_CMD=py -%1"
goto :eof
