@echo off
echo Stopping any running instance of FireRiskMap.exe...
taskkill /F /IM FireRiskMap.exe >nul 2>&1

echo Installing dependencies...
python -m pip install -r requirements.txt
python -m pip install pyinstaller

echo.
echo Building FireRiskMap.exe...
python -m PyInstaller ^
    --onefile ^
    --noconsole ^
    --name FireRiskMap ^
    --hidden-import lxml._elementpath ^
    --hidden-import win10toast ^
    --collect-all httpx ^
    --collect-all certifi ^
    FireRiskMaps.py

echo.
if exist dist\FireRiskMap.exe (
    echo SUCCESS: dist\FireRiskMap.exe is ready.
) else (
    echo FAILED: dist\FireRiskMap.exe was not created.
)
pause
