"""USB-stick root: loader selection, package choice, image spec, GPT layout and the update script."""
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
import uuid
import zlib

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import pkgbase
import usb_root
from build import USBROOT_UPDATE_FILES
from validate import gpt_partitions, has_kernel_payload, usb_stick, FREEBSD_UFS

ROOT = Path(__file__).resolve().parents[1]
UPDATE = ROOT / "config/switchbsd-update"


class LoaderSelection(unittest.TestCase):
    def test_shipped_file_selects_stick_with_ram_fallback(self):
        roots = usb_root.check_loader_local(usb_root.LOADER_LOCAL.read_text())
        self.assertEqual(roots, ["ufs:/dev/gpt/switchroot", "ufs:/dev/md0"])
        settings = usb_root.loader_settings(usb_root.LOADER_LOCAL.read_text())
        self.assertEqual(settings["mfsroot_name"], "/boot/rootfs.ufs")

    def test_rejects_missing_fallback_wrong_order_and_read_write(self):
        good = usb_root.LOADER_LOCAL.read_text()
        for bad in (good.replace(" ufs:/dev/md0", ""),
                    good.replace('"ufs:/dev/gpt/switchroot ufs:/dev/md0"',
                                 '"ufs:/dev/md0 ufs:/dev/gpt/switchroot"'),
                    good.replace('options="ro"', 'options="rw"'),
                    good.replace("/boot/rootfs.ufs", "/boot/rootfs-codex.ufs")):
            with self.assertRaisesRegex(RuntimeError, "switchroot"):
                usb_root.check_loader_local(bad)

    def test_update_zip_installs_the_selection_and_never_network_settings(self):
        self.assertEqual(USBROOT_UPDATE_FILES["boot/loader.conf.local"],
                         "switchbsd/usbroot/loader.conf.local")
        self.assertIn("boot/kernel/kernel", USBROOT_UPDATE_FILES)
        self.assertIn("boot/rootfs.ufs", USBROOT_UPDATE_FILES)
        self.assertFalse(any(name.startswith("boot/loader.conf.d/") for name in USBROOT_UPDATE_FILES))


class Packages(unittest.TestCase):
    def test_config_never_installs_a_kernel(self):
        cfg = usb_root.config()
        self.assertFalse(any(name.startswith("FreeBSD-kernel") for name in cfg["base"]))
        self.assertIn("pkg", cfg["ports"]["packages"])
        self.assertIn("codex", cfg["ports"]["packages"])

    def test_kernel_and_unknown_packages_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "kernel packages"):
            usb_root.base_packages({"base": ["FreeBSD-runtime", "FreeBSD-kernel-generic"]},
                                   {"FreeBSD-runtime", "FreeBSD-kernel-generic"})
        with self.assertRaisesRegex(RuntimeError, "FreeBSD-nope"):
            usb_root.base_packages({"base": ["FreeBSD-runtime", "FreeBSD-nope"]}, {"FreeBSD-runtime"})
        self.assertEqual(usb_root.base_packages({"base": ["FreeBSD-runtime"]}, {"FreeBSD-runtime"}),
                         ["FreeBSD-runtime"])

    def test_repo_names_from_package_files(self):
        with tempfile.TemporaryDirectory() as d:
            for name in ("FreeBSD-runtime-15.1p0.pkg", "FreeBSD-set-minimal-15.1p0.pkg",
                         "FreeBSD-ssh-dbg-15.1p0.pkg", "meta.conf", "packagesite.pkg"):
                Path(d, name).write_bytes(b"")
            self.assertEqual(pkgbase.repo_names(d),
                             {"FreeBSD-runtime", "FreeBSD-set-minimal", "FreeBSD-ssh-dbg"})


class RepositoryConfig(unittest.TestCase):
    def test_switch_repo_is_signed_and_official_base_disabled(self):
        text = pkgbase.repo_conf("http://192.0.2.5:8080/")
        self.assertIn('url: "http://192.0.2.5:8080/${ABI}/latest"', text)
        self.assertIn('signature_type: "pubkey"', text)
        self.assertIn(f'pubkey: "/{pkgbase.STICK_PUBKEY}"', text)
        self.assertIn("FreeBSD-base: {\n  enabled: no\n}", text)

    def test_build_time_conf_reads_local_repositories_only(self):
        with tempfile.TemporaryDirectory() as d:
            conf = usb_root.private_pkg_conf(Path(d), Path("/repo/base"), Path("/keys/pub"),
                                             Path("/repo/ports"))
            repos = (Path(d) / "repos/repos.conf").read_text()
            self.assertIn('url: "file:///repo/base"', repos)
            self.assertIn('url: "file:///repo/ports"', repos)
            self.assertNotIn("https://", repos)
            self.assertIn('ABI = "FreeBSD:15:aarch64"', conf.read_text())
            self.assertNotIn("PKG_DBDIR", conf.read_text())


class Spec(unittest.TestCase):
    OURS = ("#mtree 2.0\n"
            ". type=dir uid=0 gid=0 mode=0755\n"
            "./bin type=dir uid=0 gid=0 mode=0755\n"
            "./bin/sh type=file uid=0 gid=0 mode=0644\n"
            "./etc type=dir uid=0 gid=0 mode=0755\n"
            "./etc/rc.conf type=file uid=0 gid=0 mode=0644\n"
            "./etc/ssh type=dir uid=0 gid=0 mode=0755\n"
            "./etc/ssh/sshd_config type=file uid=0 gid=0 mode=0644\n"
            "./usr/bin/su type=file uid=0 gid=0 mode=0755\n"
            "./usr/lib/libx.so type=link uid=0 gid=0 mode=0755 link=libx.so.1\n")
    METALOG = ("#mtree 2.0\n"
               "./bin/sh type=file uname=root gname=wheel mode=0555 size=123 sha256digest=abc\n"
               "etc/ssh/sshd_config type=file uname=root gname=wheel mode=0644 sha256digest=def\n"
               "./usr/bin/su type=file uname=root gname=wheel mode=04555 flags=schg\n"
               "./usr/lib/libx.so type=file uname=root gname=wheel mode=0444\n"
               "./usr/bin/removed type=file uname=root gname=wheel mode=0555\n")

    def test_package_attributes_kept_and_added_files_root_owned(self):
        spec = usb_root.merge_spec(self.METALOG, self.OURS).splitlines()
        self.assertEqual(spec[0], "#mtree 2.0")
        lines = {line.split()[0]: line for line in spec[1:]}
        self.assertEqual(lines["./bin/sh"], "./bin/sh type=file uname=root gname=wheel mode=0555")
        self.assertEqual(lines["./usr/bin/su"],
                         "./usr/bin/su type=file uname=root gname=wheel mode=04555 flags=schg")
        # A file the build replaced keeps pkg's owner but no stale digest.
        self.assertEqual(lines["./etc/ssh/sshd_config"],
                         "./etc/ssh/sshd_config type=file uname=root gname=wheel mode=0644")
        self.assertEqual(lines["./etc/rc.conf"], "./etc/rc.conf type=file uid=0 gid=0 mode=0644")
        # The tree decides the node type.
        self.assertEqual(lines["./usr/lib/libx.so"],
                         "./usr/lib/libx.so type=link uname=root gname=wheel mode=0444 link=libx.so.1")
        self.assertNotIn("./usr/bin/removed", lines)
        self.assertEqual(len(lines), 9)

    def test_without_metalog_spec_is_ownership_spec(self):
        self.assertEqual(usb_root.merge_spec("", self.OURS), self.OURS)


def gpt_image(path, partitions, entries=128):
    """A GPT disk image: protective MBR, header at LBA 1, entries from LBA 2."""
    table = bytearray(entries * 128)
    for index, (kind, name, first, last, data) in enumerate(partitions):
        entry = kind.bytes_le + uuid.uuid4().bytes_le + struct.pack("<QQQ", first, last, 0)
        entry += name.encode("utf-16-le").ljust(72, b"\0")
        table[index * 128:(index + 1) * 128] = entry
    header = bytearray(92)
    header[:8] = b"EFI PART"
    struct.pack_into("<IIIIQQQQ16sQIII", header, 8, 0x10000, 92, 0, 0, 1, 0, 34, 0,
                     uuid.uuid4().bytes_le, 2, entries, 128, zlib.crc32(table))
    end = max([last for _, _, _, last, _ in partitions] + [34]) + 1
    with open(path, "wb") as stream:
        stream.truncate(end * 512)
        stream.seek(512)
        stream.write(header)
        stream.seek(1024)
        stream.write(table)
        for _, _, first, _, data in partitions:
            stream.seek(first * 512)
            stream.write(data)


class StickImage(unittest.TestCase):
    def test_labelled_ufs_partition_with_matching_contents(self):
        with tempfile.TemporaryDirectory() as d:
            ufs = Path(d) / "root.ufs"
            ufs.write_bytes(os.urandom(4096))
            image = Path(d) / "stick.img"
            gpt_image(image, [(FREEBSD_UFS, "switchroot", 40, 47, ufs.read_bytes())])
            with image.open("rb") as stream:
                self.assertEqual(gpt_partitions(stream), [(FREEBSD_UFS, "switchroot", 40, 47)])
            usb_stick(image, ufs)

    def test_wrong_label_type_or_contents_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            ufs = Path(d) / "root.ufs"
            ufs.write_bytes(os.urandom(4096))
            image = Path(d) / "stick.img"
            gpt_image(image, [(FREEBSD_UFS, "rootfs", 40, 47, ufs.read_bytes())])
            with self.assertRaisesRegex(RuntimeError, "labelled switchroot"):
                usb_stick(image, ufs)
            efi = uuid.UUID("c12a7328-f81f-11d2-ba4b-00a0c93ec93b")
            gpt_image(image, [(efi, "switchroot", 40, 47, ufs.read_bytes())])
            with self.assertRaisesRegex(RuntimeError, "freebsd-ufs"):
                usb_stick(image, ufs)
            gpt_image(image, [(FREEBSD_UFS, "switchroot", 40, 47, os.urandom(4096))])
            with self.assertRaisesRegex(RuntimeError, "does not contain"):
                usb_stick(image, ufs)
            gpt_image(image, [(FREEBSD_UFS, "switchroot", 40, 43, ufs.read_bytes()[:2048])])
            with self.assertRaisesRegex(RuntimeError, "smaller"):
                usb_stick(image, ufs)

    def test_not_gpt_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            image = Path(d) / "mbr.img"
            image.write_bytes(b"\0" * 4096)
            with self.assertRaisesRegex(RuntimeError, "GPT"):
                usb_stick(image, image)


class StickFiles(unittest.TestCase):
    def test_empty_kernel_directory_is_not_a_kernel_payload(self):
        with tempfile.TemporaryDirectory() as d:
            boot = Path(d)
            kernel = boot / "kernel"
            kernel.mkdir()
            self.assertFalse(has_kernel_payload(boot))
            (kernel / "kernel").write_bytes(b"kernel")
            self.assertTrue(has_kernel_payload(boot))

    def test_console_gettytab_entry(self):
        self.assertTrue(usb_root.has_root_console_autologin(usb_root.CONSOLE_GETTY_ENTRY))
        self.assertFalse(usb_root.has_root_console_autologin(
            usb_root.CONSOLE_GETTY_ENTRY.replace(":nc:", ":")))

    def test_console_autologin_and_fstab(self):
        self.assertIn(f'"/usr/libexec/getty {usb_root.CONSOLE_GETTY}"', usb_root.TTYS)
        self.assertTrue(usb_root.TTYS.splitlines()[-1].startswith("console\t"))
        self.assertEqual(usb_root.FSTAB.split("\t")[:3], ["/dev/gpt/switchroot", "/", "ufs"])

    def test_overlay_sources_exist_and_rc_script_enabled_by_default(self):
        for source, target, mode in usb_root.OVERLAY:
            self.assertTrue((ROOT / source).is_file(), source)
        rc = (ROOT / "config/rc.d/switchbsd").read_text()
        self.assertIn("# PROVIDE: switchbsd", rc)
        self.assertIn(': ${switchbsd_enable:="YES"}', rc)
        self.assertIn("SWITCHBSD: USERLAND_READY", rc)
        rc_conf = (ROOT / "config/usbroot-rc.conf").read_text()
        self.assertIn('growfs_enable="YES"', rc_conf)
        self.assertIn('sshd_enable="NO"', rc_conf)

    def test_sshd_reads_hand_and_sd_card_keys(self):
        config = (ROOT / "config/sshd_config").read_text()
        self.assertIn("AuthorizedKeysFile .ssh/authorized_keys .ssh/authorized_keys.switchbsd", config)


class UpdateScript(unittest.TestCase):
    """switchbsd-update against fake pkg and freebsd-version commands."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        base = Path(self.temporary.name)
        self.bin, self.log = base / "bin", base / "log"
        self.bin.mkdir()
        self.log.write_text("")
        self.conf = base / "SwitchBSD.conf"
        self.conf.write_text(pkgbase.repo_conf(pkgbase.URL_PLACEHOLDER), newline="\n")
        self.fake("pkg", 'echo "pkg $*" >> "$MOCK_LOG"\n'
                         'case "$*" in "update -f -r FreeBSD") exit "${MOCK_PORTS_FAIL:-0}" ;;\n'
                         '"upgrade -U -n") [ -n "${MOCK_DRY_ERROR:-}" ] && echo "$MOCK_DRY_ERROR" >&2; '
                         'exit "${MOCK_DRY_STATUS:-0}" ;; esac\n')
        self.fake("freebsd-version", 'case $1 in -u) echo "${MOCK_USERLAND:-15.1-RELEASE}" ;;\n'
                                     '-r) echo 15.1-RELEASE ;; esac\n')

    def tearDown(self):
        self.temporary.cleanup()

    def fake(self, name, body):
        path = self.bin / name
        path.write_text("#!/bin/sh\n" + body, newline="\n")
        path.chmod(0o755)

    def run_update(self, *args, **env):
        environment = dict(os.environ, PATH=os.pathsep.join((str(self.bin), os.environ.get("PATH", ""))),
                           SWITCHBSD_UPDATE_CONF=self.conf.as_posix(), MOCK_LOG=self.log.as_posix(), **env)
        return subprocess.run(["/bin/sh", str(UPDATE), *args], env=environment,
                              capture_output=True, text=True, timeout=20)

    def calls(self):
        return self.log.read_text().splitlines()

    def test_placeholder_refuses_without_running_pkg(self):
        result = self.run_update()
        self.assertEqual(result.returncode, 1)
        self.assertIn("switchbsd-update --repo", result.stderr)
        self.assertEqual(self.calls(), [])

    def test_repo_is_remembered_then_base_and_tools_upgraded(self):
        result = self.run_update("--repo", "http://192.0.2.5:8080/")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('url: "http://192.0.2.5:8080/${ABI}/latest"', self.conf.read_text())
        self.assertIn("FreeBSD-base", self.conf.read_text())
        self.assertEqual(self.calls(), ["pkg update -f -r SwitchBSD", "pkg update -f -r FreeBSD",
                                        "pkg upgrade -U -y"])
        self.assertNotIn("SD card", result.stdout)
        # Later runs reuse the saved address.
        self.log.write_text("")
        self.assertEqual(self.run_update("-n").returncode, 0)
        self.assertEqual(self.calls()[-1], "pkg upgrade -U -n")

    def test_unreachable_tool_repository_still_updates_base(self):
        self.run_update("--repo", "http://192.0.2.5:8080", "-n")
        self.log.write_text("")
        result = self.run_update(MOCK_PORTS_FAIL="1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("unreachable", result.stdout)
        self.assertEqual(self.calls()[-1], "pkg upgrade -U -y")

    def test_empty_dry_run_is_success_but_pkg_error_is_not(self):
        self.run_update("--repo", "http://192.0.2.5:8080", "-n")
        self.assertEqual(self.run_update("-n", MOCK_DRY_STATUS="1").returncode, 0)
        failure = self.run_update("-n", MOCK_DRY_STATUS="1", MOCK_DRY_ERROR="pkg: broken catalogue")
        self.assertEqual(failure.returncode, 1)
        self.assertIn("broken catalogue", failure.stderr)

    def test_kernel_mismatch_warns_to_copy_the_kernel(self):
        self.run_update("--repo", "http://192.0.2.5:8080", "-n")
        result = self.run_update(MOCK_USERLAND="15.1-RELEASE-p1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("running kernel is 15.1-RELEASE", result.stdout)
        self.assertIn("boot/kernel/kernel", result.stdout)

    def test_bad_urls_and_arguments_rejected(self):
        for args in (("--repo", "ftp://host"), ("--repo", 'http://host/"x'), ("--repo",), ("--bogus",)):
            result = self.run_update(*args)
            self.assertEqual(result.returncode, 2, args)
        self.assertIn(pkgbase.URL_PLACEHOLDER, self.conf.read_text())
        self.assertEqual(self.calls(), [])


if __name__ == "__main__":
    unittest.main()
