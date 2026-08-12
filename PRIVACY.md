# Privacy

MDD Sim Gateway is self-hosted and has no product analytics or telemetry. Operational data is stored locally under the configured data directory. Network requests occur only for configured carrier/IMS operation, subscriptions, notification channels, eSIM provisioning, dependency installation and explicit update checks.

Local data may contain SIM identifiers, phone numbers, SMS/call metadata, notification credentials, proxy subscription URLs and a SIM PIN. Runtime directories and credential-bearing files are owner-only, but host storage and backups still require protection. Support bundles redact known identities, credentials, URLs, activation codes and cryptographic material; always review a bundle before sharing it. Removing the data directory during `uninstall --purge` is irreversible unless you have a backup.
