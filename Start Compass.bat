@echo off
rem Double-click to start Compass on Windows. Works from wherever this folder is.
title Compass
cd /d "%~dp0"
call "%~dp0compass.cmd" %*
if errorlevel 1 pause
