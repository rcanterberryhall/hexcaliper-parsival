@echo off
REM Wrapper for the Parsival calendar sidecar, invoked by Task Scheduler every 4 hours.
REM Appends timestamped stdout/stderr to logs\calendar_sidecar.log for later inspection.
set "PY=C:\Users\reid.hall\AppData\Local\Programs\Python\Python313\python.exe"
set "SCRIPT=C:\GitHub\hexcaliper-parsival\scripts\outlook_sidecar.py"
set "LOGDIR=C:\GitHub\hexcaliper-parsival\scripts\logs"
if not exist "%LOGDIR%" mkdir "%LOGDIR%"
echo ==== run %date% %time% ==== >> "%LOGDIR%\calendar_sidecar.log"
"%PY%" "%SCRIPT%" --calendar >> "%LOGDIR%\calendar_sidecar.log" 2>&1
echo ---- exit %ERRORLEVEL% ---- >> "%LOGDIR%\calendar_sidecar.log"
