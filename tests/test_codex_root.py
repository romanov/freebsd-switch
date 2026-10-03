"""Codex root staging: ELF dependency closure, link handling and configuration."""
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import codex_root
from codex_root import (elf_dynamic, search_dirs, resolve_closure, missing_libraries,
                        broken_links, copy_tree, loader_local_name)

ROOT = Path(__file__).resolve().parents[1]
VADDR = 0x200000


def elf(needed=(), runpath=None, dynamic=True):
    """A minimal AArch64 ELF image with one PT_LOAD and an optional PT_DYNAMIC."""
    strtab = b"\0"
    entries = []
    for name in needed:
        entries.append((1, len(strtab)))
        strtab += name.encode() + b"\0"
    if runpath:
        entries.append((29, len(strtab)))
        strtab += runpath.encode() + b"\0"
    count = 2 if dynamic else 1
    dyn_offset = 64 + 56 * count
    str_offset = dyn_offset + (len(entries) + 2) * 16
    entries += [(5, VADDR + str_offset), (0, 0)]
    dyn = b"".join(struct.pack("<qQ", tag, value) for tag, value in entries)
    size = str_offset + len(strtab)
    header = bytearray(64)
    header[:6] = b"\x7fELF\x02\x01"
    struct.pack_into("<H", header, 18, 183)
    struct.pack_into("<Q", header, 32, 64)
    struct.pack_into("<HH", header, 54, 56, count)
    headers = struct.pack("<IIQQQQQQ", 1, 5, 0, VADDR, VADDR, size, size, 0x1000)
    if not dynamic:
        return bytes(header) + headers
    headers += struct.pack("<IIQQQQQQ", 2, 6, dyn_offset, VADDR + dyn_offset,
                           VADDR + dyn_offset, len(dyn), len(dyn), 8)
    return bytes(header) + headers + dyn + strtab


def put(path, data=b""):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def link(path, target):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.symlink_to(target)


class ElfDynamic(unittest.TestCase):
    def test_needed_and_runpath(self):
        with tempfile.TemporaryDirectory() as d:
            path = put(Path(d) / "app", elf(["libfoo.so.1", "libc.so.7"], "$ORIGIN/../lib/extra"))
            self.assertEqual(elf_dynamic(path), (["libfoo.so.1", "libc.so.7"], ["$ORIGIN/../lib/extra"]))

    def test_static_and_non_elf(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(elf_dynamic(put(Path(d) / "init", elf(dynamic=False))), ([], []))
            self.assertIsNone(elf_dynamic(put(Path(d) / "script", b"#!/bin/sh\n")))

    def test_origin_runpath_searched_first(self):
        self.assertEqual(search_dirs("usr/local/bin/app", ["$ORIGIN/../lib/extra"]),
                         ["usr/local/lib/extra", "lib", "usr/lib", "usr/local/lib", "lib/casper"])


class Closure(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        base = Path(self.temp.name)
        self.root, self.pkg, self.world = base / "root", base / "pkg", base / "world"
        put(self.root / "usr/local/bin/app", elf(["libfoo.so.1", "libc.so.7"]))
        put(self.pkg / "usr/local/lib/libfoo.so.1.2", elf(["libc.so.7"]))
        link(self.pkg / "usr/local/lib/libfoo.so.1", "libfoo.so.1.2")
        put(self.world / "lib/libc.so.7", elf())
        # A decoy: package libraries win over base ones with the same soname.
        put(self.world / "usr/lib/libfoo.so.1", elf(["libmissing.so.9"]))

    def tearDown(self):
        self.temp.cleanup()

    def test_closure_prefers_packages_and_keeps_soname_links(self):
        used = resolve_closure(self.root, [self.pkg, self.world])
        self.assertEqual(used, [self.pkg, self.world])
        lib = self.root / "usr/local/lib"
        self.assertTrue((lib / "libfoo.so.1.2").is_file())
        self.assertEqual(os.readlink(lib / "libfoo.so.1"), "libfoo.so.1.2")
        self.assertTrue((self.root / "lib/libc.so.7").is_file())
        self.assertFalse((self.root / "usr/lib/libfoo.so.1").exists())
        self.assertEqual(missing_libraries(self.root), [])
        self.assertEqual(broken_links(self.root), [])

    def test_library_already_in_root_is_not_copied(self):
        put(self.root / "usr/local/lib/libfoo.so.1", elf())
        put(self.root / "lib/libc.so.7", elf())
        self.assertEqual(resolve_closure(self.root, [self.pkg, self.world]), [])
        self.assertFalse((self.root / "usr/local/lib/libfoo.so.1.2").exists())

    def test_missing_kerberos_library_is_actionable(self):
        put(self.root / "usr/local/libexec/git-core/git-remote-https", elf(["libgssapi_krb5.so.122"]))
        with self.assertRaisesRegex(RuntimeError, r"libgssapi_krb5\.so\.122 \(needed by "
                                    r"usr/local/libexec/git-core/git-remote-https\).*WITHOUT_KERBEROS"):
            resolve_closure(self.root, [self.pkg, self.world])

    def test_unresolved_library_reported(self):
        self.assertEqual(missing_libraries(self.root),
                         [("usr/local/bin/app", "libfoo.so.1"), ("usr/local/bin/app", "libc.so.7")])

    def test_absolute_link_resolves_inside_tree(self):
        link(self.root / "etc/ssl/certs/abcd.0", "/usr/share/certs/trusted/ca.pem")
        self.assertEqual(broken_links(self.root), ["etc/ssl/certs/abcd.0"])
        put(self.root / "usr/share/certs/trusted/ca.pem", b"PEM")
        self.assertEqual(broken_links(self.root), [])


class CopyTree(unittest.TestCase):
    def test_codex_rescue_aliases_preserve_network_programs(self):
        with tempfile.TemporaryDirectory() as d:
            world, root = Path(d) / "world", Path(d) / "root"
            put(root / "rescue/rescue", elf(dynamic=False))
            put(root / "sbin/dhclient-script", b"old script")
            put(world / "rescue/dhclient-script", b"new script")
            for names in codex_root.RESCUE_LINKS.values():
                for name in names:
                    put(world / "rescue" / name)
            network_chown = elf(["libc.so.7"])
            put(root / "usr/sbin/chown", network_chown)
            codex_root.link_rescue(root, world)
            self.assertFalse((root / "usr/sbin/chown").is_symlink())
            self.assertEqual((root / "usr/sbin/chown").read_bytes(), network_chown)
            self.assertEqual((root / "sbin/dhclient-script").read_bytes(), b"new script")
            self.assertEqual(broken_links(root), [])

    def test_hard_links_kept_and_docs_skipped(self):
        with tempfile.TemporaryDirectory() as d:
            source, target = Path(d) / "pkg", Path(d) / "root"
            git = put(source / "usr/local/bin/git", b"git")
            add = source / "usr/local/libexec/git-core/git-add"
            add.parent.mkdir(parents=True)
            os.link(git, add)
            link(source / "usr/local/bin/git-shell", "git")
            put(source / "usr/local/share/man/man1/git.1", b"man")
            copy_tree(source, target, {}, codex_root.SKIP)
            self.assertEqual((target / "usr/local/bin/git").stat().st_ino,
                             (target / "usr/local/libexec/git-core/git-add").stat().st_ino)
            self.assertEqual(os.readlink(target / "usr/local/bin/git-shell"), "git")
            self.assertFalse((target / "usr/local/share/man").exists())


class ValidateCodexRoot(unittest.TestCase):
    def test_self_contained_root_and_gzip_copy(self):
        import gzip
        import validate
        with tempfile.TemporaryDirectory() as d:
            root, sd, ufs = Path(d) / "root", Path(d) / "sd", Path(d) / "rootfs-codex.ufs"
            for name in codex_root.CODEX_REQUIRED:
                if name.startswith("var/"):
                    (root / name).mkdir(parents=True)
                else:
                    put(root / name, elf(["libc.so.7"]) if name.startswith("usr/local/bin/") else b"x")
            ufs.write_bytes(b"ufs image")
            put(sd / "boot/rootfs-codex.ufs.gz", gzip.compress(b"ufs image"))
            put(sd / "boot/loader.conf.local", (ROOT / "config/loader.conf.local").read_bytes())
            with self.assertRaisesRegex(RuntimeError, r"unresolved libraries: libc\.so\.7"):
                validate.codex(sd, root, ufs)
            put(root / "lib/libc.so.7", elf())
            validate.codex(sd, root, ufs)
            link(root / "usr/local/lib/libgone.so.1", "libgone.so.1.0")
            with self.assertRaisesRegex(RuntimeError, "broken symlinks: usr/local/lib/libgone.so.1"):
                validate.codex(sd, root, ufs)
            (root / "usr/local/lib/libgone.so.1").unlink()
            ufs.write_bytes(b"changed")
            with self.assertRaisesRegex(RuntimeError, "does not decompress"):
                validate.codex(sd, root, ufs)


class Configuration(unittest.TestCase):
    def test_loader_local_selects_codex_root_without_gz(self):
        text = (ROOT / "config/loader.conf.local").read_text()
        self.assertEqual(loader_local_name(text), "/boot/rootfs-codex.ufs")
        for bad in ('mfsroot_name="/boot/rootfs-codex.ufs.gz"\n', "# nothing selected\n"):
            with self.assertRaisesRegex(RuntimeError, "without .gz"):
                loader_local_name(bad)

    def test_package_roots(self):
        cfg = codex_root.config()
        self.assertEqual(cfg["abi"], "FreeBSD:15:aarch64")
        for name in ("codex", "bash", "ripgrep", "git-lite"):
            self.assertIn(name, cfg["packages"])

    def test_rescue_links_and_base_tools_do_not_collide(self):
        rescue = [f"{d}/{n}" for d, names in codex_root.RESCUE_LINKS.items() for n in names]
        tools = codex_root.tools()
        self.assertEqual(len(rescue), len(set(rescue)))
        self.assertEqual(len(tools), len(set(tools)))
        self.assertEqual(set(rescue) & set(tools), set())
        self.assertIn("libexec/ld-elf.so.1", tools)

    def test_git_https_keeps_kerberos_and_kernel_has_usb_nics(self):
        self.assertNotIn("WITHOUT_KERBEROS", (ROOT / "config/src.conf").read_text())
        kernel = (ROOT / "config/SWITCHDIAG").read_text()
        for device in ("axge", "axe", "cdce", "urndis", "ipheth"):
            self.assertRegex(kernel, rf"(?m)^device\s+{device}\b")


def shell():
    for candidate in ("/bin/sh", r"C:\msys64\usr\bin\dash.exe"):
        if os.path.isfile(candidate):
            return candidate
    return None


@unittest.skipUnless(shell(), "no POSIX shell to syntax-check with")
class Scripts(unittest.TestCase):
    def test_shell_syntax(self):
        with tempfile.TemporaryDirectory() as d:
            for name in ("rc", "rc.codex", "switchbsd-net"):
                # The Windows checkout has CRLF files; the image is built from LF.
                copy = Path(d) / name
                copy.write_text((ROOT / "config" / name).read_text(), newline="\n")
                result = subprocess.run([shell(), "-n", str(copy)], capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, f"{name}: {result.stderr}")


if __name__ == "__main__":
    unittest.main()
