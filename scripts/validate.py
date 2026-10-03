#!/usr/bin/env python3
"""Artifact checks that do not execute target binaries or touch disk devices."""
import gzip
import hashlib
from pathlib import Path
import json
import struct
import subprocess
import sys
import tempfile
import uuid
import zipfile
from build import (ROOT, SRC, BUILD, DIST, USB_UPDATE_FILES, DIAGNOSTIC_UPDATE_FILES, USBROOT_UPDATE_FILES,
                   NETWORK_UPDATE_FILES, DIAGNOSTIC_TOOLS, RESCUE_TOOLS, NETWORK_PROGRAMS, RTLD,
                   digest, check_hash, elf_dynamic, library_closure, ownership_spec,
                   ssh_fingerprint, CODEX_UPDATE_FILES)
import codex_root
import usb_root

FREEBSD_UFS = uuid.UUID("516e7cb6-6ecf-11d6-8ff8-00022d09712b")


def gpt_partitions(stream):
    """(type UUID, name, first LBA, last LBA) of each used GPT entry, 512-byte sectors."""
    stream.seek(512)
    header = stream.read(92)
    if header[:8] != b"EFI PART":
        raise RuntimeError("Missing GPT header")
    entries_lba, count, size = struct.unpack_from("<QII", header, 72)
    if size < 128 or count > 1024:
        raise RuntimeError("Unsupported GPT partition entry layout")
    stream.seek(entries_lba * 512)
    table = stream.read(count * size)
    partitions = []
    for index in range(count):
        entry = table[index * size:(index + 1) * size]
        kind = uuid.UUID(bytes_le=entry[:16])
        if kind.int == 0:
            continue
        first, last = struct.unpack_from("<QQ", entry, 32)
        name = entry[56:128].decode("utf-16-le").split("\0", 1)[0]
        partitions.append((kind, name, first, last))
    return partitions


def usb_stick(image, ufs, label=usb_root.LABEL):
    """The stick image holds exactly the UFS root, in a freebsd-ufs partition named label."""
    with open(image, "rb") as stream:
        partitions = gpt_partitions(stream)
        if [(kind, name) for kind, name, _, _ in partitions] != [(FREEBSD_UFS, label)]:
            raise RuntimeError(f"USB root image must have one freebsd-ufs partition labelled {label}")
        _, _, first, last = partitions[0]
        length = Path(ufs).stat().st_size
        if (last - first + 1) * 512 < length:
            raise RuntimeError("USB root partition is smaller than its UFS image")
        stream.seek(first * 512)
        hasher = hashlib.sha256()
        remaining = length
        while remaining:
            block = stream.read(min(remaining, 1 << 20))
            if not block:
                raise RuntimeError("USB root image is truncated")
            hasher.update(block)
            remaining -= len(block)
    if hasher.hexdigest() != digest(ufs):
        raise RuntimeError("USB root partition does not contain build/rootfs-usb.ufs")


def usb_root_tree(sd, root=usb_root.USB_ROOT):
    for name in usb_root.USB_REQUIRED:
        if not ((root / name).exists() or (root / name).is_symlink()):
            raise RuntimeError("Incomplete USB root: " + name)
    for name in ("sbin/init", "usr/sbin/sshd", "usr/local/sbin/pkg", "usr/local/bin/codex"):
        elf_aarch64(root / name)
    for source, target, _ in usb_root.OVERLAY:
        check_hash(root / target, digest(ROOT / source))
    usb_root.check_loader_local((sd / "switchbsd/usbroot/loader.conf.local").read_text())
    if list((root / "boot").glob("kernel*")):
        raise RuntimeError("The USB root must not contain a kernel; the loader reads it from SD")
    identity = json.loads((root / "etc/switchbsd-build.json").read_text())
    if any(name.startswith("FreeBSD-kernel") for name in identity["packages"]):
        raise RuntimeError("The USB root installed a kernel package")
    check_hash(sd / "boot/kernel/kernel", identity["kernel_sha256"])
    key = root / "etc/ssh/ssh_host_ed25519_key"
    if key.stat().st_mode & 0o077:
        raise RuntimeError("USB root SSH host key must be readable by root only")
    if ssh_fingerprint(key.with_suffix(".pub").read_text()) + " (ED25519)" != identity["ssh_host_key_fingerprint"]:
        raise RuntimeError("USB root SSH host key does not match its build identity")
    metalog = usb_root.METALOG.read_text() if usb_root.METALOG.is_file() else ""
    if usb_root.SPEC.read_text() != usb_root.merge_spec(metalog, ownership_spec(root)):
        raise RuntimeError("USB root spec does not cover the current tree")


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
                 "switchbsd/coreboot.rom", "bootloader/payloads/hekate-switchbsd.bin",
                 "boot/entropy", "boot/loader.conf.d/network.conf.sample",
                 "boot/rootfs-codex.ufs.gz", "boot/loader.conf.local",
                 "switchbsd/usbroot/loader.conf.local", "switchbsd/USBROOT.md"]:
        if not (sd / name).is_file():
            raise RuntimeError("Incomplete SD bundle: " + name)


def codex(sd, root=codex_root.CODEX_ROOT, ufs=codex_root.CODEX_UFS):
    """The codex root is self-contained: every library and link resolves inside it."""
    for name in codex_root.CODEX_REQUIRED:
        if not ((root / name).exists() or (root / name).is_symlink()):
            raise RuntimeError("Incomplete codex root: " + name)
    codex_root.loader_local_name((sd / "boot/loader.conf.local").read_text())
    for path, _ in codex_root.elf_files(root):
        elf_aarch64(path)
    missing = codex_root.missing_libraries(root)
    if missing:
        raise RuntimeError("Codex root has unresolved libraries: " +
                           ", ".join(f"{soname} ({name})" for name, soname in missing))
    broken = codex_root.broken_links(root)
    if broken:
        raise RuntimeError("Codex root has broken symlinks: " + ", ".join(broken))
    with gzip.open(sd / "boot/rootfs-codex.ufs.gz", "rb") as stream:
        if hashlib.file_digest(stream, "sha256").hexdigest() != digest(ufs):
            raise RuntimeError("rootfs-codex.ufs.gz does not decompress to build/rootfs-codex.ufs")


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
    for name in RESCUE_TOOLS:
        if not (root / "sbin" / name).samefile(root / "rescue" / name):
            raise RuntimeError("Invalid /sbin command alias: " + name)
    for name in ("switchbsd-report", "switchbsd-net"):
        if not (root / "bin" / name).stat().st_mode & 0o111:
            raise RuntimeError(f"RAM-root command is not executable: {name}")
        check_hash(root / "bin" / name, digest(ROOT / "config" / name))
    report = root / "bin/switchbsd-report"
    check_hash(root / "root/DIAGNOSTICS.txt", digest(ROOT / "docs/diagnostics.md"))
    check_hash(root / "root/NETWORK.txt", digest(ROOT / "NETWORK-UPDATE.md"))
    identity = json.loads((root / "etc/switchbsd-build.json").read_text())
    check_hash(report, identity["report_sha256"])
    check_hash(root / "bin/switchbsd-net", identity["network_script_sha256"])
    check_hash(root / "etc/ssh/sshd_config", identity["sshd_config_sha256"])
    network_root(root, identity)
    check_hash(BUILD / "sd/boot/kernel/kernel", identity["kernel_sha256"])
    check_hash(BUILD / "sd/switchbsd/coreboot.rom", identity["firmware_sha256"])
    expected = "SwitchBSD build time (UTC): " + identity["built_at"]
    if (BUILD / "sd/switchbsd/BUILD-TIME.txt").read_text().strip() != expected:
        raise RuntimeError("RAM-root and SD build times do not match")


def network_root(root, identity, spec=None):
    """Dynamic network programs, their libraries, SSH keys and root ownership."""
    elf_aarch64(root / RTLD)
    for name in NETWORK_PROGRAMS:
        elf_aarch64(root / name)
        if elf_dynamic(root / name)[0] != "/" + RTLD:
            raise RuntimeError(f"Unexpected dynamic linker for {name}")
        if not (root / name).stat().st_mode & 0o111:
            raise RuntimeError(f"Network program is not executable: {name}")
    # Resolving against the RAM root proves every needed library was copied.
    for name in library_closure(root, NETWORK_PROGRAMS):
        elf_aarch64(root / name)
    key = root / "etc/ssh/ssh_host_ed25519_key"
    if key.stat().st_mode & 0o077:
        raise RuntimeError("SSH host key must be readable by root only")
    expected = ssh_fingerprint(key.with_suffix(".pub").read_text()) + " (ED25519)"
    recorded = (root / "etc/ssh/ssh_host_ed25519_key.fingerprint").read_text().strip()
    if not expected == recorded == identity["ssh_host_key_fingerprint"]:
        raise RuntimeError("SSH host key fingerprint does not match the build identity")
    for name in ("etc/pwd.db", "etc/spwd.db", "etc/passwd", "sbin/dhclient-script",
                 "etc/regdomain.xml"):
        if not (root / name).is_file():
            raise RuntimeError("Missing RAM-root network file: " + name)
    if (root / "var/empty").stat().st_mode & 0o222:
        raise RuntimeError("/var/empty must not be writable")
    spec = spec or BUILD / "rootfs.mtree"
    if spec.read_text() != ownership_spec(root):
        raise RuntimeError("RAM-root ownership spec does not cover the current tree")


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
    if (sd / "boot/entropy").stat().st_size != 4096:
        raise RuntimeError("Boot entropy file must be 4096 bytes")
    config = (sd / "boot/loader.conf").read_text()
    for value in ('mfsroot_type="mfs_root"', 'mfsroot_name="/boot/rootfs.ufs"',
                  'vfs.root.mountfrom="ufs:/dev/md0"',
                  'boot_multicons="YES"', 'boot_serial="NO"'):
        if value not in config:
            raise RuntimeError("Missing required loader setting: " + value)
    subprocess.run(["/sbin/fsck_ufs", "-n", BUILD / "rootfs.ufs"], check=True)
    subprocess.run(["/sbin/fsck_ufs", "-n", codex_root.CODEX_UFS], check=True)
    codex(sd)
    network_root(codex_root.CODEX_ROOT,
                 json.loads((codex_root.CODEX_ROOT / "etc/switchbsd-build.json").read_text()),
                 BUILD / "rootfs-codex.mtree")
    subprocess.run(["/sbin/fsck_ufs", "-n", usb_root.USB_UFS], check=True)
    usb_root_tree(sd)
    usb_stick(usb_root.USB_IMAGE, usb_root.USB_UFS)
    with gzip.open(usb_root.USB_IMAGE_GZ, "rb") as stream:
        if hashlib.file_digest(stream, "sha256").hexdigest() != digest(usb_root.USB_IMAGE):
            raise RuntimeError("The gzipped USB root image does not match build/" + usb_root.USB_IMAGE.name)
    image = DIST / "freebsd-switch-15.1.img"
    with image.open("rb") as stream:
        mbr = stream.read(512)
    if mbr[510:512] != b"\x55\xaa" or mbr[450] != 0xef:
        raise RuntimeError("Missing MBR EFI/FAT partition")
    offset = struct.unpack_from("<I", mbr, 454)[0] * 512
    artifacts = ("EFI/BOOT/BOOTAA64.EFI", "boot/kernel/kernel", "boot/rootfs.ufs",
                 "boot/loader.conf", "switchbsd/coreboot.rom",
                 "bootloader/payloads/hekate-switchbsd.bin",
                 "boot/rootfs-codex.ufs.gz", "boot/loader.conf.local",
                 "boot/entropy", "boot/loader.conf.d/network.conf.sample")
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
    with zipfile.ZipFile(DIST / "freebsd-switch-15.1-codex-update.zip") as archive:
        if archive.testzip() is not None:
            raise RuntimeError("Corrupt codex update ZIP archive")
        if archive_build_time(archive) != build_time:
            raise RuntimeError("ZIP build times do not match")
        for name in CODEX_UPDATE_FILES:
            if archive.read(name) != (sd / name).read_bytes():
                raise RuntimeError("Codex update does not match SD bundle: " + name)
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
    with zipfile.ZipFile(DIST / "freebsd-switch-15.1-network-update.zip") as archive:
        if archive.testzip() is not None:
            raise RuntimeError("Corrupt network update ZIP archive")
        if archive_build_time(archive) != build_time:
            raise RuntimeError("ZIP build times do not match")
        if set(archive.namelist()) != set(NETWORK_UPDATE_FILES):
            raise RuntimeError("Unexpected files in network update ZIP archive")
        for name in NETWORK_UPDATE_FILES:
            if archive.read(name) != (sd / name).read_bytes():
                raise RuntimeError("Network update does not match SD bundle: " + name)
    # The user's Wi-Fi and SSH settings must never be overwritten by an update.
    with zipfile.ZipFile(DIST / "freebsd-switch-15.1-usbroot-update.zip") as archive:
        if archive.testzip() is not None:
            raise RuntimeError("Corrupt USB root update ZIP archive")
        if archive_build_time(archive) != build_time:
            raise RuntimeError("ZIP build times do not match")
        if set(archive.namelist()) != set(USBROOT_UPDATE_FILES):
            raise RuntimeError("Unexpected files in USB root update ZIP archive")
        for name, source in USBROOT_UPDATE_FILES.items():
            if archive.read(name) != (sd / source).read_bytes():
                raise RuntimeError("USB root update does not match SD bundle: " + name)
        usb_root.check_loader_local(archive.read("boot/loader.conf.local").decode())
    for path in DIST.glob("*.zip"):
        with zipfile.ZipFile(path) as archive:
            if any(name.lower().startswith("boot/loader.conf.d/") and name.lower().endswith(".conf")
                   for name in archive.namelist()):
                raise RuntimeError(f"{path.name} would replace the user's loader.conf.d settings")
    for line in (DIST / "SHA256SUMS").read_text().splitlines():
        sha, name = line.split("  ", 1)
        check_hash(DIST / name, sha)
    print("SD image, RAM roots, USB root image, AArch64 binaries, codex libraries and "
          "distribution checksums passed.")


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, OSError, subprocess.CalledProcessError) as exc:
        sys.exit(str(exc))
