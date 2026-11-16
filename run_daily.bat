@echo off
rem Launched daily (and at logon, as a catch-up) by Task Scheduler "Daily Job Search" (created by setup.py).
cd /d "%~dp0"
rem setup.py saves the Python it ran with and the folder that has the `claude` CLI, since scheduled
rem tasks don't always get your full PATH.
set "PYEXE=python"
if exist ".python-path" set /p PYEXE=<.python-path
set "CLAUDEDIR=%APPDATA%\npm"
if exist ".claude-dir" set /p CLAUDEDIR=<.claude-dir
set "PATH=%CLAUDEDIR%;%APPDATA%\npm;%USERPROFILE%\.local\bin;%PATH%"
rem Skip if today's report already exists (the logon trigger after a daily run).
"%PYEXE%" -c "import datetime,pathlib,sys; sys.exit(pathlib.Path('reports',f'{datetime.date.today()}.html').exists())" || exit /b 0
"%PYEXE%" jobsearch.py >> jobsearch_stdout.log 2>&1
