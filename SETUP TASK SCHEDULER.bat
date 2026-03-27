@echo off
echo Setting up Updates Bot via Task Scheduler...

:: Remove old duplicate startup entries
del /f /q "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\Updates Bot.vbs" 2>nul
del /f /q "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\UpdatesBot.vbs" 2>nul
echo Removed old startup folder entries.

:: Create the scheduled task
schtasks /delete /tn "UpdatesBot" /f 2>nul

schtasks /create ^
  /tn "UpdatesBot" ^
  /tr "\"C:\Users\lapto\AppData\Local\Programs\Python\Python311\python.exe\" \"C:\Users\lapto\updates-bot\bot.py\"" ^
  /sc ONLOGON ^
  /delay 0001:00 ^
  /rl HIGHEST ^
  /f

if %errorlevel% equ 0 (
    echo.
    echo Done! The bot will now auto-start 1 minute after login.
    echo It will also restart automatically if it crashes.
) else (
    echo.
    echo Something went wrong. Try running this file as Administrator.
    echo Right-click it and choose "Run as administrator".
)

echo.
pause
