@echo off
echo Setting up auto-start for Updates Bot...

set STARTUP=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup
set TARGET=C:\Users\lapto\updates-bot\START BOT HIDDEN.vbs
set SHORTCUT=%STARTUP%\Updates Bot.vbs

copy "%TARGET%" "%SHORTCUT%" >nul

if exist "%SHORTCUT%" (
    echo.
    echo Done! Updates Bot will now start automatically when you log in.
) else (
    echo.
    echo Something went wrong. Try running this as Administrator.
)

echo.
pause
