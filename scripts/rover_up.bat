@echo off
rem Starts the laptop runner and rover_controller together. Options: see rover_up.ps1.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0rover_up.ps1" %*
