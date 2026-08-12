# MDD Sim Gateway

![MDD Sim Gateway](assets/logo-lockup.svg)

MDD Sim Gateway is a self-hosted multi-SIM communications gateway. It brings cellular
modems, USB smart-card readers, 4G data, Wi-Fi Calling, voice, SMS, eSIM management and
country-specific proxy exits into one bilingual Web console.

Current version: **1.3.3** · [中文](README.md)

> **Compliance warning (public edition):** This software is only for use by the verified subscriber of a number where the carrier expressly permits that use. Do not use it for fraud, bulk or nuisance calling, marketing, verification-code collection, renting numbers or lines, call forwarding for others, concealing the controller's location, or providing telecommunications services to third parties. Users must follow local law, subscriber identity rules, and carrier terms. This project grants no telecom licence or carrier authorisation. The public edition stores and runs at most **five SIM lines** and provides neither standalone SIP accounts nor Telegram commands for calls, SMS, or hangup. Technical restrictions do not make any particular use lawful.

> This software directly controls cellular radios, SIMs, network routes and IMS. Use it only
> with devices and numbers you own or are authorized to manage. Carrier support for Wi-Fi
> Calling still depends on the plan, region, device identity and network policy.

### Interface preview

#### Overview

![MDD Sim Gateway English overview (fictional demo data)](screenshots/overview-redacted.en.png)

#### Devices

![MDD Sim Gateway English devices page (fictional demo data)](screenshots/devices-redacted.en.png)

#### Calls

![MDD Sim Gateway English calls page (fictional demo data)](screenshots/calls-redacted.en.png)

#### Messages

![MDD Sim Gateway English messages page (fictional demo data)](screenshots/messages-redacted.en.png)

## Capabilities

- Detect supported ModemManager cellular modules and ordinary PC/SC readers automatically.
- Control 4G data, radio flight mode and VoWiFi independently for each physical modem.
- Perform EAP-AKA and IMS-AKA in the physical SIM/eSIM without reading or storing Ki/OP/OPc.
- Show each modem UICC's three logical-channel allocations, roles and explicit failures.
- Provide an authenticated browser softphone, SMS, call history and incoming-event notifications; the public edition does not accept standalone SIP clients.
- Filter Clash subscription nodes by country and run each country through an isolated sing-box
  TUN. VoWiFi fails closed unless the selected exit passes a runtime UDP check.
- Send standard/custom Webhooks, Telegram notifications and PushPlus messages.
- Telegram is notification-only and does not accept remote control commands.
- Manage eUICC profiles through a pinned local lpac build, including dual-SE readers.
- Offer HTTPS, first-run administrator setup, CSRF protection, login throttling, local backups,
  audit records, redacted support bundles and read-only release checks.

| Hardware | 4G data | Wi-Fi Calling | SIM access |
|---|---:|---:|---|
| ModemManager-compatible cellular module | Yes | Yes | Modem APDU/logical-channel bridge |
| DJI/Quectel EC25-class module | Yes | Yes | Automatically provisioned virtual slots |
| USB PC/SC reader | No | Yes | Direct PC/SC |
| Santi Electronics SCR Prime (`04d9:c001`) | No | Yes | Direct PC/SC; install with the `patchprime` driver patch |
| eUICC/eSIM reader | No | Yes | PC/SC and lpac |

The Santi Electronics SCR Prime has been verified on physical hardware. Support in this table
describes the implemented path; it does not guarantee that every SIM, firmware build or carrier
will permit the service.

## Install

The recommended target is an ARM64 Debian, Ubuntu or Armbian host with systemd, Docker, USB,
TUN support and a stable network connection.

```bash
git clone https://github.com/MddIdd/mdd-sim-gateway.git
cd mdd-sim-gateway
sudo ./install.sh install
```

The installer reuses a working system Docker daemon, or installs the distribution package when
Docker is absent. It provisions pcscd, ModemManager/NetworkManager, checksummed sing-box, a pinned
lpac source build, the Web console and the per-SIM VoWiFi engine. It does not prune Docker or
modify unrelated containers.

Open `https://<gateway-address>:8443` and create the administrator account immediately while the
gateway is on a trusted LAN or VPN. Until first-run setup is complete, another client that can
reach the management port could claim the initial administrator account.

Common commands:

```bash
sudo ./install.sh status
sudo ./install.sh logs
sudo ./install.sh reload
sudo ./install.sh build-lpac
sudo ./install.sh uninstall
```

See [installation](docs/INSTALL.md), [architecture](docs/ARCHITECTURE.md),
[troubleshooting](docs/TROUBLESHOOTING.md) and [security](SECURITY.md) for details.

## Country exits

Add a Clash subscription in Network Exits, then configure a country. Matching applies to node
names. All matching nodes enter a sing-box `urltest` pool, and the UI reports the node actually
selected. A separate UDP probe is mandatory because IKEv2/ESP NAT traversal depends on UDP
500/4500. Only that SIM's ePDG routes enter the country's dedicated TUN.

## Security and privacy

- Administrator passwords use salted scrypt hashes. Session cookies are HttpOnly, Secure and
  SameSite=Strict; state-changing requests require a CSRF token.
- Engine callbacks use a random per-install token.
- Runtime data directories are owner-only and credential-bearing files are written as mode 0600.
- Support bundles redact identities, URLs, notification credentials, activation codes and
  cryptographic material. Review every bundle before sharing it.
- The product has no analytics or telemetry. Network requests occur only for configured
  carrier/IMS operation, subscriptions, notifications, eSIM provisioning, dependency installation
  and explicit release checks.
- Do not expose Docker, ModemManager, pcscd, SIP, AMI or the management port directly to the
  public Internet. Prefer a trusted LAN or VPN and a trusted TLS certificate.

## License and acknowledgements

MDD Sim Gateway is released under **GPL-3.0-only**. Build-time derivative patches that must remain
under an upstream license are identified separately. The project is a derivative of
[pagecat/vowifi_gateway](https://github.com/pagecat/vowifi_gateway) (MIT), which contributes the
VoWiFi engine and the overall control-plane/engine/WebUI architecture; MDD Sim Gateway adds 4G
cellular data and SMS, per-country network egress routing, unified device management and automatic
provisioning, failover and a test suite. It further derives from or interoperates with SWu-IKEv2,
sysmocom Asterisk and pjproject, phcoder/asterisk-docker, mitshell/card, sing-box, lpac, PCSC,
CCID, pyscard and frankmorgner/vsmartcard. See [NOTICE](NOTICE) and
[THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md).

This is an independent project and is not endorsed by carriers, hardware vendors or upstream
projects.
