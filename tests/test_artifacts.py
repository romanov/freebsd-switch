import hashlib
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from build import check_hash, require
from validate import elf_aarch64, pe_aarch64, nonoverlap, firmware_sizes, sd_files


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


if __name__ == "__main__":
    unittest.main()
