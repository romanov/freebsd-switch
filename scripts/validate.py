#!/usr/bin/env python3
"""Artifact checks that do not execute target binaries or touch disk devices."""
from pathlib import Path
import json
import struct
import subprocess
import sys
import tempfile
import zipfile
from build import (ROOT, SRC, BUILD, DIST, USB_UPDATE_FILES, DIAGNOSTIC_UPDATE_FILES,
                   DIAGNOSTIC_TOOLS, RESCUE_TOOLS, digest, check_hash)


def elf_aarch64(path, static=False):
    data = path.read_bytes()
    if len(data) < 64 or data[:6] != b"\x7fELF\x02\x01" or struct.unpack_from("<H", data, 18)[0] != 183:
        raise RuntimeError(f"Expected little-endian AArch64 ELF: {path}")
    phoff = struct.unpack_from("<Q", data, 32)[0]
    entsize, count = struct.unpack_from("<HH", data, 54)
    if count and (entsize < 56 or phoff < 64 or phoff + entsize * count > len(data)):
        raise RuntimeError(f"Truncated ELF program headers: {path}")
    if static and any(struct.unpack_from("<I", data, phoff + i * entsize)[0] == 3 for i in range(count)):
        raise RuntimeError(f"Unexpected dynamic interpreter in RAM-root binary: {path}")
    return data


def pe_aarch64(path):
    data = path.read_bytes()
    if len(data) < 64 or data[:2] != b"MZ":
        raise RuntimeError(f"Not a PE executable: {path}")
    offset = struct.unpack_from("<I", data, 60)[0]
    if offset + 6 > len(data) or data[offset:offset+4] != b"PE\0\0" or struct.unpack_from("<H", data, offset+4)[0] != 0xaa64:
        raise RuntimeError(f"Expected AArch64 PE: {path}")


def nonoverlap(regions):
    ordered = sorted(regions)
    for i, (base, size, name) in enumerate(ordered):
        if size <= 0:
            raise RuntimeError(f"Invalid region size: {name}")
        if i and ordered[i-1][0] + ordered[i-1][1] > base:
            raise RuntimeError(f"Memory overlap: {ordered[i-1][2]} / {name}")


def firmware_sizes(rom, payload, bootblock):
    for path in (rom, payload, bootblock):
        if not path.is_file():
            raise RuntimeError(f"Missing firmware artifact: {path}; run make firmware")
    if rom.stat().st_size != 0xa00000:
        raise RuntimeError("Coreboot ROM must be exactly 10 MiB; Hekate ABI size mismatch")
    if not 0 < payload.stat().st_size <= 0x30000:
        raise RuntimeError("Experimental Hekate exceeds ordinary Hekate payload limit")
    bb = bootblock.read_bytes()
    if not 0 < len(bb) <= 0x7000 or not rom.read_bytes().startswith(bb):
        raise RuntimeError("Coreboot bootblock is oversized or not at ROM offset zero")
    if b"LARCHIVE" not in rom.read_bytes():
        raise RuntimeError("Coreboot ROM does not contain CBFS")


def firmware():
    fw = BUILD / "firmware"
    rom = fw / "coreboot.rom"
    bootblock = SRC / "coreboot/build/cbfs/fallback/bootblock.bin"
    if not bootblock.exists():
        matches = list((SRC / "coreboot/build").rglob("bootblock.bin"))
        if len(matches) != 1:
            raise RuntimeError("Cannot locate Coreboot bootblock for layout validation")
        bootblock = matches[0]
    firmware_sizes(rom, fw / "hekate-switchbsd.bin", bootblock)
    bbelf = bootblock.with_suffix(".debug").read_bytes()
    if (bbelf[:6] != b"\x7fELF\x01\x01" or
            struct.unpack_from("<H", bbelf, 18)[0] != 40 or
            struct.unpack_from("<I", bbelf, 24)[0] != 0x40010000):
        raise RuntimeError("Coreboot bootblock must enter ARM32 at 0x40010000")
    data = elf_aarch64(fw / "UEFI.elf")
    if struct.unpack_from("<Q", data, 24)[0] != 0x80110000:
        raise RuntimeError("Unexpected UEFI entry address")
    phoff = struct.unpack_from("<Q", data, 32)[0]
    entsize, count = struct.unpack_from("<HH", data, 54)
    loads = [struct.unpack_from("<IIQQQQQQ", data, phoff + i * entsize)
             for i in range(count) if struct.unpack_from("<I", data, phoff + i * entsize)[0] == 1]
    if not loads or any(p[4] < 0x80110000 or p[4] + p[6] > 0x801f0000 for p in loads):
        raise RuntimeError("UEFI load segments escape reserved firmware region")
    cbfs = subprocess.check_output([SRC / "coreboot/build/cbfstool", rom, "print"], text=True)
    for name in ("fallback/romstage", "fallback/ramstage", "fallback/payload", "fallback/bl31"):
        if name not in cbfs:
            raise RuntimeError("Missing CBFS stage: " + name)
    nonoverlap([(0x40010000, 0x7000, "bootblock"), (0x40017000, 0xf000, "romstage"),
                (0x40026000, 0x1000, "console"), (0x40027000, 0x17000, "CBFS cache"),
                (0x4003e000, 20, "handoff"), (0x4003e020, 4, "USB ready marker")])
    nonoverlap([(0x80110000, 0xe0000, "UEFI FD"), (0x80200000, 0x40000, "ramstage"),
                (0xcf600000, 0xa00000, "ROM"), (0xd0000000, 0x800000, "CBFS cache"),
                (0xdfb80000, 0x480000, "framebuffer"), (0xfec00000, 0x1400000, "TrustZone")])
    print("Firmware architectures, size limits and static layout checks passed; hardware unverified.")


def sd_files(sd):
    for name in ["EFI/BOOT/BOOTAA64.EFI", "boot/kernel/kernel", "boot/rootfs.ufs",
                 "boot/loader.conf", "boot/defaults/loader.conf", "boot/lua/loader.lua",
                 "switchbsd/coreboot.rom", "bootloader/payloads/hekate-switchbsd.bin"]:
        if not (sd / name).is_file():
            raise RuntimeError("Incomplete SD bundle: " + name)


def archive_build_time(archive):
    name = "switchbsd/BUILD-TIME.txt"
    if name not in archive.namelist():
        raise RuntimeError("ZIP archive is missing build time: " + name)
    value = archive.read(name).decode("ascii").strip()
    prefix = "SwitchBSD build time (UTC): "
    if not value.startswith(prefix) or not value[len(prefix):]:
        raise RuntimeError("Invalid build time in ZIP archive")
    if archive.comment.decode("ascii") != value:
        raise RuntimeError("ZIP comment does not match build time file")
    return value


def diagnostic_root(root):
    for name in DIAGNOSTIC_TOOLS:
        path = root / "rescue" / name
        elf_aarch64(path, static=True)
        if not path.stat().st_mode & 0o111:
            raise RuntimeError("Diagnostic tool is not executable: " + name)
    rescue = root / "rescue/rescue"
    for name in RESCUE_TOOLS:
        if not (root / "rescue" / name).samefile(rescue):
            raise RuntimeError("Invalid rescue applet: " + name)
    for name in (*RESCUE_TOOLS, *DIAGNOSTIC_TOOLS):
        if not (root / "bin" / name).samefile(root / "rescue" / name):
            raise RuntimeError("Invalid diagnostic command alias: " + name)
    report = root / "bin/switchbsd-report"
    if not report.stat().st_mode & 0o111:
        raise RuntimeError("Diagnostic report command is not executable")
    check_hash(report, digest(ROOT / "config/switchbsd-report"))
    check_hash(root / "root/DIAGNOSTICS.txt", digest(ROOT / "docs/diagnostics.md"))
    identity = json.loads((root / "etc/switchbsd-build.json").read_text())
    check_hash(report, identity["report_sha256"])
    check_hash(BUILD / "sd/boot/kernel/kernel", identity["kernel_sha256"])
    check_hash(BUILD / "sd/switchbsd/coreboot.rom", identity["firmware_sha256"])
    expected = "SwitchBSD build time (UTC): " + identity["built_at"]
    if (BUILD / "sd/switchbsd/BUILD-TIME.txt").read_text().strip() != expected:
        raise RuntimeError("RAM-root and SD build times do not match")


def main():
    firmware()
    if "--firmware" in sys.argv:
        return
    sd = BUILD / "sd"
    sd_files(sd)
    pe_aarch64(sd / "EFI/BOOT/BOOTAA64.EFI")
    elf_aarch64(sd / "boot/kernel/kernel")
    for name in ("sbin/init", "rescue/rescue", "rescue/uname"):
        elf_aarch64(BUILD / "root" / name, static=True)
    diagnostic_root(BUILD / "root")
    check_hash(sd / "boot/rootfs.ufs", digest(BUILD / "rootfs.ufs"))
    config = (sd / "boot/loader.conf").read_text()
    for value in ('mfsroot_type="mfs_root"', 'mfsroot_name="/boot/rootfs.ufs"',
                  'vfs.root.mountfrom="ufs:/dev/md0"',
                  'boot_multicons="YES"', 'boot_serial="NO"'):
        if value not in config:
            raise RuntimeError("Missing required loader setting: " + value)
    subprocess.run(["/sbin/fsck_ufs", "-n", BUILD / "rootfs.ufs"], check=True)
    image = DIST / "freebsd-switch-15.1.img"
    with image.open("rb") as stream:
        mbr = stream.read(512)
    if mbr[510:512] != b"\x55\xaa" or mbr[450] != 0xef:
        raise RuntimeError("Missing MBR EFI/FAT partition")
    offset = struct.unpack_from("<I", mbr, 454)[0] * 512
    artifacts = ("EFI/BOOT/BOOTAA64.EFI", "boot/kernel/kernel", "boot/rootfs.ufs",
                 "boot/loader.conf", "switchbsd/coreboot.rom",
                 "bootloader/payloads/hekate-switchbsd.bin")
    with tempfile.TemporaryDirectory() as directory:
        for name in artifacts:
            extracted = Path(directory) / "artifact"
            subprocess.run(["mcopy", "-o", "-i", f"{image}@@{offset}",
                            "::/" + name, extracted], check=True)
            check_hash(extracted, digest(sd / name))
    with zipfile.ZipFile(DIST / "freebsd-switch-15.1-sd.zip") as archive:
        if archive.testzip() is not None:
            raise RuntimeError("Corrupt SD ZIP archive")
        build_time = archive_build_time(archive)
        for name in artifacts:
            if name not in archive.namelist():
                raise RuntimeError("Incomplete SD ZIP archive: " + name)
    with zipfile.ZipFile(DIST / "freebsd-switch-15.1-firmware-update.zip") as archive:
        if archive.testzip() is not None:
            raise RuntimeError("Corrupt firmware update ZIP archive")
        if archive_build_time(archive) != build_time:
            raise RuntimeError("ZIP build times do not match")
        for name in ("switchbsd/coreboot.rom", "bootloader/payloads/hekate-switchbsd.bin"):
            if archive.read(name) != (sd / name).read_bytes():
                raise RuntimeError("Firmware update does not match SD bundle: " + name)
    with zipfile.ZipFile(DIST / "freebsd-switch-15.1-console-update.zip") as archive:
        if archive.testzip() is not None:
            raise RuntimeError("Corrupt console update ZIP archive")
        if archive_build_time(archive) != build_time:
            raise RuntimeError("ZIP build times do not match")
        for name in ("boot/loader.conf", "switchbsd/CONSOLE-UPDATE.md"):
            if archive.read(name) != (sd / name).read_bytes():
                raise RuntimeError("Console update does not match SD bundle: " + name)
    with zipfile.ZipFile(DIST / "freebsd-switch-15.1-usb-update.zip") as archive:
        if archive.testzip() is not None:
            raise RuntimeError("Corrupt USB update ZIP archive")
        if archive_build_time(archive) != build_time:
            raise RuntimeError("ZIP build times do not match")
        for name in USB_UPDATE_FILES:
            if archive.read(name) != (sd / name).read_bytes():
                raise RuntimeError("USB update does not match SD bundle: " + name)
    with zipfile.ZipFile(DIST / "freebsd-switch-15.1-diagnostics-update.zip") as archive:
        if archive.testzip() is not None:
            raise RuntimeError("Corrupt diagnostics update ZIP archive")
        if archive_build_time(archive) != build_time:
            raise RuntimeError("ZIP build times do not match")
        if set(archive.namelist()) != set(DIAGNOSTIC_UPDATE_FILES):
            raise RuntimeError("Unexpected files in diagnostics update ZIP archive")
        for name in DIAGNOSTIC_UPDATE_FILES:
            if archive.read(name) != (sd / name).read_bytes():
                raise RuntimeError("Diagnostics update does not match SD bundle: " + name)
    for line in (DIST / "SHA256SUMS").read_text().splitlines():
        sha, name = line.split("  ", 1)
        check_hash(DIST / name, sha)
    print("SD image, RAM root, AArch64 binaries and distribution checksums passed.")


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, OSError, subprocess.CalledProcessError) as exc:
        sys.exit(str(exc))
