"""Exercise switchbsd-net against fake commands and a temporary root tree."""
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "config/switchbsd-net"
KEY1 = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIC9xGpVRKBbK8B5cn1yIuJcl6IrUGs1rNhYINT3WGmVh me@pc"
KEY2 = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIPWo8D3Gb2O6bM5GsGHyNw0Km1PqGXDdMZJnqZGsiBbB laptop"
PSK = "correct horse $tap\\le"
PASSWORD = "pa ss word"

# Every command with side effects is a fake that logs its arguments. The fake
# ifconfig keeps wlan0 as a file and reports it associated once created.
MOCKS = {
    "kenv": r'''echo "kenv $*" >> "$MOCK_LOG"
data="$MOCK_DIR/kenv"
case "$1:$2" in
    :) cat "$data" ;;
    -q:-u) grep -v "^$3=" "$data" > "$data.new"; mv "$data.new" "$data" ;;
    -q:*) line=$(grep "^$2=" "$data") || exit 1
          line=${line#*=\"}; printf '%s\n' "${line%\"}" ;;
    *) exit 99 ;;
esac
''',
    "sysctl": r'''[ "$*" = '-n net.wlan.devices' ] || exit 1
echo "$MOCK_WLAN_DEVICES"
''',
    "ifconfig": r'''echo "ifconfig $*" >> "$MOCK_LOG"
case "$*" in
    -l) echo "lo0 $MOCK_WIRED $( [ -f "$MOCK_DIR/wlan0" ] && echo wlan0)" ;;
    wlan0) [ -f "$MOCK_DIR/wlan0" ] || exit 1
           printf 'wlan0: flags=8843<UP>\n\tstatus: associated\n' ;;
    'wlan0 create wlandev'*) : > "$MOCK_DIR/wlan0"; echo wlan0 ;;
    'wlan0 destroy') rm -f "$MOCK_DIR/wlan0" ;;
    lo0\ inet\ *) ;;
    *' inet') printf '\tinet 192.0.2.10 netmask 0xffffff00 broadcast 192.0.2.255\n' ;;
esac
''',
    "pw": r'''echo "pw $*" >> "$MOCK_LOG"
cat > "$MOCK_DIR/pw.stdin"
''',
    # Daemons are running while a marker file exists.
    "pgrep": r'''[ "$1" = -x ] && [ -f "$MOCK_DIR/$2.running" ]
''',
    "pkill": r'''echo "pkill $*" >> "$MOCK_LOG"
rm -f "$MOCK_DIR/$2.running"
''',
    "wpa_supplicant": r'''echo "wpa_supplicant $*" >> "$MOCK_LOG"
: > "$MOCK_DIR/wpa_supplicant.running"
''',
    "route": r'''printf '   gateway: 192.0.2.1\n'
''',
    "wpa_cli": r'''echo wpa_state=COMPLETED
''',
}
LOGGED = ("dhclient", "sleep")


class NetworkScriptTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        base = Path(self.temporary.name)
        self.mocks, self.state, self.root = base / "bin", base / "state", base / "root"
        for directory in (self.mocks, self.state, self.root / "etc/ssh",
                          self.root / "var/run", self.root / "usr/sbin"):
            directory.mkdir(parents=True)
        for name, body in MOCKS.items():
            self.executable(self.mocks / name, body)
        for name in LOGGED:
            self.executable(self.mocks / name, f'echo "{name} $*" >> "$MOCK_LOG"\n')
        self.executable(self.root / "usr/sbin/sshd",
                        'echo "sshd $*" >> "$MOCK_LOG"; : > "$MOCK_DIR/sshd.running"\n')
        (self.root / "etc/ssh/ssh_host_ed25519_key.fingerprint").write_text("SHA256:test (ED25519)\n")
        self.log = self.state / "log"
        self.log.write_text("")

    def tearDown(self):
        self.temporary.cleanup()

    @staticmethod
    def executable(path, body):
        path.write_text("#!/bin/sh\n" + body, newline="\n")
        path.chmod(0o755)

    def run_net(self, command, settings=(), wlan_devices="", wired=""):
        (self.state / "kenv").write_text("".join(f'{name}="{value}"\n' for name, value in settings),
                                         newline="\n")
        env = dict(os.environ, PATH=os.pathsep.join((str(self.mocks), os.environ.get("PATH", ""))),
                   SWITCHBSD_NET_ROOT=self.root.as_posix(), MOCK_DIR=self.state.as_posix(),
                   MOCK_LOG=self.log.as_posix(), MOCK_WLAN_DEVICES=wlan_devices, MOCK_WIRED=wired)
        return subprocess.run(["/bin/sh", str(SCRIPT), command], env=env,
                              capture_output=True, text=True, timeout=20)

    def calls(self):
        return self.log.read_text().splitlines()

    def kenv_names(self):
        return [line.split("=", 1)[0] for line in (self.state / "kenv").read_text().splitlines()]

    def assert_private(self, path):
        if os.name != "nt":
            self.assertEqual(stat.S_IMODE(path.stat().st_mode) & 0o077, 0, path)

    def test_wifi_keys_and_password_are_applied_once_and_secrets_removed(self):
        settings = (("switchbsd.wifi.ssid", "Home Net"), ("switchbsd.wifi.psk", PSK),
                    ("switchbsd.wifi.country", "DE"), ("switchbsd.ssh.key", KEY1),
                    ("switchbsd.ssh.key2", KEY2), ("switchbsd.ssh.password", PASSWORD))
        result = self.run_net("start", settings, wlan_devices="rtwn0")
        self.assertEqual(result.returncode, 0, result.stderr)
        wpa = self.root / "etc/wpa_supplicant.conf"
        self.assertEqual(wpa.read_text(), 'ctrl_interface=/var/run/wpa_supplicant\n'
                         'ctrl_interface_group=wheel\nnetwork={\n\tssid="Home Net"\n'
                         f'\tscan_ssid=1\n\tpsk="{PSK}"\n}}\n')
        self.assert_private(wpa)
        keys = self.root / "root/.ssh/authorized_keys"
        self.assertEqual(keys.read_text(), f"{KEY1}\n{KEY2}\n")
        self.assert_private(keys)
        self.assert_private(keys.parent)
        self.assertEqual((self.state / "pw.stdin").read_text(), PASSWORD + "\n")
        calls = self.calls()
        self.assertIn("pw usermod root -h 0", calls)
        for name in ("switchbsd.wifi.psk", "switchbsd.ssh.password"):
            self.assertIn(f"kenv -q -u {name}", calls)
            self.assertNotIn(name, self.kenv_names())
        self.assertIn("switchbsd.wifi.ssid", self.kenv_names())
        self.assertIn("ifconfig wlan0 create wlandev rtwn0 country DE", calls)
        self.assertIn(f"wpa_supplicant -B -D bsd -i wlan0 -c {self.root.as_posix()}/etc/wpa_supplicant.conf",
                      calls)
        self.assertIn("dhclient wlan0", calls)
        self.assertIn("sshd ", calls)
        self.assertIn("SWITCHBSD: SSH_READY", result.stdout)
        self.assertIn("SWITCHBSD: NETWORK_READY wlan0 192.0.2.10", result.stdout)
        self.assertIn("SSH: ssh root@192.0.2.10", result.stdout)
        self.assertIn("SSH host key: SHA256:test (ED25519)", result.stdout)
        for secret in (PSK, PASSWORD):
            self.assertNotIn(secret, result.stdout + result.stderr)

        status = self.run_net("status", wlan_devices="rtwn0")
        self.assertEqual(status.returncode, 0, status.stderr)
        for line in ("Wi-Fi adapters: rtwn0", "Wi-Fi settings: yes", "wpa_state=COMPLETED",
                     "Address wlan0: 192.0.2.10", "Default route: 192.0.2.1",
                     "SSH server: running", "SSH authorized keys: 2", "SSH root password: set"):
            self.assertIn(line, status.stdout)
        for secret in (PSK, PASSWORD):
            self.assertNotIn(secret, status.stdout + status.stderr)

        # A restart reuses the generated files instead of the kernel environment.
        self.log.write_text("")
        restart = self.run_net("restart", (("switchbsd.ssh.password", "changed"),),
                               wlan_devices="rtwn0")
        self.assertEqual(restart.returncode, 0, restart.stderr)
        calls = self.calls()
        self.assertEqual(calls[:4], ["pkill -x sshd", "pkill -x dhclient",
                                     "pkill -x wpa_supplicant", "ifconfig wlan0 destroy"])
        self.assertFalse(any(call.startswith("pw ") for call in calls))
        for call in ("ifconfig wlan0 create wlandev rtwn0 country DE", "dhclient wlan0", "sshd "):
            self.assertIn(call, calls)

    def test_open_network_needs_no_password(self):
        result = self.run_net("start", (("switchbsd.wifi.ssid", "Cafe"), ("switchbsd.ssh.key", KEY1)),
                              wlan_devices="rtwn0")
        self.assertEqual(result.returncode, 0, result.stderr)
        config = (self.root / "etc/wpa_supplicant.conf").read_text()
        self.assertIn("\tkey_mgmt=NONE\n", config)
        self.assertNotIn("psk=", config)
        self.assertIn("ifconfig wlan0 create wlandev rtwn0", self.calls())

    def test_invalid_psk_skips_wifi_but_ssh_still_starts(self):
        result = self.run_net("start", (("switchbsd.wifi.ssid", "Home"), ("switchbsd.wifi.psk", "short"),
                                        ("switchbsd.ssh.key", KEY1)),
                              wlan_devices="rtwn0", wired="ue0")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("switchbsd.wifi.psk must be 8 to 63 characters", result.stdout)
        self.assertFalse((self.root / "etc/wpa_supplicant.conf").exists())
        calls = self.calls()
        self.assertFalse(any(call.startswith("wpa_supplicant") for call in calls))
        self.assertIn("kenv -q -u switchbsd.wifi.psk", calls)
        self.assertIn("dhclient ue0", calls)
        self.assertIn("SWITCHBSD: SSH_READY", result.stdout)
        self.assertNotIn("short", result.stdout)

    def test_without_settings_wired_dhcp_runs_but_ssh_stays_off(self):
        result = self.run_net("start", wired="ue0")
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.calls()
        self.assertIn("dhclient ue0", calls)
        self.assertFalse(any(call.startswith(("sshd", "wpa_supplicant", "pw")) for call in calls))
        self.assertIn("boot/loader.conf.d/network.conf", result.stdout)
        self.assertIn("SSH: not started", result.stdout)
        self.assertIn("SWITCHBSD: NETWORK_READY ue0 192.0.2.10", result.stdout)
        self.assertNotIn("SSH_READY", result.stdout)

    def test_missing_adapter_wait_is_bounded(self):
        result = self.run_net("start", (("switchbsd.wifi.ssid", "Home"), ("switchbsd.wifi.psk", "password1"),
                                        ("switchbsd.ssh.password", PASSWORD)))
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.calls()
        self.assertEqual(calls.count("sleep 1"), 15)
        self.assertIn("Wi-Fi: no USB adapter found", result.stdout)
        self.assertFalse(any(call.startswith(("wpa_supplicant", "ifconfig wlan0 create")) for call in calls))
        self.assertIn("SWITCHBSD: SSH_READY", result.stdout)
        self.assertIn("Network: no address yet", result.stdout)

    def test_unusable_settings_are_reported(self):
        result = self.run_net("start", (("switchbsd.wifi.ssid", "Home"), ("switchbsd.wifi.country", "germany"),
                                        ("switchbsd.ssh.key", "not a key")), wlan_devices="rtwn0")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("ignoring switchbsd.wifi.country=germany", result.stdout)
        self.assertIn("not an OpenSSH public key", result.stdout)
        self.assertIn("ifconfig wlan0 create wlandev rtwn0", self.calls())
        self.assertEqual((self.root / "root/.ssh/authorized_keys").read_text(), "")
        self.assertIn("SSH: not started", result.stdout)

    def test_scan_and_usage(self):
        result = self.run_net("scan", wlan_devices="rtwn0")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls()[-3:], ["ifconfig wlan0 create wlandev rtwn0",
                                             "ifconfig wlan0 up", "ifconfig wlan0 scan"])
        result = self.run_net("reset")
        self.assertEqual(result.returncode, 2)
        self.assertIn("Usage:", result.stderr)


if __name__ == "__main__":
    unittest.main()
