@echo off
REM 双击启动本地 AI 网关(默认本机 8788)。想换端口/开放局域网请在下面加参数。
cd /d "%~dp0"
python gateway.py %*
pause
