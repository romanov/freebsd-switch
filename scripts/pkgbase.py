#!/usr/bin/env python3
"""Host-built pkgbase repository for the USB-stick root, and a LAN server for it.

The repository is built from the same pinned source and object tree as the
SD card's kernel, so `pkg upgrade` on the Switch never runs userland ahead of
the kernel it booted. Kernel packages are built too, but the stick never
installs them: the loader reads the kernel from the SD card.
"""
import os
import subprocess
import sys
from build import ROOT, BUILD, run, require, freebsd_make

REPO = BUILD / "pkgbase-repo"
KEY = BUILD / "pkgbase/signing.key"
ABI = "FreeBSD:15:aarch64"
REPO_NAME = "SwitchBSD"
# The stick's copy of the public key; pkg checks every catalogue against it.
STICK_PUBKEY = "usr/share/keys/switchbsd/pkgbase.pub"
URL_PLACEHOLDER = "http://BUILD-HOST:8080"


def signing_key():
    """RSA key that signs the repository catalogue: made once, then reused.

    Replacing it means every stick needs the new public key, so the build
    never rotates it on its own.
    """
    if not KEY.is_file():
        require(["openssl"])
        KEY.parent.mkdir(parents=True, exist_ok=True)
        run(["openssl", "genrsa", "-out", KEY, "4096"], log="pkgbase-key.log")
        KEY.chmod(0o600)
    public = KEY.with_suffix(".pub")
    if not public.is_file():
        run(["openssl", "rsa", "-in", KEY, "-pubout", "-out", public], log="pkgbase-key.log")
    return KEY, public


def repo_dir():
    """The newest package set; `make packages` points `latest` at it."""
    return REPO / ABI / "latest"


def build_repo():
    """Package the staged world and kernel after make freebsd.

    update-packages keeps the version of every package whose contents did not
    change, so the Switch only sees upgrades for real source changes.
    """
    require(["make", "pkg"])
    if not (BUILD / "world/boot/kernel/kernel").is_file():
        raise RuntimeError("FreeBSD world missing; run make freebsd before make pkgbase")
    key, _ = signing_key()
    args, env = freebsd_make()
    run(args + ["update-packages", "KERNCONF=SWITCHDIAG", "NO_MODULES=yes",
                f"REPODIR={REPO}", f"PKG_REPO_SIGNING_KEY={key}"],
        env=env, log="pkgbase.log")
    if not (repo_dir() / "meta.conf").is_file():
        raise RuntimeError(f"No package repository at {repo_dir()}; see logs/pkgbase.log")
    print("pkgbase repository:", repo_dir())


def repo_names(directory=None):
    """Package names in a repository directory, from its .pkg file names."""
    names = set()
    for _, _, files in os.walk(directory or repo_dir()):
        for name in files:
            if name.endswith(".pkg") and name.startswith("FreeBSD-"):
                # FreeBSD-runtime-15.1p0.pkg -> FreeBSD-runtime
                names.add(name[:-4].rsplit("-", 1)[0])
    return names


def repo_conf(url, pubkey="/" + STICK_PUBKEY):
    """pkg repository configuration for the host-built base packages.

    The official FreeBSD-base repository stays disabled: base packages must
    come from the build that made the SD card's kernel.
    """
    url = url.rstrip("/")
    return (f'{REPO_NAME}: {{\n'
            f'  url: "{url}/${{ABI}}/latest",\n'
            '  signature_type: "pubkey",\n'
            f'  pubkey: "{pubkey}",\n'
            '  enabled: yes,\n'
            '  priority: 10\n'
            '}\n'
            'FreeBSD-base: {\n  enabled: no\n}\n')


def serve():
    """Serve the repository on the LAN until interrupted."""
    if not repo_dir().is_dir():
        raise RuntimeError("No pkgbase repository; run make pkgbase")
    port = os.environ.get("PKGBASE_PORT", "8080")
    bind = os.environ.get("PKGBASE_BIND", "0.0.0.0")
    print(f"Serving {REPO} on http://{bind}:{port}/ (Ctrl-C stops).")
    print(f"On the Switch: switchbsd-update --repo http://<this host's address>:{port}")
    try:
        subprocess.run([sys.executable, "-m", "http.server", "--bind", bind, "--directory", str(REPO),
                        port], cwd=ROOT, check=True)
    except KeyboardInterrupt:
        pass
