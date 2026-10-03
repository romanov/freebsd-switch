import hashlib
import os
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from build import (check_hash, require, elf_dynamic, library_closure, ownership_spec,
                   ssh_fingerprint)
from validate import elf_aarch64, pe_aarch64, nonoverlap, firmware_sizes, sd_files


def dynamic_elf(interpreter, needed):
    """AArch64 ELF64 with PT_INTERP, PT_LOAD and a PT_DYNAMIC naming libraries."""
    strtab = b"\0" + b"".join(name.encode() + b"\0" for name in needed)
    interp = interpreter.encode() + b"\0"
    interp_at, strtab_at = 64 + 3 * 56, 512
    dynamic_at, vaddr = strtab_at + len(strtab) + 7 & ~7, 0x10000
    offsets, position = [], 1
    for name in needed:
        offsets.append(position)
        position += len(name) + 1
    dynamic = b"".join(struct.pack("<qQ", 1, o) for o in offsets)
    dynamic += struct.pack("<qQ", 5, vaddr + strtab_at) + struct.pack("<qQ", 0, 0)
    size = dynamic_at + len(dynamic)
    data = bytearray(size)
    data[:6] = b"\x7fELF\x02\x01"
    struct.pack_into("<H", data, 18, 183)
    struct.pack_into("<Q", data, 32, 64)
    struct.pack_into("<HH", data, 54, 56, 3)
    for index, (kind, offset, address, length) in enumerate((
            (3, interp_at, vaddr + interp_at, len(interp)), (1, 0, vaddr, size),
            (2, dynamic_at, vaddr + dynamic_at, len(dynamic)))):
        struct.pack_into("<IIQQQQQQ", data, 64 + index * 56, kind, 4, offset, address, address,
                         length, length, 8)
    data[interp_at:interp_at + len(interp)] = interp
    data[strtab_at:strtab_at + len(strtab)] = strtab
    data[dynamic_at:] = dynamic
    return bytes(data)


class ArtifactValidation(unittest.TestCase):
    def test_oversized_payload_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            rom, payload, bootblock = (Path(d) / n for n in ("rom", "payload", "bootblock"))
            rom.write_bytes(b"\0" * 0xa00000)
            payload.write_bytes(b"\0" * 0x30001)
            bootblock.write_bytes(b"\0" * 16)
            with self.assertRaisesRegex(RuntimeError, "payload limit"):
                firmware_sizes(rom, payload, bootblock)

    def test_wrong_rom_size_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            rom, payload, bootblock = (Path(d) / n for n in ("rom", "payload", "bootblock"))
            for p in (rom, payload, bootblock):
                p.write_bytes(b"\0" * 16)
            with self.assertRaisesRegex(RuntimeError, "10 MiB"):
                firmware_sizes(rom, payload, bootblock)

    def test_incomplete_sd_bundle_is_actionable(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaisesRegex(RuntimeError, "Incomplete SD bundle: EFI/BOOT/BOOTAA64.EFI"):
                sd_files(Path(d))

    def test_corrupt_download_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "archive"
            p.write_bytes(b"corrupt")
            with self.assertRaisesRegex(RuntimeError, "SHA-256 mismatch"):
                check_hash(p, hashlib.sha256(b"expected").hexdigest())

    def test_missing_tool_is_actionable(self):
        with patch("shutil.which", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "Missing commands: aarch64-none-elf-gcc"):
                require(["aarch64-none-elf-gcc"])

    def test_handoff_cannot_overlap_bootblock(self):
        with self.assertRaisesRegex(RuntimeError, "overlap"):
            nonoverlap([(0x40010000, 0x7000, "bootblock"), (0x40016000, 20, "handoff")])
        nonoverlap([(0x40010000, 0x7000, "bootblock"), (0x40017000, 20, "handoff")])

    def test_wrong_architecture_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "program"
            data = bytearray(64)
            data[:6] = b"\x7fELF\x02\x01"
            struct.pack_into("<H", data, 18, 62)
            p.write_bytes(data)
            with self.assertRaisesRegex(RuntimeError, "AArch64"):
                elf_aarch64(p)

    def test_ram_root_cannot_need_dynamic_linker(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "init"
            data = bytearray(120)
            data[:6] = b"\x7fELF\x02\x01"
            struct.pack_into("<H", data, 18, 183)
            struct.pack_into("<Q", data, 32, 64)
            struct.pack_into("<HH", data, 54, 56, 1)
            struct.pack_into("<I", data, 64, 3)
            p.write_bytes(data)
            with self.assertRaisesRegex(RuntimeError, "dynamic interpreter"):
                elf_aarch64(p, static=True)

    def test_truncated_pe_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "BOOTAA64.EFI"
            data = bytearray(64)
            data[:2] = b"MZ"
            struct.pack_into("<I", data, 60, 0xffff)
            p.write_bytes(data)
            with self.assertRaisesRegex(RuntimeError, "AArch64 PE"):
                pe_aarch64(p)

    def test_dynamic_program_dependencies_are_read(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "sshd"
            p.write_bytes(dynamic_elf("/libexec/ld-elf.so.1", ["libssh.so.5", "libc.so.7"]))
            self.assertEqual(elf_dynamic(p), ("/libexec/ld-elf.so.1", ["libssh.so.5", "libc.so.7"]))
            elf_aarch64(p)
            p.write_bytes(p.read_bytes()[:300])
            with self.assertRaisesRegex(RuntimeError, "malformed ELF dynamic section"):
                elf_dynamic(p)

    def test_library_closure_follows_dependencies_and_reports_missing(self):
        with tempfile.TemporaryDirectory() as d:
            world = Path(d)
            for name, needed in (("usr/sbin/sshd", ["libssh.so.5", "libc.so.7"]),
                                 ("usr/lib/libssh.so.5", ["libc.so.7"]), ("lib/libc.so.7", [])):
                (world / name).parent.mkdir(parents=True, exist_ok=True)
                (world / name).write_bytes(dynamic_elf("/libexec/ld-elf.so.1", needed))
            self.assertEqual(library_closure(world, ["usr/sbin/sshd"]),
                             ["lib/libc.so.7", "usr/lib/libssh.so.5"])
            (world / "lib/libc.so.7").unlink()
            with self.assertRaisesRegex(RuntimeError, "libc.so.7 needed by usr/sbin/sshd"):
                library_closure(world, ["usr/sbin/sshd"])

    def test_ownership_spec_makes_every_node_root_owned(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "rescue").mkdir()
            (root / "rescue/sh").write_bytes(b"sh")
            (root / "rescue/sh").chmod(0o555)
            (root / "var/empty").mkdir(parents=True)
            (root / "var/empty").chmod(0o555)
            try:
                (root / "sh").symlink_to("rescue/sh")
                links = True
            except OSError:
                links = False  # Windows without symlink privilege
            lines = ownership_spec(root).splitlines()
            (root / "var/empty").chmod(0o755)
            self.assertEqual(lines[0], "#mtree 2.0")
            entries = {line.split()[0]: line.split()[1:] for line in lines[1:]}
            expected = {".", "./rescue", "./rescue/sh", "./var", "./var/empty"} | ({"./sh"} if links else set())
            self.assertEqual(set(entries), expected)
            for fields in entries.values():
                self.assertIn("uid=0", fields)
                self.assertIn("gid=0", fields)
            self.assertEqual(entries["./rescue/sh"][0], "type=file")
            self.assertEqual(entries["./var/empty"][0], "type=dir")
            if os.name != "nt":
                self.assertIn("mode=0555", entries["./rescue/sh"])
                self.assertIn("mode=0555", entries["./var/empty"])
            if links:
                self.assertEqual(entries["./sh"][0], "type=link")
                self.assertIn("link=rescue/sh", entries["./sh"])
            (root / "bad name").write_text("")
            with self.assertRaisesRegex(RuntimeError, "mtree escaping"):
                ownership_spec(root)

    def test_ssh_fingerprint_matches_openssh(self):
        # ssh-keygen -l output for this key: SHA256:sczKHF1P... (ED25519).
        key = ("ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIMclOmouX8TyQE0DODHcQNlL5MQWAmbj+p4xuhnA+KhY "
               "vector\n")
        self.assertEqual(ssh_fingerprint(key), "SHA256:sczKHF1PKadH2xfvti5eKJIrKyzY3XP3wiwC9AstnBU")
        with self.assertRaisesRegex(RuntimeError, "Invalid SSH public key"):
            ssh_fingerprint("ssh-ed25519")


if __name__ == "__main__":
    unittest.main()
