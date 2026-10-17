"""Grows the VRAM BAR of Battlemage cards on boards whose BIOS has no Resizable BAR support.

The BIOS sizes the BAR and every bridge window above it to the card's 256 MiB default, and the kernel can't
grow them later: the card's own switch port has a BAR that pins its parent window in place. Setting the size in
config space, then removing and rescanning the whole root port, makes the kernel lay that port out from scratch.
Needs Above 4G Decoding in the BIOS. Adapted from immich-app/pokedex.

Runs as an init container of the DRA kubelet plugin: after a reboot kubelet prepares every claim through the
plugin again, so no pod gets the card before this is done. If something has it open regardless (the plugin pod
was replaced on a running node), the card is left alone until the next reboot.
"""

import os
import struct
import time
from pathlib import Path

PCI = Path("/sys/bus/pci")
PROC = Path("/host/proc")
VENDOR = "0x8086"
DEVICES = {"0xe20b", "0xe212"}  # Arc B580, Arc Pro B50
BAR = 2  # the VRAM
BRIDGE = "0x0604"  # class

COMMAND = 0x04
MEMORY_SPACE = 1 << 1
EXTENDED_CAPABILITIES = 0x100
REBAR = 0x15  # capability ID
REBAR_CONTROL = 0x08  # of the first entry
REBAR_INDEX = 0x7  # field of the control register: which BAR the entry sizes
REBAR_SIZE = 0x3F00  # field of the control register: 2^n MiB


def read(dev, name):
    return (dev / name).read_text().strip()


def config(dev, offset, fmt="I", value=None):
    with open(dev / "config", "r+b") as f:
        f.seek(offset)
        if value is None:
            return struct.unpack("<" + fmt, f.read(struct.calcsize(fmt)))[0]
        f.write(struct.pack("<" + fmt, value))


def bar_mib(dev):
    start, end, _ = read(dev, "resource").splitlines()[BAR].split()
    return (int(end, 16) - int(start, 16) + 1) >> 20


def endpoints(under):
    return [d for d in (PCI / "devices").iterdir() if under in d.resolve().parents and not read(d, "class").startswith(BRIDGE)]


def rebar_control(dev):
    offset = EXTENDED_CAPABILITIES
    while offset:
        header = config(dev, offset)
        if header & 0xFFFF == REBAR:
            return offset + REBAR_CONTROL
        offset = header >> 20


def window_fits(root, mib):
    """Whether the root bus has a window above 4 GiB that the grown BAR can go in (Above 4G Decoding is on)."""
    bus = f"PCI Bus {root.parent.name.removeprefix('pci')}"
    for line in Path("/proc/iomem").read_text().splitlines():
        span, _, what = line.partition(" : ")
        if not line.startswith(" ") and what == bus:
            start, end = (int(x, 16) for x in span.split("-"))
            if start >= 1 << 32 and end - start + 1 >= mib << 20:
                return True
    return False


def users(under):
    """Processes with a DRM node of a device under `under` open."""
    nodes = {os.makedev(*map(int, n.read_text().split(":"))) for d in endpoints(under) for n in d.glob("drm/*/dev")}
    found = set()
    for pid in PROC.iterdir():
        if not pid.name.isdigit():
            continue
        try:
            fds = list((pid / "fd").iterdir())
        except FileNotFoundError:
            continue
        for fd in fds:
            try:
                # only stat device nodes: stat on anything else can hang on a stuck network filesystem
                if os.readlink(fd).startswith("/dev/") and fd.stat().st_rdev in nodes:
                    found.add(f"{read(pid, 'comm')} ({pid.name})")
                    break
            except FileNotFoundError:
                pass
    return found


for dev in sorted((PCI / "devices").iterdir()):
    if read(dev, "vendor") != VENDOR or read(dev, "device") not in DEVICES:
        continue
    size = int(read(dev, f"resource{BAR}_resize"), 16).bit_length() - 1  # bit n: 2^n MiB
    if bar_mib(dev) >= 1 << size:
        print(f"{dev.name}: BAR{BAR} is already {bar_mib(dev)} MiB")
        continue

    control = rebar_control(dev)
    if control is None or config(dev, control) & REBAR_INDEX != BAR:
        print(f"{dev.name}: skipped, first Resizable BAR entry isn't BAR{BAR}")
        continue

    root = next(p for p in dev.resolve().parents if p.parent.name.startswith("pci"))
    card = dev.resolve().parent.parent  # its own switch port
    if others := [d.name for d in endpoints(root) if card not in d.resolve().parents]:
        print(f"{dev.name}: skipped, root port {root.name} also has {', '.join(others)}")
        continue
    if not window_fits(root, 1 << size):
        print(f"{dev.name}: skipped, no window above 4 GiB fits {1 << size} MiB")
        continue
    if busy := users(card):
        print(f"{dev.name}: skipped, in use by {', '.join(sorted(busy))}")
        continue

    print(f"{dev.name}: resizing BAR{BAR} {bar_mib(dev)} -> {1 << size} MiB via root port {root.name}")
    for d in endpoints(card):
        if (d / "driver").exists():
            (d / "driver/unbind").write_text(d.name)
    # stop decoding memory, or the new size overlaps whatever sits above the old BAR
    config(dev, COMMAND, "H", config(dev, COMMAND, "H") & ~MEMORY_SPACE)
    config(dev, control, value=config(dev, control) & ~REBAR_SIZE | size << 8)
    (PCI / "devices" / root.name / "remove").write_text("1")
    (PCI / "rescan").write_text("1")
    time.sleep(5)
    print(f"{dev.name}: BAR{BAR} is now {bar_mib(dev)} MiB")
