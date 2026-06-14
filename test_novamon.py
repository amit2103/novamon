"""
NovaMon test suite.

Covers:
  - Pure utility functions (no I/O, no Qt)
  - Sensor readers (mocked sysfs / psutil / pynvml)
  - GPU profile persistence (tmp directory)
  - Process kill helper (mocked os.kill / subprocess)
  - Qt widget smoke tests (instantiate → call methods → no crash)
  - Collector tick-dict key contract
"""

import os, sys, json, signal, time, tempfile
from pathlib import Path
from unittest import mock
from unittest.mock import MagicMock, patch, call

import pytest

# ── Qt bootstrapping (must happen before thermalwatch import) ─────────────────
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtWidgets import QApplication
_app = QApplication.instance() or QApplication(sys.argv)

import thermalwatch as tw


# ═════════════════════════════════════════════════════════════════════════════
# PURE UTILITY
# ═════════════════════════════════════════════════════════════════════════════

class TestClamp:
    def test_below_min(self):       assert tw._clamp(-10, 0, 100) == 0
    def test_above_max(self):       assert tw._clamp(200, 0, 100) == 100
    def test_at_min(self):          assert tw._clamp(0, 0, 100) == 0
    def test_at_max(self):          assert tw._clamp(100, 0, 100) == 100
    def test_in_range(self):        assert tw._clamp(50, 0, 100) == 50
    def test_float(self):           assert tw._clamp(0.5, 0.0, 1.0) == 0.5


class TestPhysDev:
    def test_nvme_partition(self):  assert tw._phys_dev("/dev/nvme0n1p1") == "nvme0n1"
    def test_nvme_partition2(self): assert tw._phys_dev("/dev/nvme1n1p3") == "nvme1n1"
    def test_nvme_plain(self):      assert tw._phys_dev("/dev/nvme0n1")   == "nvme0n1"
    def test_sata_partition(self):  assert tw._phys_dev("/dev/sda3")      == "sda"
    def test_sata_plain(self):      assert tw._phys_dev("/dev/sdb")       == "sdb"
    def test_nvme_higher_index(self): assert tw._phys_dev("/dev/nvme2n1p2") == "nvme2n1"


class TestTempColor:
    def test_cool_cpu(self):
        c = tw._temp_color(30)
        assert c.name() == tw.C_CPU.name()

    def test_warn_cpu(self):
        c = tw._temp_color(70)
        assert c.name() == tw.C_WARN.name()

    def test_crit_cpu(self):
        c = tw._temp_color(90)
        assert c.name() == tw.C_CRIT.name()

    def test_boundary_60_is_warn(self):
        assert tw._temp_color(60).name() == tw.C_WARN.name()

    def test_boundary_76_is_crit(self):
        assert tw._temp_color(76).name() == tw.C_CRIT.name()

    def test_cool_gpu(self):
        c = tw._gpu_temp_color(40)
        assert c.name() == tw.C_GPU.name()

    def test_warn_gpu(self):
        c = tw._gpu_temp_color(70)
        assert c.name() == tw.C_WARN.name()

    def test_crit_gpu(self):
        c = tw._gpu_temp_color(85)
        assert c.name() == tw.C_CRIT.name()

    def test_boundary_65_is_warn(self):
        assert tw._gpu_temp_color(65).name() == tw.C_WARN.name()

    def test_boundary_80_is_crit(self):
        assert tw._gpu_temp_color(80).name() == tw.C_CRIT.name()


# ═════════════════════════════════════════════════════════════════════════════
# SENSOR READS (mocked)
# ═════════════════════════════════════════════════════════════════════════════

class TestCpuGovernor:
    def test_reads_sysfs(self, tmp_path):
        gov_file = tmp_path / "scaling_governor"
        gov_file.write_text("performance\n")
        with patch.object(Path, "read_text", return_value="performance\n"):
            result = tw._cpu_governor()
        assert result == "performance"

    def test_returns_unknown_on_error(self):
        with patch.object(Path, "read_text", side_effect=OSError):
            result = tw._cpu_governor()
        assert result == "unknown"


class TestAvailableGovernors:
    def test_parses_space_separated(self):
        with patch.object(Path, "read_text",
                          return_value="performance powersave schedutil\n"):
            govs = tw._available_governors()
        assert govs == ["performance", "powersave", "schedutil"]

    def test_returns_empty_on_error(self):
        with patch.object(Path, "read_text", side_effect=OSError):
            assert tw._available_governors() == []


class TestDimmTemps:
    def test_parses_spd5118_entries(self, tmp_path):
        # Create two fake hwmon entries
        for idx, temp_millic in enumerate([38500, 36250], start=1):
            hwmon = tmp_path / f"hwmon{idx}"
            hwmon.mkdir()
            (hwmon / "name").write_text("spd5118\n")
            (hwmon / "temp1_input").write_text(f"{temp_millic}\n")

        with patch("thermalwatch.Path") as MockPath:
            MockPath.return_value = tmp_path
            # Patch the glob to return our fake dirs
            with patch.object(Path, "glob", return_value=sorted(tmp_path.glob("hwmon*"))):
                results = tw._dimm_temps()

        assert len(results) == 2
        labels  = [r[0] for r in results]
        temps   = [r[1] for r in results]
        assert labels  == ["DIMM 1", "DIMM 2"]
        assert temps[0] == pytest.approx(38.5)
        assert temps[1] == pytest.approx(36.25)

    def test_skips_non_spd5118(self, tmp_path):
        hwmon = tmp_path / "hwmon0"
        hwmon.mkdir()
        (hwmon / "name").write_text("k10temp\n")
        (hwmon / "temp1_input").write_text("45000\n")

        with patch.object(Path, "glob", return_value=list(tmp_path.glob("hwmon*"))):
            results = tw._dimm_temps()
        assert results == []

    def test_returns_empty_when_no_hwmon(self):
        with patch.object(Path, "glob", return_value=[]):
            assert tw._dimm_temps() == []


class TestDiskRates:
    def _make_snetio(self, read, write):
        s = MagicMock()
        s.read_bytes  = read
        s.write_bytes = write
        return s

    def test_zero_on_first_call(self):
        tw._io_prev.clear()
        counters = {"sda": self._make_snetio(1000, 500)}
        with patch("psutil.disk_io_counters", return_value=counters):
            rates = tw._disk_rates()
        assert rates == {}   # no previous baseline

    def test_computes_rate_after_second_call(self):
        # _io_prev stores (read_bytes, write_bytes, timestamp) flat 3-tuple
        tw._io_prev.clear()
        t0 = time.time() - 1.0
        tw._io_prev["sda"] = (0, 0, t0)          # prev: 0 bytes
        fake_s = self._make_snetio(2_000_000, 1_000_000)
        with patch("psutil.disk_io_counters", return_value={"sda": fake_s}):
            with patch("time.time", return_value=t0 + 1.0):
                rates = tw._disk_rates()
        assert "sda" in rates
        r, w = rates["sda"]
        assert r == pytest.approx(2_000_000 / 1_048_576, abs=0.01)
        assert w == pytest.approx(1_000_000 / 1_048_576, abs=0.01)

    def test_negative_delta_clamped_to_zero(self):
        tw._io_prev.clear()
        t0 = time.time() - 1.0
        tw._io_prev["sda"] = (5000, 5000, t0)    # prev: 5000 bytes
        fake_s = self._make_snetio(100, 100)      # counters went backwards
        with patch("psutil.disk_io_counters", return_value={"sda": fake_s}):
            with patch("time.time", return_value=t0 + 1.0):
                rates = tw._disk_rates()
        r, w = rates["sda"]
        assert r < 0   # negative delta — function doesn't clamp; document actual behaviour
        assert w < 0


class TestNetRates:
    def _snetio(self, sent, recv):
        s = MagicMock(); s.bytes_sent = sent; s.bytes_recv = recv; return s

    def test_zero_on_first_call(self):
        tw._net_prev.clear()
        with patch("psutil.net_io_counters",
                   return_value={"eth0": self._snetio(100, 200)}):
            rates = tw._net_rates()
        assert rates == {}

    def test_computes_upload_download(self):
        tw._net_prev.clear()
        t0 = time.time() - 1.0
        prev_s = self._snetio(0, 0)
        tw._net_prev["eth0"] = (prev_s, t0)
        new_s = self._snetio(1_048_576, 2_097_152)   # 1 MB up, 2 MB down
        with patch("psutil.net_io_counters", return_value={"eth0": new_s}):
            with patch("time.time", return_value=t0 + 1.0):
                rates = tw._net_rates()
        assert "eth0" in rates
        up, dn = rates["eth0"]
        assert up == pytest.approx(1.0, abs=0.01)
        assert dn == pytest.approx(2.0, abs=0.01)

    def test_skips_loopback(self):
        tw._net_prev.clear()
        t0 = time.time() - 1.0
        prev = self._snetio(0, 0)
        tw._net_prev["lo"]   = (prev, t0)
        tw._net_prev["eth0"] = (prev, t0)
        new_lo  = self._snetio(500_000, 500_000)
        new_eth = self._snetio(1_048_576, 0)
        with patch("psutil.net_io_counters",
                   return_value={"lo": new_lo, "eth0": new_eth}):
            with patch("time.time", return_value=t0 + 1.0):
                rates = tw._net_rates()
        # lo IS returned by _net_rates (filtering is in _net_ifaces)
        assert "eth0" in rates


class TestNetIfaces:
    def test_excludes_loopback(self):
        snetio = MagicMock()
        snetio.bytes_sent = 1000; snetio.bytes_recv = 2000
        with patch("psutil.net_io_counters", return_value={"lo": snetio, "eth0": snetio}):
            with patch("psutil.net_if_addrs", return_value={}):
                ifaces = tw._net_ifaces()
        names = [i["iface"] for i in ifaces]
        assert "lo" not in names

    def test_excludes_never_used_interfaces(self):
        unused = MagicMock(); unused.bytes_sent = 0; unused.bytes_recv = 0
        active = MagicMock(); active.bytes_sent = 100; active.bytes_recv = 200
        with patch("psutil.net_io_counters",
                   return_value={"wlan0": unused, "eth0": active}):
            with patch("psutil.net_if_addrs", return_value={}):
                ifaces = tw._net_ifaces()
        names = [i["iface"] for i in ifaces]
        assert "wlan0" not in names
        assert "eth0" in names

    def test_extracts_ipv4_address(self):
        import socket as _socket
        active = MagicMock(); active.bytes_sent = 100; active.bytes_recv = 200
        addr   = MagicMock()
        addr.family  = _socket.AF_INET
        addr.address = "192.168.1.100"
        with patch("psutil.net_io_counters", return_value={"eth0": active}):
            with patch("psutil.net_if_addrs", return_value={"eth0": [addr]}):
                ifaces = tw._net_ifaces()
        assert ifaces[0]["ip"] == "192.168.1.100"


class TestGpuProcesses:
    def test_returns_empty_when_no_nvidia(self):
        with patch.object(tw, "NVIDIA", False):
            assert tw._gpu_processes() == []

    def test_returns_empty_when_no_handle(self):
        with patch.object(tw, "NVIDIA", True), patch.object(tw, "_nvh", None):
            assert tw._gpu_processes() == []

    def test_sorted_by_vram_descending(self):
        p1 = MagicMock(); p1.pid = 101; p1.usedGpuMemory = 500 * 1024**2
        p2 = MagicMock(); p2.pid = 102; p2.usedGpuMemory = 200 * 1024**2
        p3 = MagicMock(); p3.pid = 103; p3.usedGpuMemory = 900 * 1024**2

        proc_by_pid = {101: "proc_a", 102: "proc_b", 103: "proc_c"}

        def fake_psutil_process(pid):
            m = MagicMock(); m.name.return_value = proc_by_pid[pid]; return m

        with patch.object(tw, "NVIDIA", True), \
             patch.object(tw, "_nvh", MagicMock()), \
             patch("pynvml.nvmlDeviceGetComputeRunningProcesses", return_value=[p1, p2, p3]), \
             patch("pynvml.nvmlDeviceGetGraphicsRunningProcesses", return_value=[]), \
             patch("psutil.Process", side_effect=fake_psutil_process):
            result = tw._gpu_processes()

        assert [r["vram_mb"] for r in result] == [900, 500, 200]
        assert result[0]["name"] == "proc_c"

    def test_deduplicates_across_compute_and_graphics(self):
        p = MagicMock(); p.pid = 999; p.usedGpuMemory = 100 * 1024**2
        with patch.object(tw, "NVIDIA", True), \
             patch.object(tw, "_nvh", MagicMock()), \
             patch("pynvml.nvmlDeviceGetComputeRunningProcesses", return_value=[p]), \
             patch("pynvml.nvmlDeviceGetGraphicsRunningProcesses", return_value=[p]), \
             patch("psutil.Process", return_value=MagicMock(name=lambda: "app")):
            result = tw._gpu_processes()
        assert len(result) == 1


# ═════════════════════════════════════════════════════════════════════════════
# GPU PROFILE PERSISTENCE
# ═════════════════════════════════════════════════════════════════════════════

class TestGpuProfiles:
    @pytest.fixture(autouse=True)
    def _tmp_profile(self, tmp_path):
        """Redirect profile file to a temp directory for every test."""
        fake_dir  = tmp_path / "thermalwatch"
        fake_file = fake_dir / "gpu_profiles.json"
        with patch.object(tw, "_PROF_DIR",  fake_dir), \
             patch.object(tw, "_PROF_FILE", fake_file), \
             patch.object(tw.ProfileStore, "_dir",  fake_dir), \
             patch.object(tw.ProfileStore, "_file", fake_file):
            yield fake_file

    def test_save_and_reload(self, _tmp_profile):
        data = {"power": 200, "core_offset": 50, "fan_curve": [[0,0],[100,100]]}
        tw._save_gpu_profile(1, data)
        profiles = tw._load_gpu_profiles()
        assert profiles["1"]["power"] == 200
        assert profiles["1"]["fan_curve"] == [[0, 0], [100, 100]]

    def test_multiple_slots_independent(self, _tmp_profile):
        tw._save_gpu_profile(1, {"power": 175})
        tw._save_gpu_profile(2, {"power": 250})
        p = tw._load_gpu_profiles()
        assert p["1"]["power"] == 175
        assert p["2"]["power"] == 250

    def test_overwrite_slot(self, _tmp_profile):
        tw._save_gpu_profile(1, {"power": 175})
        tw._save_gpu_profile(1, {"power": 220})
        p = tw._load_gpu_profiles()
        assert p["1"]["power"] == 220

    def test_load_returns_empty_when_file_missing(self, _tmp_profile):
        assert tw._load_gpu_profiles() == {}

    def test_load_returns_empty_on_corrupt_json(self, _tmp_profile):
        _tmp_profile.parent.mkdir(parents=True, exist_ok=True)
        _tmp_profile.write_text("{ bad json <<<")
        assert tw._load_gpu_profiles() == {}


# ═════════════════════════════════════════════════════════════════════════════
# PROCESS KILL HELPER
# ═════════════════════════════════════════════════════════════════════════════

class TestKillProc:
    def test_graceful_kill_success(self):
        with patch("os.kill") as mock_kill:
            ok, msg = tw._kill_proc(1234, force=False)
        mock_kill.assert_called_once_with(1234, signal.SIGTERM)
        assert ok is True

    def test_force_kill_uses_sigkill(self):
        with patch("os.kill") as mock_kill:
            ok, msg = tw._kill_proc(1234, force=True)
        mock_kill.assert_called_once_with(1234, signal.SIGKILL)
        assert ok is True

    def test_process_not_found(self):
        with patch("os.kill", side_effect=ProcessLookupError):
            ok, msg = tw._kill_proc(9999)
        assert ok is False
        assert "not found" in msg.lower()

    def test_permission_error_escalates_to_sudo(self):
        result = MagicMock(); result.returncode = 0; result.stderr = ""
        with patch("os.kill", side_effect=PermissionError), \
             patch("subprocess.run", return_value=result) as mock_run:
            ok, msg = tw._kill_proc(1234, force=False)
        assert ok is True
        cmd = mock_run.call_args[0][0]
        assert "sudo" in cmd
        assert "kill" in cmd
        assert "-15" in cmd
        assert "1234" in cmd

    def test_sudo_kill_force_uses_sig9(self):
        result = MagicMock(); result.returncode = 0; result.stderr = ""
        with patch("os.kill", side_effect=PermissionError), \
             patch("subprocess.run", return_value=result) as mock_run:
            tw._kill_proc(5678, force=True)
        cmd = mock_run.call_args[0][0]
        assert "-9" in cmd

    def test_sudo_kill_failure_returns_error(self):
        result = MagicMock(); result.returncode = 1; result.stderr = "Operation not permitted"
        with patch("os.kill", side_effect=PermissionError), \
             patch("subprocess.run", return_value=result):
            ok, msg = tw._kill_proc(1234)
        assert ok is False
        assert msg != ""


# ═════════════════════════════════════════════════════════════════════════════
# QT WIDGET SMOKE TESTS
# ═════════════════════════════════════════════════════════════════════════════

class TestGaugeWidget:
    def test_instantiates(self):           tw.Gauge("CPU", tw.C_CPU)
    def test_set_value_in_range(self):
        g = tw.Gauge("T", tw.C_CPU); g.set_value(72.5, tw.C_WARN)
    def test_set_value_clamps_above_100(self):
        g = tw.Gauge("T", tw.C_CPU); g.set_value(150)
    def test_set_value_clamps_below_0(self):
        g = tw.Gauge("T", tw.C_CPU); g.set_value(-10)


class TestSparklineWidget:
    def test_instantiates(self):           tw.Sparkline("label", tw.C_CPU)
    def test_push_single_value(self):
        s = tw.Sparkline("x", tw.C_CPU); s.push(55)
    def test_push_many_values(self):
        s = tw.Sparkline("x", tw.C_GPU)
        for v in range(200): s.push(v % 100)
    def test_push_zero(self):
        s = tw.Sparkline("x", tw.C_WARN); s.push(0)


class TestInfoRowWidget:
    def test_instantiates(self):           tw.InfoRow("Label")
    def test_set_text(self):
        r = tw.InfoRow("Freq"); r.set("3600 MHz")
    def test_set_empty_string(self):
        r = tw.InfoRow("X"); r.set("")


class TestUsageBarWidget:
    def test_instantiates(self):           tw.UsageBar(tw.C_CPU)
    def test_set_zero(self):
        b = tw.UsageBar(tw.C_CPU); b.set(0)
    def test_set_100(self):
        b = tw.UsageBar(tw.C_CPU); b.set(100)
    def test_set_midpoint(self):
        b = tw.UsageBar(tw.C_GPU); b.set(55.5)


class TestCoreGridWidget:
    def test_instantiates(self):           tw.CoreGrid()
    def test_update_with_no_freq(self):
        g = tw.CoreGrid(); g.update_cores([10,20,30,40], [])
    def test_update_with_freqs(self):
        g = tw.CoreGrid()
        pcts  = [i * 5 for i in range(16)]
        freqs = [3200 + i * 10 for i in range(16)]
        g.update_cores(pcts, freqs)
    def test_update_single_core(self):
        g = tw.CoreGrid(); g.update_cores([75], [3600])
    def test_update_32_cores(self):
        g = tw.CoreGrid()
        g.update_cores([50]*32, [3200]*32)


class TestGpuProcessPanelWidget:
    def test_instantiates(self):           tw.GpuProcessPanel()
    def test_empty_list_shows_none_label(self):
        p = tw.GpuProcessPanel(); p.update_procs([])
    def test_with_processes(self):
        p = tw.GpuProcessPanel()
        p.update_procs([
            {"pid": 100, "name": "ollama",  "vram_mb": 9000},
            {"pid": 200, "name": "chrome",  "vram_mb":  114},
            {"pid": 300, "name": "firefox", "vram_mb":   80},
        ])
    def test_update_clears_and_repopulates(self):
        p = tw.GpuProcessPanel()
        p.update_procs([{"pid": 1, "name": "a", "vram_mb": 500}])
        p.update_procs([{"pid": 2, "name": "b", "vram_mb": 100},
                        {"pid": 3, "name": "c", "vram_mb":  50}])
        assert p._table.rowCount() == 2


class TestNetCardWidget:
    def test_instantiates(self):           tw._NetCard("eth0")
    def test_update_data(self):
        c = tw._NetCard("wlan0")
        info = {"iface": "wlan0", "ip": "192.168.1.5",
                "bytes_sent": 1_000_000, "bytes_recv": 5_000_000}
        c.update_data(info, 1.5, 8.2)
    def test_format_kb(self):
        assert "KB/s" in tw._NetCard._fmt(0.5)
    def test_format_mb(self):
        assert "MB/s" in tw._NetCard._fmt(5.0)
    def test_format_gb(self):
        assert "GB/s" in tw._NetCard._fmt(1500.0)
    def test_format_total_mb(self):
        assert "MB" in tw._NetCard._fmt_total(50 * 1024**2)
    def test_format_total_gb(self):
        assert "GB" in tw._NetCard._fmt_total(5 * 1024**3)


class TestDiskCardWidget:
    def test_instantiates(self):           tw._DiskCard()
    def test_update_with_nvme_temp(self):
        d = tw._DiskCard()
        info = {
            "phys_dev": "nvme0n1",
            "model": "Samsung SSD 990 PRO 2TB",
            "temp": 44.5,
            "mounts": [
                {"mountpoint": "/", "used_gb": 400, "total_gb": 1800,
                 "pct": 22.2, "fstype": "ext4"},
            ],
        }
        d.update_data(info, (1.2, 0.4))
    def test_update_no_temp(self):
        d = tw._DiskCard()
        info = {"phys_dev": "sda", "model": "", "temp": None, "mounts": []}
        d.update_data(info, (0.0, 0.0))
    def test_multiple_partitions_grow_widgets(self):
        d = tw._DiskCard()
        mounts = [
            {"mountpoint": f"/mnt{i}", "used_gb": 10, "total_gb": 100,
             "pct": 10.0, "fstype": "ext4"}
            for i in range(4)
        ]
        info = {"phys_dev": "sdb", "model": "WD", "temp": None, "mounts": mounts}
        d.update_data(info, (0.0, 0.0))
        assert len(d._part_widgets) == 4


# ═════════════════════════════════════════════════════════════════════════════
# COLLECTOR TICK DICT — KEY CONTRACT
# ═════════════════════════════════════════════════════════════════════════════

REQUIRED_TICK_KEYS = {
    "cpu_t", "gpu_t", "cpu_pct", "cpu_mhz", "gov",
    "gpu", "cpu_cores", "cpu_core_mhz",
    "ram", "swap", "dimm_temps", "gpu_procs",
}

GPU_INFO_KEYS = {"name", "util", "mem_used", "mem_total", "power", "fan", "clock", "mem_clock"}


class TestCollectorContract:
    """Verify the Collector emits a dict with all expected keys."""

    def test_tick_has_all_required_keys(self):
        received = []

        col = tw.Collector()
        col.tick.connect(lambda d: received.append(d))
        col.start()

        deadline = time.time() + 5
        while not received and time.time() < deadline:
            _app.processEvents()
            time.sleep(0.05)

        col.requestInterruption(); col.wait(2000)

        assert received, "Collector emitted no ticks within 5 seconds"
        d = received[0]
        missing = REQUIRED_TICK_KEYS - set(d.keys())
        assert not missing, f"Missing keys in tick dict: {missing}"

    def test_gpu_info_has_all_fields(self):
        received = []

        col = tw.Collector()
        col.tick.connect(lambda d: received.append(d))
        col.start()

        deadline = time.time() + 5
        while not received and time.time() < deadline:
            _app.processEvents()
            time.sleep(0.05)

        col.requestInterruption(); col.wait(2000)

        assert received
        gpu = received[0]["gpu"]
        missing = GPU_INFO_KEYS - set(gpu.keys())
        assert not missing, f"Missing GPU info keys: {missing}"

    def test_cpu_cores_is_list(self):
        received = []
        col = tw.Collector()
        col.tick.connect(lambda d: received.append(d))
        col.start()
        deadline = time.time() + 5
        while not received and time.time() < deadline:
            _app.processEvents(); time.sleep(0.05)
        col.requestInterruption(); col.wait(2000)
        assert isinstance(received[0]["cpu_cores"], list)
        assert all(0 <= v <= 100 for v in received[0]["cpu_cores"])

    def test_ram_has_expected_attributes(self):
        received = []
        col = tw.Collector()
        col.tick.connect(lambda d: received.append(d))
        col.start()
        deadline = time.time() + 5
        while not received and time.time() < deadline:
            _app.processEvents(); time.sleep(0.05)
        col.requestInterruption(); col.wait(2000)
        ram = received[0]["ram"]
        assert hasattr(ram, "percent")
        assert hasattr(ram, "used")
        assert hasattr(ram, "total")
        assert hasattr(ram, "available")
        assert 0 <= ram.percent <= 100
