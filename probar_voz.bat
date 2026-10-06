@echo off
cd /d C:\Users\cop3\chatterbox

call C:\Users\cop3\miniconda3\Scripts\activate.bat chatterbox-v3

echo.
echo ==========================================
echo       CONFIGURACION DE LA VOZ
echo ==========================================
echo.
echo Modifica los parametros y cierra Notepad.
echo Al cerrarlo se generara automaticamente
echo la nueva voz.
echo.

start /wait notepad hablar_es_es.py

echo.
echo Generando nueva voz...
echo.

python hablar_es_es.py

if errorlevel 1 (
    echo.
    echo Ha ocurrido un error al generar la voz.
    pause
    exit /b
)

echo.
echo Abriendo el audio...
start "" voz_espana.wav

exit