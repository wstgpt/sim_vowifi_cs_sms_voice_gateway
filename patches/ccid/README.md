# CCID patches

SPDX-License-Identifier: LGPL-2.1-or-later

These unified diffs modify [LudovicRousseau/CCID](https://github.com/LudovicRousseau/CCID),
which is LGPL-2.1-or-later. As derivative works of CCID they carry CCID's license, not the
GPL-3.0-only default of MDD Sim Gateway. `install.sh` applies them to a pinned CCID source
tarball and builds the driver on the host; the resulting driver is a separate component
loaded by pcscd.

| Patch | Purpose |
|---|---|
| `01_hsic_slot_status.patch` | HSIC readers answer `GetSlotStatus` with "no ICC present" even when a card is seated. Presence is instead confirmed by `IccPowerOn`/ATR on the `IFDHICCPresence` tick. |
| `02_hsic_malformed_atr.patch` | HSIC firmware drops the trailing TCK byte from the ATR. The patch recomputes it (ISO 7816-3 XOR of T0 through the last historical byte) so ATR parsing and PTS negotiation proceed normally. |
| `03_scr_prime_reader.patch` | Adds SCR Prime (`04d9:c001`) to the generated libccid supported-reader table. Its USB descriptor exposes a standard CCID interface, but libccid 1.6.2 does not list the VID/PID. |

Redistributing a build that includes these patches means also offering the modified CCID
source under LGPL-2.1-or-later.
