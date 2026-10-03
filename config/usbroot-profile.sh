# SwitchBSD USB root: environment for console and SSH logins.
SSL_CERT_FILE=/etc/ssl/cert.pem
LANG=C.UTF-8
export SSL_CERT_FILE LANG
if [ "$(tty 2>/dev/null)" = /dev/console ]; then
    echo 'SWITCHBSD: CONSOLE_SHELL'
    echo 'USB root: / persists on the USB stick. Updates: switchbsd-update'
    echo 'Diagnostics: switchbsd-report > /tmp/report.txt; Network: switchbsd-net status'
fi
