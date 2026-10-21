#!/usr/bin/env python3
# Copyright © Advanced Micro Devices, Inc., or its affiliates. All rights reserved.
# SPDX-License-Identifier: MIT
# Contributors: KC, BM
# Maintainer: KC

"""capture-app.py — snapshot a live device's installed application into a recipe.

An application slot is a scarce resource. This captures whatever is installed in
one and writes a self-contained bundle recipe directory, so it can be rebuilt
into a .rave2app and reinstalled later — on the same board or another one.

    ./host/capture-app.py <board-ip> [options]

Run it while the target sits in Buddy (the menu), so the application is not the
running system. The result is a directory containing `recipe.yaml` plus the
captured members, ready for:

    python3 bundles/_packer/make_bundle.py <capture-dir>/recipe.yaml

Two installed shapes are recognised, and detected automatically:

  * rootfs-style (`x86-rootfs-tar`, `x86-rootfs-ext4`, `custom-build`) — a single
    application slot, captured as a tar.
  * disk-image (`x86-disk-image`) — several partitions in the app region,
    captured one raw member each.

What the bundle reproduces is the slot *as it currently is*. It does not care how
the application was installed, or what it will be reinstalled onto; the guarantee
is that a reinstalled clone boots with the same arguments.
"""

import argparse
import datetime
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys

SSH_OPTS = [
    "-o", "StrictHostKeyChecking=no",
    "-o", "UserKnownHostsFile=/dev/null",
    "-o", "IdentityAgent=none",
    "-o", "LogLevel=ERROR",
    "-o", "ConnectTimeout=10",
]

# Where the menu records what it installed, and how it boots it.
INSTALLED_APP_JSON = "/var/lib/rave-menu/storage/installed-app.json"
REGION_MANIFEST = "/var/lib/rave-menu/app-region.json"
ESP_CANDIDATES = ("/boot/efi/EFI/BOOT", "/boot/efi/EFI/rave2", "/boot/EFI/BOOT")

ROOTFS_KINDS = ("x86-rootfs-tar", "x86-rootfs-ext4", "custom-build")

GIB = 1 << 30
MIB = 1 << 20
SECTOR = 512
# Mirrors make_bundle.STANDARD_SLOT_GIB: below this a bundle is not jumbo.
STANDARD_SLOT_GIB = 50
# A rootfs is restored by unpacking, so the slot has to hold the tar's contents
# plus ext4 metadata and the 5% mkfs keeps in reserve.
ROOTFS_OVERHEAD = 1.10
# Short codes app_region.py understands, so a generated recipe stays readable.
# It also accepts a raw GUID, which is the fallback for anything not listed.
TYPE_CODES = {
    "EBD0A0A2-B9E5-4433-87C0-68B6B72699C7": "0700",
    "0FC63DAF-8483-4772-8E79-3D69D8477DE4": "8300",
    "0657FD6D-A4AB-43C4-84E5-0933C84B4F4F": "8200",
    "C12A7328-F81F-11D2-BA4B-00A0C93EC93B": "ef00",
}

# Refused by the device's adopt_boot_entry(), so refuse at capture time instead
# of shipping a bundle that cannot install.
UNSAFE_ENTRY_CHARS = re.compile(r"""[`"'${}\\\r]""")


class CaptureError(Exception):
    """Fatal, with a message intended for the operator."""


def say(msg):
    print(f"==> {msg}", flush=True)


def warn(msg):
    print(f"    WARNING: {msg}", flush=True)


class Target:
    """A board reachable over ssh."""

    def __init__(self, host, user):
        self.host = host
        self.user = user
        self.dest = f"{user}@{host}"

    def _argv(self, command):
        return ["ssh", *SSH_OPTS, self.dest, command]

    def run(self, command, check=True):
        proc = subprocess.run(self._argv(command), capture_output=True, text=True)
        if check and proc.returncode != 0:
            raise CaptureError(
                f"remote command failed ({proc.returncode}): {command}\n"
                f"{proc.stderr.strip()}"
            )
        return proc.stdout

    def read_file(self, path, sudo=True):
        """Return the contents of a remote file, or None if it is not there."""
        cat = f"sudo cat {shlex.quote(path)}" if sudo else f"cat {shlex.quote(path)}"
        proc = subprocess.run(self._argv(f"{cat} 2>/dev/null"),
                              capture_output=True, text=True)
        return proc.stdout if proc.returncode == 0 else None

    def stream_to_file(self, command, dest_path, label, expect_bytes=None):
        """Run a remote command and write its stdout to `dest_path`.

        Returns (bytes_written, sha256). The digest is taken over what is stored,
        which is what the device verifies on install.
        """
        digest = hashlib.sha256()
        written = 0
        next_report = 1 << 30
        proc = subprocess.Popen(self._argv(command),
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            with open(dest_path, "wb") as out:
                while True:
                    chunk = proc.stdout.read(4 << 20)
                    if not chunk:
                        break
                    out.write(chunk)
                    digest.update(chunk)
                    written += len(chunk)
                    if written >= next_report:
                        pct = f" ({100 * written // expect_bytes}%)" if expect_bytes else ""
                        say(f"  {label}: {written >> 30} GiB{pct}")
                        next_report += 1 << 30
        finally:
            proc.stdout.close()
            rc = proc.wait()
            stderr = proc.stderr.read().decode("utf-8", "replace").strip()
            proc.stderr.close()
        if rc != 0:
            os.unlink(dest_path)
            raise CaptureError(f"{label}: remote capture failed ({rc}): {stderr}")
        if expect_bytes is not None and written != expect_bytes:
            os.unlink(dest_path)
            raise CaptureError(
                f"{label}: expected {expect_bytes} bytes, got {written}. "
                "Refusing a truncated capture."
            )
        return written, digest.hexdigest()

# --- probing -----------------------------------------------------------------

def assert_no_install_running(target):
    """An install in flight makes every source of truth here unreliable.

    `installed-app.json` and the region manifest are written at the *end* of an
    install, so mid-install they still describe the previous app — while the
    partitions they name may already be gone. The slot is also mounted
    read-write, so anything captured would be torn.
    """
    procs = target.run(
        "ps w 2>/dev/null | grep -E 'install_app_bundle|program_remote_versal|"
        "program_versal_payload' | grep -v grep || true")
    if procs.strip():
        first = procs.strip().splitlines()[0].strip()
        raise CaptureError(
            "an install is still running on this board, so what it reports about "
            "itself is not yet true:\n  " + first[:120] + "\n"
            "Wait for it to finish, then capture."
        )


def probe_installed_app(target):
    raw = target.read_file(INSTALLED_APP_JSON)
    if raw is None:
        raise CaptureError(
            f"{INSTALLED_APP_JSON} not found — this board has no App Store "
            "installation to capture."
        )
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise CaptureError(f"{INSTALLED_APP_JSON} is not valid JSON: {exc}")
    x86 = data.get("x86") or {}
    kind = x86.get("kind")
    if not kind:
        raise CaptureError(
            "the installed-app record has no x86 kind — the last install did not "
            "complete, so there is nothing safe to capture."
        )
    return data, kind


def load_region_manifest(target, required=False):
    raw = target.read_file(REGION_MANIFEST)
    if raw is None:
        if required:
            raise CaptureError(
                f"{REGION_MANIFEST} not found; cannot read the app region.")
        return None
    try:
        return json.loads(raw)
    except ValueError as exc:
        raise CaptureError(f"{REGION_MANIFEST} is not valid JSON: {exc}")


def jumbo_storage(manifest, needed_bytes, overhead=1.0, slack_gib=0):
    """The storage declaration a clone of this slot needs, or None if standard.

    A jumbo app is installed into the disk tail rather than the 50 GiB standard
    slot, so a clone that forgets to say so is built for a slot its members
    cannot fit — and the device only finds out mid-install, after it has already
    cleared the region.

    The floor is measured, not copied from the source bundle: the source's own
    minimum only says what it could have been installed into, and a filesystem
    captured from a roomier slot needs more than that. `needed_bytes` accounts
    for fixed partition sizes and the grow member (or the rootfs archive).
    Shrinking grow members prevents each restore into a bigger slot from
    raising the next clone's floor again. Rootfs archives additionally need
    overhead and slack for the filesystem created when they are unpacked.
    """
    if (manifest or {}).get("region_kind") != "jumbo":
        return None
    # Raw disk members already include filesystem metadata, and their layout
    # is rounded to MiB below. Adding padding here would make a full-size clone
    # require more room than the source region it demonstrably fits inside.
    minimum = -(-int(needed_bytes * overhead) // GIB) + slack_gib
    # The source ran as a jumbo app, so it stays one however little of its slot
    # it turned out to be using; 51 GiB is the smallest floor a jumbo bundle may
    # declare.
    minimum = max(minimum, STANDARD_SLOT_GIB + 1)
    region = (manifest.get("region") or {})
    first, last = region.get("first_sector"), region.get("last_sector")
    region_gib = ((last - first + 1) * SECTOR) // GIB if first and last else 0
    # The source ran in the region it was given, so ask for that much back when
    # the room is there; never less than the floor.
    return {"minimum_storage": minimum, "desired_storage": max(minimum, region_gib)}


def assert_not_booted_from(target, partuuids):
    """Capturing the filesystem we are running from would give a torn snapshot."""
    cmdline = target.run("cat /proc/cmdline")
    match = re.search(r"root=PARTUUID=(\S+)", cmdline)
    running = match.group(1).lower() if match else ""
    for name, partuuid in partuuids.items():
        if partuuid and running and partuuid.lower() == running:
            raise CaptureError(
                f"the target is booted FROM {name}. Reboot into Buddy and retry."
            )


# --- boot contract -----------------------------------------------------------

def read_boot_contract(target):
    """Read the boot entry the device is actually using for the app.

    The deployed entry is the source of truth: it is what boots today, whatever
    produced it.
    """
    for esp in ESP_CANDIDATES:
        cfg = target.read_file(f"{esp}/app-boot.cfg")
        if cfg is None:
            continue
        mode = ""
        match = re.search(r"^\s*set\s+app_mode=(\S+)", cfg, re.M)
        if match:
            mode = match.group(1)
        entry = target.read_file(f"{esp}/app-entry.cfg")
        return {"esp": esp, "mode": mode, "deployed_entry": entry}
    raise CaptureError(
        "no rave2 app-boot.cfg found on the ESP; cannot tell how this app boots."
    )


def entry_body(text):
    """Strip generated lines from a deployed app-entry.cfg, leaving the payload.

    `set root=(...)` and the leading comment are written by the installer for the
    *source* device, so they must not travel with the clone: the destination
    generates its own.
    """
    lines = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if re.match(r"^set\s+root=", line):
            continue
        lines.append(" ".join(line.split()))
    return lines


def normalise_for_compare(lines):
    """Drop the parts that legitimately differ between source and destination."""
    out = []
    for line in lines:
        line = re.sub(r"root=PARTUUID=\S+", "root=PARTUUID=<uuid>", line)
        out.append(line)
    return out


def simulate_adopt(body_lines, partuuid_placeholder="<uuid>"):
    """Apply the device's adopt_boot_entry() rewrites to a raw menuentry body.

    Mirrors install_app_bundle.sh: strip LABEL=, rewrite root= to a PARTUUID with
    the fixed `rw rootwait quiet` suffix. Kept deliberately identical so the
    cross-check below compares like with like.
    """
    out = []
    for line in body_lines:
        line = re.sub(r"\s+LABEL=\S*", " ", line)
        line = re.sub(r"\s+root=\S*",
                      f" root=PARTUUID={partuuid_placeholder} rw rootwait quiet", line)
        out.append(" ".join(line.split()))
    return out


MENUENTRY_RE = re.compile(
    r"^\s*menuentry\s+(['\"])(?P<name>.*?)\1\s*\{(?P<body>.*?)^\s*\}",
    re.S | re.M,
)


def find_origin_entries_file(target, boot_dev, deployed_lines):
    """Find which file on app-boot the deployed entry was adopted from.

    Verified, not guessed: a candidate is accepted only if adopting it reproduces
    the deployed entry. If nothing matches, the slot has diverged from whatever
    installed it and the caller must fall back.

    Returns (path_on_app_boot, entry_name) or None.
    """
    mnt = "/tmp/app-clone-boot"
    target.run(f"sudo mkdir -p {mnt} && sudo mount -o ro {boot_dev} {mnt}")
    try:
        listing = target.run(
            f"sudo grep -rl menuentry {mnt} 2>/dev/null || true")
        want = normalise_for_compare(deployed_lines)
        for path in listing.splitlines():
            path = path.strip()
            if not path:
                continue
            text = target.read_file(path)
            if not text:
                continue
            for match in MENUENTRY_RE.finditer(text):
                body = [ln.strip() for ln in match.group("body").splitlines() if ln.strip()]
                got = normalise_for_compare(simulate_adopt(body))
                if got == want:
                    return path[len(mnt):] or "/", match.group("name")
        return None
    finally:
        target.run(f"sudo umount {mnt} 2>/dev/null; sudo rmdir {mnt} 2>/dev/null",
                   check=False)


# --- capture: disk-image -----------------------------------------------------

def capture_disk_image(target, out_dir, app, shrink=True):
    manifest = load_region_manifest(target, required=True)
    parts = manifest.get("partitions") or []
    if not parts:
        raise CaptureError("the region manifest lists no partitions.")

    assert_not_booted_from(target, {p["name"]: p.get("partuuid") for p in parts})

    types = read_partition_types(target, manifest.get("disk", "/dev/nvme0n1"))
    device_dir = os.path.join(out_dir, "device")
    os.makedirs(device_dir, exist_ok=True)

    layout = []
    needed_bytes = 0
    root_shrunk = False
    for part in parts:
        name = part["name"]
        dev = part["device"]
        fs = part.get("filesystem") or ""
        size = int(target.run(f"sudo blockdev --getsize64 {dev}").strip())
        suffix = {"vfat": "fat", "ext4": "ext4"}.get(fs, "img")
        member = f"x86/disk/p{part['index']}.{suffix}"
        local = os.path.join(device_dir, os.path.basename(member))

        if fs == "ext4":
            # e2image -ra writes zeros over free blocks, so the ~20 GiB of empty
            # space in a grown app-root compresses away in the bundle's gzip.
            # Streaming to stdout also needs no scratch space on the target.
            # It emits the *filesystem*, which may be smaller than its partition.
            cmd = f"sudo e2image -ra {dev} -"
            expect = ext4_bytes(target, dev)
        else:
            cmd = f"sudo dd if={dev} bs=4M"
            expect = size
        say(f"capturing {name} ({fs or 'raw'}, {expect >> 20} MiB of a "
            f"{size >> 20} MiB partition) from {dev}")
        written, sha = target.stream_to_file(cmd, local, name, expect_bytes=expect)
        say(f"  {name}: {written} bytes, sha256 {sha[:16]}…")
        # Older format-v2 installers only expand grow partitions. Shrinking a
        # fixed filesystem would silently remove its free space on restore.
        if fs == "ext4" and part.get("grow") and shrink:
            before = written
            written = shrink_ext4(local, name)
            if name == "app-root":
                root_shrunk = written < before

        item = {
            "part": name,
            "produces": member,
            "from": f"device/{os.path.basename(member)}",
            "filesystem": fs,
            "type": types.get(dev, "8300"),
            "grow": bool(part.get("grow")),
        }
        if not item["grow"]:
            item["size_mib"] = size >> 20
        # Fixed partitions keep their declared sizes. Round the grow member up
        # to the device's MiB alignment, without adding a gap to already aligned
        # members: a full-size capture must still fit its original region.
        needed_bytes += (-(-written // MIB) * MIB if item["grow"] else size)
        layout.append(item)

    boot = resolve_disk_image_boot(target, parts)
    return {"shape": "x86-disk-image", "layout": layout, "boot": boot,
            "shrunk": root_shrunk,
            "storage": jumbo_storage(manifest, needed_bytes)}


def ext4_bytes(target, dev):
    """Size of the ext4 filesystem on `dev`, which e2image will emit."""
    head = target.run(f"sudo dumpe2fs -h {dev} 2>/dev/null")
    fields = {}
    for key in ("Block count", "Block size"):
        match = re.search(rf"^{key}:\s+(\d+)", head, re.M)
        if not match:
            raise CaptureError(f"cannot read '{key}' from {dev}; is it really ext4?")
        fields[key] = int(match.group(1))
    return fields["Block count"] * fields["Block size"]


# e2fsck: 0 clean, 1 errors corrected, 2 corrected and a reboot wanted. Anything
# above that leaves a filesystem resize2fs must not be pointed at.
FSCK_OK = (0, 1, 2)
# What an e2fsprogs too old for the image says. It is not a damaged filesystem,
# and the distinction decides whether another set of tools is worth trying.
TOO_OLD = re.compile(r"unsupported feature|Get a newer version", re.I)
# Borrowed when this host's e2fsprogs cannot read the captured filesystem: a
# VAS3 rootfs carries ext4's orphan_file, which e2fsprogs refuses before 1.47.
# Small, stock, and it only ever sees the capture directory.
EXT4_TOOLS_IMAGE = "debian:bookworm-slim"


def ext4_tool_runners():
    """Ways to run e2fsprogs against a captured image, best first.

    Yields (where, run) where run(tool, args, path) behaves like subprocess.run.
    """
    if all(shutil.which(tool) for tool in ("e2fsck", "resize2fs")):
        yield "this host", lambda tool, args, path: subprocess.run(
            [tool, *args, path], capture_output=True, text=True)

    engine = next((e for e in ("docker", "podman") if shutil.which(e)), None)
    if engine:
        def in_container(tool, args, path):
            directory, name = os.path.split(os.path.abspath(path))
            return subprocess.run(
                [engine, "run", "--rm", "--user", f"{os.getuid()}:{os.getgid()}",
                 "-v", f"{directory}:/work", "-w", "/work", EXT4_TOOLS_IMAGE,
                 tool, *args, name],
                capture_output=True, text=True)
        yield f"{engine} {EXT4_TOOLS_IMAGE}", in_container


def shrink_ext4(path, label):
    """Shrink a captured ext4 image to the data it holds. Returns its new size.

    A capture is the size of the slot it came from, not of its contents, so a
    clone restored into a roomier slot grows to fill it and the *next* clone
    asks for that much again. Shrinking here breaks that ratchet: the device
    grows the filesystem back to whatever partition it lands in, so the only
    thing lost is free space that was never data.

    Done on the host copy — the board's filesystem is never written to. If no
    available e2fsprogs can read the image, the full-size capture is kept and
    the clone is simply sized for the slot it came from.
    """
    before = os.path.getsize(path)
    tried = False
    for where, run in ext4_tool_runners():
        tried = True
        say(f"  {label}: checking the captured filesystem ({where})")
        fsck = run("e2fsck", ["-fp"], path)
        if fsck.returncode not in FSCK_OK:
            output = f"{fsck.stdout}\n{fsck.stderr}"
            if TOO_OLD.search(output):
                warn(f"{label}: the e2fsprogs on {where} is too old for this "
                     "filesystem's ext4 features; trying another")
                continue
            warn(f"{label}: e2fsck exited {fsck.returncode}; keeping the "
                 f"full-size capture.\n    {output.strip()[:200]}")
            return before
        if fsck.returncode != 0:
            warn(f"{label}: the source filesystem had errors, which e2fsck "
                 "corrected in the capture. Worth looking at the board.")

        say(f"  {label}: shrinking to its contents")
        resize = run("resize2fs", ["-M"], path)
        if resize.returncode != 0:
            warn(f"{label}: resize2fs exited {resize.returncode}; keeping the "
                 f"capture as it is.\n    {resize.stderr.strip()[:200]}")
            return os.path.getsize(path)
        after = os.path.getsize(path)
        say(f"  {label}: {before >> 20} MiB -> {after >> 20} MiB")
        return after

    if tried:
        warn(f"{label}: nothing available here can read this filesystem, so the "
             f"{before >> 30} GiB capture is kept at the size of the slot it came "
             "from and the clone will ask for a slot that large. Install "
             "e2fsprogs 1.47 or later, or docker/podman, and recapture.")
    else:
        warn(f"{label}: no e2fsck/resize2fs and no docker/podman; keeping the "
             f"full-size capture ({before >> 30} GiB)")
    return before


def read_partition_types(target, disk):
    """Map device node -> GPT type, as a short code where one is known."""
    out = {}
    for line in target.run(f"sudo sfdisk -d {disk} 2>/dev/null || true").splitlines():
        match = re.match(r"^(\S+)\s*:.*\btype=([0-9A-Fa-f-]{36})", line)
        if match:
            guid = match.group(2).upper()
            out[match.group(1)] = TYPE_CODES.get(guid, guid)
    return out


def resolve_disk_image_boot(target, parts):
    contract = read_boot_contract(target)
    if contract["mode"] != "entries" or not contract["deployed_entry"]:
        raise CaptureError(
            f"this disk-image app boots with app_mode={contract['mode'] or 'unknown'}, "
            "which the cloner cannot reproduce yet. Only adopted-entry installs "
            "are supported."
        )
    deployed = entry_body(contract["deployed_entry"])
    if not deployed:
        raise CaptureError("the deployed app-entry.cfg has no usable body.")
    unsafe = UNSAFE_ENTRY_CHARS.search("\n".join(deployed))
    if unsafe:
        raise CaptureError(
            f"the deployed boot entry contains {unsafe.group(0)!r}, which the "
            "device refuses. Refusing to produce a bundle that cannot install."
        )

    boot_dev = next((p["device"] for p in parts if p["name"] == "app-boot"), None)
    if not boot_dev:
        raise CaptureError("the region has no 'app-boot' partition to read entries from.")

    say("locating the entries file the running entry came from")
    found = find_origin_entries_file(target, boot_dev, deployed)
    if not found:
        raise CaptureError(
            "the running boot entry does not match any entries file on app-boot, "
            "so this slot has diverged from whatever installed it. Reproducing it "
            "needs boot.cmdline (plan Phase 3b), which is not implemented yet.\n"
            "The running entry was:\n  " + "\n  ".join(deployed)
        )
    path, entry = found
    say(f"  adopted from {path} (menuentry '{entry}')")
    return {"entries_cfg": path, "entry": entry, "deployed": deployed}


# --- capture: rootfs ---------------------------------------------------------

def capture_rootfs(target, out_dir, app, slot_label, exclude_versal):
    slot_dev = f"/dev/disk/by-partlabel/{slot_label}"
    if target.run(f"test -e {slot_dev} && echo ok || true").strip() != "ok":
        raise CaptureError(f"no partition labelled '{slot_label}' on the target")
    partuuid = target.run(f"sudo blkid -s PARTUUID -o value {slot_dev}").strip()
    assert_not_booted_from(target, {f"the app slot ({slot_label})": partuuid})

    mnt = "/mnt/app-capture"
    existing = target.run(
        f"findmnt -rn -o TARGET --source {slot_dev} 2>/dev/null | head -n 1").strip()
    if existing:
        say(f"using the menu's existing mount at {existing}")
        mount_point, owned = existing, False
    else:
        target.run(f"sudo mkdir -p {mnt} && sudo mount -o ro {slot_dev} {mnt}")
        mount_point, owned = mnt, True

    device_dir = os.path.join(out_dir, "device")
    os.makedirs(device_dir, exist_ok=True)
    local = os.path.join(device_dir, "rootfs.tar")
    try:
        excludes = "--exclude=./lib/versal " if exclude_versal else ""
        say(f"streaming the slot from {mount_point}")
        # --numeric-owner keeps uid/gid without consulting either passwd database;
        # --one-file-system never descends into anything mounted inside the slot.
        _, sha = target.stream_to_file(
            f"sudo tar -cf - --numeric-owner --one-file-system {excludes}"
            f"-C {mount_point} .",
            local, "rootfs")
        payloads = probe_boot_payloads(target, mount_point)
    finally:
        if owned:
            target.run(f"sudo umount {mnt} 2>/dev/null || true", check=False)

    verify_rootfs_tar(local)
    boot = resolve_rootfs_boot(target, payloads)
    storage = jumbo_storage(load_region_manifest(target), os.path.getsize(local),
                            overhead=ROOTFS_OVERHEAD, slack_gib=1)
    return {"shape": "x86-rootfs-tar", "member": local, "sha256": sha, "boot": boot,
            "storage": storage}


def verify_rootfs_tar(path):
    listing = subprocess.run(["tar", "-tf", path], capture_output=True, text=True)
    if listing.returncode != 0:
        raise CaptureError(f"the capture is not a readable tar: {listing.stderr.strip()}")
    names = listing.stdout.splitlines()
    if not any(re.match(r"^\./boot/(vmlinuz|bzImage)", n) for n in names):
        raise CaptureError(
            "no kernel under ./boot — this image would not boot. Capture rejected.")
    bad = [n for n in names if n.startswith("/") or ".." in n.split("/")]
    if bad:
        raise CaptureError(f"capture contains unsafe path {bad[0]!r} — rejected.")


def probe_boot_payloads(target, mount_point):
    """Which boot payloads the slot holds. Must run while it is still mounted."""
    names = ("/boot/xen.gz", "/boot/bzImage", "/boot/initrd",
             "/boot/vmlinuz", "/boot/initrd.img")
    tests = " ".join(f"test -e {mount_point}{n} && echo {n};" for n in names)
    out = target.run(f"sudo sh -c '{tests} true'", check=False) or ""
    return set(out.split())


def menu_boot_params(target, esp):
    """The menu's own kernel arguments, which a direct-mode app silently inherits.

    Read from the config actually booting the board, not from our copy of the
    template: a fielded ESP may be older than this checkout.
    """
    cfg = target.read_file(f"{esp}/grub.cfg") or ""

    def literal(name):
        m = re.search(rf'^\s*set\s+{name}="([^"]*)"', cfg, re.M)
        return m.group(1).strip() if m else ""

    xen, kernel = literal("xen_params"), literal("kernel_params")
    # $ssdt_param expands from grubenv, so resolve it rather than copying a
    # variable reference into a file that will have no such variable.
    env = target.run(f"sudo grub-editenv {esp}/grubenv list 2>/dev/null || true",
                     check=False) or ""
    m = re.search(r"^ssdt_enabled=(\S+)", env, re.M)
    if m:
        kernel = f"{kernel} ssdt={m.group(1)}".strip()
    return xen, kernel


# Tokens install_app_bundle.sh's render_boot_entry()/adopt_boot_entry() append
# for themselves. They must not travel: the destination re-adds them around a
# root= only it can know.
INSTALLER_ADDED_ARGS = ("rw", "rootwait", "quiet")


def strip_installer_args(tokens):
    return [t for t in tokens
            if not t.startswith("root=") and t not in INSTALLER_ADDED_ARGS]


def cmdline_from_deployed(lines):
    """Turn a deployed app-entry.cfg body back into a boot.cmdline mapping.

    The inverse of the device's render_boot_entry(). A rootfs slot in
    app_mode=entries boots from an entry that is real but undeclared, and a
    recipe carrying no boot contract is refused by the packer -- so describe the
    entry the slot actually boots with, which is the same thing said in the form
    a recipe can hold.
    """
    cmdline = {}
    for line in lines:
        parts = line.split()
        if not parts:
            continue
        verb, rest = parts[0], parts[1:]
        if verb == "linux" and rest:
            cmdline["kernel"] = rest[0]
            args = " ".join(strip_installer_args(rest[1:]))
            if args:
                cmdline["kernel_args"] = args
        elif verb == "initrd" and rest:
            cmdline["ramdisk"] = rest[0]
        elif verb == "multiboot2" and rest:
            cmdline["hypervisor"] = rest[0]
            # rest[1] is the argv[0] placeholder Xen ignores, and which the
            # device re-inserts; anything after it is the real argument list.
            args = " ".join(strip_installer_args(rest[2:]))
            if args:
                cmdline["hypervisor_args"] = args
        elif verb == "module2" and rest:
            if "kernel" not in cmdline:
                cmdline["kernel"] = rest[0]
                args = " ".join(strip_installer_args(rest[1:]))
                if args:
                    cmdline["kernel_args"] = args
            else:
                cmdline["ramdisk"] = rest[0]
    if not cmdline.get("kernel"):
        raise CaptureError(
            "the deployed boot entry names no kernel, so it cannot be described "
            "as a boot.cmdline:\n  " + "\n  ".join(lines))
    return cmdline


def resolve_rootfs_boot(target, payloads):
    contract = read_boot_contract(target)
    if contract["mode"] == "entries" and contract["deployed_entry"]:
        deployed = entry_body(contract["deployed_entry"])
        return {"deployed": deployed, "entries_cfg": None,
                "cmdline": cmdline_from_deployed(deployed)}

    # app_mode=direct: the arguments were never the app's, they were the menu's.
    # Write down what this board was really running, so the clone boots the same
    # way on a destination whose menu defaults differ.
    xen, kernel = menu_boot_params(target, contract["esp"])
    if "/boot/xen.gz" in payloads and "/boot/bzImage" in payloads:
        cmdline = {"hypervisor": "/boot/xen.gz", "hypervisor_args": xen,
                   "kernel": "/boot/bzImage", "kernel_args": kernel}
        if "/boot/initrd" in payloads:
            cmdline["ramdisk"] = "/boot/initrd"
    elif "/boot/bzImage" in payloads:
        # The bare-metal branches of the template never passed $kernel_params.
        cmdline = {"kernel": "/boot/bzImage"}
        if "/boot/initrd" in payloads:
            cmdline["ramdisk"] = "/boot/initrd"
    elif "/boot/vmlinuz" in payloads:
        cmdline = {"kernel": "/boot/vmlinuz"}
        if "/boot/initrd.img" in payloads:
            cmdline["ramdisk"] = "/boot/initrd.img"
    else:
        raise CaptureError(
            "the app slot has no kernel where the boot menu looks for one "
            "(/boot/{xen.gz,bzImage,vmlinuz}); cannot describe how it boots")
    return {"deployed": None, "entries_cfg": None, "direct": True,
            "cmdline": cmdline}


# --- recipe emission ---------------------------------------------------------

def yaml_quote(text):
    return '"' + str(text).replace("\\", "\\\\").replace('"', '\\"') + '"'


def clone_source_id(app):
    """The source app's id, reduced to the [a-z0-9-] a bundle id allows."""
    return re.sub(r"[^a-z0-9-]+", "-", str(app.get("id", "app")).lower()).strip("-")


def render_recipe(app, capture, stamp, source_host):
    # The stamp lives in `version`, not `id`: the packer names the bundle
    # <id>-<version>, so putting it in both spells it twice. Two captures of the
    # same source app are two versions of one clone, which is what the catalog
    # already models.
    clone_id = f"app-clone-{clone_source_id(app)}"
    src_name = app.get("name") or app.get("id") or "application"
    label = f"{src_name} clone {stamp}"

    lines = [
        "# recipe.yaml — generated by host/capture-app.py. Do not hand-edit;",
        "# recapture instead, so the recipe and the members stay in step.",
        "#",
        f"# Captured from : {source_host}",
        f"# Captured at   : {datetime.datetime.now().astimezone().isoformat(timespec='seconds')}",
        f"# Source app    : {app.get('id')} {app.get('version', '')}".rstrip(),
        f"# Installed as  : {capture['shape']}",
        "#",
        "# Build it with:",
        "#     python3 bundles/_packer/make_bundle.py <this-dir>/recipe.yaml",
        "recipe_version: 1",
        "",
        f"id: {clone_id}",
        f"name: {yaml_quote(label)}",
        f'version: "{stamp}"',
        f"vendor: {yaml_quote(app.get('vendor', 'AMD'))}",
        "description: >-",
        f"  Clone of the '{app.get('id')}' application slot, captured from a live",
        f"  RAVE2 device on {stamp}.",
        "",
    ]

    storage = capture.get("storage")
    if storage:
        lines += [
            "# The source was installed in the jumbo region, not the 50 GiB standard",
            "# slot, so the clone has to ask for one too. minimum_storage is measured",
            "# from the members as captured, not copied from the source bundle;",
            "# desired_storage is the slot the source was running in.",
            "type: jumbo",
            f"minimum_storage: {storage['minimum_storage']}  # GiB",
            f"desired_storage: {storage['desired_storage']}  # GiB",
            "",
        ]

    if capture["shape"] == "x86-disk-image":
        note = ("# with `e2image -ra` and shrunk to its contents; the device grows it\n"
                "# back to fill whatever partition it lands in."
                if capture.get("shrunk") else
                "# with `e2image -ra` and kept at its source filesystem size.")
        lines += [
            "# The app region, one raw member per partition. app-root was captured",
            note,
            "components:",
            "  - name: app-disk",
            "    kind: x86-disk-image",
            "    layout:",
        ]
        for item in capture["layout"]:
            lines += [
                f"      - part: {item['part']}",
                f"        produces: {item['produces']}",
                "        source:",
                "          type: file",
                f"          from: {item['from']}",
            ]
            if item.get("size_mib") is not None:
                lines.append(f"        size_mib: {item['size_mib']}")
            if item["grow"]:
                lines.append("        grow: true")
            lines += [
                f'        type: "{item["type"]}"',
                f"        filesystem: {item['filesystem']}",
            ]
        lines += [
            "",
            "# Versal images ride along inside the captured filesystem as ordinary",
            "# files: they are restored but NOT reprogrammed (plan D18).",
            "",
            "boot:",
            f"  label: {yaml_quote(label)}",
            "  make_default: false",
            "  # Adopted from the app's own entries file. Verified at capture time:",
            "  # adopting this entry reproduces what the source board was running.",
            f"  entries_cfg: {capture['boot']['entries_cfg']}",
            f"  entry: {capture['boot']['entry']}",
        ]
    else:
        lines += [
            "slot:",
            "  filesystem: ext4",
            "  label: app-a",
            '  mkfs_options: "-O ^orphan_file"   # menu GRUB ext driver can\'t read orphan_file',
            "",
            "components:",
            "  - name: x86-rootfs",
            "    kind: x86-rootfs-tar",
            "    source:",
            "      type: file",
            "      from: device/rootfs.tar",
            "      produces: x86/rootfs.tar",
            "",
            "boot:",
            f"  label: {yaml_quote(label)}",
            "  make_default: false",
        ]
        cmdline = capture["boot"].get("cmdline")
        if cmdline:
            if capture["boot"].get("direct"):
                lines += [
                    "  # The source was in app_mode=direct, so it booted with the MENU's",
                    "  # arguments rather than any of its own. They are written down here so",
                    "  # the clone boots the same way on a board whose menu defaults differ.",
                ]
            else:
                lines += [
                    "  # Read back from the entry the source board was actually booting,",
                    "  # so the clone declares the boot it really had rather than",
                    "  # inheriting the destination menu's arguments.",
                ]
            lines += [
                "  # root= is deliberately absent: the installer sets it, knowing which",
                "  # partition the app landed on.",
                "  cmdline:",
            ]
            for key in ("hypervisor", "hypervisor_args", "kernel",
                        "kernel_args", "ramdisk"):
                if cmdline.get(key):
                    lines.append(f"    {key}: {yaml_quote(cmdline[key])}")

    lines += [
        "",
        "catalog:",
        f"  short_description: {yaml_quote('Clone of ' + str(src_name))}",
        "  description_long: >-",
        f"    A clone of the '{app.get('id')}' application slot taken from a live",
        "    RAVE2 device, so the exact system that was running there can be",
        "    reinstalled. x86 only — no Versal images are programmed.",
        "  experimental: true",
        "",
    ]
    return "\n".join(lines)


# --- main --------------------------------------------------------------------

def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Capture a live device's installed application into a bundle recipe.")
    parser.add_argument("host", nargs="?", default=os.environ.get("TARGET_HOST", ""),
                        help="board IP or hostname")
    parser.add_argument("--user", default=os.environ.get("TARGET_USER", "amd-edf"))
    parser.add_argument("--out-dir", default=os.environ.get(
        "OUT_DIR", os.path.expanduser("~/app-captures")))
    parser.add_argument("--slot-label", default=os.environ.get("SLOT_LABEL", "app-a"),
                        help="rootfs shape only: partlabel of the app slot")
    parser.add_argument("--exclude-versal", action="store_true",
                        default=os.environ.get("EXCLUDE_VERSAL") == "1",
                        help="rootfs shape only: drop /lib/versal from the capture")
    parser.add_argument("--no-shrink", action="store_true",
                        default=os.environ.get("NO_SHRINK") == "1",
                        help="disk-image shape only: keep captured ext4 images at "
                             "the size of the slot they came from")
    args = parser.parse_args(argv)

    if not args.host:
        parser.error("no target. Pass a board address, or set TARGET_HOST.")

    target = Target(args.host, args.user)
    say(f"target {target.dest}")
    target.run("true")

    assert_no_install_running(target)
    app, kind = probe_installed_app(target)
    say(f"installed: {app.get('name')} {app.get('version')} ({kind})")

    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir = os.path.join(args.out_dir, f"app-clone-{clone_source_id(app)}-{stamp}")
    os.makedirs(out_dir, exist_ok=True)
    say(f"capture directory {out_dir}")

    if kind == "x86-disk-image":
        capture = capture_disk_image(target, out_dir, app, shrink=not args.no_shrink)
    elif kind in ROOTFS_KINDS:
        capture = capture_rootfs(target, out_dir, app, args.slot_label,
                                 args.exclude_versal)
        if capture["boot"].get("direct"):
            warn("the source was booting with the menu's arguments, not its own; "
                 "they have been written into the recipe so the clone does not "
                 "depend on the destination's menu.")
    else:
        raise CaptureError(f"unsupported installed kind {kind!r}")

    recipe_path = os.path.join(out_dir, "recipe.yaml")
    with open(recipe_path, "w") as fh:
        fh.write(render_recipe(app, capture, stamp, args.host))

    total = sum(os.path.getsize(os.path.join(dp, f))
                for dp, _, fs in os.walk(out_dir) for f in fs)
    print(f"""
    capture : {out_dir}
    recipe  : {recipe_path}
    on disk : {total >> 20} MiB

Build the bundle with:

      python3 bundles/_packer/make_bundle.py {recipe_path}
""")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except CaptureError as exc:
        sys.stderr.write(f"capture-app: ERROR: {exc}\n")
        sys.exit(1)
    except KeyboardInterrupt:
        sys.stderr.write("\ncapture-app: interrupted\n")
        sys.exit(130)
