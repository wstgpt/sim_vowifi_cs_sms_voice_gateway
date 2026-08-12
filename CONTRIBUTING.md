# Contributing

Open an issue before a large behavior or hardware change. Keep device operations fail-closed, never add real subscriber data to fixtures, and preserve upstream attribution.

Before submitting a change:

```bash
python3 -m unittest discover -s tests -v
python3 -m py_compile control/app/*.py engine/*.py host/*.py
cd webui && npm ci && npm run build
```

Use focused commits, document user-visible changes in `CHANGELOG.md`, and add tests for routing, authentication, device state and secret redaction.
