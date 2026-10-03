#!/usr/bin/env python3
"""Boot a copy of the OS image with QEMU's own UART/UEFI; retain UART transcript."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from build import ROOT, BUILD, DIST, make_disk, require, assemble_os


def main():
    require(["qemu-system-aarch64"])
    firmware = Path(os.environ.get("QEMU_EFI", "/usr/local/share/qemu/edk2-aarch64-code.fd"))
    if not firmware.is_file():
        raise RuntimeError("Set QEMU_EFI to a QEMU AArch64 UEFI firmware image")
    if not (BUILD / "sd/boot/rootfs.ufs").exists():
        assemble_os()
    sd = BUILD / "qemu-sd"
    if sd.exists():
        shutil.rmtree(sd)
    shutil.copytree(BUILD / "sd", sd)
    config = sd / "boot/loader.conf"
    config.write_text("\n".join(line for line in config.read_text().splitlines()
                                 if not line.startswith("hw.uart.console=")) + '\nautoboot_delay="1"\n')
    disk = BUILD / "qemu.img"
    make_disk(sd, disk)
    log = ROOT / "logs/qemu-uart.log"
    cmd = ["qemu-system-aarch64", "-machine", "virt,gic-version=2", "-cpu", "cortex-a57",
           "-m", "2048", "-smp", "1", "-accel", "tcg", "-bios", str(firmware),
           "-drive", f"if=none,file={disk},format=raw,id=sd,snapshot=on",
           "-device", "virtio-blk-device,drive=sd", "-device", "virtio-rng-device",
           "-nographic", "-monitor", "none", "-nic", "none"]
    with log.open("wb") as out:
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=out, stderr=subprocess.STDOUT)
        try:
            deadline = time.monotonic() + int(os.environ.get("QEMU_TIMEOUT", "300"))
            sent = False
            while time.monotonic() < deadline and proc.poll() is None:
                content = log.read_bytes()
                if b"Diagnostic shell on /dev/console" in content and not sent:
                    # Give init's shell time to enter the terminal read loop.
                    time.sleep(2)
                    proc.stdin.write(b"uname -a && echo SWITCHBSD: SHELL_INTERACTIVE\n")
                    proc.stdin.flush()
                    sent = True
                if (sent and b"SWITCHBSD: USERLAND_READY" in content and
                        b"\r\nSWITCHBSD: SHELL_INTERACTIVE\r\n" in content):
                    print(f"QEMU: userland and interactive serial shell passed. Transcript: {log}")
                    return
                time.sleep(1)
            raise RuntimeError(f"QEMU did not reach the interactive shell; inspect {log}")
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
    except (RuntimeError, OSError) as exc:
        sys.exit(str(exc))
