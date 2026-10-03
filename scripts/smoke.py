#!/usr/bin/env python3
"""Boot copies of the OS image with QEMU's own UART/UEFI; retain UART transcripts.

The base variant boots the diagnostic root (no boot/loader.conf.local). The
codex variant boots rootfs-codex.ufs.gz with a QEMU user-mode network, whose
built-in DHCP server answers even without host internet access.
"""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from build import ROOT, BUILD, DIST, make_disk, require, assemble_os

VARIANTS = {
    "base": {"log": "qemu-uart.log", "timeout": "QEMU_TIMEOUT", "default": 300, "nic": ["-nic", "none"],
             "command": b"uname -a && echo SWITCHBSD: SHELL_INTERACTIVE\n",
             "expect": [b"\r\nSWITCHBSD: SHELL_INTERACTIVE\r\n"]},
    "codex": {"log": "qemu-codex-uart.log", "timeout": "QEMU_CODEX_TIMEOUT", "default": 900,
              "nic": ["-netdev", "user,id=net0", "-device", "virtio-net-device,netdev=net0"],
              "command": b"codex --version && rg --version && git --version && echo SWITCHBSD: CODEX_OK\n",
              "expect": [b"Network: vtnet0 10.0.2.15", b"\r\nSWITCHBSD: CODEX_OK\r\n"]},
}


def boot(firmware, variant):
    spec = VARIANTS[variant]
    sd = BUILD / f"qemu-sd-{variant}"
    if sd.exists():
        shutil.rmtree(sd)
    shutil.copytree(BUILD / "sd", sd)
    if variant == "base":
        for name in ("boot/loader.conf.local", "boot/rootfs-codex.ufs.gz"):
            (sd / name).unlink()
    config = sd / "boot/loader.conf"
    config.write_text("\n".join(line for line in config.read_text().splitlines()
                                 if not line.startswith("hw.uart.console=")) + '\nautoboot_delay="1"\n')
    disk = BUILD / f"qemu-{variant}.img"
    make_disk(sd, disk)
    log = ROOT / "logs" / spec["log"]
    cmd = ["qemu-system-aarch64", "-machine", "virt,gic-version=2", "-cpu", "cortex-a57",
           "-m", "2048", "-smp", "1", "-accel", "tcg", "-bios", str(firmware),
           "-drive", f"if=none,file={disk},format=raw,id=sd,snapshot=on",
           "-device", "virtio-blk-device,drive=sd", "-device", "virtio-rng-device",
           "-nographic", "-monitor", "none"] + spec["nic"]
    with log.open("wb") as out:
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=out, stderr=subprocess.STDOUT)
        try:
            deadline = time.monotonic() + int(os.environ.get(spec["timeout"], spec["default"]))
            sent = False
            while time.monotonic() < deadline and proc.poll() is None:
                content = log.read_bytes()
                if b"Diagnostic shell on /dev/console" in content and not sent:
                    # Give init's shell time to enter the terminal read loop.
                    time.sleep(2)
                    proc.stdin.write(spec["command"])
                    proc.stdin.flush()
                    sent = True
                if (sent and b"SWITCHBSD: USERLAND_READY" in content and
                        all(marker in content for marker in spec["expect"])):
                    print(f"QEMU {variant}: userland and interactive serial shell passed. Transcript: {log}")
                    return
                time.sleep(1)
            raise RuntimeError(f"QEMU {variant} root did not pass the shell test; inspect {log}")
        finally:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()


def main():
    require(["qemu-system-aarch64"])
    firmware = Path(os.environ.get("QEMU_EFI", "/usr/local/share/qemu/edk2-aarch64-code.fd"))
    if not firmware.is_file():
        raise RuntimeError("Set QEMU_EFI to a QEMU AArch64 UEFI firmware image")
    if not (BUILD / "sd/boot/rootfs-codex.ufs.gz").exists():
        assemble_os()
    for variant in VARIANTS:
        boot(firmware, variant)


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, OSError) as exc:
        sys.exit(str(exc))
