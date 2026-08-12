"""
engine.py - Per-SIM engine lifecycle manager.

Supports two modes:
- Docker mode (default): Each instance runs in a Docker container
- Native mode (MDD_ENGINE_MODE=native): Runs as host processes

The engine consists of:
1. pin_keeper.py - Holds SIM PIN verification
2. swu_ike.py - SWu (ePDG) IKEv2/IPsec tunnel
3. ami_usim.py - USIM<->AMI bridge for IMS authentication
4. Asterisk - SIP/IMS stack

Runtime files (bind-mounted in Docker, local in native):
- run/swu_status.json - Tunnel state {state: CONNECTED}
- run/pin_status.json - PIN state
- run/usim_status.json - USIM state
- run/pcscf - Discovered P-CSCF address
- run/charon.log - IKE tunnel log
- logs/ - Asterisk logs
"""
from __future__ import annotations

from datetime import datetime
import json
import logging
import os
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path

try:
    import docker
    HAS_DOCKER = True
except ImportError:
    HAS_DOCKER = False

from . import config as cfg, egress, sysinfo

log = logging.getLogger("mdd.engine")

# Bounded so a line that rebuilds every two minutes cannot fill a Pi's SD card. Only the
# recent tail is diagnostically useful.
DIAGNOSTIC_RECORDS = 200
# Asterisk writes colour escapes even when captured to a file; strip them so the stored
# record stays greppable.
_ANSI = re.compile(r"\x1b\[[0-9;]*m")
# Lines worth keeping from a container that is about to be destroyed: the IMS registration
# exchange and the reasons Asterisk gives for not completing it. Matching on words such as
# "registration" or the endpoint name pulls in DEBUG chatter instead — nearly every debug
# line names res_pjsip_outbound_registration.c — which then crowds the real evidence out of
# the bounded tail. Match protocol lines and operator-visible failures only.
_SIP_EVIDENCE = re.compile(
    r"SIP/2\.0 \d{3}"                       # response status line
    r"|^(?:REGISTER|INVITE|MESSAGE|SUBSCRIBE) sip:"   # request line
    r"|No response received"                # the registration timed out with no answer
    r"|transport '[^']+' failed"            # the tunnel died under an established transport
    r"|Status: \w+"                         # Asterisk's own registration verdict
    r"|Failed to authenticate"
    r"|[Uu]nable to register")
# A DEBUG line only earns its place when it carries an actual SIP status line.
_DEBUG_LINE = re.compile(r"\bDEBUG\b")
_DISPLAY_TIMESTAMP = re.compile(
    r"^\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}[+-]\d{4}\] ")
_DOCKER_TIMESTAMP = re.compile(
    r"^(\\d{4}-\\d{2}-\\d{2}T\\d{2}:\\d{2}:\\d{2})(?:\\.\\d+)?(Z|[+-]\\d{2}:\\d{2}) (.*)$")
_ASTERISK_TIMESTAMP = re.compile(r"^\[[A-Z][a-z]{2}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2}\]\s*")
# Enough to cover a full REGISTER exchange plus the failures around it.
SIP_EVIDENCE_LINES = 40

DATA_DIR = cfg.DATA_DIR
IMAGE = os.environ.get("MDD_ENGINE_IMAGE", "mdd-sim-gateway/engine")
PCSCD_SOCK = os.environ.get("MDD_PCSCD_DIR", "/run/pcscd")
# Absolute host path to the project data dir (needed for bind mounts when the manager
# itself runs in a container; defaults to DATA_DIR on the host).
HOST_DATA_DIR = os.environ.get("MDD_HOST_DATA", DATA_DIR)
MANAGED_LABEL = "io.mdd-sim-gateway.managed"

# Engine mode: "docker" (default) or "native"
ENGINE_MODE = os.environ.get("MDD_ENGINE_MODE", "docker").lower()


def _owned(container) -> bool:
    labels = (container.attrs.get("Config") or {}).get("Labels") or {}
    image = str((container.attrs.get("Config") or {}).get("Image") or "")
    return labels.get(MANAGED_LABEL) == "true" or image.startswith("mdd-sim-gateway/")


def _host_data_path(path: str) -> str:
    """Translate a control-container path under MDD_DATA to the same file on the host."""
    absolute = os.path.abspath(path)
    data_root = os.path.abspath(DATA_DIR)
    try:
        if os.path.commonpath([absolute, data_root]) == data_root:
            return os.path.join(os.path.abspath(HOST_DATA_DIR), os.path.relpath(absolute, data_root))
    except ValueError:
        pass
    return absolute


def _runtime_data_path(path: str) -> str:
    """Translate a TLS path persisted while the manager used Docker's /data mount."""
    value = str(path or "")
    if value.startswith("/data/") and os.path.abspath(DATA_DIR) != "/data":
        translated = os.path.join(DATA_DIR, os.path.relpath(value, "/data"))
        if os.path.exists(translated):
            return translated
    return value


_docker_client = None
_docker_client_lock = threading.Lock()


def _client():
    """Reuse the Docker HTTP connection pool instead of rebuilding it on every status sample."""
    global _docker_client
    if _docker_client is None:
        with _docker_client_lock:
            if _docker_client is None:
                _docker_client = docker.from_env(timeout=30)
    return _docker_client


def close_client():
    """Release the shared Docker client during control-plane shutdown."""
    global _docker_client
    with _docker_client_lock:
        client, _docker_client = _docker_client, None
    if client is not None:
        try:
            client.close()
        except Exception:
            pass


def container_name(iid: str) -> str:
    return f"mdd-sim-gateway-engine-{iid}"


def _instance_paths(iid: str):
    base = os.path.join(DATA_DIR, "instances", str(iid))
    host_base = os.path.join(HOST_DATA_DIR, "instances", str(iid))
    os.makedirs(os.path.join(base, "run"), exist_ok=True)
    os.makedirs(os.path.join(base, "logs"), exist_ok=True)
    return base, host_base


def _clear_runtime_state(base: str):
    """Remove observations owned by the previous engine process."""
    run_dir = os.path.join(base, "run")
    for name in ("swu_status.json", "pcscf", "pcscf.applied", "pin_status.json",
                 "usim_status.json", "engine.env", "swu.ctl"):
        try:
            os.unlink(os.path.join(run_dir, name))
        except FileNotFoundError:
            pass


def _tail_lines(path: str, limit: int) -> list[str]:
    try:
        with open(path, errors="replace") as handle:
            return handle.read().splitlines()[-limit:]
    except OSError:
        return []


def _charon_evidence(base: str) -> dict:
    """Summarise IKE health from the tunnel log."""
    lines = _tail_lines(os.path.join(base, "run", "charon.log"), 400)
    last_state = ""
    for line in reversed(lines):
        bare = _DISPLAY_TIMESTAMP.sub("", line, count=1)
        if bare.startswith("STATE ") or "tunnel CONNECTED" in bare:
            last_state = line.strip()
            break
    return {"retransmits": sum(1 for line in lines if "retransmit" in line),
            "timeouts": sum(1 for line in lines if "TIMEOUT" in line),
            "last_state": last_state, "tail": lines[-40:]}


def ike_evidence(iid: str) -> dict:
    """Retransmit/timeout counts for one line, without building a whole diagnostic snapshot."""
    base, _host_base = _instance_paths(str(iid))
    return _charon_evidence(base)


def _sip_evidence(raw: str) -> list[str]:
    """Keep the SIP protocol lines and registration failures from a container log."""
    kept = []
    for line in raw.splitlines():
        line = _ANSI.sub("", line).rstrip()
        bare = _DISPLAY_TIMESTAMP.sub("", line, count=1)
        if not _SIP_EVIDENCE.search(bare):
            continue
        if _DEBUG_LINE.search(bare) and "SIP/2.0" not in bare:
            continue
        kept.append(line)
    return kept[-SIP_EVIDENCE_LINES:]


def _egress_evidence(inst: dict) -> dict:
    """Which exit node this line was using when it failed."""
    try:
        country = egress.line_country(inst)
        current = (egress.status().get("exits") or {}).get(country) or {}
        return {"country": country, "node": current.get("node", ""),
                "selection": current.get("selection", ""),
                "candidate_count": current.get("candidate_count"),
                "ready": current.get("ready")}
    except Exception:
        return {}


def _host_evidence() -> dict:
    """The host conditions that can take every line down at once, plus what they mean."""
    try:
        snapshot = sysinfo.collect(DATA_DIR)
        return {"alerts": [item["code"] for item in sysinfo.alerts(snapshot)],
                "throttling": snapshot.get("throttling") or {},
                "undervoltage": snapshot.get("undervoltage") or {},
                "temperature_c": snapshot.get("temperature_c"),
                "load": (snapshot.get("load") or {}).get("per_core"),
                "memory": snapshot.get("memory") or {},
                "network": snapshot.get("network") or {}}
    except Exception:
        return {}


def _append_diagnostic(base: str, record: dict):
    path = os.path.join(base, "logs", "diagnostics.jsonl")
    lines = _tail_lines(path, DIAGNOSTIC_RECORDS - 1)
    lines.append(json.dumps(record, ensure_ascii=False, sort_keys=True))
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")
    os.replace(tmp, path)


def capture_diagnostics(iid: str, inst: dict, base: str, reason: str):
    """Persist the evidence that recreating the container is about to destroy."""
    try:
        record = {"ts": int(time.time()), "instance": str(iid), "reason": reason,
                  "registration": registration_state(iid),
                  "pcscf": read_pcscf(iid) or "",
                  "charon": _charon_evidence(base),
                  "egress": _egress_evidence(inst),
                  "host": _host_evidence()}
        for name in ("swu_status.json", "usim_status.json", "pin_status.json"):
            record[name[:-5]] = read_run_json(iid, name) or {}
        record["sip"] = _sip_evidence(logs(iid, 600))
        _append_diagnostic(base, record)
    except Exception as exc:  # noqa
        log.warning("diagnostic capture failed for instance %s: %s", iid, exc)


# ============================================================================
# Native engine support
# ============================================================================

# Track native engine processes per instance
_native_processes: dict[str, dict] = {}
_native_lock = threading.Lock()


def _native_start(iid: str, inst: dict, settings: dict) -> str:
    """Start engine processes natively (no Docker)."""
    base, _ = _instance_paths(iid)
    run_dir = os.path.join(base, "run")
    logs_dir = os.path.join(base, "logs")
    
    # Read instance config
    instance_json = os.path.join(base, "instance.json")
    if not os.path.exists(instance_json):
        raise FileNotFoundError(f"instance.json not found: {instance_json}")
    
    with open(instance_json) as f:
        config = json.load(f)
    
    env = os.environ.copy()
    env.update({
        "MDD_ID": iid,
        "MDD_RUNDIR": run_dir,
        "USIM_PIN": config.get("usim_pin", ""),
        "USIM_READER": str(config.get("usim_reader", "0")),
        "USIM_READER_INDEX": str(config.get("usim_reader_index", 0)),
        "USIM_READER_PORT": str(config.get("usim_reader_port", 0)),
        "USIM_IMSI": config.get("usim_imsi", ""),
        "SWU_SOURCE": config.get("swu_source", "3gpp"),
        "SWU_EPDG": config.get("swu_epdg", ""),
        "SWU_APN": config.get("swu_apn", "ims"),
        "SWU_MCC": config.get("swu_mcc", ""),
        "SWU_MNC": config.get("swu_mnc", ""),
        "SWU_IMEI": config.get("swu_imei", ""),
        "SWU_IMEISV": config.get("swu_imeisv", ""),
    })
    
    # Get paths to engine scripts
    repo_dir = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    engine_dir = os.path.join(repo_dir, "engine")
    
    scripts = {
        "pin_keeper": os.path.join(engine_dir, "pin_keeper.py"),
        "swu_ike": os.path.join(engine_dir, "swu_ike.py"),
        "ami_usim": os.path.join(engine_dir, "ami_usim.py"),
    }
    
    processes = {}
    
    # Start pin_keeper
    if os.path.exists(scripts["pin_keeper"]):
        cmd = ["python3", "-u", scripts["pin_keeper"]]
        p = subprocess.Popen(cmd, env=env, 
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        processes["pin_keeper"] = p
        time.sleep(0.5)  # Give it time to start
    
    # Start swu_ike with supervisor loop
    if os.path.exists(scripts["swu_ike"]):
        # Create log file
        charon_log = os.path.join(run_dir, "charon.log")
        with open(charon_log, "w") as f:
            f.write("")
        
        cmd = [
            "python3", "-u", scripts["swu_ike"],
            "-m", str(config.get("usim_reader_index", 0)),
            "-s", config.get("swu_source", "3gpp"),
            "-d", config.get("swu_epdg", ""),
            "-a", config.get("swu_apn", "ims"),
            "-I", config.get("usim_imsi", ""),
            "-M", config.get("swu_mcc", ""),
            "-N", config.get("swu_mnc", ""),
            "-E", config.get("swu_imei", ""),
            "-V", config.get("swu_imeisv", ""),
        ]
        
        # Run with log capture
        log_capture = os.path.join(engine_dir, "log_capture.py")
        if os.path.exists(log_capture):
            cmd = [
                "python3", "-u", log_capture,
                "--current", charon_log,
                "--archive-dir", os.path.join(logs_dir, "ike"),
            ] + cmd
        
        p = subprocess.Popen(cmd, env=env,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        processes["swu_ike"] = p
        time.sleep(1)  # Give it time to establish tunnel
    
    # Start ami_usim
    if os.path.exists(scripts["ami_usim"]) and os.path.exists("/usr/local/etc/ami_usim.ini"):
        cmd = ["python3", "-u", scripts["ami_usim"], "/usr/local/etc/ami_usim.ini"]
        p = subprocess.Popen(cmd, env=env,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        processes["ami_usim"] = p
        time.sleep(0.5)
    
    # Start Asterisk
    cmd = ["asterisk", "-f"]
    asterisk_log = os.path.join(logs_dir, "asterisk.log")
    p = subprocess.Popen(cmd, env=env,
                       stdout=open(asterisk_log, "w"), stderr=subprocess.STDOUT)
    processes["asterisk"] = p
    
    # Store process info
    with _native_lock:
        _native_processes[iid] = {
            "processes": processes,
            "started_at": time.time(),
            "base": base,
        }
    
    log.info("started native engine for instance %s", iid)
    return f"native-{iid}"


def _native_stop(iid: str) -> bool:
    """Stop native engine processes for an instance."""
    with _native_lock:
        info = _native_processes.pop(iid, None)
    
    if info:
        for name, proc in info["processes"].items():
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        log.info("stopped native engine for instance %s", iid)
        return True
    return False


def _native_is_running(iid: str) -> bool:
    """Check if native engine processes are running."""
    with _native_lock:
        info = _native_processes.get(iid)
    
    if not info:
        return False
    
    for name, proc in info["processes"].items():
        if proc.poll() is None:
            return True
    
    return False


# ============================================================================
# Docker engine support (original)
# ============================================================================

def start(inst: dict, settings: dict, dev_mounts: bool = False, reason: str = "rebuild"):
    """(Re)create and start the engine for an instance."""
    iid = str(inst["id"])
    
    # Fail closed before creating the container when country routing is enabled.
    egress.ensure_line(inst, settings)
    cfg.write_instance_json(inst, settings)
    base, host_base = _instance_paths(iid)
    ports = inst.get("ports", {})
    
    if ENGINE_MODE == "native":
        return _native_start(iid, inst, settings)
    
    # Docker mode (original)
    if not HAS_DOCKER:
        raise RuntimeError("Docker not available")
    
    client = _client()
    # remove any existing container
    try:
        old = client.containers.get(container_name(iid))
        if not _owned(old):
            raise RuntimeError(f"refusing to replace foreign container {old.name}")
        capture_diagnostics(iid, inst, base, reason)
        old.remove(force=True)
    except docker.errors.NotFound:
        pass

    _clear_runtime_state(base)

    volumes = {
        os.path.join(host_base, "instance.json"): {"bind": "/config/instance.json", "mode": "ro"},
        os.path.join(host_base, "logs"): {"bind": "/logs", "mode": "rw"},
        os.path.join(host_base, "run"): {"bind": "/run/mdd-sim-gateway", "mode": "rw"},
        PCSCD_SOCK: {"bind": "/run/pcscd", "mode": "rw"},
    }
    if os.path.exists("/etc/localtime"):
        volumes["/etc/localtime"] = {"bind": "/etc/localtime", "mode": "ro"}
    
    tls = settings.get("tls", {})
    configured_cert = _runtime_data_path(tls.get("cert_path"))
    configured_key = _runtime_data_path(tls.get("key_path"))
    cert_host = key_host = None
    if configured_cert and os.path.exists(configured_cert) and \
            configured_key and os.path.exists(configured_key):
        cert_host = _host_data_path(configured_cert)
        key_host = _host_data_path(configured_key)
    else:
        ss_crt = os.path.join(DATA_DIR, "certs", "self-signed.crt")
        ss_key = os.path.join(DATA_DIR, "certs", "self-signed.key")
        if os.path.exists(ss_crt) and os.path.exists(ss_key):
            cert_host = os.path.join(HOST_DATA_DIR, "certs", "self-signed.crt")
            key_host = os.path.join(HOST_DATA_DIR, "certs", "self-signed.key")
        else:
            log.warning("no TLS cert available for engine %s WSS/8089", iid)
    if cert_host and key_host:
        volumes[cert_host] = {"bind": "/etc/asterisk/certificate.crt", "mode": "ro"}
        volumes[key_host] = {"bind": "/etc/asterisk/certificate.key", "mode": "ro"}

    if dev_mounts:
        eng = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "engine")
        for f in ["pin_keeper.py", "ami_usim.py", "render.py", "notify.py", "swu_ike.py",
                  "log_capture.py"]:
            volumes[os.path.join(eng, f)] = {"bind": f"/usr/local/bin/{f}", "mode": "ro"}
        volumes[os.path.join(eng, "entrypoint.sh")] = {"bind": "/entrypoint.sh", "mode": "ro"}
        volumes[os.path.join(eng, "templates")] = {"bind": "/opt/mdd-sim-gateway/templates", "mode": "ro"}

    port_bindings = {f"{8089}/tcp": ports.get("webrtc", 8089)}
    if (settings.get("debug") or {}).get("ami", False):
        port_bindings[f"{5038}/tcp"] = ("127.0.0.1", ports.get("ami", 5038))
    rtp_start = ports.get("rtp_start", 10000)
    for p in range(rtp_start, rtp_start + cfg.rtp_span(ports)):
        port_bindings[f"{p}/udp"] = p

    c = client.containers.run(
        IMAGE,
        name=container_name(iid),
        detach=True,
        cap_add=["NET_ADMIN"],
        devices=["/dev/net/tun:/dev/net/tun:rwm"],
        volumes=volumes,
        ports=port_bindings,
        restart_policy={"Name": "unless-stopped"},
        labels={MANAGED_LABEL: "true", "io.mdd-sim-gateway.component": "engine"},
        environment={
            "MDD_ID": iid,
            "SWU_LIVENESS_PERIOD": str(inst.get("liveness_period", 0)),
        },
        sysctls={
            "net.ipv6.conf.all.accept_ra": "0",
            "net.ipv6.conf.default.accept_ra": "0",
            "net.ipv6.conf.all.autoconf": "0",
            "net.ipv6.conf.default.autoconf": "0",
            "net.ipv6.conf.all.use_tempaddr": "0",
            "net.ipv6.conf.default.use_tempaddr": "0",
        },
        extra_hosts={"host.docker.internal": "host-gateway"},
    )
    log.info("started engine container %s", c.name)
    return c.id


def stop(iid: str, expected_container_id: str | None = None):
    if ENGINE_MODE == "native":
        return _native_stop(iid)
    
    try:
        c = _client().containers.get(container_name(iid))
        if not _owned(c):
            raise RuntimeError(f"refusing to remove foreign container {c.name}")
        if expected_container_id and str(c.id) != str(expected_container_id):
            log.info("not stopping replacement engine %s (expected generation %s, found %s)",
                     iid, expected_container_id, c.id)
            return False
        c.remove(force=True)
        return True
    except docker.errors.NotFound:
        return False


def capture_and_stop(iid: str, inst: dict, reason: str,
                     expected_container_id: str | None = None) -> bool:
    """Snapshot a failing line, then remove its engine."""
    if expected_container_id:
        if ENGINE_MODE == "native":
            if not _native_is_running(iid):
                return False
        else:
            try:
                current = _client().containers.get(container_name(iid))
                if not _owned(current):
                    raise RuntimeError(f"refusing to inspect foreign container {current.name}")
                if str(current.id) != str(expected_container_id):
                    return False
            except docker.errors.NotFound:
                return False
    base, _ = _instance_paths(iid)
    capture_diagnostics(iid, inst, base, reason)
    return (stop(iid, expected_container_id=expected_container_id)
            if expected_container_id else stop(iid))


def delete_instance_data(iid: str) -> bool:
    """Remove one deleted line's rendered config, runtime markers and bounded logs."""
    if ENGINE_MODE == "native":
        _native_stop(iid)
    
    root = os.path.realpath(os.path.join(DATA_DIR, "instances"))
    target = os.path.realpath(os.path.join(root, str(iid)))
    if os.path.dirname(target) != root:
        raise ValueError("invalid instance id")
    if not os.path.isdir(target):
        return False
    shutil.rmtree(target)
    return True


def is_running(iid: str) -> bool:
    if ENGINE_MODE == "native":
        return _native_is_running(iid)
    return container_runtime(iid)["running"]


def container_runtime(iid: str) -> dict:
    """Return running state and bridge address from one Docker inspect operation."""
    if ENGINE_MODE == "native":
        running = _native_is_running(iid)
        return {"running": running, "ip": None, "container_id": f"native-{iid}"}
    
    try:
        c = _client().containers.get(container_name(iid))
        running = c.status == "running"
        ip = None
        if running:
            for network in c.attrs.get("NetworkSettings", {}).get("Networks", {}).values():
                if network.get("IPAddress"):
                    ip = network["IPAddress"]
                    break
        return {"running": running, "ip": ip, "container_id": getattr(c, "id", None)}
    except docker.errors.NotFound:
        return {"running": False, "ip": None, "container_id": None}


def container_ip(iid: str) -> str | None:
    return container_runtime(iid)["ip"]


def read_run_json(iid: str, name: str) -> dict | None:
    path = os.path.join(DATA_DIR, "instances", str(iid), "run", name)
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return None


def read_pcscf(iid: str) -> str | None:
    path = os.path.join(DATA_DIR, "instances", str(iid), "run", "pcscf")
    try:
        with open(path) as f:
            v = f.read().strip()
            return v or None
    except Exception:
        return None


def tunnel_installed(iid: str) -> bool:
    """True if the ims tunnel is up: the swu_ike daemon writes run/swu_status.json
    {state: CONNECTED} once the SWu (ePDG) IPsec tunnel is established."""
    st = read_run_json(iid, "swu_status.json")
    return st is not None and st.get("state") == "CONNECTED"


def exec_cli(iid: str, command: str) -> str:
    if ENGINE_MODE == "native":
        # Run asterisk CLI directly
        try:
            result = subprocess.run(
                ["asterisk", "-rx", command],
                capture_output=True, text=True, timeout=10
            )
            return result.stdout
        except Exception as e:
            return f"error: {e}"
    
    try:
        c = _client().containers.get(container_name(iid))
        rc, out = c.exec_run(["asterisk", "-rx", command])
        return out.decode(errors="replace") if isinstance(out, bytes) else str(out)
    except Exception as e:  # noqa
        return f"error: {e}"


def registration_state(iid: str) -> str:
    """Read IMS registration through the local Asterisk CLI."""
    output = exec_cli(iid, "pjsip show registrations")
    if re.search(r"\bRejected\b", output, re.I):
        return "Rejected"
    if re.search(r"\bUnregistered\b", output, re.I):
        return "Unregistered"
    if re.search(r"\bRegistered\b", output):
        return "Registered"
    return "unknown"


def _format_docker_logs(raw: str, local_tz=None) -> str:
    """Render Docker's per-record UTC time in the same local format as the IKE log."""
    rendered = []
    for line in raw.splitlines(keepends=True):
        content = line.rstrip("\r\n")
        ending = line[len(content):]
        match = _DOCKER_TIMESTAMP.match(content)
        if not match:
            rendered.append(line)
            continue
        zone = "+00:00" if match.group(2) == "Z" else match.group(2)
        event_time = datetime.fromisoformat(match.group(1) + zone)
        event_time = event_time.astimezone(local_tz) if local_tz else event_time.astimezone()
        message = _ASTERISK_TIMESTAMP.sub("", match.group(3), count=1)
        rendered.append("[%s] %s%s" % (
            event_time.strftime("%Y-%m-%d %H:%M:%S%z"), message, ending))
    return "".join(rendered)


def logs(iid: str, tail: int = 200, since=None) -> str:
    if ENGINE_MODE == "native":
        # Read from local log file
        base, _ = _instance_paths(iid)
        log_path = os.path.join(base, "logs", "asterisk.log")
        try:
            with open(log_path) as f:
                lines = f.readlines()
            return "".join(lines[-tail:])
        except Exception as e:
            return f"error: {e}"
    
    try:
        c = _client().containers.get(container_name(iid))
        kwargs = {"tail": tail, "timestamps": True}
        if since is not None:
            kwargs["since"] = since
        raw = c.logs(**kwargs).decode(errors="replace")
        return _format_docker_logs(raw)
    except Exception as e:  # noqa
        return f"error: {e}"


def charon_log(iid: str, tail: int = 200) -> str:
    """Recent SWu tunnel (IKE) log lines from the instance run dir."""
    path = os.path.join(DATA_DIR, "instances", str(iid), "run", "charon.log")
    try:
        with open(path, errors="replace") as f:
            return "".join(f.readlines()[-tail:])
    except Exception:
        return ""


def usim_status(iid: str) -> dict:
    return read_run_json(iid, "usim_status.json") or {}
