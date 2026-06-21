@echo off
setlocal EnableDelayedExpansion

set "SOURCE=%~1"
set "TARGET=%~2"
set "PID=%~3"
set "MARKER=%~4"
set "STAGED=%TARGET%.new"
set "BACKUP=%TARGET%.bak"
set "HAD_TARGET=0"

echo TensaLauncher Updater
echo =====================
echo.
echo Waiting for launcher to close (PID: %PID%)...

set /a counter=0
:wait_loop
tasklist /FI "PID eq %PID%" 2>NUL | find "%PID%" >NUL
if ERRORLEVEL 1 goto process_closed

ping -n 2 127.0.0.1 >NUL
set /a counter+=1

if !counter! GTR 60 (
    echo ERROR: Timeout waiting for launcher to close
    echo Please close the launcher manually and run this script again
    exit /b 1
)
goto wait_loop

:process_closed
echo Launcher closed successfully
echo.

ping -n 3 127.0.0.1 >NUL

if exist "%TARGET%" set "HAD_TARGET=1"

if not exist "%SOURCE%" (
    echo ERROR: Update file not found: %SOURCE%
    goto recover_or_exit
)

echo Preparing update...

set /a retry=0
:cleanup_staged_loop
if exist "%STAGED%" del /f /q "%STAGED%" 2>NUL
if exist "%STAGED%" (
    if !retry! LSS 30 (
        ping -n 2 127.0.0.1 >NUL
        set /a retry+=1
        goto cleanup_staged_loop
    )
    echo ERROR: Cannot prepare staging file
    goto recover_or_exit
)

copy /y "%SOURCE%" "%STAGED%" >NUL 2>&1
if errorlevel 1 goto stage_failed
if not exist "%STAGED%" goto stage_failed
for %%A in ("%STAGED%") do if %%~zA LEQ 0 goto stage_failed

set /a retry=0
:cleanup_backup_loop
if exist "%BACKUP%" del /f /q "%BACKUP%" 2>NUL
if exist "%BACKUP%" (
    if !retry! LSS 30 (
        ping -n 2 127.0.0.1 >NUL
        set /a retry+=1
        goto cleanup_backup_loop
    )
    echo ERROR: Cannot prepare backup file
    goto recover_or_exit
)

if "%HAD_TARGET%"=="0" goto install_staged

echo Backing up current version...
set /a retry=0
:backup_loop
move /y "%TARGET%" "%BACKUP%" >NUL 2>&1
if exist "%TARGET%" (
    if !retry! LSS 30 (
        ping -n 2 127.0.0.1 >NUL
        set /a retry+=1
        goto backup_loop
    )
    echo ERROR: Cannot move old file to backup
    goto recover_or_exit
)
if not exist "%BACKUP%" (
    echo ERROR: Cannot create backup file
    goto recover_or_exit
)

:install_staged
echo Installing new version...
set /a retry=0
:install_loop
copy /y "%STAGED%" "%TARGET%" >NUL 2>&1
if not exist "%TARGET%" (
    if !retry! LSS 30 (
        ping -n 2 127.0.0.1 >NUL
        set /a retry+=1
        goto install_loop
    )
    goto install_failed
)
for %%A in ("%TARGET%") do if %%~zA LEQ 0 goto install_failed

del /f /q "%BACKUP%" 2>NUL
del /f /q "%STAGED%" 2>NUL
del /f /q "%SOURCE%" 2>NUL
goto success

:stage_failed
echo ERROR: Failed to stage update file
goto recover_or_exit

:install_failed
echo ERROR: Failed to install staged update
if "%HAD_TARGET%"=="1" goto restore_backup
goto keep_marker_for_retry

:restore_backup
echo Restoring previous version...
if not exist "%BACKUP%" goto recover_or_exit
set /a retry=0
:restore_loop
copy /y "%BACKUP%" "%TARGET%" >NUL 2>&1
if not exist "%TARGET%" (
    if !retry! LSS 30 (
        ping -n 2 127.0.0.1 >NUL
        set /a retry+=1
        goto restore_loop
    )
    goto keep_marker_for_retry
)
for %%A in ("%TARGET%") do if %%~zA LEQ 0 goto keep_marker_for_retry
del /f /q "%STAGED%" 2>NUL
goto recover_or_exit

:recover_or_exit
if exist "%TARGET%" (
    if defined MARKER if exist "%MARKER%" del /f /q "%MARKER%" 2>NUL
)
exit /b 1

:keep_marker_for_retry
echo ERROR: Previous version could not be restored; keeping pending update marker for retry
exit /b 1

:success
if defined MARKER (
    if exist "%MARKER%" del /f /q "%MARKER%" 2>NUL
)

echo.
echo Update completed successfully!
echo.

(goto) 2>nul & del "%~f0"
