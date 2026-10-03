#!/usr/bin/env python3
"""Pinned FreeBSD/Switch experiment builder. Never opens a host disk device."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from datetime import datetime, timezone
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SRC, BUILD, DIST = (ROOT / n for n in ("sources", "build", "dist"))
JOBS = os.environ.get("JOBS", "8")
USB_UPDATE_FILES = ("switchbsd/coreboot.rom", "bootloader/payloads/hekate-switchbsd.bin",
                    "boot/kernel/kernel", "boot/rootfs.ufs", "switchbsd/USB-UPDATE.md",
                    "switchbsd/BUILD-TIME.txt")


def run(args, *, cwd=ROOT, env=None, log=None):
    args = [str(x) for x in args]
    print("+", " ".join(args), flush=True)
    if log:
        path = ROOT / "logs" / log
        with path.open("w") as out:
            result = subprocess.run(args, cwd=cwd, env=env, stdout=out,
                                    stderr=subprocess.STDOUT)
        if result.returncode:
            raise RuntimeError(f"Command failed; see {path}\n" +
                               "\n".join(path.read_text(errors="replace").splitlines()[-25:]))
    else:
        subprocess.run(args, cwd=cwd, env=env, check=True)


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def check_hash(path, expected):
    if digest(path) != expected:
        raise RuntimeError(f"SHA-256 mismatch: {path}; remove this cached file and retry fetch")


def require(names):
    missing = [n for n in names if not shutil.which(n)]
    if missing:
        raise RuntimeError("Missing commands: " + ", ".join(missing) + "; see README.md prerequisites")


def doctor():
    if os.uname().sysname != "FreeBSD":
        raise RuntimeError("Build host must be FreeBSD 15.1 or newer")
    require(["make", "gmake", "clang", "git", "tar", "makefs", "mkimg", "iasl",
             "gcc14", "g++14", "bison", "bash", "aarch64-none-elf-gcc", "arm-none-eabi-gcc",
             "mcopy", "qemu-system-aarch64"])
    newlib = Path(shutil.which("arm-none-eabi-gcc")).resolve().parents[1] / "arm-none-eabi"
    if not (newlib / "include/stdlib.h").is_file() or not (newlib / "lib/libc.a").is_file():
        raise RuntimeError("Missing target C library; install arm-none-eabi-newlib")
    print("Host tools available. Switch hardware is not checked by this command.")


def fetch():
    # Entries are ordered: parents precede nested dependencies.
    for entry in json.loads((ROOT / "sources.lock.json").read_text()):
        archive = ROOT / "cache" / entry["archive"]
        if not archive.exists():
            part = archive.with_suffix(archive.suffix + ".part")
            print("Downloading", entry["name"], flush=True)
            urllib.request.urlretrieve(entry["url"], part)
            check_hash(part, entry["sha256"])
            part.replace(archive)
        check_hash(archive, entry["sha256"])
        dest = SRC / entry["name"]
        stamp = dest / ".source-version"
        if stamp.exists() and stamp.read_text().strip() == entry["revision"]:
            continue
        dest.mkdir(parents=True, exist_ok=True)
        if any(dest.iterdir()):
            raise RuntimeError(f"Unstamped source tree {dest}; preserve local edits and move it aside before fetch")
        run(["tar", "xf", archive, f"--strip-components={entry['strip']}", "-C", dest])
        stamp.write_text(entry["revision"] + "\n")


def freebsd():
    require(["make", "clang"])
    if not (SRC / "freebsd/Makefile").exists():
        raise RuntimeError("FreeBSD sources missing; run make fetch")
    from prepare_firmware import replace, write
    replace("freebsd/sys/dev/uart/uart_dev_ns8250.c",
            "static struct acpi_uart_compat_data acpi_compat_data[] = {",
            "static struct acpi_uart_compat_data acpi_compat_data[] = {\n"
            '\t{"NVDA0100", &uart_ns8250_class, 2, 1, 408000000, 0, "Tegra210 UART (Switch experiment)"},')
    # Firmware-initialized USB-C host controller (DSDT SWBS0001).
    write("freebsd/sys/dev/usb/controller/switchbsd_ehci_acpi.c",
          (ROOT / "config/switchbsd-ehci-acpi.c").read_text())
    replace("freebsd/sys/conf/files.arm64",
            "dev/usb/controller/generic_ehci_acpi.c\t\toptional ehci acpi\n",
            "dev/usb/controller/generic_ehci_acpi.c\t\toptional ehci acpi\n"
            "dev/usb/controller/switchbsd_ehci_acpi.c\toptional ehci acpi\n")
    shutil.copyfile(ROOT / "config/SWITCHDIAG", SRC / "freebsd/sys/arm64/conf/SWITCHDIAG")
    env = dict(os.environ, MAKEOBJDIRPREFIX=str(BUILD / "obj"))
    args = ["/usr/bin/make", "-C", SRC / "freebsd", f"-j{JOBS}",
            "TARGET=arm64", "TARGET_ARCH=aarch64", f"SRCCONF={ROOT}/config/src.conf",
            "__MAKE_CONF=/dev/null"]
    run(args + ["buildworld"], env=env, log="buildworld.log")
    run(args + ["buildkernel", "KERNCONF=SWITCHDIAG", "NO_MODULES=yes"], env=env, log="buildkernel.log")
    stage = BUILD / "world"
    stage.mkdir(exist_ok=True)
    run(args + ["installworld", f"DESTDIR={stage}", "NO_ROOT=yes", "DB_FROM_SRC=yes"],
        env=env, log="installworld.log")
    run(args + ["installkernel", "KERNCONF=SWITCHDIAG", f"DESTDIR={stage}",
                "NO_ROOT=yes", "NO_MODULES=yes"], env=env, log="installkernel.log")


def firmware():
    run([sys.executable, ROOT / "scripts/firmware.py"])


def copy(source, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)


def assemble_os():
    world = BUILD / "world"
    needed = [world / "boot/loader.efi", world / "boot/kernel/kernel",
              world / "rescue/rescue", world / "sbin/init"]
    for path in needed:
        if not path.is_file():
            raise RuntimeError(f"Missing artifact: {path}; run make freebsd and make firmware")
    root = BUILD / "root"
    sd = BUILD / "sd"
    for p in (root, sd):
        if p.exists():
            shutil.rmtree(p)
        p.mkdir()
    for d in ["rescue", "bin", "sbin", "etc", "dev", "tmp", "var/run", "root"]:
        (root / d).mkdir(parents=True, exist_ok=True)
    copy(world / "rescue/rescue", root / "rescue/rescue")
    (root / "rescue/rescue").chmod(0o555)
    # Verify the staged rescue links before creating aliases to the multicall binary.
    for name in ["sh", "mount", "umount", "hostname", "sysctl", "ls", "cat",
                 "dmesg", "kenv", "reboot", "halt", "sleep", "ps", "df", "stty", "ifconfig"]:
        if not (world / "rescue" / name).is_file():
            raise RuntimeError("Missing rescue applet in staged world: " + name)
        os.link(root / "rescue/rescue", root / "rescue" / name)
    # uname is not a FreeBSD rescue applet. Build it against the staged ARM64
    # libc so the RAM root stays independent of the dynamic linker.
    run(["clang", "--target=aarch64-unknown-freebsd15.1", f"--sysroot={world}",
         "-fuse-ld=lld", "-static", "-O2", "-o", root / "rescue/uname",
         SRC / "freebsd/usr.bin/uname/uname.c"], log="uname.log")
    (root / "bin/sh").symlink_to("../rescue/sh")
    copy(world / "sbin/init", root / "sbin/init")
    (root / "sbin/init").chmod(0o555)
    copy(ROOT / "config/rc", root / "etc/rc")
    (root / "etc/rc").chmod(0o555)
    (root / "etc/fstab").write_text("/dev/md0 / ufs rw 0 0\n")
    (root / "etc/ttys").write_text('console none vt100 off secure\n')
    (root / "etc/login.conf").write_text("default:\\\n\t:umask=022:\n\ndaemon:\\\n\t:tc=default:\n")
    copy(world / "usr/share/misc/termcap", root / "etc/termcap")
    (root / "tmp").chmod(0o1777)
    run(["makefs", "-t", "ffs", "-B", "little", "-s", "128m", "-o", "version=2",
         BUILD / "rootfs.ufs", root], log="rootfs.log")
    # Use the release's boot scripts; only configuration and rootfs differ.
    shutil.copytree(world / "boot", sd / "boot", ignore=shutil.ignore_patterns("*.debug", "*.symbols"))
    copy(world / "boot/loader.efi", sd / "EFI/BOOT/BOOTAA64.EFI")
    copy(ROOT / "config/loader.conf", sd / "boot/loader.conf")
    copy(BUILD / "rootfs.ufs", sd / "boot/rootfs.ufs")
    return sd


def image():
    # Refuse to publish a Switch bundle until the actual firmware exists.
    for name in ("coreboot.rom", "hekate-switchbsd.bin"):
        if not (BUILD / "firmware" / name).is_file():
            raise RuntimeError("Missing firmware: " + name + "; run make firmware")
    sd = assemble_os()
    build_time = datetime.now(timezone.utc).isoformat()
    build_time_file = sd / "switchbsd/BUILD-TIME.txt"
    build_time_file.parent.mkdir(parents=True, exist_ok=True)
    build_time_file.write_text(f"SwitchBSD build time (UTC): {build_time}\n")
    copy(BUILD / "firmware/coreboot.rom", sd / "switchbsd/coreboot.rom")
    copy(BUILD / "firmware/hekate-switchbsd.bin", sd / "bootloader/payloads/hekate-switchbsd.bin")
    (sd / "bootloader/ini").mkdir(parents=True, exist_ok=True)
    (sd / "bootloader/ini/switchbsd.ini").write_text(
        "[FreeBSD 15.1 experiment]\npayload=bootloader/payloads/hekate-switchbsd.bin\n")
    copy(ROOT / "sources.lock.json", sd / "switchbsd/sources.lock.json")
    for name in ("README.md", "UPDATE.md", "CONSOLE-UPDATE.md", "USB-UPDATE.md", "docs/boot-test.md",
                 "docs/firmware.md", "docs/licenses.md", "docs/hardware-boot-2026-10-03.md"):
        copy(ROOT / name, sd / "switchbsd" / name)
    for name in ("switch/LICENSE", "hekate/LICENSE", "coreboot/COPYING",
                 "coreboot/3rdparty/arm-trusted-firmware/license.rst",
                 "edk2/License.txt", "freebsd/COPYRIGHT"):
        copy(SRC / name, sd / "switchbsd/licenses" / name)
    with zipfile.ZipFile(DIST / "freebsd-switch-15.1-sd.zip", "w", zipfile.ZIP_DEFLATED) as z:
        for path in sorted(sd.rglob("*")):
            if path.is_file():
                z.write(path, path.relative_to(sd))
        z.comment = f"SwitchBSD build time (UTC): {build_time}".encode()
    make_disk(sd, DIST / "freebsd-switch-15.1.img")
    metadata = {"target": "Erista", "release": "15.1", "hardware_verified": False,
                "built_at": build_time,
                "sources": json.loads((ROOT / "sources.lock.json").read_text()),
                "host": subprocess.check_output(["uname", "-a"], text=True).strip(),
                "toolchains": {}, "inputs": {}, "artifacts": {}}
    package_names = {"python312", "git", "gmake", "bash", "gcc14", "bison", "acpica-tools",
                     "aarch64-none-elf-gcc", "aarch64-none-elf-binutils", "arm-none-eabi-gcc",
                     "arm-none-eabi-binutils", "arm-none-eabi-newlib", "mtools", "qemu-nox11"}
    metadata["packages"] = {
        name: version for line in subprocess.check_output(["pkg", "query", "%n\t%v"], text=True).splitlines()
        for name, version in [line.split("\t", 1)] if name in package_names}
    for tool in ("clang", "gcc14", "arm-none-eabi-gcc", "aarch64-none-elf-gcc",
                 "gmake", "iasl", "qemu-system-aarch64"):
        output = subprocess.run([tool, "--version" if tool != "iasl" else "-v"],
                                capture_output=True, text=True, check=True).stdout.strip()
        metadata["toolchains"][tool] = output
    for directory in ("scripts", "config", "patches/generated"):
        for path in sorted((ROOT / directory).rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts:
                metadata["inputs"][str(path.relative_to(ROOT))] = digest(path)
    for path in sorted((BUILD / "firmware").glob("*")):
        if path.is_file():
            metadata["artifacts"][path.name] = digest(path)
    (DIST / "build-info.json").write_text(json.dumps(metadata, indent=2) + "\n")
    with zipfile.ZipFile(DIST / "freebsd-switch-15.1-firmware-update.zip", "w", zipfile.ZIP_DEFLATED) as z:
        for name in ("switchbsd/coreboot.rom", "bootloader/payloads/hekate-switchbsd.bin",
                     "switchbsd/UPDATE.md", "switchbsd/sources.lock.json",
                     "switchbsd/BUILD-TIME.txt"):
            z.write(sd / name, name)
        z.write(DIST / "build-info.json", "switchbsd/build-info.json")
        z.comment = f"SwitchBSD build time (UTC): {build_time}".encode()
    with zipfile.ZipFile(DIST / "freebsd-switch-15.1-console-update.zip", "w", zipfile.ZIP_DEFLATED) as z:
        z.write(sd / "boot/loader.conf", "boot/loader.conf")
        z.write(sd / "switchbsd/CONSOLE-UPDATE.md", "switchbsd/CONSOLE-UPDATE.md")
        z.write(build_time_file, "switchbsd/BUILD-TIME.txt")
        z.comment = f"SwitchBSD build time (UTC): {build_time}".encode()
    # The USB keyboard needs the new firmware, kernel driver and RAM-root rc.
    with zipfile.ZipFile(DIST / "freebsd-switch-15.1-usb-update.zip", "w", zipfile.ZIP_DEFLATED) as z:
        for name in USB_UPDATE_FILES:
            z.write(sd / name, name)
        z.comment = f"SwitchBSD build time (UTC): {build_time}".encode()
    checksums()


def make_disk(sd, output):
    fat = BUILD / (output.stem + ".fat")
    run(["makefs", "-t", "msdos", "-s", "512m", "-o", "fat_type=32,sectors_per_cluster=8",
         fat, sd], log="fat-image.log")
    run(["mkimg", "-s", "mbr", "-a", "1", "-p", f"efi:={fat}", "-o", output])


def checksums():
    paths = sorted(p for p in DIST.iterdir() if p.is_file() and p.name != "SHA256SUMS")
    (DIST / "SHA256SUMS").write_text("".join(f"{digest(p)}  {p.name}\n" for p in paths))


def validate():
    run([sys.executable, ROOT / "scripts/validate.py"])


def test():
    run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"])


def smoke():
    run([sys.executable, ROOT / "scripts/smoke.py"])


def all_steps():
    for step in (doctor, fetch, firmware, freebsd, image, validate, test, smoke):
        step()


def main():
    for n in ("sources", "cache", "build", "dist", "logs"):
        (ROOT / n).mkdir(exist_ok=True)
    commands = {k: globals()[k] for k in ("doctor", "fetch", "firmware", "freebsd", "image", "validate", "test", "smoke")}
    commands["all"] = all_steps
    command = sys.argv[1] if len(sys.argv) == 2 else "help"
    if command == "help":
        print("make " + " | ".join(commands))
        return
    if command not in commands:
        raise RuntimeError(f"Unknown command: {command}")
    commands[command]()


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, OSError, subprocess.CalledProcessError) as error:
        sys.exit(str(error))
