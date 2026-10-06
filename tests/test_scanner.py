"""The Windows (WIA) path, run against a fake win32com so it is tested on any OS."""
import sys
import types

import cv2
import pytest

from photopilot import scanner

from .test_detect import make_bed


class Prop:
    def __init__(self, pid, value, max_=None):
        self.PropertyID, self._v, self.SubTypeMax = int(pid), value, max_

    @property
    def Value(self):
        return self._v

    @Value.setter
    def Value(self, v):
        if self.SubTypeMax is not None and v > self.SubTypeMax:
            raise ValueError("out of range")  # what a WIA driver does
        self._v = v


class FakeImage:
    def __init__(self, bed):
        self.bed = bed

    def SaveFile(self, path):
        cv2.imwrite(path, self.bed)


class FakeItem:
    def __init__(self, bed):
        self.Properties = [Prop(p, 0, 99999) for p in ("6146", "4103", "4104", "6147", "6148",
                                                       "6149", "6150")]
        self.Properties += [Prop("6151", 0, 5100), Prop("6152", 0, 7020)]
        self.bed = bed

    def Transfer(self, fmt):
        assert fmt == scanner.WIA_FORMAT_BMP
        return FakeImage(self.bed)


class FakeDevice:
    def __init__(self, bed):
        # Bed a bit larger than what the item allows -> falls back to SubTypeMax.
        self.Properties = [Prop("3074", 8600), Prop("3075", 11800), Prop("3088", 0)]
        self.Items = {1: FakeItem(bed)}


class FakeInfo:
    Type = 1
    DeviceID = "{6BDD1FC6-810F-11D0-BEC7-08002BE2092F}\\0000"

    def __init__(self, bed):
        self.Properties = [Prop("7", "EPSON Perfection V850")]
        self.dev = FakeDevice(bed)

    def Connect(self):
        return self.dev


def install_fake(monkeypatch, info):
    manager = types.SimpleNamespace(DeviceInfos=[info])
    client = types.SimpleNamespace(Dispatch=lambda name: manager)
    mod = types.ModuleType("win32com")
    mod.client = client
    monkeypatch.setitem(sys.modules, "win32com", mod)
    monkeypatch.setitem(sys.modules, "win32com.client", client)
    monkeypatch.setattr(scanner, "IS_WINDOWS", True)


def test_wia_scan(monkeypatch, tmp_path):
    bed, _ = make_bed(245)
    info = FakeInfo(bed)
    install_fake(monkeypatch, info)

    assert scanner.list_devices() == [(FakeInfo.DeviceID, "EPSON Perfection V850")]
    img = scanner.scan(device="v850", dpi=600, keep_as=tmp_path / "raw.bmp")
    assert img.shape == bed.shape
    assert (tmp_path / "raw.bmp").exists()

    dev = info.dev
    props = {str(p.PropertyID): p.Value for p in dev.Items[1].Properties}
    assert props["6147"] == props["6148"] == 600
    assert props["6151"] == 5100 and props["6152"] == 7020  # clamped to the driver's maximum
    assert dev.Properties[2].Value == 2  # flatbed selected


def test_wia_unknown_device(monkeypatch, tmp_path):
    install_fake(monkeypatch, FakeInfo(make_bed(245)[0]))
    try:
        scanner.scan(device="V39")
    except scanner.ScannerError as e:
        assert "V39" in str(e)
    else:
        raise AssertionError("expected ScannerError")


@pytest.mark.skipif(sys.platform == "win32", reason="SANE path is for Linux/macOS")
def test_sane_scan_in_memory(monkeypatch, tmp_path):
    bed, _ = make_bed(245)
    src = tmp_path / "bed.tif"
    cv2.imwrite(str(src), bed)
    fake = tmp_path / "bin" / "scanimage"
    fake.parent.mkdir()
    fake.write_text(f'#!/bin/sh\necho "$@" > "{tmp_path}/args"\ncat "{src}"\n')
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", f"{fake.parent}:/usr/bin:/bin")
    img = scanner.scan(device="epson2:libusb:001:004", dpi=1200)
    assert img.shape == bed.shape
    args = (tmp_path / "args").read_text()
    assert "--resolution 1200" in args and "-d epson2:libusb:001:004" in args
    assert sorted(p.name for p in tmp_path.iterdir()) == ["args", "bed.tif", "bin"]  # no temp files
