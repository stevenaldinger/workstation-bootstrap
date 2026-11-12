"""Pinned versions, image digests, and tunable limits."""

from __future__ import annotations

import re

from prove_engine.model import Image

TART_VERSION = "2.41.1"
TART_URL = f"https://github.com/cirruslabs/tart/releases/download/{TART_VERSION}/tart.tar.gz"
TART_SHA256 = "e680a734febccfce7d4e910c1f7f13fc3b00f19a181c945e2d746de3214c32c2"

GUEST_USER = "admin"
# The vanilla image's well-known login. The guest is a throwaway clone with no data in it.
GUEST_PASSWORD = "admin"  # noqa: S105
DEFAULT_COMMAND = "sw_vers -productVersion && uname -m"
BASE_PREFIX = "ws-base-"
RUN_PREFIX = "wsrun-"
SCENARIO_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,40}$")
SHARE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,40}$")

# The image ships a 50 GB disk with about 18 GB free; a full bootstrap needs far more.
DISK_GB = 100
# The first pull needs the compressed image plus its expanded copy.
FREE_GB_FIRST_PULL = 60
FREE_GB_PER_RUN = 15
IP_WAIT_SECONDS = 180
SSH_READY_SECONDS = 120
VNC_WAIT_SECONDS = 30
STOP_SECONDS = 30
EXIT_TIMED_OUT = 124
# Every Tart command has a time limit so a stuck one cannot hang a run. `tart pull` moves about
# 24 GB, so it prints a still-running line every HEARTBEAT_SECONDS and has a long limit.
TART_TIMEOUT_SECONDS = 120
CLONE_TIMEOUT_SECONDS = 1800
PULL_TIMEOUT_SECONDS = 4 * 3600
HEARTBEAT_SECONDS = 30

# How the engine reaches the throwaway guest. The guest is cloned fresh for every run, so its
# host key changes each time and a known_hosts file would only produce false alarms
# (StrictHostKeyChecking=no, no known_hosts). It accepts only the image's well-known password
# (PubkeyAuthentication=no). Host config is ignored (-F /dev/null), LogLevel=ERROR hides the
# "Permanently added" notice, and the keepalive options end a hung session instead of
# waiting for the command timeout.
_SSH_OPTION_PAIRS = (
    ("-F", "/dev/null"),
    ("-o", "StrictHostKeyChecking=no"),
    ("-o", "UserKnownHostsFile=/dev/null"),
    ("-o", "GlobalKnownHostsFile=/dev/null"),
    ("-o", "LogLevel=ERROR"),
    ("-o", "PubkeyAuthentication=no"),
    ("-o", "PreferredAuthentications=password,keyboard-interactive"),
    ("-o", "NumberOfPasswordPrompts=1"),
    ("-o", "ConnectTimeout=10"),
    ("-o", "ServerAliveInterval=15"),
    ("-o", "ServerAliveCountMax=4"),
)
SSH_OPTIONS = tuple(part for pair in _SSH_OPTION_PAIRS for part in pair)


IMAGES = (
    Image(
        26,
        "ghcr.io/cirruslabs/macos-tahoe-vanilla",
        "26.6.2",
        "sha256:eeec54bfe1f076e27786c5d92b89187a05b1d109b5071eb2dcdf02d596e34640",
    ),
    Image(
        27,
        "ghcr.io/cirruslabs/macos-golden-gate-vanilla",
        "27.2",
        "sha256:b35695ce30453bcc1db961a476740be72a44c2a2e464b73ece1ff0fcac12a873",
    ),
)
