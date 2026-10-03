#!/usr/bin/env python3
"""Boot both RAM roots; test diagnostics, networking, SSH and the Codex CLI."""
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from build import ROOT, BUILD, DIST, make_disk, require, assemble_os, host_key

# Throwaway smoke-test settings. The Wi-Fi network does not exist: QEMU has no
# USB Wi-Fi, so this exercises the missing-adapter path and secret removal.
SMOKE_PASSWORD = "smoke test password"
SMOKE_SSID = "SwitchBSD-smoke"
SMOKE_PSK = "smoke-test-psk"

CHECKS = (
    ("SHELL_INTERACTIVE", "uname -a"),
    ("USB_TOOLS_OK", "usbconfig list && usbconfig show_ifdrv && usbconfig dump_device_desc"),
    ("STORAGE_TOOLS_OK", "devinfo -rv > /tmp/devinfo.txt && diskinfo -v /dev/md0 && "
                         "geom disk list && camcontrol devlist -v"),
    ("CHECKSUM_OK", '[ "$(printf abc | sha256 -q)" = '
                    'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad ]'),
    ("PAGER_OK", "printf 'pager check\\n' > /tmp/pager.txt && less -F -X /tmp/pager.txt"),
    ("TIMEOUT_OK", 'timeout -k 1s 1s sleep 5; [ "$?" -eq 124 ]'),
    ("NETWORK_OK", "switchbsd-net status && "
                   """[ -n "$(ifconfig vtnet0 inet | sed -n '/inet 10[.]0[.]2[.]/p')" ]"""),
    # The SSID proves the CRLF settings file reached the kernel environment.
    ("SECRETS_SCRUBBED", f'[ "$(kenv -q switchbsd.wifi.ssid)" = {SMOKE_SSID} ] && '
                         "[ -z \"$(kenv | sed -n -e '/^switchbsd[.]wifi[.]psk=/p' "
                         "-e '/^switchbsd[.]ssh[.]password=/p')\" ] && "
                         """[ "$(ls -l /etc/wpa_supplicant.conf | sed 's/ .*//')" = -rw------- ] && """
                         """[ -n "$(switchbsd-net status | sed -n '/^SSH root password: set$/p')" ]"""),
    ("WIFI_TOOLS_OK", "sysctl net.wlan.devices && wpa_supplicant -v && wpa_cli -v"),
    ("DIAGNOSTICS_OK", 'switchbsd-report > /tmp/report.txt && cat /tmp/report.txt && '
                       '[ "$(tail -n 2 /tmp/report.txt | head -n 1)" = '
                       '"Commands unavailable or failed: 0" ] && sha256 /tmp/report.txt'),
)
# Boot-time output from switchbsd-net start, before the shell.
BOOT_MARKERS = (b"Wi-Fi: no USB adapter found", b"SWITCHBSD: SSH_READY",
                b"SWITCHBSD: NETWORK_READY vtnet0 10.0.2.15")
CODEX_CHECKS = (
    ("CODEX_OK", "codex --version && rg --version && git --version && "
                 "test -r /root/DIAGNOSTICS.txt && test -r /root/NETWORK.txt && "
                 "git init -q /root/work/smoke && "
                 "printf 'codex wifi smoke\\n' > /root/work/smoke/check.txt && "
                 "rg 'codex wifi smoke' /root/work/smoke/check.txt"),
)


def free_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def ssh_checks(port, directory, variant):
    """Log in from the host with the key and the password, then copy a file."""
    key = directory / "id_ed25519"
    known_hosts = directory / "known_hosts"
    # Strict checking against the build-host key proves the image uses it.
    host_public = " ".join(host_key().with_suffix(".pub").read_text().split()[:2])
    known_hosts.write_text(f"[127.0.0.1]:{port} {host_public}\n")
    common = ["-F", "/dev/null", "-o", "StrictHostKeyChecking=yes",
              "-o", f"UserKnownHostsFile={known_hosts}", "-o", "GlobalKnownHostsFile=/dev/null",
              "-o", "ConnectTimeout=20", "-o", "LogLevel=ERROR"]
    with_key = common + ["-i", str(key), "-o", "IdentitiesOnly=yes", "-o", "BatchMode=yes",
                         "-o", "PasswordAuthentication=no"]
    askpass = directory / "askpass"
    askpass.write_text(f"#!/bin/sh\necho '{SMOKE_PASSWORD}'\n")
    askpass.chmod(0o700)
    password_env = dict(os.environ, SSH_ASKPASS=str(askpass), SSH_ASKPASS_REQUIRE="force",
                        DISPLAY=os.environ.get("DISPLAY", ":0"))
    with_password = common + ["-o", "PubkeyAuthentication=no",
                              "-o", "PreferredAuthentications=password",
                              "-o", "NumberOfPasswordPrompts=1"]
    attempts = (
        ("SSH key login", ["ssh", *with_key, "-p", str(port), "root@127.0.0.1",
                           "uname -a && echo SSH_KEY_OK"], None, "SSH_KEY_OK"),
        ("SSH password login", ["ssh", *with_password, "-p", str(port), "root@127.0.0.1",
                                "echo SSH_PASSWORD_OK"], password_env, "SSH_PASSWORD_OK"),
        # scp uses the SFTP subsystem by default.
        ("scp download", ["scp", *with_key, "-P", str(port), "root@127.0.0.1:/etc/switchbsd-build.json",
                          str(directory / "identity.json")], None, None),
    )
    if variant == "codex":
        attempts += (("Codex over SSH", ["ssh", *with_key, "-p", str(port), "root@127.0.0.1",
                       "codex --version && rg --version && git --version && echo SSH_CODEX_OK"],
                      None, "SSH_CODEX_OK"),)
    for name, command, env, expected in attempts:
        for attempt in range(3):
            result = subprocess.run(command, env=env, stdin=subprocess.DEVNULL,
                                    capture_output=True, text=True, timeout=60)
            if result.returncode == 0 and (expected is None or expected in result.stdout):
                print(f"QEMU: {name} passed", flush=True)
                break
            time.sleep(3)
        else:
            raise RuntimeError(f"QEMU {name} failed: {result.stdout}{result.stderr}")
    identity = json.loads((directory / "identity.json").read_text())
    if identity["ssh_host_key_fingerprint"] != json.loads(
            (BUILD / "root/etc/switchbsd-build.json").read_text())["ssh_host_key_fingerprint"]:
        raise RuntimeError("Downloaded build identity does not match this build")


def main():
    require(["qemu-system-aarch64", "ssh", "scp", "ssh-keygen"])
    firmware = Path(os.environ.get("QEMU_EFI", "/usr/local/share/qemu/edk2-aarch64-code.fd"))
    if not firmware.is_file():
        raise RuntimeError("Set QEMU_EFI to a QEMU AArch64 UEFI firmware image")
    if not (BUILD / "sd/boot/rootfs-codex.ufs.gz").exists():
        assemble_os()
    for variant in ("base", "codex"):
        prepare_and_boot(firmware, variant)


def prepare_and_boot(firmware, variant):
    sd = BUILD / f"qemu-sd-{variant}"
    if sd.exists():
        shutil.rmtree(sd)
    shutil.copytree(BUILD / "sd", sd)
    if variant == "base":
        for name in ("boot/loader.conf.local", "boot/rootfs-codex.ufs.gz"):
            (sd / name).unlink()
    config = sd / "boot/loader.conf"
    config.write_text("\n".join(line for line in config.read_text().splitlines()
                                 if not line.startswith(("hw.uart.console=", "boot_serial="))) +
                      '\nautoboot_delay="1"\nboot_serial="YES"\n')
    with tempfile.TemporaryDirectory() as temporary:
        directory = Path(temporary)
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "smoke",
                        "-f", directory / "id_ed25519"], check=True)
        public_key = (directory / "id_ed25519.pub").read_text().strip()
        # CRLF line endings, as Windows Notepad saves them.
        (sd / "boot/loader.conf.d/network.conf").write_bytes("".join(f"{line}\r\n" for line in (
            "# SwitchBSD smoke test",
            f'switchbsd.wifi.ssid="{SMOKE_SSID}"',
            f'switchbsd.wifi.psk="{SMOKE_PSK}"',
            f'switchbsd.ssh.key="{public_key}"',
            f'switchbsd.ssh.password="{SMOKE_PASSWORD}"')).encode())
        disk = BUILD / f"qemu-{variant}.img"
        make_disk(sd, disk)
        port = free_port()
        boot(firmware, disk, port, directory, variant)


def boot(firmware, disk, port, directory, variant):
    log = ROOT / "logs" / ("qemu-codex-uart.log" if variant == "codex" else "qemu-uart.log")
    checks = CHECKS + (CODEX_CHECKS if variant == "codex" else ())
    cmd = ["qemu-system-aarch64", "-machine", "virt,gic-version=2", "-cpu", "cortex-a57",
           "-m", "2048", "-smp", "1", "-accel", "tcg", "-bios", str(firmware),
           "-drive", f"if=none,file={disk},format=raw,id=sd,snapshot=on",
           "-device", "virtio-blk-device,drive=sd", "-device", "virtio-rng-device",
           "-device", "qemu-xhci", "-device", "usb-kbd",
           "-netdev", f"user,id=net0,hostfwd=tcp:127.0.0.1:{port}-:22",
           "-device", "virtio-net-device,netdev=net0",
           "-nographic", "-monitor", "none"]
    with log.open("wb") as out:
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=out, stderr=subprocess.STDOUT)
        try:
            timeout = os.environ.get("QEMU_CODEX_TIMEOUT", "900") if variant == "codex" else os.environ.get("QEMU_TIMEOUT", "300")
            deadline = time.monotonic() + int(timeout)
            step = 0
            sent = False
            while time.monotonic() < deadline and proc.poll() is None:
                content = log.read_bytes()
                if b"Diagnostic shell on /dev/console" in content and not sent:
                    # Give init's shell time to enter the terminal read loop.
                    time.sleep(2)
                    marker, command = checks[step]
                    print(f"QEMU {variant}: checking {marker}", flush=True)
                    # A leading newline separates markers from pager terminal
                    # escape sequences and command output without a final LF.
                    line = (command + f" && printf '\\nSWITCHBSD: {marker}\\n' || "
                            f"printf '\\nSWITCHBSD: CHECK_FAILED_{marker}\\n'\n").encode()
                    # Pace input so long commands do not overrun the UART FIFO.
                    for offset in range(0, len(line), 16):
                        proc.stdin.write(line[offset:offset + 16])
                        proc.stdin.flush()
                        time.sleep(0.02)
                    sent = True
                marker = checks[step][0]
                if f"\r\nSWITCHBSD: CHECK_FAILED_{marker}\r\n".encode() in content:
                    raise RuntimeError(f"QEMU check failed: {marker}; inspect {log}")
                if sent and f"\r\nSWITCHBSD: {marker}\r\n".encode() in content:
                    step += 1
                    if step == len(checks):
                        if b"\r\nSWITCHBSD: DIAGNOSTIC_REPORT_END\r\n" not in content:
                            raise RuntimeError(f"QEMU report is incomplete; inspect {log}")
                        for boot_marker in BOOT_MARKERS:
                            if boot_marker not in content:
                                raise RuntimeError(f"QEMU boot output lacks {boot_marker.decode()}; "
                                                   f"inspect {log}")
                        ssh_checks(port, directory, variant)
                        print(f"QEMU {variant}: userland, diagnostic tools/report, interactive serial shell, "
                              f"DHCP and SSH key/password/scp access passed. Transcript: {log}")
                        return
                    sent = False
                time.sleep(1)
            raise RuntimeError(f"QEMU {variant} did not complete {checks[step][0]}; inspect {log}")
        finally:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        sys.exit(str(exc))
