"""Country-aware ePDG egress coordination.

The control plane never edits host routes from its container namespace.  Instead it publishes a
small desired-state document under the shared data directory.  The host-side
``mdd-sim-gateway-orchestrator`` resolves each line's ePDG and owns the per-country sing-box TUN + /32
routes.  Engine startup waits for the corresponding line to become ready, preventing an IKE
attempt from leaking through the wrong country's default route.
"""
from __future__ import annotations

import json
import os
import time
from copy import deepcopy

from . import config as cfg

_HERE = os.path.dirname(__file__)
_MCC_PATH = os.path.join(_HERE, "mcc_country.json")
_ORCH_DIR = os.path.join(cfg.DATA_DIR, "orchestrator")
_DESIRED = os.path.join(_ORCH_DIR, "desired.json")
_STATUS = os.path.join(_ORCH_DIR, "proxy-status.json")
_RESELECT = os.path.join(_ORCH_DIR, "exit-reselect.json")
# A line healthy at least this long has proved its exit node can carry IMS.
RESELECT_MIN_STABLE_SECONDS = float(os.environ.get("MDD_EXIT_RESELECT_MIN_STABLE", "600"))


class EgressError(RuntimeError):
    pass


def _atomic_json(path: str, value: dict):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temporary = path + ".tmp"
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, sort_keys=True)
        handle.write("\n")
    os.replace(temporary, path)


def _read_json(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as handle:
            value = json.load(handle)
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def _mcc_map() -> dict[str, str]:
    return _read_json(_MCC_PATH)


def normalize_country(value: str | None) -> str:
    value = str(value or "").strip().lower().replace("_", "-")
    # Accept en-GB / zh_US style locale values but store ISO-3166 alpha-2 only.
    if "-" in value:
        value = value.rsplit("-", 1)[-1]
    return value if len(value) == 2 and value.isalpha() else ""


def country_for_mcc(mcc: str | int | None) -> str:
    return normalize_country(_mcc_map().get(str(mcc or "").zfill(3)))


def line_country(inst: dict) -> str:
    """Per-line override wins; otherwise infer the SIM home country from its MCC."""
    return normalize_country(inst.get("proxy_country")) or country_for_mcc(inst.get("mcc"))


def epdg_for(inst: dict) -> str:
    if inst.get("epdg"):
        return str(inst["epdg"]).strip()
    mcc = str(inst.get("mcc") or "").zfill(3)
    mnc = str(inst.get("mnc") or "").zfill(3)
    if not mcc.strip("0") or not mnc.strip("0"):
        return ""
    return f"epdg.epc.mnc{mnc}.mcc{mcc}.pub.3gppnetwork.org"


def desired_document(instances: list[dict], settings: dict) -> dict:
    proxy = deepcopy(settings.get("proxy") or {})
    lines = []
    for inst in instances:
        lines.append({
            "id": str(inst.get("id", "")),
            "name": inst.get("name", ""),
            "enabled": bool(inst.get("enabled", True)),
            "mcc": str(inst.get("mcc", "")),
            "mnc": str(inst.get("mnc", "")),
            "country": line_country(inst),
            "epdg": epdg_for(inst),
        })
    return {"version": 1, "updated_at": int(time.time()), "proxy": proxy,
            "hardware": deepcopy(settings.get("hardware") or {}), "lines": lines}


def publish(instances: list[dict] | None = None, settings: dict | None = None) -> dict:
    instances = instances if instances is not None else cfg.list_instances()
    settings = settings if settings is not None else cfg.get_settings()
    document = desired_document(instances, settings)
    _atomic_json(_DESIRED, document)
    return document


def status() -> dict:
    return _read_json(_STATUS)


def request_reselect(inst: dict, reason: str, stable_for: float = 0.0) -> str:
    """Ask the host to move this SIM's country exit to a different node.

    Raised when the control plane gives up on a line. A line that cannot register is the only
    reliable evidence that an exit is unusable for VoWiFi: the ePDG tunnel can be established
    over a path on which SIP then goes unanswered, so no latency probe would catch it. The
    orchestrator owns sing-box and performs the change; this only records the request.

    ``stable_for`` is how long the line was healthy before it broke. Past a threshold the exit
    has demonstrably carried IMS, so the failure belongs to something else — a carrier-side
    problem, or a rekey a marginal path failed to survive. Moving the exit then costs another
    tunnel teardown, changes nothing, and evicts a node the operator may have pinned.

    Returns the country whose exit was asked to move, or "" when there is nothing to ask.
    """
    if stable_for >= RESELECT_MIN_STABLE_SECONDS:
        return ""
    country = line_country(inst)
    current = (status().get("exits") or {}).get(country) or {}
    # A locked exit is a deliberate operator choice — including while it is failing — and a
    # country routed direct has no node to move. A "preferred" pin still allows the move: the
    # host applies the preference when it picks the replacement. The orchestrator enforces
    # this too; skipping here keeps the file quiet.
    if not country or current.get("selection") == "manual" or current.get("mode") == "direct":
        return ""
    document = _read_json(_RESELECT)
    countries = document.get("countries")
    if not isinstance(countries, dict):
        countries = {}
    countries[country] = {"ts": time.time(), "reason": reason,
                          # The node that was in use when the line failed, so the orchestrator
                          # can put it in a cooldown instead of picking it again immediately.
                          "node": str(current.get("node") or ""),
                          "line": str(inst.get("id") or "")}
    _atomic_json(_RESELECT, {"version": 1, "countries": countries})
    return country


def ensure_line(inst: dict, settings: dict, timeout: float = 18.0) -> dict:
    """Publish desired state and wait until the host confirms the line's ePDG route.

    Proxy routing is opt-in globally.  With it enabled, missing/unhealthy exits fail closed unless
    the country entry explicitly selects ``direct``.  That is intentional: silently using the
    host default route can expose the wrong geography to an operator ePDG.
    """
    proxy = settings.get("proxy") or {}
    publish(settings=settings)
    if not proxy.get("enabled", False):
        return {"ready": True, "mode": "legacy"}
    country = line_country(inst)
    if not country:
        raise EgressError("cannot determine SIM country from MCC; set a line country override")
    exits = proxy.get("exits") or {}
    exit_cfg = exits.get(country) or {}
    if not exit_cfg.get("enabled", False):
        raise EgressError(f"no enabled proxy exit configured for country {country.upper()}")
    deadline = time.monotonic() + max(1.0, timeout)
    iid = str(inst.get("id", ""))
    last = {}
    while time.monotonic() < deadline:
        state = status()
        last = (state.get("lines") or {}).get(iid) or {}
        if last.get("ready"):
            return last
        # A terminal config error should be returned immediately; DNS/probe errors may recover.
        if last.get("terminal"):
            break
        time.sleep(0.4)
    reason = last.get("error") or "country egress route was not ready before engine startup"
    raise EgressError(f"{country.upper()} exit unavailable: {reason}")
