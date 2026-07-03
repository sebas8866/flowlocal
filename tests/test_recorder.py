"""Unit tests for flowlocal.recorder.Recorder's device-name resolution
cache (FIX 1c) and refresh_devices() locked no-op while recording (M3).

sounddevice is imported lazily inside Recorder methods, so these tests
monkeypatch Recorder.list_devices (a staticmethod) rather than touching
real audio hardware.
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flowlocal.recorder import Recorder  # noqa: E402


class _FakeStream:
    def __init__(self, *args, **kwargs):
        pass

    def start(self):
        pass

    def stop(self):
        pass

    def close(self):
        pass


class DeviceNameCacheTest(unittest.TestCase):
    def setUp(self):
        self.recorder = Recorder()
        self.list_devices_calls = 0

        def fake_list_devices():
            self.list_devices_calls += 1
            return [(0, "Built-in Mic"), (1, "USB Headset")]

        self._patcher = mock.patch.object(
            Recorder, "list_devices", staticmethod(fake_list_devices)
        )
        self._patcher.start()

        self._sd_patcher = mock.patch("sounddevice.InputStream", _FakeStream)
        self._sd_patcher.start()

    def tearDown(self):
        self._patcher.stop()
        self._sd_patcher.stop()

    def test_first_start_resolves_and_caches(self):
        self.recorder.start(device_name="USB Headset")
        self.assertEqual(self.list_devices_calls, 1)
        self.assertEqual(self.recorder._resolved_device_index, 1)
        self.recorder.stop()

    def test_second_start_with_same_name_uses_cache(self):
        self.recorder.start(device_name="USB Headset")
        self.recorder.stop()
        self.recorder.start(device_name="USB Headset")
        # list_devices should only have been called once across both starts.
        self.assertEqual(self.list_devices_calls, 1)
        self.recorder.stop()

    def test_different_name_re_resolves(self):
        self.recorder.start(device_name="USB Headset")
        self.recorder.stop()
        self.recorder.start(device_name="Built-in Mic")
        self.assertEqual(self.list_devices_calls, 2)
        self.assertEqual(self.recorder._resolved_device_index, 0)
        self.recorder.stop()

    def test_unresolved_name_does_not_cache_a_miss(self):
        self.recorder.start(device_name="Nonexistent Device")
        self.assertIsNone(self.recorder._resolved_device_index)
        self.recorder.stop()
        # Retries resolution next time rather than sticking with a stale miss.
        self.recorder.start(device_name="Nonexistent Device")
        self.assertEqual(self.list_devices_calls, 2)
        self.recorder.stop()

    def test_refresh_devices_invalidates_cache(self):
        self.recorder.start(device_name="USB Headset")
        self.recorder.stop()
        self.assertEqual(self.recorder._resolved_device_index, 1)

        with mock.patch("sounddevice._terminate"), mock.patch("sounddevice._initialize"):
            self.recorder.refresh_devices()

        self.assertIsNone(self.recorder._resolved_device_index)
        self.assertIsNone(self.recorder._resolved_device_name)


class RefreshDevicesWhileRecordingTest(unittest.TestCase):
    def setUp(self):
        self.recorder = Recorder()
        self._sd_patcher = mock.patch("sounddevice.InputStream", _FakeStream)
        self._sd_patcher.start()

    def tearDown(self):
        self._sd_patcher.stop()

    def test_refresh_is_noop_while_recording(self):
        self.recorder.start()
        try:
            with mock.patch("sounddevice._terminate") as fake_terminate, mock.patch(
                "sounddevice._initialize"
            ) as fake_initialize:
                self.recorder.refresh_devices()
            fake_terminate.assert_not_called()
            fake_initialize.assert_not_called()
        finally:
            self.recorder.stop()


if __name__ == "__main__":
    unittest.main()
