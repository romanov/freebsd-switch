#!/usr/bin/env python3
"""Codex RAM root: FreeBSD aarch64 packages plus the base runtime they link.

Only files the root executes or links are staged. Declared package
dependencies such as python and X11 stay out unless a staged ELF file
actually needs one of their libraries.
"""
import gzip
import json
import os
from pathlib import Path
import posixpath
import shutil
import struct
import subprocess
import time
from build import ROOT, BUILD, run, digest, copy, stage_base_root, ownership_spec

PKG = ROOT / "cache/packages"
PKGROOT = BUILD / "pkgroot"
CODEX_ROOT = BUILD / "root-codex"
CODEX_UFS = BUILD / "rootfs-codex.ufs"
LOADER_ROOT_NAME = "/boot/rootfs-codex.ufs"
# rtld's default path is /lib:/usr/lib; rc.codex adds /usr/local/lib with ldconfig.
LIBRARY_DIRS = ("lib", "usr/lib", "usr/local/lib", "lib/casper")
SKIP = ("usr/local/share/doc", "usr/local/share/man", "usr/local/man", "usr/local/share/locale",
        "usr/local/share/info", "usr/local/share/examples", "usr/local/share/bash-completion",
        "usr/local/share/zsh", "usr/local/share/fish", "usr/local/etc/bash_completion.d",
        "usr/local/include", "usr/local/libdata/pkgconfig", "usr/local/share/licenses")
# Rescue applets exposed at their usual paths. dhclient-script runs with
# PATH=/usr/bin:/usr/sbin:/bin:/sbin and hardcodes /sbin/ifconfig and /bin/hostname.
RESCUE_LINKS = {
    "bin": ("cat", "chmod", "cp", "date", "dd", "df", "echo", "ed", "expr", "hostname", "kenv",
            "kill", "ln", "ls", "mkdir", "mv", "ps", "pwd", "realpath", "rm", "rmdir", "sleep",
            "stty", "sync", "test"),
    "sbin": ("dhclient", "dmesg", "halt", "ifconfig", "ldconfig", "md5", "mount", "mount_msdosfs",
             "ping", "reboot", "route", "sysctl", "umount"),
    "usr/bin": ("bzip2", "fetch", "gzip", "head", "id", "less", "nc", "sed", "tail", "tar", "tee",
                "vi", "xz", "zstd"),
    "usr/sbin": ("chown",),
}
ETC_FILES = ("etc/master.passwd", "etc/passwd", "etc/pwd.db", "etc/spwd.db", "etc/group",
             "etc/services", "etc/protocols", "etc/hosts", "etc/nsswitch.conf", "etc/ntp.conf",
             "etc/ssl/cert.pem")
TERMINFO = ("xterm", "xterm-256color", "xterm-color", "vt100", "vt220")
CODEX_REQUIRED = ("usr/local/bin/codex", "usr/local/bin/bash", "usr/local/bin/rg",
                  "usr/local/bin/git", "usr/local/bin/switchbsd-net", "libexec/ld-elf.so.1",
                  "etc/rc.codex", "etc/ssl/cert.pem", "etc/pwd.db", "etc/dhclient-enter-hooks",
                  "sbin/dhclient-script", "var/empty", "var/run/dhclient")


def config():
    return json.loads((ROOT / "config/codex-packages.json").read_text())


def pkg_args(cfg):
    for name in ("repos", "db", "cache"):
        (PKG / name).mkdir(parents=True, exist_ok=True)
    # A private configuration: the host's pkg.conf, repositories and package
    # database are never read or changed, and no administrator access is needed.
    (PKG / "pkg.conf").write_text(
        f'ABI = "{cfg["abi"]}";\nOSVERSION = {cfg["osversion"]};\nIGNORE_OSVERSION = true;\n'
        f'PKG_DBDIR = "{PKG}/db";\nPKG_CACHEDIR = "{PKG}/cache";\n'
        f'REPOS_DIR = ["{PKG}/repos"];\nASSUME_ALWAYS_YES = true;\n')
    (PKG / "repos/FreeBSD.conf").write_text(
        'FreeBSD: {\n  url: "pkg+https://pkg.FreeBSD.org/${ABI}/' + cfg["repo"] + '",\n'
        '  mirror_type: "srv",\n  signature_type: "fingerprints",\n'
        '  fingerprints: "/usr/share/keys/pkg",\n  enabled: yes\n}\n')
    return ["pkg", "-C", PKG / "pkg.conf"]


def compact_manifest(package):
    return json.loads(subprocess.check_output(["tar", "-xOf", package, "+COMPACT_MANIFEST"]))


def cached_manifest(cfg):
    path = PKG / "manifest.json"
    if os.environ.get("PKG_REFRESH") or not path.exists():
        return None
    manifest = json.loads(path.read_text())
    if manifest.get("config") != cfg:
        return None
    for entry in manifest["packages"]:
        package = ROOT / entry["path"]
        if not package.is_file() or digest(package) != entry["sha256"]:
            return None
    return manifest


def fetch_packages():
    """Fetch the codex packages and their dependencies once; reruns reuse the cache.

    pkg verifies the repository signature and every package checksum. The
    official repository deletes superseded packages, so versions and hashes are
    recorded in build-info.json instead of being pinned here.
    """
    cfg = config()
    manifest = cached_manifest(cfg)
    if manifest:
        print("Codex packages: cached", ", ".join(f'{p["name"]}-{p["version"]}' for p in manifest["packages"]
                                                  if p["name"] in cfg["packages"]))
        return manifest
    from build import require
    require(["pkg", "tar"])
    args = pkg_args(cfg)
    out = PKG / cfg["abi"].replace(":", "-")
    if out.exists():
        shutil.rmtree(out)
    out.mkdir()
    run(args + ["update", "-f"], log="pkg-update.log")
    run(args + ["fetch", "-y", "-U", "-d", "-o", out] + cfg["packages"], log="pkg-fetch.log")
    packages = []
    for package in sorted(out.rglob("*.pkg")):
        meta = compact_manifest(package)
        packages.append({"name": meta["name"], "version": meta["version"], "origin": meta["origin"],
                         "sha256": digest(package), "path": str(package.relative_to(ROOT))})
    missing = set(cfg["packages"]) - {p["name"] for p in packages}
    if missing:
        raise RuntimeError("pkg fetch did not return: " + ", ".join(sorted(missing)) + "; see logs/pkg-fetch.log")
    manifest = {"config": cfg, "packages": packages}
    (PKG / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def extract_packages(manifest):
    if PKGROOT.exists():
        shutil.rmtree(PKGROOT)
    trees = {}
    for entry in manifest["packages"]:
        tree = PKGROOT / entry["name"]
        tree.mkdir(parents=True)
        run(["tar", "-xf", ROOT / entry["path"], "-C", tree, "--exclude", "+*"])
        trees[entry["name"]] = tree
    return trees


def walk(source):
    """Every path below source, sorted, without following directory symlinks."""
    for directory, dirs, files in os.walk(source):
        dirs.sort()
        base = Path(directory)
        for name in sorted(dirs + files):
            yield base / name


def skipped(relative, skip):
    return any(relative == s or relative.startswith(s + "/") for s in skip)


def copy_file(path, dest, inodes):
    """Copy one file or symlink; a second name for a seen inode becomes a hard link."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.is_symlink() or dest.exists():
        dest.unlink()
    if path.is_symlink():
        dest.symlink_to(os.readlink(path))
        return
    info = path.stat()
    key = (info.st_dev, info.st_ino)
    if info.st_nlink > 1 and key in inodes:
        os.link(inodes[key], dest)
    else:
        shutil.copy2(path, dest)
        inodes[key] = dest


def copy_tree(source, target, inodes, skip=()):
    # git-core is mostly hard links to git; copytree would store each one in full.
    for path in walk(source):
        relative = path.relative_to(source).as_posix()
        if skipped(relative, skip):
            continue
        if path.is_dir() and not path.is_symlink():
            (target / relative).mkdir(parents=True, exist_ok=True)
        else:
            copy_file(path, target / relative, inodes)


def elf_dynamic(path):
    """Return (DT_NEEDED, DT_RUNPATH/DT_RPATH) of a 64-bit ELF file, or None if not ELF."""
    with Path(path).open("rb") as stream:
        head = stream.read(64)
        if len(head) < 64 or head[:4] != b"\x7fELF":
            return None
        if head[4:6] != b"\x02\x01":
            raise RuntimeError(f"Expected 64-bit little-endian ELF: {path}")
        phoff = struct.unpack_from("<Q", head, 32)[0]
        entsize, count = struct.unpack_from("<HH", head, 54)
        stream.seek(phoff)
        table = stream.read(entsize * count)
        if count and (entsize < 56 or len(table) < entsize * count):
            raise RuntimeError(f"Truncated ELF program headers: {path}")
        headers = [struct.unpack_from("<IIQQQQQQ", table, i * entsize) for i in range(count)]
        dynamic = [h for h in headers if h[0] == 2]
        if not dynamic:
            return [], []
        stream.seek(dynamic[0][2])
        data = stream.read(dynamic[0][5])
        entries = []
        for offset in range(0, len(data) - 15, 16):
            tag, value = struct.unpack_from("<qQ", data, offset)
            if tag == 0:
                break
            entries.append((tag, value))
        strtab = [v for t, v in entries if t == 5]
        if not strtab:
            return [], []
        # DT_STRTAB is a virtual address; map it through the PT_LOAD segments.
        base = next((h[2] + strtab[0] - h[3] for h in headers
                     if h[0] == 1 and h[3] <= strtab[0] < h[3] + h[5]), None)
        if base is None:
            raise RuntimeError(f"ELF string table outside loaded segments: {path}")

        def string(offset):
            stream.seek(base + offset)
            value = b""
            while b"\0" not in value:
                chunk = stream.read(256)
                if not chunk:
                    raise RuntimeError(f"Unterminated ELF dynamic string: {path}")
                value += chunk
            return value.split(b"\0", 1)[0].decode()

        needed = [string(v) for t, v in entries if t == 1]
        runpath = [p for t, v in entries if t in (15, 29) for p in string(v).split(":") if p]
        return needed, runpath


def search_dirs(relative, runpath):
    origin = "/" + Path(relative).parent.as_posix()
    dirs = [posixpath.normpath(p.replace("${ORIGIN}", origin).replace("$ORIGIN", origin)).lstrip("/")
            for p in runpath]
    return dirs + [d for d in LIBRARY_DIRS if d not in dirs]


def resolve(tree, relative):
    """Follow symlinks inside a staged tree; absolute targets are relative to the tree."""
    for _ in range(40):
        path = tree / relative
        if not path.is_symlink():
            return relative if path.exists() else None
        target = os.readlink(path)
        if target.startswith("/"):
            relative = posixpath.normpath(target).lstrip("/")
        else:
            relative = posixpath.normpath(posixpath.join(posixpath.dirname(relative), target))
        if relative.startswith(".."):
            return None
    return None


def find_library(tree, soname, dirs):
    for directory in dirs:
        relative = f"{directory}/{soname}"
        if resolve(tree, relative):
            return relative
    return None


def elf_files(root):
    for path in walk(root):
        if path.is_file() and not path.is_symlink():
            dynamic = elf_dynamic(path)
            if dynamic is not None:
                yield path, dynamic


def missing_libraries(root):
    """(file, soname) pairs the staged root cannot satisfy by itself."""
    missing = []
    for path, (needed, runpath) in elf_files(root):
        relative = path.relative_to(root).as_posix()
        dirs = search_dirs(relative, runpath)
        missing += [(relative, soname) for soname in needed if not find_library(root, soname, dirs)]
    return missing


def resolve_closure(root, sources):
    """Copy every shared library the root's ELF files need from sources, in order.

    Returns the source trees that contributed files.
    """
    used, missing, inodes = [], {}, {}
    queue = [path for path, _ in elf_files(root)]
    while queue:
        path = queue.pop()
        needed, runpath = elf_dynamic(path)
        relative = path.relative_to(root).as_posix()
        dirs = search_dirs(relative, runpath)
        for soname in needed:
            if find_library(root, soname, dirs):
                continue
            for source in sources:
                found = find_library(source, soname, dirs)
                if found:
                    break
            else:
                missing.setdefault(soname, relative)
                continue
            real = resolve(source, found)
            copy_file(source / real, root / real, inodes)
            if real != found:
                link = root / found
                link.parent.mkdir(parents=True, exist_ok=True)
                if link.is_symlink():
                    link.unlink()
                link.symlink_to(posixpath.relpath(real, posixpath.dirname(found)))
            if source not in used:
                used.append(source)
            queue.append(root / real)
    if missing:
        detail = "; ".join(f"{soname} (needed by {by})" for soname, by in sorted(missing.items()))
        hint = ""
        if any(s.startswith(("libgssapi", "libkrb5", "libk5crypto", "libcom_err")) for s in missing):
            hint = "; Kerberos libraries are missing: remove WITHOUT_KERBEROS from config/src.conf and rerun make freebsd"
        raise RuntimeError("Missing shared libraries for the codex root: " + detail + hint)
    return used


def broken_links(root):
    return [path.relative_to(root).as_posix() for path in walk(root)
            if path.is_symlink() and not resolve(root, path.relative_to(root).as_posix())]


def loader_local_name(text):
    """The mfsroot name loader.conf.local selects. The loader appends .gz itself."""
    names = [line.split("=", 1)[1].strip().strip('"') for line in text.splitlines()
             if line.strip().startswith("mfsroot_name=")]
    if names != [LOADER_ROOT_NAME]:
        raise RuntimeError(f'loader.conf.local must set mfsroot_name="{LOADER_ROOT_NAME}" '
                           "(without .gz; the loader finds and decompresses the .gz file)")
    return names[0]


def tools():
    lines = (ROOT / "config/codex-tools.txt").read_text().splitlines()
    return [line.strip() for line in lines if line.strip() and not line.startswith("#")]


def link_rescue(root, world):
    copy(world / "rescue/dhclient-script", root / "rescue/dhclient-script")
    (root / "rescue/dhclient-script").chmod(0o555)
    (root / "sbin/dhclient-script").unlink(missing_ok=True)
    (root / "sbin/dhclient-script").symlink_to("../rescue/dhclient-script")
    for directory, names in RESCUE_LINKS.items():
        (root / directory).mkdir(parents=True, exist_ok=True)
        for name in names:
            if not (world / "rescue" / name).is_file():
                raise RuntimeError("Missing rescue applet in staged world: " + name)
            if not (root / "rescue" / name).exists():
                os.link(root / "rescue/rescue", root / "rescue" / name)
            target = posixpath.relpath(f"rescue/{name}", directory)
            link = root / directory / name
            if not (link.exists() or link.is_symlink()):
                link.symlink_to(target)


def stage_etc(root, world):
    for name in ETC_FILES:
        # Keep the shared root's SSH/DHCP users and password databases.
        if (root / name).exists():
            continue
        if not (world / name).exists():
            raise RuntimeError(f"Missing {name} in staged world; rerun make freebsd (installworld and distribution)")
        copy_file(world / name, root / name, {})
    # Hashed links in etc/ssl/certs point into usr/share/certs/trusted.
    for name in ("etc/ssl/certs", "usr/share/certs/trusted"):
        if (world / name).is_dir():
            copy_tree(world / name, root / name, {})
    terminfo = world / "usr/share/terminfo"
    for entry in TERMINFO:
        for sub in (entry[0], f"{ord(entry[0]):x}"):
            if (terminfo / sub / entry).is_file():
                copy_file(terminfo / sub / entry, root / "usr/share/terminfo" / sub / entry, {})
    if (world / "usr/share/locale/C.UTF-8").is_dir():
        copy_tree(world / "usr/share/locale/C.UTF-8", root / "usr/share/locale/C.UTF-8", {})
    (root / "etc/dhclient-enter-hooks").write_text(
        "# No resolvconf in this root: dhclient-script writes /etc/resolv.conf itself.\n"
        'resolvconf_enable="NO"\n')
    (root / "etc/switchbsd-build-epoch").write_text(f"{int(time.time())}\n")
    copy(ROOT / "config/rc.codex", root / "etc/rc.codex")
    (root / "etc/rc.codex").chmod(0o444)
    for name, mode in (("var/empty", 0o555), ("var/db", 0o755), ("var/run/dhclient", 0o755),
                       ("var/tmp", 0o1777)):
        (root / name).mkdir(parents=True, exist_ok=True)
        (root / name).chmod(mode)


def stage_codex_root(root, world, manifest):
    """Add packages, base tools, libraries and network configuration to a base root."""
    trees = extract_packages(manifest)
    roots = config()["packages"]
    inodes = {}
    for name in roots:
        copy_tree(trees[name], root, inodes, SKIP)
    world_inodes = {}
    for relative in tools():
        if not ((world / relative).is_file() or (world / relative).is_symlink()):
            raise RuntimeError(f"Missing base tool in staged world: {relative}; see config/codex-tools.txt")
        copy_file(world / relative, root / relative, world_inodes)
    link_rescue(root, world)
    (root / "usr/local/bin/switchbsd-net").symlink_to("../../../bin/switchbsd-net")
    # /root becomes tmpfs at boot; retain copies of the on-device guides.
    for name in ("DIAGNOSTICS.txt", "NETWORK.txt"):
        copy(root / "root" / name, root / "usr/share/switchbsd" / name)
    login = root / "etc/login.conf"
    login.write_text(login.read_text().replace(":path=/bin", ":path=/usr/local/bin /bin"))
    stage_etc(root, world)
    sources = [trees[name] for name in trees] + [world]
    used = resolve_closure(root, sources)
    names = roots + [name for name, tree in trees.items() if tree in used and name not in roots]
    for name in names:
        for licenses in sorted((trees[name] / "usr/local/share/licenses").glob("*")):
            copy_tree(licenses, root / "usr/local/share/licenses" / licenses.name, {})
    remaining = missing_libraries(root)
    if remaining:
        raise RuntimeError("Codex root still has unresolved libraries: " + ", ".join(f"{s} ({f})" for f, s in remaining))
    return names


def tree_size(root):
    seen, total = set(), 0
    for path in walk(root):
        if path.is_file() and not path.is_symlink():
            info = path.stat()
            if (info.st_dev, info.st_ino) not in seen:
                seen.add((info.st_dev, info.st_ino))
                total += info.st_size
    return total


def assemble_codex_root(sd, build_time=None):
    """Stage build/root-codex, write its UFS image and put the gzip copy on the SD bundle."""
    world = BUILD / "world"
    manifest = fetch_packages()
    if CODEX_ROOT.exists():
        shutil.rmtree(CODEX_ROOT)
    stage_base_root(CODEX_ROOT, build_time)
    names = stage_codex_root(CODEX_ROOT, world, manifest)
    # Headroom for UFS metadata; codex sessions and /tmp live on tmpfs instead.
    mib = 2 ** 20
    size = -(-int(tree_size(CODEX_ROOT) * 1.1 + 96 * mib) // (16 * mib)) * 16
    spec = BUILD / "rootfs-codex.mtree"
    spec.write_text(ownership_spec(CODEX_ROOT))
    run(["makefs", "-t", "ffs", "-B", "little", "-s", f"{size}m", "-o", "version=2", "-F", spec,
         CODEX_UFS, CODEX_ROOT], log="rootfs-codex.log")
    target = sd / "boot/rootfs-codex.ufs.gz"
    target.parent.mkdir(parents=True, exist_ok=True)
    with CODEX_UFS.open("rb") as source, target.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, compresslevel=9, mtime=0) as out:
            shutil.copyfileobj(source, out, 1 << 20)
    loader_local_name((ROOT / "config/loader.conf.local").read_text())
    copy(ROOT / "config/loader.conf.local", sd / "boot/loader.conf.local")
    licenses = sd / "switchbsd/licenses/packages"
    if licenses.exists():
        shutil.rmtree(licenses)
    copy_tree(CODEX_ROOT / "usr/local/share/licenses", licenses, {})
    packages = {p["name"]: p for p in manifest["packages"]}
    (BUILD / "root-codex.json").write_text(json.dumps(
        {"staged": names, "ufs_mib": size, "packages": manifest["packages"]}, indent=2) + "\n")
    print(f"Codex root: {size} MiB UFS, {target.stat().st_size // mib} MiB gzip; packages "
          + ", ".join(f'{n}-{packages[n]["version"]}' for n in names))
