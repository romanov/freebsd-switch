#!/usr/bin/env python3
"""Pinned FreeBSD/Switch experiment builder. Never opens a host disk device."""
import base64
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import struct
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
DIAGNOSTIC_UPDATE_FILES = ("boot/rootfs.ufs", "switchbsd/docs/diagnostics.md",
                           "switchbsd/BUILD-TIME.txt")
# Third-party notices for the Wi-Fi firmware and network programs.
NETWORK_LICENSES = ("freebsd/sys/contrib/dev/rtwn/LICENSE", "freebsd/contrib/wpa/COPYING",
                    "freebsd/crypto/openssh/LICENCE")
# The kernel and RAM root add Wi-Fi and SSH; the build 8 firmware is unchanged.
# The user's boot/loader.conf.d/network.conf is never shipped or replaced.
NETWORK_UPDATE_FILES = ("boot/kernel/kernel", "boot/rootfs.ufs", "boot/entropy",
                        "boot/loader.conf.d/network.conf.sample",
                        "switchbsd/NETWORK-UPDATE.md", "switchbsd/BUILD-TIME.txt",
                        *("switchbsd/licenses/" + name for name in NETWORK_LICENSES))
RESCUE_TOOLS = (
    "sh", "mount", "umount", "hostname", "sysctl", "ls", "cat", "dmesg", "kenv",
    "reboot", "halt", "sleep", "ps", "df", "stty", "ifconfig", "date", "camcontrol",
    "geom", "gpart", "mount_msdosfs", "cp", "mkdir", "rm", "mv", "chmod", "ln",
    "sync", "head", "tail", "sed", "tee", "less", "vi", "test",
    "route", "ping", "dhclient", "pkill", "pgrep",
)
# FreeBSD builds OpenSSH and wpa_supplicant only as dynamic programs, so these
# are copied from the staged world with the run-time linker and their shared
# libraries. The recovery shell and diagnostic tools stay static.
NETWORK_PROGRAMS = (
    "usr/sbin/sshd", "usr/libexec/sshd-session", "usr/libexec/sshd-auth",
    "usr/libexec/sftp-server", "usr/sbin/wpa_supplicant", "usr/sbin/wpa_cli",
    # pw and pwd_mkdb set the SSH password; dhclient-script calls arp and chown.
    "usr/sbin/pw", "usr/sbin/pwd_mkdb", "usr/sbin/arp", "usr/sbin/chown",
)
RTLD = "libexec/ld-elf.so.1"
# The run-time linker's default search order; the RAM root has no hints file.
LIBRARY_DIRS = ("lib/casper", "lib", "usr/lib")
# Root has no usable password until switchbsd.ssh.password sets one at boot.
# sshd and dhclient need their privilege-separation users.
MASTER_PASSWD = (
    "root:*:0:0::0:0:Charlie &:/root:/bin/sh\n"
    "sshd:*:22:22::0:0:Secure Shell Daemon:/var/empty:/usr/sbin/nologin\n"
    "_dhcp:*:65:65::0:0:dhcp programs:/var/empty:/usr/sbin/nologin\n"
    "nobody:*:65534:65534::0:0:Unprivileged user:/nonexistent:/usr/sbin/nologin\n")
GROUP = "wheel:*:0:root\nsshd:*:22:\n_dhcp:*:65:\nnogroup:*:65533:\nnobody:*:65534:\n"
# Small static tools built from the pinned source against the staged ARM64 world.
# Library dependencies follow the corresponding FreeBSD Makefiles.
DIAGNOSTIC_TOOLS = {
    "uname": (("usr.bin/uname/uname.c",), ()),
    "usbconfig": (("usr.sbin/usbconfig/usbconfig.c", "usr.sbin/usbconfig/dump.c"),
                  ("-lusb", "-lpthread")),
    "devinfo": (("usr.sbin/devinfo/devinfo.c",), ("-ldevinfo",)),
    "diskinfo": (("usr.sbin/diskinfo/diskinfo.c",), ("-lutil",)),
    # As in rescue, omit the optional Casper service dependency.
    "sha256": (("sbin/md5/md5.c",), ("-lmd",)),
    "timeout": (("bin/timeout/timeout.c",), ()),
}


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
             "mcopy", "qemu-system-aarch64", "ssh", "scp", "ssh-keygen", "/usr/sbin/pwd_mkdb"])
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


def elf_dynamic(path):
    """Return the interpreter and DT_NEEDED names of a little-endian ELF64 file."""
    data = Path(path).read_bytes()
    if data[:6] != b"\x7fELF\x02\x01":
        raise RuntimeError(f"Expected little-endian ELF64: {path}")
    try:
        phoff = struct.unpack_from("<Q", data, 32)[0]
        entsize, count = struct.unpack_from("<HH", data, 54)
        # (p_type, p_offset, p_vaddr, p_filesz) of each program header.
        headers = [struct.unpack_from("<I4xQQ8xQ", data, phoff + i * entsize) for i in range(count)]
        interpreter, dynamic = None, None
        for kind, offset, _, size in headers:
            if kind == 3:  # PT_INTERP
                interpreter = data[offset:offset + size].split(b"\0", 1)[0].decode()
            elif kind == 2:  # PT_DYNAMIC
                dynamic = (offset, size)
        if dynamic is None:
            return interpreter, []
        needed, strtab = [], None
        for position in range(dynamic[0], dynamic[0] + dynamic[1], 16):
            tag, value = struct.unpack_from("<qQ", data, position)
            if tag == 0:  # DT_NULL
                break
            if tag == 1:  # DT_NEEDED
                needed.append(value)
            elif tag == 5:  # DT_STRTAB
                strtab = value
        if not needed:
            return interpreter, []
        # DT_STRTAB is a virtual address; find the PT_LOAD segment holding it.
        base = next((offset + strtab - vaddr for kind, offset, vaddr, size in headers
                     if kind == 1 and strtab is not None and vaddr <= strtab < vaddr + size), None)
        if base is None:
            raise RuntimeError(f"ELF dynamic string table is not loadable: {path}")
        names = []
        for index in needed:
            end = data.index(b"\0", base + index)
            names.append(data[base + index:end].decode())
        return interpreter, names
    except (struct.error, ValueError, UnicodeDecodeError) as error:
        raise RuntimeError(f"Truncated or malformed ELF dynamic section: {path}") from error


def library_closure(world, programs):
    """Shared libraries the programs need, as paths relative to the world tree."""
    libraries, pending = set(), [(p, p) for p in programs]
    while pending:
        path, program = pending.pop()
        for name in elf_dynamic(world / path)[1]:
            relative = next((f"{d}/{name}" for d in LIBRARY_DIRS if (world / d / name).is_file()), None)
            if relative is None:
                raise RuntimeError(f"Missing shared library {name} needed by {program}; "
                                   "check the staged world from make freebsd")
            if relative not in libraries:
                libraries.add(relative)
                pending.append((relative, program))
    return sorted(libraries)


def ownership_spec(root):
    """mtree spec making every RAM-root node root:wheel while keeping its mode.

    makefs otherwise records the build user's IDs, and sshd rejects a
    privilege-separation directory or home directory not owned by root.
    """
    root = Path(root)
    entries = [(".", root)]
    for directory, dirnames, filenames in os.walk(root):
        for name in dirnames + filenames:
            path = Path(directory) / name
            entries.append(("./" + path.relative_to(root).as_posix(), path))
    lines = ["#mtree 2.0"]
    for name, path in sorted(entries):
        if any(c.isspace() or c in "#\\" for c in name):
            raise RuntimeError(f"RAM-root path needs mtree escaping: {name}")
        info = path.lstat()
        fields = f"uid=0 gid=0 mode={stat.S_IMODE(info.st_mode):04o}"
        if stat.S_ISLNK(info.st_mode):
            lines.append(f"{name} type=link {fields} link={os.readlink(path)}")
        elif stat.S_ISDIR(info.st_mode):
            lines.append(f"{name} type=dir {fields}")
        else:
            lines.append(f"{name} type=file {fields}")
    return "\n".join(lines) + "\n"


def ssh_fingerprint(public_key):
    """OpenSSH SHA256 fingerprint of a public key line."""
    fields = public_key.split()
    try:
        blob = base64.b64decode(fields[1], validate=True)
    except (IndexError, ValueError) as error:
        raise RuntimeError("Invalid SSH public key") from error
    return "SHA256:" + base64.b64encode(hashlib.sha256(blob).digest()).decode().rstrip("=")


def host_key():
    """The Switch's SSH host key: made once on the build host, then reused."""
    key = BUILD / "ssh/ssh_host_ed25519_key"
    if not key.is_file():
        key.parent.mkdir(exist_ok=True)
        run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "root@freebsd-switch", "-f", key])
    if not key.with_suffix(".pub").is_file():
        raise RuntimeError(f"Missing {key}.pub; remove {key} to create a new host key")
    return key


def assemble_os(build_time=None):
    if build_time is None:
        build_time = datetime.now(timezone.utc).isoformat()
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
    for d in ["rescue", "bin", "sbin", "etc/ssh", "dev", "tmp", "var/run/dhclient", "var/db",
              "var/log", "var/empty", "root", "mnt"]:
        (root / d).mkdir(parents=True, exist_ok=True)
    copy(world / "rescue/rescue", root / "rescue/rescue")
    (root / "rescue/rescue").chmod(0o555)
    # Verify the staged rescue links before creating aliases to the multicall binary.
    for name in RESCUE_TOOLS:
        if not (world / "rescue" / name).is_file():
            raise RuntimeError("Missing rescue applet in staged world: " + name)
        os.link(root / "rescue/rescue", root / "rescue" / name)
    # These commands are not rescue applets. Static linking preserves the
    # recovery shell's independence from a dynamic linker and shared libraries.
    for name, (sources, libraries) in DIAGNOSTIC_TOOLS.items():
        run(["clang", "--target=aarch64-unknown-freebsd15.1", f"--sysroot={world}",
             "-fuse-ld=lld", "-static", "-O2", "-Wl,-s", "-o", root / "rescue" / name,
             *(SRC / "freebsd" / p for p in sources), *libraries], log=f"{name}.log")
        (root / "rescue" / name).chmod(0o555)
    # mount(8) searches /sbin and /rescue for filesystem helpers. /bin aliases
    # also keep scripts with conventional absolute command paths usable;
    # dhclient-script runs /sbin/ifconfig.
    for name in (*RESCUE_TOOLS, *DIAGNOSTIC_TOOLS):
        (root / "bin" / name).symlink_to("../rescue/" + name)
    for name in RESCUE_TOOLS:
        (root / "sbin" / name).symlink_to("../rescue/" + name)
    for name in ("switchbsd-report", "switchbsd-net"):
        copy(ROOT / "config" / name, root / "bin" / name)
        (root / "bin" / name).chmod(0o555)
    copy(ROOT / "docs/diagnostics.md", root / "root/DIAGNOSTICS.txt")
    copy(ROOT / "NETWORK-UPDATE.md", root / "root/NETWORK.txt")
    for path in (RTLD, *NETWORK_PROGRAMS, *library_closure(world, NETWORK_PROGRAMS)):
        copy(world / path, root / path)
        (root / path).chmod(0o555 if path in (RTLD, *NETWORK_PROGRAMS) else 0o444)
    # dhclient runs /sbin/dhclient-script; the rescue copy starts /rescue/sh.
    copy(world / "rescue/dhclient-script", root / "sbin/dhclient-script")
    (root / "sbin/dhclient-script").chmod(0o555)
    (root / "etc/dhclient.conf").write_text('timeout 20;\nsend host-name "freebsd-switch";\n')
    # Without this, dhclient-script hands DNS servers to the absent resolvconf.
    (root / "etc/dhclient-enter-hooks").write_text('resolvconf_enable="NO"\n')
    for name in ("regdomain.xml", "services", "protocols"):
        copy(world / "etc" / name, root / "etc" / name)
    copy(ROOT / "config/sshd_config", root / "etc/ssh/sshd_config")
    key = host_key()
    copy(key, root / "etc/ssh" / key.name)
    (root / "etc/ssh" / key.name).chmod(0o600)
    public_key = key.with_suffix(".pub").read_text()
    (root / "etc/ssh" / (key.name + ".pub")).write_text(public_key)
    fingerprint = ssh_fingerprint(public_key) + " (ED25519)"
    (root / "etc/ssh" / (key.name + ".fingerprint")).write_text(fingerprint + "\n")
    (root / "etc/master.passwd").write_text(MASTER_PASSWD)
    (root / "etc/master.passwd").chmod(0o600)
    (root / "etc/group").write_text(GROUP)
    run(["/usr/sbin/pwd_mkdb", "-i", "-p", "-d", root / "etc", root / "etc/master.passwd"],
        log="pwd_mkdb.log")
    (root / "var/empty").chmod(0o555)
    identity = {"diagnostic_revision": 2, "built_at": build_time, "release": "15.1",
                "kernel_sha256": digest(world / "boot/kernel/kernel"),
                "source_lock_sha256": digest(ROOT / "sources.lock.json"),
                "report_sha256": digest(ROOT / "config/switchbsd-report"),
                "network_script_sha256": digest(ROOT / "config/switchbsd-net"),
                "sshd_config_sha256": digest(ROOT / "config/sshd_config"),
                "ssh_host_key_fingerprint": fingerprint,
                "builder_sha256": digest(ROOT / "scripts/build.py")}
    firmware = BUILD / "firmware/coreboot.rom"
    if firmware.is_file():
        identity["firmware_sha256"] = digest(firmware)
    (root / "etc/switchbsd-build.json").write_text(json.dumps(identity, indent=2) + "\n")
    copy(world / "sbin/init", root / "sbin/init")
    (root / "sbin/init").chmod(0o555)
    copy(ROOT / "config/rc", root / "etc/rc")
    (root / "etc/rc").chmod(0o555)
    (root / "etc/fstab").write_text("/dev/md0 / ufs rw 0 0\n")
    (root / "etc/ttys").write_text('console none vt100 off secure\n')
    # SSH sessions take PATH from here; pw uses the password format.
    (root / "etc/login.conf").write_text(
        "default:\\\n\t:passwd_format=sha512:\\\n\t:path=/bin /sbin /usr/bin /usr/sbin /rescue:\\\n"
        "\t:umask=022:\n\ndaemon:\\\n\t:tc=default:\n")
    copy(world / "usr/share/misc/termcap", root / "etc/termcap")
    (root / "tmp").chmod(0o1777)
    spec = BUILD / "rootfs.mtree"
    spec.write_text(ownership_spec(root))
    run(["makefs", "-t", "ffs", "-B", "little", "-s", "128m", "-o", "version=2", "-F", spec,
         BUILD / "rootfs.ufs", root], log="rootfs.log")
    # Use the release's boot scripts; only configuration and rootfs differ.
    shutil.copytree(world / "boot", sd / "boot", ignore=shutil.ignore_patterns("*.debug", "*.symbols"))
    copy(world / "boot/loader.efi", sd / "EFI/BOOT/BOOTAA64.EFI")
    copy(ROOT / "config/loader.conf", sd / "boot/loader.conf")
    copy(BUILD / "rootfs.ufs", sd / "boot/rootfs.ufs")
    # The loader seeds random(4) from this file, so sshd and wpa_supplicant need
    # not wait for the Switch to gather entropy. FreeBSD cannot rewrite it on
    # the SD card, so each build gets a fresh one.
    (sd / "boot/entropy").write_bytes(os.urandom(4096))
    copy(ROOT / "config/network.conf.sample", sd / "boot/loader.conf.d/network.conf.sample")
    return sd


def image():
    # Refuse to publish a Switch bundle until the actual firmware exists.
    for name in ("coreboot.rom", "hekate-switchbsd.bin"):
        if not (BUILD / "firmware" / name).is_file():
            raise RuntimeError("Missing firmware: " + name + "; run make firmware")
    build_time = datetime.now(timezone.utc).isoformat()
    sd = assemble_os(build_time)
    build_time_file = sd / "switchbsd/BUILD-TIME.txt"
    build_time_file.parent.mkdir(parents=True, exist_ok=True)
    build_time_file.write_text(f"SwitchBSD build time (UTC): {build_time}\n")
    copy(BUILD / "firmware/coreboot.rom", sd / "switchbsd/coreboot.rom")
    copy(BUILD / "firmware/hekate-switchbsd.bin", sd / "bootloader/payloads/hekate-switchbsd.bin")
    (sd / "bootloader/ini").mkdir(parents=True, exist_ok=True)
    (sd / "bootloader/ini/switchbsd.ini").write_text(
        "[FreeBSD 15.1 experiment]\npayload=bootloader/payloads/hekate-switchbsd.bin\n")
    copy(ROOT / "sources.lock.json", sd / "switchbsd/sources.lock.json")
    for name in ("README.md", "UPDATE.md", "CONSOLE-UPDATE.md", "USB-UPDATE.md", "NETWORK-UPDATE.md",
                 "docs/boot-test.md", "docs/firmware.md", "docs/licenses.md",
                 "docs/hardware-boot-2026-10-03.md", "docs/diagnostics.md"):
        copy(ROOT / name, sd / "switchbsd" / name)
    for name in ("switch/LICENSE", "hekate/LICENSE", "coreboot/COPYING",
                 "coreboot/3rdparty/arm-trusted-firmware/license.rst",
                 "edk2/License.txt", "freebsd/COPYRIGHT", *NETWORK_LICENSES):
        copy(SRC / name, sd / "switchbsd/licenses" / name)
    with zipfile.ZipFile(DIST / "freebsd-switch-15.1-sd.zip", "w", zipfile.ZIP_DEFLATED) as z:
        for path in sorted(sd.rglob("*")):
            if path.is_file():
                z.write(path, path.relative_to(sd))
        z.comment = f"SwitchBSD build time (UTC): {build_time}".encode()
    make_disk(sd, DIST / "freebsd-switch-15.1.img")
    metadata = {"target": "Erista", "release": "15.1", "hardware_verified": False,
                "built_at": build_time,
                "diagnostics": json.loads((BUILD / "root/etc/switchbsd-build.json").read_text()),
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
    with zipfile.ZipFile(DIST / "freebsd-switch-15.1-diagnostics-update.zip", "w", zipfile.ZIP_DEFLATED) as z:
        for name in DIAGNOSTIC_UPDATE_FILES:
            z.write(sd / name, name)
        z.comment = f"SwitchBSD build time (UTC): {build_time}".encode()
    with zipfile.ZipFile(DIST / "freebsd-switch-15.1-network-update.zip", "w", zipfile.ZIP_DEFLATED) as z:
        for name in NETWORK_UPDATE_FILES:
            z.write(sd / name, name)
        z.comment = f"SwitchBSD build time (UTC): {build_time}".encode()
    checksums()
    print("Switch SSH host key:", metadata["diagnostics"]["ssh_host_key_fingerprint"])


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
