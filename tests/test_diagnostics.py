"""Exercise report failure handling without probing the host's hardware."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "config/switchbsd-report"


class DiagnosticReportTests(unittest.TestCase):
    def run_report(self, timeout_body, *args):
        with tempfile.TemporaryDirectory() as directory:
            probe = Path(directory) / "timeout"
            probe.write_text("#!/bin/sh\n" + timeout_body)
            probe.chmod(0o755)
            # All external probes go through this fake timeout; no host device
            # access and no reliance on which commands the host has installed.
            return subprocess.run(["/bin/sh", str(REPORT), *args],
                                  env=dict(os.environ, PATH=directory),
                                  capture_output=True, text=True, timeout=5)

    def test_failed_and_timed_out_probes_do_not_lose_rest_of_report(self):
        result = self.run_report('''
[ "$1:$2:$3" = '-k:2s:10s' ] || exit 99
shift 3
case "$1" in
    usbconfig) echo 'USB probe timed out' >&2; exit 124 ;;
    devinfo) echo 'device tree unavailable' >&2; exit 1 ;;
    geom) echo 'command unavailable' >&2; exit 127 ;;
    dmesg) echo 'last kernel message' ;;
    *) echo "mock: $*" ;;
esac
''')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('USB probe timed out', result.stdout)
        for code in (124, 1, 127):
            self.assertIn(f'[unavailable or failed: exit {code}; continuing]', result.stdout)
        self.assertIn('last kernel message', result.stdout)
        self.assertIn('Commands unavailable or failed: 5', result.stdout)
        self.assertTrue(result.stdout.endswith('SWITCHBSD: DIAGNOSTIC_REPORT_END\n'))

    def test_report_only_requests_read_only_probes(self):
        result = self.run_report('''
[ "$1:$2:$3" = '-k:2s:10s' ] || exit 99
shift 3
case "$*" in
    'cat /etc/switchbsd-build.json'|'date -u'|'uname -a'|\
    'sysctl hw.machine hw.model hw.physmem hw.ncpu kern.smp.active kern.boottime kern.console'|\
    'usbconfig list'|'usbconfig show_ifdrv'|'usbconfig dump_device_desc'|\
    'devinfo -rv'|'sysctl kern.disks'|'camcontrol devlist -v'|'geom disk list'|\
    'diskinfo -v /dev/md0'|'mount'|'df -h'|'ifconfig -a'|'ps ax'|'kenv'|'dmesg')
        echo "mock: $*" ;;
    *) echo "Unexpected probe: $*"; exit 99 ;;
esac
''')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Commands unavailable or failed: 0', result.stdout)
        self.assertNotIn('Unexpected probe', result.stdout)

    def test_missing_timeout_fails_without_running_probes(self):
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run(["/bin/sh", str(REPORT)],
                                    env=dict(os.environ, PATH=directory),
                                    capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 1)
        self.assertIn('timeout is missing', result.stderr)
        self.assertEqual(result.stdout, '')

    def test_help_and_invalid_arguments_do_not_probe_devices(self):
        result = self.run_report('echo UNEXPECTED_PROBE; exit 99\n', '--help')
        self.assertEqual(result.returncode, 0)
        self.assertIn('Usage:', result.stdout)
        self.assertNotIn('UNEXPECTED_PROBE', result.stdout)
        result = self.run_report('echo UNEXPECTED_PROBE; exit 99\n', '--reset')
        self.assertEqual(result.returncode, 2)
        self.assertNotIn('UNEXPECTED_PROBE', result.stdout)
