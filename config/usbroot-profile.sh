# SwitchBSD USB root: environment for console and SSH logins.
if [ -z "${SWITCHBSD_PROFILE_LOADED:-}" ]; then
    SWITCHBSD_PROFILE_LOADED=1
    SSL_CERT_FILE=/etc/ssl/cert.pem
    LANG=C.UTF-8
    export SWITCHBSD_PROFILE_LOADED SSL_CERT_FILE LANG
    if [ -t 0 ] && [ -z "${SSH_CONNECTION:-}" ]; then
        echo 'SWITCHBSD: CONSOLE_SHELL'
        echo 'USB root: / persists on the USB stick. Updates: switchbsd-update'
        echo 'Diagnostics: switchbsd-report > /tmp/report.txt; Network: switchbsd-net status'
    fi
fi
