#!/usr/bin/env python3
"""USB-stick root: a persistent FreeBSD install that updates itself with pkg.

The base system comes from the host-built pkgbase repository (pkgbase.py), so
it always matches the SD card's kernel. Codex and its tools come from the
cached FreeBSD packages. Unlike the RAM roots, nothing is trimmed: pkg owns
every file and can upgrade it on the Switch. The loader, kernel and fallback
RAM root stay on the SD card because the firmware cannot read USB.
"""
import gzip
import json
import os
import re
import shutil
import subprocess
import time
from datetime import datetime, timezone
from build import (ROOT, BUILD, DIST, run, copy, digest, ownership_spec, host_key,
                   ssh_fingerprint)
import pkgbase

USB_ROOT = BUILD / "root-usb"
USB_UFS = BUILD / "rootfs-usb.ufs"
USB_IMAGE = BUILD / "freebsd-switch-15.1-usbroot.img"
USB_IMAGE_GZ = DIST / "freebsd-switch-15.1-usbroot.img.gz"
PORTS_CACHE = ROOT / "cache/usbroot-packages"
PKG_CONF = BUILD / "usbroot-pkg"
METALOG = BUILD / "root-usb.metalog"
SPEC = BUILD / "rootfs-usb.mtree"
LABEL = "switchroot"
ROOT_DEVICE = f"/dev/gpt/{LABEL}"
LOADER_LOCAL = ROOT / "config/loader.conf.local.usbroot"
# A local console may have no carrier-detect line (QEMU serial and the Switch
# UART). nc makes getty use CLOCAL while Pc supplies console terminal settings.
CONSOLE_GETTY = "switchbsd"
CONSOLE_GETTY_ENTRY = ("\nswitchbsd|SwitchBSD local console:\\\n"
                       "\t:al=root:nc:tc=Pc:\n")
TTYS = ("# SwitchBSD USB root: a root shell on the console, whichever device it is.\n"
        f'console\t"/usr/libexec/getty {CONSOLE_GETTY}"\txterm\ton\tsecure\n')
FSTAB = f"{ROOT_DEVICE}\t/\tufs\trw\t1\t1\n"
# (source in the repository, target in the root, mode)
OVERLAY = (
    ("config/usbroot-rc.conf", "etc/rc.conf", 0o644),
    ("config/usbroot-profile.sh", "etc/profile.d/switchbsd.sh", 0o644),
    ("config/sshd_config", "etc/ssh/sshd_config", 0o644),
    ("config/rc.d/switchbsd", "usr/local/etc/rc.d/switchbsd", 0o555),
    ("config/switchbsd-net", "usr/local/bin/switchbsd-net", 0o555),
    ("config/switchbsd-report", "usr/local/bin/switchbsd-report", 0o555),
    ("config/switchbsd-update", "usr/local/sbin/switchbsd-update", 0o555),
    ("docs/diagnostics.md", "root/DIAGNOSTICS.txt", 0o644),
    ("NETWORK-UPDATE.md", "root/NETWORK.txt", 0o644),
    ("USBROOT.md", "root/USBROOT.txt", 0o644),
)
USB_REQUIRED = ("bin/sh", "sbin/init", "etc/rc", "usr/sbin/sshd", "usr/sbin/wpa_supplicant",
                "usr/local/sbin/pkg", "usr/local/bin/codex", "usr/local/bin/bash",
                "usr/local/bin/rg", "usr/local/bin/git", "etc/pwd.db", "etc/ssl/cert.pem",
                *(target for _, target, _ in OVERLAY))


def config():
    return json.loads((ROOT / "config/usbroot-packages.json").read_text())


def base_packages(cfg, available):
    """The base packages to install, checked against the repository's names."""
    names = list(cfg["base"])
    kernels = [n for n in names if n.startswith("FreeBSD-kernel")]
    if kernels:
        raise RuntimeError("The USB root must not install kernel packages (the kernel is on the SD "
                           "card): " + ", ".join(kernels))
    missing = [n for n in names if n not in available]
    if missing:
        raise RuntimeError("Not in the pkgbase repository: " + ", ".join(missing) +
                           "; fix config/usbroot-packages.json (see make pkgbase output)")
    return names


def loader_settings(text):
    """name -> value for the settings in a loader.conf file."""
    settings = {}
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            name, value = line.split("=", 1)
            settings[name.strip()] = value.strip().strip('"')
    return settings


def check_loader_local(text):
    """The stick is tried first, the diagnostic RAM root is the fallback, and
    the root starts read-only so rc can check it."""
    settings = loader_settings(text)
    roots = settings.get("vfs.root.mountfrom", "").split()
    if (roots[:1] != [f"ufs:{ROOT_DEVICE}"] or "ufs:/dev/md0" not in roots or
            settings.get("mfsroot_name") != "/boot/rootfs.ufs" or
            settings.get("vfs.root.mountfrom.options") != "ro"):
        raise RuntimeError(f"{LOADER_LOCAL.name} must mount ufs:{ROOT_DEVICE} first, fall back to "
                           'ufs:/dev/md0 with mfsroot_name="/boot/rootfs.ufs", and mount read-only')
    return roots


def ports_config(cfg):
    return cfg["ports"]


def private_pkg_conf(directory, base_repo, pubkey, ports_repo):
    """pkg configuration for installing into the stick tree from local repositories.

    Neither the host's pkg configuration nor its database is used. The ports
    repository keeps the name FreeBSD, so the Switch upgrades those packages
    from the official repository of the same name.
    """
    cfg = ports_config(config())
    (directory / "repos").mkdir(parents=True, exist_ok=True)
    (directory / "cache").mkdir(exist_ok=True)
    conf = directory / "pkg.conf"
    conf.write_text(f'ABI = "{cfg["abi"]}";\nOSVERSION = {cfg["osversion"]};\n'
                    'IGNORE_OSVERSION = true;\nASSUME_ALWAYS_YES = true;\n'
                    f'PKG_CACHEDIR = "{directory}/cache";\nREPOS_DIR = ["{directory}/repos"];\n')
    (directory / "repos/repos.conf").write_text(
        f'{pkgbase.REPO_NAME}: {{\n  url: "file://{base_repo}",\n  signature_type: "pubkey",\n'
        f'  pubkey: "{pubkey}",\n  enabled: yes\n}}\n'
        f'FreeBSD: {{\n  url: "file://{ports_repo}",\n  signature_type: "none",\n  enabled: yes\n}}\n')
    return conf


def ports_repo_conf(cfg):
    """The official package repository, for upgrading Codex and its tools on the Switch."""
    return ('FreeBSD: {\n  url: "pkg+https://pkg.FreeBSD.org/${ABI}/' + cfg["repo"] + '",\n'
            '  mirror_type: "srv",\n  signature_type: "fingerprints",\n'
            '  fingerprints: "/usr/share/keys/pkg",\n  enabled: yes\n}\n')


def spec_entries(text):
    """path -> mtree line, for every entry line of an mtree file."""
    entries = {}
    for line in text.splitlines():
        if not line.strip() or line.startswith(("#", "/")):
            continue
        name = line.split(None, 1)[0]
        name = name if name.startswith("./") or name == "." else "./" + name.lstrip("/")
        entries[name] = name + line[len(line.split(None, 1)[0]):]
    return entries


# METALOG keywords makefs should apply. Sizes and digests describe the
# packaged file, which the build may have replaced.
SPEC_KEYWORDS = ("type", "uname", "gname", "uid", "gid", "mode", "flags", "link")


def merge_spec(metalog, ownership):
    """One spec for makefs covering every node of the tree.

    Package files keep pkg's recorded owner, mode and flags; files the build
    added become root:wheel with their on-disk mode. METALOG entries for
    files no longer in the tree are dropped.
    """
    packaged, tree = spec_entries(metalog), spec_entries(ownership)
    lines = []
    for name, line in sorted(tree.items()):
        if name in packaged:
            ours = dict(field.split("=", 1) for field in line.split()[1:])
            kept = {}
            for field in packaged[name].split()[1:]:
                keyword, _, value = field.partition("=")
                if keyword in SPEC_KEYWORDS:
                    kept[keyword] = value
            # The tree decides the node type; a replaced file keeps pkg's owner.
            kept["type"] = ours["type"]
            kept.pop("link", None)
            if ours["type"] == "link":
                kept["link"] = ours["link"]
            line = " ".join([name, *(f"{k}={v}" for k, v in kept.items())])
        lines.append(line)
    return "#mtree 2.0\n" + "\n".join(lines) + "\n"


def install_packages(root, cfg):
    _, public = pkgbase.signing_key()
    base = base_packages(cfg, pkgbase.repo_names())
    ports = ports_config(cfg)
    from codex_root import fetch_packages
    manifest = fetch_packages(ports, PORTS_CACHE, "USB root packages")
    ports_repo = PORTS_CACHE / ports["abi"].replace(":", "-")
    # An unsigned local catalogue of the verified, cached packages.
    run(["pkg", "repo", ports_repo], log="usbroot-ports-repo.log")
    if PKG_CONF.exists():
        shutil.rmtree(PKG_CONF)
    conf = private_pkg_conf(PKG_CONF, pkgbase.repo_dir(), public, ports_repo)
    # METALOG records owners, modes and flags instead of applying them, so no
    # administrator access is needed; makefs applies them from the spec.
    METALOG.unlink(missing_ok=True)
    run(["pkg", "-C", conf, "-o", f"METALOG={METALOG}", "-r", root, "install", "-y",
         *base, *ports["packages"]], log="usbroot-install.log")
    installed = subprocess.check_output(["pkg", "-C", conf, "-r", root, "query", "%n\t%v"],
                                        text=True).splitlines()
    packages = dict(line.split("\t", 1) for line in installed)
    kernels = [n for n in packages if n.startswith("FreeBSD-kernel")]
    if kernels:
        raise RuntimeError("A base package pulled in a kernel package: " + ", ".join(kernels))
    return packages, manifest


def write(root, relative, text, mode=0o644):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(mode)


def has_root_console_autologin(gettytab):
    """Check the gettytab entry used by the USB root's console."""
    return bool(re.search(rf"(?m)^{re.escape(CONSOLE_GETTY)}(?:\|[^:\n]+)?:\\\n[ \t]*:al=root:nc:tc=Pc:",
                          gettytab))


def overlay(root, cfg, build_time, packages):
    for source, target, mode in OVERLAY:
        (root / target).unlink(missing_ok=True)
        copy(ROOT / source, root / target)
        (root / target).chmod(mode)
    gettytab_path = root / "etc/gettytab"
    gettytab = gettytab_path.read_text() + CONSOLE_GETTY_ENTRY
    if not has_root_console_autologin(gettytab):
        raise RuntimeError(f"etc/gettytab has no {CONSOLE_GETTY} autologin entry for the console")
    gettytab_path.write_text(gettytab)
    if "profile.d" not in (root / "etc/profile").read_text():
        raise RuntimeError("etc/profile does not read /etc/profile.d; the console guide would not show")
    # FreeBSD's root login runs /root/.profile directly. Source the same
    # environment script there so the local shell and Codex get its settings.
    profile = root / "root/.profile"
    profile.write_text(profile.read_text().replace(
        "if [ -x /usr/bin/resizewin ] ; then /usr/bin/resizewin -z ; fi", "") +
        "\n. /etc/profile.d/switchbsd.sh\n")
    write(root, "etc/ttys", TTYS)
    write(root, "etc/fstab", FSTAB)
    # rc runs firstboot scripts, such as growfs, once and then deletes this.
    write(root, "firstboot", "")
    key = host_key()
    copy(key, root / "etc/ssh" / key.name)
    (root / "etc/ssh" / key.name).chmod(0o600)
    public_key = key.with_suffix(".pub").read_text()
    write(root, f"etc/ssh/{key.name}.pub", public_key)
    fingerprint = ssh_fingerprint(public_key) + " (ED25519)"
    write(root, f"etc/ssh/{key.name}.fingerprint", fingerprint + "\n")
    _, public = pkgbase.signing_key()
    copy(public, root / pkgbase.STICK_PUBKEY)
    url = os.environ.get("SWITCHBSD_PKG_URL", pkgbase.URL_PLACEHOLDER)
    write(root, "usr/local/etc/pkg/repos/SwitchBSD.conf", pkgbase.repo_conf(url))
    write(root, "usr/local/etc/pkg/repos/FreeBSD.conf", ports_repo_conf(ports_config(cfg)))
    write(root, "etc/switchbsd-build-epoch", f"{int(time.time())}\n")
    (root / "root/work").mkdir(parents=True, exist_ok=True)
    # pkg's post-install scripts do not run for an install into another root.
    run(["/usr/sbin/pwd_mkdb", "-i", "-p", "-d", root / "etc", root / "etc/master.passwd"],
        log="usbroot-pwd_mkdb.log")
    run(["cap_mkdb", root / "etc/login.conf"], log="usbroot-cap_mkdb.log")
    run(["/usr/sbin/services_mkdb", "-l", "-q", "-o", root / "var/db/services.db", root / "etc/services"],
        log="usbroot-services_mkdb.log")
    world = BUILD / "world"
    identity = {"root": "usb", "built_at": build_time, "release": "15.1",
                "kernel_sha256": digest(world / "boot/kernel/kernel"),
                "source_lock_sha256": digest(ROOT / "sources.lock.json"),
                "report_sha256": digest(ROOT / "config/switchbsd-report"),
                "network_script_sha256": digest(ROOT / "config/switchbsd-net"),
                "update_script_sha256": digest(ROOT / "config/switchbsd-update"),
                "sshd_config_sha256": digest(ROOT / "config/sshd_config"),
                "ssh_host_key_fingerprint": fingerprint,
                "pkgbase_key_sha256": digest(public),
                "builder_sha256": digest(ROOT / "scripts/usb_root.py"),
                "packages": packages}
    write(root, "etc/switchbsd-build.json", json.dumps(identity, indent=2) + "\n")


def make_image(root):
    """UFS image labelled through its GPT partition, gzipped for the stick writer."""
    from codex_root import tree_size
    mib = 2 ** 20
    # growfs fills the stick on first boot; this is only enough to start.
    size = -(-int(tree_size(root) * 1.2 + 512 * mib) // (64 * mib)) * 64
    metalog = METALOG.read_text() if METALOG.is_file() else ""
    SPEC.write_text(merge_spec(metalog, ownership_spec(root)))
    run(["makefs", "-t", "ffs", "-B", "little", "-s", f"{size}m", "-o", "version=2",
         "-o", "softupdates=1", "-N", root / "etc", "-F", SPEC, USB_UFS, root],
        log="rootfs-usb.log")
    run(["mkimg", "-s", "gpt", "-p", f"freebsd-ufs/{LABEL}:={USB_UFS}", "-o", USB_IMAGE])
    with USB_IMAGE.open("rb") as source, USB_IMAGE_GZ.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, compresslevel=6, mtime=0) as out:
            shutil.copyfileobj(source, out, 1 << 20)
    return size


def assemble_usb_root(sd, build_time=None):
    """Stage build/root-usb, write the stick image and the SD card's loader selection."""
    if build_time is None:
        build_time = datetime.now(timezone.utc).isoformat()
    if not pkgbase.repo_dir().is_dir():
        raise RuntimeError("No pkgbase repository; run make pkgbase before make image")
    check_loader_local(LOADER_LOCAL.read_text())
    cfg = config()
    if USB_ROOT.exists():
        # pkgbase marks core files schg even in a staging tree. Clear those
        # host-side flags before replacing a previous image build; METALOG
        # still supplies the packaged flags to makefs.
        run(["chflags", "-R", "noschg,nouchg", USB_ROOT])
        shutil.rmtree(USB_ROOT)
    USB_ROOT.mkdir()
    packages, manifest = install_packages(USB_ROOT, cfg)
    overlay(USB_ROOT, cfg, build_time, packages)
    for name in USB_REQUIRED:
        if not ((USB_ROOT / name).exists() or (USB_ROOT / name).is_symlink()):
            raise RuntimeError("Incomplete USB root: " + name)
    size = make_image(USB_ROOT)
    copy(LOADER_LOCAL, sd / "switchbsd/usbroot/loader.conf.local")
    (BUILD / "root-usb.json").write_text(json.dumps(
        {"ufs_mib": size, "packages": packages, "ports": manifest["packages"],
         "image_gz_sha256": digest(USB_IMAGE_GZ)}, indent=2) + "\n")
    mib = 2 ** 20
    print(f"USB root: {size} MiB UFS, {USB_IMAGE_GZ.stat().st_size // mib} MiB gzip, "
          f"{len(packages)} packages")
