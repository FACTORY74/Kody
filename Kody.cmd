@echo off
setlocal

set "ROOT_DIRECTORY=%~dp0"
set "PYTHON_EXECUTABLE=%ROOT_DIRECTORY%.venv\Scripts\python.exe"

if not exist "%PYTHON_EXECUTABLE%" (
    echo Kody cannot start because its virtual environment is missing.
    echo Follow command_lines.txt from:
    echo %ROOT_DIRECTORY%
    pause
    exit /b 1
)

"%PYTHON_EXECUTABLE%" "%ROOT_DIRECTORY%main.py" %*
set "EXIT_CODE=%ERRORLEVEL%"

if not "%EXIT_CODE%"=="0" pause
exit /b %EXIT_CODE%
