
import json
import hashlib
import html
import os
import re
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import uuid
import io
import zipfile
from collections import deque
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote

import requests
from bs4 import BeautifulSoup
from requests_toolbelt.multipart.encoder import MultipartEncoder, MultipartEncoderMonitor
from flask import Flask, jsonify, redirect, render_template_string, request, url_for, send_file

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024

# CineDrive v15: central non-secret configuration from GitHub/repository.
# Railway environment variables always take precedence. Secrets are intentionally
# blocked from config.json/CONFIG_URL and must remain in Railway.
CENTRAL_CONFIG_STATUS: dict[str, Any] = {
    "enabled": False, "source": "defaults", "loaded_at": 0,
    "error": "", "applied_keys": [], "fingerprint": "",
}
_CONFIG_SECRET_KEYS = {
    "BOT_TOKEN", "BOT_TOKENS", "CATALOG_BOT_TOKEN", "SECRET_KEY",
    "TMDB_API_KEY", "SUPABASE_SERVICE_ROLE_KEY", "API_ID", "API_HASH",
}
_CONFIG_KEY_MAP = {
    "app.max_queue": "MAX_QUEUE",
    "telegram.api_base": "TELEGRAM_API_BASE",
    "telegram.target_gb": "TELEGRAM_TARGET_GB",
    "telegram.audio_kbps": "TELEGRAM_AUDIO_KBPS",
    "telegram.default_thread_id": "DEFAULT_THREAD_ID",
    "telegram.episode_buttons_per_row": "EPISODE_BUTTONS_PER_ROW",
    "tmdb.language": "TMDB_LANGUAGE",
    "encoding.codec": "TELEGRAM_VIDEO_CODEC",
    "encoding.preset": "TELEGRAM_X265_PRESET",
    "encoding.threads": "TELEGRAM_X265_THREADS",
    "encoding.frame_threads": "TELEGRAM_X265_FRAME_THREADS",
    "encoding.wpp": "TELEGRAM_X265_WPP",
    "encoding.turbo": "TELEGRAM_X265_TURBO",
    "encoding.fallback_h264": "TELEGRAM_FALLBACK_H264",
    "scheduler.enabled": "SCHEDULER_ENABLED",
    "scheduler.poll_seconds": "SCHEDULER_POLL_SECONDS",
    "scheduler.max_jobs_per_worker": "SCHEDULER_MAX_JOBS_PER_WORKER",
    "scheduler.claim_ttl_seconds": "SCHEDULER_CLAIM_TTL_SECONDS",
    "scheduler.series_sequential": "SERIES_SEQUENTIAL_SCHEDULER",
    "scheduler.smart_pipeline": "SMART_PIPELINE_SCHEDULER",
    "scheduler.pipeline_poll_seconds": "SMART_PIPELINE_POLL_SECONDS",
    "scheduler.auto_recovery": "V15_AUTO_RECOVERY_ENABLED",
    "scheduler.auto_cleanup": "V15_AUTO_CLEANUP_ENABLED",
    "scheduler.success_retention_seconds": "V15_SUCCESS_RETENTION_SECONDS",
    "scheduler.error_retention_seconds": "V15_ERROR_RETENTION_SECONDS",
    "scheduler.upload_retries": "V15_UPLOAD_RETRIES",
    "scheduler.failover_enabled": "V161_FAILOVER_ENABLED",
    "scheduler.worker_offline_seconds": "V161_WORKER_OFFLINE_SECONDS",
    "scheduler.failover_grace_seconds": "V161_FAILOVER_GRACE_SECONDS",
    "scheduler.failover_processing_jobs": "V161_FAILOVER_PROCESSING_JOBS",
    "cluster.enabled": "ENTERPRISE_CLUSTER_ENABLED",
    "cluster.lock_ttl_seconds": "ENTERPRISE_LOCK_TTL_SECONDS",
    "global_database.enabled": "GLOBAL_SYNC_ENABLED",
    "global_database.bootstrap_local": "GLOBAL_SYNC_BOOTSTRAP_LOCAL",
    "global_database.publish_local": "GLOBAL_DATABASE_PUBLISH_LOCAL",
    "global_database.refresh_seconds": "GLOBAL_DATABASE_REFRESH_SECONDS",
}

def _flatten_config(value: Any, prefix: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {}
    if not isinstance(value, dict):
        return out
    for key, item in value.items():
        path = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(item, dict):
            out.update(_flatten_config(item, path))
        else:
            out[path] = item
    return out

def _config_env_value(value: Any) -> str:
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)

def _load_central_config() -> None:
    merged: dict[str, Any] = {}
    sources: list[str] = []
    local_path = Path(os.getenv("CONFIG_FILE", "config.json"))
    try:
        if local_path.exists():
            parsed = json.loads(local_path.read_text(encoding="utf-8"))
            if isinstance(parsed, dict):
                merged.update(parsed)
                sources.append(str(local_path))
    except Exception as exc:
        CENTRAL_CONFIG_STATUS["error"] = f"local config: {exc}"
    config_url = os.getenv("CONFIG_URL", "").strip()
    if config_url:
        try:
            response = requests.get(config_url, timeout=20, headers={"Cache-Control": "no-cache"})
            response.raise_for_status()
            parsed = response.json()
            if isinstance(parsed, dict):
                # Remote values override repository defaults.
                merged = {**merged, **parsed}
                sources.append(config_url)
        except Exception as exc:
            CENTRAL_CONFIG_STATUS["error"] = f"remote config: {exc}"
    flat = _flatten_config(merged)
    applied: list[str] = []
    for path, env_name in _CONFIG_KEY_MAP.items():
        if env_name in _CONFIG_SECRET_KEYS or path not in flat or env_name in os.environ:
            continue
        os.environ[env_name] = _config_env_value(flat[path])
        applied.append(env_name)
    raw = json.dumps(merged, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    CENTRAL_CONFIG_STATUS.update({
        "enabled": bool(merged), "source": ", ".join(sources) or "defaults",
        "loaded_at": int(time.time()), "applied_keys": sorted(applied),
        "fingerprint": hashlib.sha256(raw.encode("utf-8")).hexdigest() if merged else "",
    })
    if merged:
        print(f"[CONFIG] loaded source={CENTRAL_CONFIG_STATUS['source']} applied={len(applied)}", flush=True)

_load_central_config()

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
BOT_TOKENS_RAW = os.getenv("BOT_TOKENS", "").strip()

def _parse_bot_tokens() -> list[str]:
    raw = BOT_TOKENS_RAW.replace("\n", ",").replace(";", ",")
    tokens = [item.strip() for item in raw.split(",") if item.strip()]
    if BOT_TOKEN:
        tokens.insert(0, BOT_TOKEN)
    unique: list[str] = []
    for token in tokens:
        if token not in unique:
            unique.append(token)
    if not unique:
        raise RuntimeError("BOT_TOKEN atau BOT_TOKENS belum diisi.")
    return unique

BOT_TOKENS = _parse_bot_tokens()
BOT_TOKEN_INDEX_RAW = os.getenv("BOT_TOKEN_INDEX", "").strip()
CHANNEL_ID = os.environ["CHANNEL_ID"]
SECRET_KEY = os.environ["SECRET_KEY"]
TMDB_API_KEY = os.environ["TMDB_API_KEY"]

TELEGRAM_API_BASE = os.getenv(
    "TELEGRAM_API_BASE",
    "https://api.telegram.org",
).strip().rstrip("/") or "https://api.telegram.org"

TELEGRAM_API_FALLBACK_BASE = os.getenv(
    "TELEGRAM_API_FALLBACK_BASE",
    "https://api.telegram.org",
).strip().rstrip("/") or "https://api.telegram.org"

def telegram_api_bases() -> list[str]:
    """Return configured Bot API base first, then official API as a safe fallback."""
    bases: list[str] = []
    for value in (TELEGRAM_API_BASE, TELEGRAM_API_FALLBACK_BASE):
        base = str(value or "").strip().rstrip("/")
        if base and base not in bases:
            bases.append(base)
    return bases
TMDB_LANGUAGE = os.getenv("TMDB_LANGUAGE", "id-ID")
TMDB_IMAGE_BASE = os.getenv("TMDB_IMAGE_BASE", "https://image.tmdb.org/t/p/w780")
MAX_QUEUE = int(os.getenv("MAX_QUEUE", "20"))
FFMPEG_PRESET = os.getenv("FFMPEG_PRESET", "veryfast")
FFMPEG_CRF = os.getenv("FFMPEG_CRF", "23")
TELEGRAM_TARGET_GB = float(os.getenv("TELEGRAM_TARGET_GB", "1.45"))
TELEGRAM_AUDIO_KBPS = int(os.getenv("TELEGRAM_AUDIO_KBPS", "128"))
TELEGRAM_VIDEO_CODEC = os.getenv("TELEGRAM_VIDEO_CODEC", "libx265")
TELEGRAM_X265_PRESET = os.getenv("TELEGRAM_X265_PRESET", "superfast").strip() or "superfast"

def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)).strip())
    except (TypeError, ValueError):
        return default

CPU_COUNT = max(1, os.cpu_count() or 1)
# Nilai 0 berarti otomatis. Batas default menjaga Railway shared CPU/RAM tetap stabil.
_configured_threads = _env_int("TELEGRAM_X265_THREADS", 0)
_configured_frame_threads = _env_int("TELEGRAM_X265_FRAME_THREADS", 0)
TELEGRAM_X265_THREADS = max(1, min(8, _configured_threads or CPU_COUNT))
TELEGRAM_X265_FRAME_THREADS = max(1, min(3, _configured_frame_threads or max(1, min(2, CPU_COUNT // 2))))
TELEGRAM_X265_WPP = os.getenv("TELEGRAM_X265_WPP", "1").strip().lower() in {"1", "true", "yes", "on"}
TELEGRAM_X265_TURBO = os.getenv("TELEGRAM_X265_TURBO", "1").strip().lower() in {"1", "true", "yes", "on"}
TELEGRAM_FALLBACK_H264 = os.getenv("TELEGRAM_FALLBACK_H264", "1").strip().lower() in {"1", "true", "yes", "on"}

DEFAULT_THREAD_ID = int(os.getenv("DEFAULT_THREAD_ID", "0") or "0")
TOPIC_OPTIONS_RAW = os.getenv("TOPIC_OPTIONS", "General:0")
def default_persistent_path(filename: str) -> Path:
    configured = os.getenv("DATA_DIR", "").strip()
    candidates = [Path(configured)] if configured else []
    candidates.extend([Path("/data"), Path("/tmp")])
    for directory in candidates:
        try:
            directory.mkdir(parents=True, exist_ok=True)
            test_file = directory / ".write-test"
            test_file.write_text("ok", encoding="utf-8")
            test_file.unlink(missing_ok=True)
            return directory / filename
        except Exception:
            continue
    return Path("/tmp") / filename

TOPIC_STORE_PATH = Path(os.getenv("TOPIC_STORE_PATH", str(default_persistent_path("telegram-topics.json"))))
SERIES_STORE_PATH = Path(os.getenv("SERIES_STORE_PATH", str(default_persistent_path("telegram-series.json"))))
BACKUP_DIR = Path(os.getenv("BACKUP_DIR", str(SERIES_STORE_PATH.parent / "backups")))
MAX_BACKUPS = max(3, int(os.getenv("MAX_BACKUPS", "30")))
SCAN_STORE_PATH = Path(os.getenv("SCAN_STORE_PATH", str(default_persistent_path("telegram-scan-results.json"))))
EPISODE_BUTTONS_PER_ROW = max(
    1,
    min(8, int(os.getenv("EPISODE_BUTTONS_PER_ROW", "5"))),
)


CLUSTER_VERSION = "16.1.0"


def _deep_merge_cluster(remote: Any, local: Any) -> Any:
    """Gabungkan dokumen cluster; nilai lokal terbaru menang, map episode tetap digabung."""
    if isinstance(remote, dict) and isinstance(local, dict):
        merged = dict(remote)
        for key, value in local.items():
            merged[key] = _deep_merge_cluster(merged.get(key), value) if key in merged else value
        return merged
    return local


GLOBAL_SYNC_ENABLED = os.getenv("GLOBAL_SYNC_ENABLED", "1").strip().lower() in {"1", "true", "yes", "on"}
GLOBAL_SYNC_BOOTSTRAP_LOCAL = os.getenv("GLOBAL_SYNC_BOOTSTRAP_LOCAL", "1").strip().lower() in {"1", "true", "yes", "on"}
GLOBAL_SYNC_INTERVAL = max(5, int(os.getenv("GLOBAL_SYNC_INTERVAL", "15") or "15"))
GLOBAL_DATABASE_SOURCE_PREFIX = "series-source:"
GLOBAL_DATABASE_CANONICAL_KEY = "series"
GLOBAL_DATABASE_PUBLISH_LOCAL = os.getenv("GLOBAL_DATABASE_PUBLISH_LOCAL", "1").strip().lower() in {"1", "true", "yes", "on"}
GLOBAL_DATABASE_REFRESH_SECONDS = max(3, int(os.getenv("GLOBAL_DATABASE_REFRESH_SECONDS", "10") or "10"))
ENTERPRISE_CLUSTER_ENABLED = os.getenv("ENTERPRISE_CLUSTER_ENABLED", "1").strip().lower() in {"1", "true", "yes", "on"}
ENTERPRISE_LOCK_TTL_SECONDS = max(300, int(os.getenv("ENTERPRISE_LOCK_TTL_SECONDS", "21600") or "21600"))
ENTERPRISE_JOB_RETENTION_SECONDS = max(3600, int(os.getenv("ENTERPRISE_JOB_RETENTION_SECONDS", "86400") or "86400"))
ENTERPRISE_QUEUE_PREFIX = "enterprise-job:"
SCHEDULER_ENABLED = os.getenv("SCHEDULER_ENABLED", "1").strip().lower() in {"1", "true", "yes", "on"}
SCHEDULER_POLL_SECONDS = max(3, int(os.getenv("SCHEDULER_POLL_SECONDS", "5") or "5"))
SCHEDULER_MAX_JOBS_PER_WORKER = max(1, int(os.getenv("SCHEDULER_MAX_JOBS_PER_WORKER", "1") or "1"))
SCHEDULER_CLAIM_TTL_SECONDS = max(300, int(os.getenv("SCHEDULER_CLAIM_TTL_SECONDS", "21600") or "21600"))
SERIES_SEQUENTIAL_SCHEDULER = os.getenv("SERIES_SEQUENTIAL_SCHEDULER", "1").strip().lower() in {"1", "true", "yes", "on"}
SMART_PIPELINE_SCHEDULER = os.getenv("SMART_PIPELINE_SCHEDULER", "1").strip().lower() in {"1", "true", "yes", "on"}
SMART_PIPELINE_POLL_SECONDS = max(2, int(os.getenv("SMART_PIPELINE_POLL_SECONDS", "5")))
SMART_PIPELINE_WAIT_TIMEOUT_SECONDS = max(3600, int(os.getenv("SMART_PIPELINE_WAIT_TIMEOUT_SECONDS", "86400")))
V15_AUTO_RECOVERY_ENABLED = os.getenv("V15_AUTO_RECOVERY_ENABLED", "1").strip().lower() in {"1", "true", "yes", "on"}
V15_AUTO_CLEANUP_ENABLED = os.getenv("V15_AUTO_CLEANUP_ENABLED", "1").strip().lower() in {"1", "true", "yes", "on"}
V15_SUCCESS_RETENTION_SECONDS = max(3600, int(os.getenv("V15_SUCCESS_RETENTION_SECONDS", "604800") or "604800"))
V15_ERROR_RETENTION_SECONDS = max(1800, int(os.getenv("V15_ERROR_RETENTION_SECONDS", "86400") or "86400"))
V15_UPLOAD_RETRIES = max(1, min(5, int(os.getenv("V15_UPLOAD_RETRIES", "3") or "3")))
V15_CLEANUP_INTERVAL_SECONDS = max(60, int(os.getenv("V15_CLEANUP_INTERVAL_SECONDS", "300") or "300"))
V15_EVENT_WAKE_SECONDS = max(1, int(os.getenv("V15_EVENT_WAKE_SECONDS", "2") or "2"))
# CineDrive v16.1 Enterprise Automatic Failover
V161_FAILOVER_ENABLED = os.getenv("V161_FAILOVER_ENABLED", "1").strip().lower() in {"1", "true", "yes", "on"}
V161_WORKER_OFFLINE_SECONDS = max(60, int(os.getenv("V161_WORKER_OFFLINE_SECONDS", os.getenv("WORKER_OFFLINE_SECONDS", "90")) or "90"))
V161_FAILOVER_GRACE_SECONDS = max(15, int(os.getenv("V161_FAILOVER_GRACE_SECONDS", "30") or "30"))
V161_FAILOVER_PROCESSING_JOBS = os.getenv("V161_FAILOVER_PROCESSING_JOBS", "1").strip().lower() in {"1", "true", "yes", "on"}
V161_FAILOVER_STATES = {"ASSIGNED", "CLAIMED", "DOWNLOADING", "PROCESSING", "PREPARING", "READY", "UPLOADING"}
SCHEDULER_WAKE_EVENT = threading.Event()

# CineDrive v13 Enterprise
CATALOG_BOT_TOKEN_RAW = os.getenv("CATALOG_BOT_TOKEN", "").strip()
V13_QUEUE_FAILOVER_ENABLED = os.getenv("V13_QUEUE_FAILOVER_ENABLED", "1").strip().lower() in {"1", "true", "yes", "on"}
V13_QUEUE_FAILOVER_SECONDS = max(30, int(os.getenv("V13_QUEUE_FAILOVER_SECONDS", "120") or "120"))
V13_MAX_RECENT_JOBS = max(10, min(200, int(os.getenv("V13_MAX_RECENT_JOBS", "50") or "50")))
_global_source_published = False
_global_source_lock = threading.Lock()


def _json_fingerprint(value: Any) -> str:
    try:
        raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except Exception:
        raw = repr(value)
    return hashlib.sha256(raw.encode("utf-8", errors="replace")).hexdigest()


def _series_identity(key: str, item: dict[str, Any]) -> str:
    """Identitas global serial, tidak bergantung Railway, bot, chat, atau topic."""
    tmdb_id = int(item.get("tmdb_id") or 0)
    season = int(item.get("season_number") or 1)
    if tmdb_id > 0:
        return f"tmdb:{tmdb_id}:season:{season}"
    title = str(item.get("series_title") or item.get("title") or key).strip().lower()
    title = re.sub(r"[^a-z0-9]+", "-", title).strip("-") or str(key)
    return f"manual:{title}:season:{season}"


def _record_score(item: dict[str, Any]) -> tuple[int, int, int]:
    episodes = item.get("episodes") if isinstance(item.get("episodes"), dict) else {}
    updated = int(item.get("updated_at") or 0)
    index_id = int(item.get("index_message_id") or 0)
    return (len(episodes), updated, index_id)


def _merge_series_record(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    """Gabungkan duplikat serial dari beberapa volume/Railway secara deterministik."""
    left = dict(left or {})
    right = dict(right or {})
    newer, older = (right, left) if _record_score(right) >= _record_score(left) else (left, right)
    merged = dict(older)
    for key, value in newer.items():
        if value not in (None, "", [], {}):
            merged[key] = value
    episodes: dict[str, Any] = {}
    for source in (left.get("episodes"), right.get("episodes")):
        if not isinstance(source, dict):
            continue
        for ep_key, ep_value in source.items():
            old = episodes.get(str(ep_key))
            if not isinstance(old, dict) or not isinstance(ep_value, dict):
                episodes[str(ep_key)] = ep_value
                continue
            old_ts = int(old.get("updated_at") or 0)
            new_ts = int(ep_value.get("updated_at") or 0)
            episodes[str(ep_key)] = ep_value if new_ts >= old_ts else old
    merged["episodes"] = episodes
    merged["updated_at"] = max(int(left.get("updated_at") or 0), int(right.get("updated_at") or 0)) or int(time.time())
    merged["global_sync_version"] = CLUSTER_VERSION
    return merged


def _normalize_series_store(*stores: Any) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    for store in stores:
        if not isinstance(store, dict):
            continue
        for raw_key, raw_item in store.items():
            if not isinstance(raw_item, dict):
                continue
            identity = _series_identity(str(raw_key), raw_item)
            if identity in normalized:
                normalized[identity] = _merge_series_record(normalized[identity], raw_item)
            else:
                normalized[identity] = _merge_series_record({}, raw_item)
    return normalized


def _merge_list_records(*lists: Any) -> list[dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    for values in lists:
        if not isinstance(values, list):
            continue
        for item in values:
            if not isinstance(item, dict):
                continue
            identity = str(item.get("chat_id") or "") + ":" + str(item.get("thread_id") or item.get("update_id") or item.get("id") or "")
            if identity == ":":
                identity = _json_fingerprint(item)
            previous = indexed.get(identity)
            prev_ts = int((previous or {}).get("last_seen_at") or (previous or {}).get("date") or 0)
            new_ts = int(item.get("last_seen_at") or item.get("date") or 0)
            if previous is None or new_ts >= prev_ts:
                indexed[identity] = item
    return list(indexed.values())


class ClusterStore:
    """Penyimpanan JSON bersama melalui Supabase PostgREST dengan fallback lokal."""

    def __init__(self) -> None:
        self.url = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
        self.key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
        self.namespace = os.getenv("CLUSTER_NAMESPACE", "cinemaxx1-production").strip() or "cinemaxx1-production"
        self.worker_id = os.getenv("CLUSTER_WORKER_ID", "").strip() or socket.gethostname()
        self.hostname = socket.gethostname()
        self.enabled = bool(self.url and self.key)
        self.last_error = ""
        self.heartbeat_error = ""
        self.workers_error = ""
        self.last_heartbeat_at = 0
        self.last_sync_at = 0
        self._lock = threading.RLock()
        if self.enabled:
            # Register immediately during Gunicorn worker startup, then refresh periodically.
            self.heartbeat()
            threading.Thread(target=self._heartbeat_loop, name="cluster-heartbeat", daemon=True).start()
        else:
            print("[CLUSTER] disabled: SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY is empty", flush=True)

    def _headers(self, prefer: str = "") -> dict[str, str]:
        headers = {
            "apikey": self.key,
            "Authorization": f"Bearer {self.key}",
            "Content-Type": "application/json",
        }
        if prefer:
            headers["Prefer"] = prefer
        return headers

    def _endpoint(self, table: str) -> str:
        return f"{self.url}/rest/v1/{table}"


    def _upsert_record(self, payload: dict[str, Any]) -> None:
        """Update by canonical identity first; insert only when no row exists.

        This avoids HTTP 409 when a legacy table has more than one unique index.
        """
        identity = {
            "namespace": f"eq.{payload['namespace']}",
            "record_type": f"eq.{payload['record_type']}",
            "record_key": f"eq.{payload['record_key']}",
        }
        update = requests.patch(
            self._endpoint("cinedrive_cluster"),
            headers=self._headers("return=representation"),
            params=identity,
            json=payload,
            timeout=25,
        )
        if not update.ok:
            raise RuntimeError(f"update HTTP {update.status_code}: {update.text[:800]}")
        try:
            updated_rows = update.json()
        except ValueError:
            updated_rows = []
        if updated_rows:
            return

        create = requests.post(
            self._endpoint("cinedrive_cluster"),
            headers=self._headers("return=representation"),
            json=payload,
            timeout=25,
        )
        if create.status_code == 409:
            # Another worker/request may have inserted the same identity concurrently.
            retry = requests.patch(
                self._endpoint("cinedrive_cluster"),
                headers=self._headers("return=representation"),
                params=identity,
                json=payload,
                timeout=25,
            )
            if not retry.ok:
                raise RuntimeError(f"retry update HTTP {retry.status_code}: {retry.text[:800]}")
            try:
                if retry.json():
                    return
            except ValueError:
                pass
        if not create.ok:
            raise RuntimeError(f"insert HTTP {create.status_code}: {create.text[:800]}")

    def get_document(self, document_key: str, default: Any) -> Any:
        if not self.enabled:
            return default
        try:
            response = requests.get(
                self._endpoint("cinedrive_cluster"),
                headers=self._headers(),
                params={
                    "namespace": f"eq.{self.namespace}",
                    "bucket": "eq.documents",
                    "item_key": f"eq.{document_key}",
                    "select": "value,data,updated_at,updated_by",
                    "limit": "1",
                },
                timeout=20,
            )
            response.raise_for_status()
            rows = response.json()
            self.last_error = ""
            self.last_sync_at = int(time.time())
            if rows:
                remote_value = rows[0].get("value", rows[0].get("data"))
                if isinstance(remote_value, type(default)):
                    return remote_value
        except Exception as exc:
            self.last_error = f"get {document_key}: {exc}"
        return default

    def list_documents(self, prefix: str = "") -> dict[str, Any]:
        """Return all document records whose item_key starts with prefix."""
        if not self.enabled:
            return {}
        try:
            params = {
                "namespace": f"eq.{self.namespace}",
                "bucket": "eq.documents",
                "select": "item_key,value,data,updated_at,updated_by",
                "order": "updated_at.asc",
            }
            if prefix:
                params["item_key"] = f"like.{prefix}*"
            response = requests.get(
                self._endpoint("cinedrive_cluster"),
                headers=self._headers(),
                params=params,
                timeout=25,
            )
            response.raise_for_status()
            result: dict[str, Any] = {}
            for row in response.json():
                if not isinstance(row, dict):
                    continue
                key = str(row.get("item_key") or "")
                value = row.get("value", row.get("data"))
                if key:
                    result[key] = value
            self.last_error = ""
            self.last_sync_at = int(time.time())
            return result
        except Exception as exc:
            self.last_error = f"list documents {prefix}: {exc}"
            return {}

    def save_document(self, document_key: str, data: Any, merge: bool = True) -> Any:
        if not self.enabled:
            return data
        with self._lock:
            try:
                final_data = data
                if merge:
                    remote = self.get_document(document_key, {} if isinstance(data, dict) else [] if isinstance(data, list) else data)
                    if isinstance(data, dict) and isinstance(remote, dict):
                        final_data = _deep_merge_cluster(remote, data)
                    elif isinstance(data, list) and isinstance(remote, list):
                        # Deduplicate topic/scan records using stable JSON representation.
                        indexed: dict[str, Any] = {}
                        for item in remote + data:
                            if isinstance(item, dict):
                                identity = str(item.get("chat_id") or item.get("update_id") or item.get("id") or json.dumps(item, sort_keys=True, ensure_ascii=False))
                            else:
                                identity = json.dumps(item, sort_keys=True, ensure_ascii=False)
                            indexed[identity] = item
                        final_data = list(indexed.values())
                payload = {
                    "namespace": self.namespace,
                    "bucket": "documents",
                    "item_key": document_key,
                    "value": final_data,
                    "worker_id": self.worker_id,
                    "record_type": "document",
                    "record_key": document_key,
                    "data": final_data,
                    "updated_by": self.worker_id,
                    "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                }
                self._upsert_record(payload)
                self.last_error = ""
                self.last_sync_at = int(time.time())
                return final_data
            except Exception as exc:
                self.last_error = f"save {document_key}: {exc}"
                return data

    def heartbeat(self) -> bool:
        """Register/update this Railway worker and verify that Supabase stored it."""
        if not self.enabled:
            return False
        last_seen = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        worker_data = {
            "worker_id": self.worker_id,
            "hostname": self.hostname,
            "version": CLUSTER_VERSION,
            "last_seen": last_seen,
            "metadata": {"pid": os.getpid(), "cpu_count": CPU_COUNT},
        }
        payload = {
            "namespace": self.namespace,
            "bucket": "workers",
            "item_key": self.worker_id,
            "value": worker_data,
            "worker_id": self.worker_id,
            "record_type": "worker",
            "record_key": self.worker_id,
            "data": worker_data,
            "updated_by": self.worker_id,
            "updated_at": last_seen,
        }
        try:
            self._upsert_record(payload)

            # Read back the exact worker row. This catches schema/RLS/upsert problems early.
            verify = requests.get(
                self._endpoint("cinedrive_cluster"),
                headers=self._headers(),
                params={
                    "namespace": f"eq.{self.namespace}",
                    "bucket": "eq.workers",
                    "item_key": f"eq.{self.worker_id}",
                    "select": "item_key,value,data,updated_at",
                    "limit": "1",
                },
                timeout=20,
            )
            if not verify.ok:
                raise RuntimeError(f"verify HTTP {verify.status_code}: {verify.text[:800]}")
            rows = verify.json()
            if not rows:
                raise RuntimeError("heartbeat write returned success but worker row was not found")

            self.heartbeat_error = ""
            self.last_heartbeat_at = int(time.time())
            print(f"[CLUSTER] heartbeat OK worker={self.worker_id} namespace={self.namespace}", flush=True)
            return True
        except Exception as exc:
            self.heartbeat_error = str(exc)
            self.last_error = f"heartbeat: {exc}"
            print(f"[CLUSTER] heartbeat ERROR: {exc}", flush=True)
            return False

    def workers(self) -> list[dict[str, Any]]:
        if not self.enabled:
            return []
        try:
            response = requests.get(
                self._endpoint("cinedrive_cluster"),
                headers=self._headers(),
                params={
                    "namespace": f"eq.{self.namespace}",
                    "bucket": "eq.workers",
                    "select": "item_key,value,data,updated_at,updated_by",
                    "order": "updated_at.desc",
                },
                timeout=20,
            )
            response.raise_for_status()
            rows = response.json()
            workers: list[dict[str, Any]] = []
            for row in rows:
                data = row.get("value", row.get("data")) if isinstance(row, dict) else None
                worker = dict(data) if isinstance(data, dict) else {}
                worker.setdefault("worker_id", row.get("item_key") if isinstance(row, dict) else "")
                worker.setdefault("last_seen", row.get("updated_at") if isinstance(row, dict) else "")
                workers.append(worker)
            self.workers_error = ""
            return workers
        except Exception as exc:
            self.workers_error = str(exc)
            self.last_error = f"workers: {exc}"
            print(f"[CLUSTER] workers ERROR: {exc}", flush=True)
            return []

    def status(self) -> dict[str, Any]:
        heartbeat_ok = self.heartbeat()
        workers = self.workers()
        now = time.time()
        active = []
        for worker in workers:
            raw = str(worker.get("last_seen") or "")
            try:
                stamp = time.mktime(time.strptime(raw[:19], "%Y-%m-%dT%H:%M:%S"))
                worker["active"] = (now - stamp) <= 120
            except Exception:
                worker["active"] = False
            if worker["active"]:
                active.append(worker)
        return {
            "success": True,
            "enabled": self.enabled,
            "version": CLUSTER_VERSION,
            "namespace": self.namespace,
            "worker_id": self.worker_id,
            "hostname": self.hostname,
            "active_worker_count": len(active),
            "workers": workers,
            "heartbeat_ok": heartbeat_ok,
            "last_heartbeat_at": self.last_heartbeat_at,
            "last_sync_at": self.last_sync_at,
            "heartbeat_error": self.heartbeat_error,
            "workers_error": self.workers_error,
            "last_error": self.heartbeat_error or self.workers_error or self.last_error,
        }

    def _heartbeat_loop(self) -> None:
        while True:
            self.heartbeat()
            time.sleep(30)


def _cluster_lock_row(self: ClusterStore, lock_key: str) -> dict[str, Any] | None:
    if not self.enabled:
        return None
    response = requests.get(
        self._endpoint("cinedrive_cluster"), headers=self._headers(),
        params={
            "namespace": f"eq.{self.namespace}", "record_type": "eq.lock",
            "record_key": f"eq.{lock_key}", "select": "item_key,value,data,updated_at,updated_by", "limit": "1",
        }, timeout=20,
    )
    response.raise_for_status()
    rows = response.json()
    return rows[0] if rows else None


def _cluster_release_lock(self: ClusterStore, lock_key: str) -> bool:
    if not self.enabled:
        return True
    try:
        response = requests.delete(
            self._endpoint("cinedrive_cluster"), headers=self._headers("return=minimal"),
            params={
                "namespace": f"eq.{self.namespace}", "record_type": "eq.lock",
                "record_key": f"eq.{lock_key}", "updated_by": f"eq.{self.worker_id}",
            }, timeout=20,
        )
        response.raise_for_status()
        return True
    except Exception as exc:
        self.last_error = f"release lock {lock_key}: {exc}"
        return False


def _cluster_acquire_lock(self: ClusterStore, lock_key: str, metadata: dict[str, Any] | None = None) -> tuple[bool, dict[str, Any]]:
    """Acquire a cross-Railway lock using a unique Supabase identity row."""
    if not (self.enabled and ENTERPRISE_CLUSTER_ENABLED):
        return True, {"owner": self.worker_id, "mode": "local"}
    now = time.time()
    existing = None
    try:
        existing = self._lock_row(lock_key)
        if existing:
            raw = existing.get("value", existing.get("data")) or {}
            stamp = str(existing.get("updated_at") or raw.get("acquired_at") or "")
            try:
                age = now - time.mktime(time.strptime(stamp[:19], "%Y-%m-%dT%H:%M:%S"))
            except Exception:
                age = 0
            owner = str(raw.get("worker_id") or existing.get("updated_by") or "")
            if age <= ENTERPRISE_LOCK_TTL_SECONDS and owner and owner != self.worker_id:
                return False, {"owner": owner, "age_seconds": int(age), "lock_key": lock_key}
            # stale lock or lock already owned by this worker
            requests.delete(
                self._endpoint("cinedrive_cluster"), headers=self._headers("return=minimal"),
                params={"namespace": f"eq.{self.namespace}", "record_type": "eq.lock", "record_key": f"eq.{lock_key}"},
                timeout=20,
            )
        acquired_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        value = {"worker_id": self.worker_id, "hostname": self.hostname, "acquired_at": acquired_at, "metadata": metadata or {}}
        payload = {
            "namespace": self.namespace, "bucket": "locks", "item_key": lock_key, "value": value,
            "worker_id": self.worker_id, "record_type": "lock", "record_key": lock_key,
            "data": value, "updated_by": self.worker_id, "updated_at": acquired_at,
        }
        response = requests.post(
            self._endpoint("cinedrive_cluster"), headers=self._headers("return=representation"), json=payload, timeout=20,
        )
        if response.status_code == 409:
            row = self._lock_row(lock_key) or {}
            raw = row.get("value", row.get("data")) or {}
            return False, {"owner": raw.get("worker_id") or row.get("updated_by") or "worker lain", "lock_key": lock_key}
        response.raise_for_status()
        return True, value
    except Exception as exc:
        self.last_error = f"acquire lock {lock_key}: {exc}"
        return False, {"owner": "unknown", "error": str(exc), "lock_key": lock_key}


def _cluster_publish_enterprise_job(self: ClusterStore, job: dict[str, Any]) -> None:
    if not (self.enabled and ENTERPRISE_CLUSTER_ENABLED):
        return
    public = {
        "id": job.get("id"), "title": job.get("title"), "state": job.get("state"),
        "overall_progress": job.get("overall_progress", 0),
        "stage_progress": job.get("stage_progress", 0),
        "progress_detail": job.get("progress_detail", ""),
        "eta_seconds": job.get("eta_seconds", 0),
        "eta_human": job.get("eta_human", "-"),
        "message": job.get("message", ""),
        "error": job.get("error"), "tmdb_id": job.get("tmdb_id"),
        "season_number": job.get("season_number"), "episode_number": job.get("episode_number"),
        "target_chat_id": job.get("target_chat_id"), "message_thread_id": job.get("message_thread_id"),
        "topic_name": job.get("topic_name"),
        "subtitle_info": job.get("subtitle_info"),
        "watermark_info": job.get("watermark_info"),
        "encode_info": job.get("encode_info"),
        "encode_profile": job.get("encode_profile"),
        "file_size_bytes": job.get("file_size_bytes", 0),
        "message_id": job.get("message_id"),
        "index_message_id": job.get("index_message_id"),
        "created_at": job.get("created_at"), "started_at": job.get("started_at"), "finished_at": job.get("finished_at"),
        # Preserve an explicit empty assignment for portable jobs.  An empty
        # assigned_worker means the global queue is waiting for any idle worker.
        "worker_id": job.get("worker_id") if "worker_id" in job else self.worker_id,
        "submitted_by": job.get("submitted_by") or self.worker_id,
        "assigned_worker": job.get("assigned_worker") if "assigned_worker" in job else self.worker_id,
        "preferred_worker": job.get("preferred_worker") or job.get("submitted_by") or self.worker_id,
        "scheduler_status": job.get("scheduler_status") or "LOCAL",
        "scheduler_local_only": bool(job.get("scheduler_local_only")),
        "scheduler_payload": job.get("scheduler_payload") if str(job.get("state")) == "QUEUED" else None,
        "bot": get_bot_identity(ACTIVE_BOT_TOKEN) if "ACTIVE_BOT_TOKEN" in globals() else {},
    }
    self.save_document(f"{ENTERPRISE_QUEUE_PREFIX}{job.get('id')}", public, merge=False)


def _cluster_enterprise_jobs(self: ClusterStore) -> list[dict[str, Any]]:
    docs = self.list_documents(ENTERPRISE_QUEUE_PREFIX) if self.enabled else {}
    rows = [dict(v) for v in docs.values() if isinstance(v, dict)]
    rows.sort(key=lambda x: str(x.get("created_at") or ""), reverse=True)
    return rows[:200]

ClusterStore._lock_row = _cluster_lock_row
ClusterStore.acquire_lock = _cluster_acquire_lock
ClusterStore.release_lock = _cluster_release_lock
ClusterStore.publish_enterprise_job = _cluster_publish_enterprise_job
ClusterStore.enterprise_jobs = _cluster_enterprise_jobs

cluster_store = ClusterStore()

def _select_active_bot_token() -> tuple[str, int]:
    if BOT_TOKEN_INDEX_RAW:
        try:
            index = max(0, int(BOT_TOKEN_INDEX_RAW) - 1) % len(BOT_TOKENS)
            return BOT_TOKENS[index], index
        except ValueError:
            pass
    # Distribusikan worker secara stabil ke token yang tersedia.
    digest = hashlib.sha256(cluster_store.worker_id.encode("utf-8")).digest()
    index = int.from_bytes(digest[:4], "big") % len(BOT_TOKENS)
    return BOT_TOKENS[index], index

ACTIVE_BOT_TOKEN, ACTIVE_BOT_INDEX = _select_active_bot_token()
_bot_identity_lock = threading.Lock()
_bot_identity_cache: dict[str, dict[str, Any]] = {}

def telegram_api_url(token: str, method: str, base: str | None = None) -> str:
    api_base = (base or TELEGRAM_API_BASE).rstrip("/")
    return f"{api_base}/bot{token}/{method}"

def get_bot_identity(token: str = ACTIVE_BOT_TOKEN) -> dict[str, Any]:
    with _bot_identity_lock:
        cached = _bot_identity_cache.get(token)
        if cached:
            return dict(cached)
    identity: dict[str, Any] = {"id": 0, "username": "", "first_name": "", "token_index": BOT_TOKENS.index(token) + 1}
    try:
        response = requests.get(telegram_api_url(token, "getMe"), timeout=30)
        result = response.json()
        if response.ok and result.get("ok") and isinstance(result.get("result"), dict):
            identity.update(result["result"])
    except Exception as exc:
        identity["error"] = str(exc)
    with _bot_identity_lock:
        _bot_identity_cache[token] = dict(identity)
    return identity

ACTIVE_BOT = get_bot_identity()
CATALOG_BOT_TOKEN = CATALOG_BOT_TOKEN_RAW or ACTIVE_BOT_TOKEN
if CATALOG_BOT_TOKEN not in BOT_TOKENS:
    BOT_TOKENS.append(CATALOG_BOT_TOKEN)
CATALOG_BOT = get_bot_identity(CATALOG_BOT_TOKEN)
print(f"[TELEGRAM] active bot index={ACTIVE_BOT_INDEX + 1}/{len(BOT_TOKENS)} username=@{ACTIVE_BOT.get('username') or '-'} worker={cluster_store.worker_id}", flush=True)
print(f"[TELEGRAM] catalog bot username=@{CATALOG_BOT.get('username') or '-'} id={CATALOG_BOT.get('id') or 0}", flush=True)



def extract_drive_folder_id(value: str) -> str:
    value = value.strip()
    if re.fullmatch(r"[A-Za-z0-9_-]{20,}", value):
        return value
    for pattern in (r"/folders/([A-Za-z0-9_-]+)", r"[?&]id=([A-Za-z0-9_-]+)"):
        match = re.search(pattern, value)
        if match:
            return match.group(1)
    raise ValueError("Link folder Google Drive publik atau Folder ID tidak valid.")

def list_public_drive_folder(folder_id: str) -> list[dict[str, str]]:
    """Read a public Google Drive folder without credentials.

    This uses Google's public embedded-folder page. The folder must be shared
    as 'Anyone with the link - Viewer'.
    """
    url = f"https://drive.google.com/embeddedfolderview?id={quote(folder_id)}#list"
    response = requests.get(
        url,
        headers={"User-Agent": "Mozilla/5.0 Chrome/126.0"},
        timeout=60,
    )
    if not response.ok:
        raise RuntimeError(f"Folder Google Drive publik gagal dibuka: HTTP {response.status_code}.")
    html = response.text
    if "Sign in" in html and "embeddedfolderview" not in response.url:
        raise RuntimeError("Folder meminta login. Ubah akses menjadi Anyone with the link - Viewer.")

    soup = BeautifulSoup(html, "html.parser")
    items: dict[str, dict[str, str]] = {}
    for tag in soup.find_all("a", href=True):
        href = unquote(str(tag.get("href") or ""))
        match = re.search(r"(?:/file/d/|[?&]id=)([A-Za-z0-9_-]{20,})", href)
        if not match:
            continue
        file_id = match.group(1)
        name = (tag.get("title") or tag.get_text(" ", strip=True) or "").strip()
        if name:
            items[file_id] = {"id": file_id, "name": name}

    # Fallback for changes in Google's public folder HTML structure.
    for match in re.finditer(r'([A-Za-z0-9_-]{20,}).{0,500}?([^"<>]{1,180}\.(?:srt|ass|ssa|vtt|mkv|mp4|avi|mov))', html, re.I | re.S):
        file_id, raw_name = match.group(1), match.group(2)
        name = re.sub(r"\s+", " ", raw_name).strip().strip("\"'")
        items.setdefault(file_id, {"id": file_id, "name": name})

    if not items:
        raise RuntimeError(
            "Isi folder publik tidak dapat dibaca. Pastikan folder, bukan hanya file video, "
            "dibagikan sebagai Anyone with the link - Viewer."
        )
    return list(items.values())


def _subtitle_name_score(video_name: str, candidate_name: str) -> int:
    video_stem = Path(video_name).stem.lower().strip()
    candidate = candidate_name.lower().strip()
    candidate_stem = Path(candidate).stem.lower().strip()
    ext = Path(candidate).suffix.lower()
    if ext not in {".srt", ".ass", ".ssa", ".vtt"}:
        return -1

    def clean(value: str) -> str:
        value = re.sub(r"[._\-]+", " ", value)
        value = re.sub(r"\b(1080p|720p|2160p|4k|web[ .-]?dl|webrip|bluray|x264|x265|hevc|aac|hdr)\b", " ", value)
        return re.sub(r"\s+", " ", value).strip()

    v = clean(video_stem)
    c = clean(candidate_stem)
    score = 0
    if c == v:
        score += 100
    elif c.startswith(v + " ") or v.startswith(c + " "):
        score += 75
    else:
        v_tokens, c_tokens = set(v.split()), set(c.split())
        if v_tokens:
            score += int(60 * len(v_tokens & c_tokens) / len(v_tokens))

    # Prioritise Indonesian labels and safer subtitle formats.
    if re.search(r"(^|[ ._\-])(id|ind|indo|indonesia|bahasa)([ ._\-]|$)", candidate):
        score += 30
    if ext == ".srt":
        score += 8
    elif ext in {".ass", ".ssa"}:
        score += 5
    elif ext == ".vtt":
        score += 3
    return score

def find_matching_subtitle(video_file_id: str, public_folder_id: str) -> dict[str, str]:
    if not public_folder_id:
        raise RuntimeError(
            "Mode pencarian otomatis memerlukan link folder Google Drive publik."
        )
    candidates = list_public_drive_folder(public_folder_id)
    video_item = next((item for item in candidates if item.get("id") == video_file_id), None)
    if not video_item:
        raise RuntimeError(
            "Video tidak ditemukan di folder publik tersebut. Pastikan link folder sesuai "
            "dan folder dibagikan sebagai Anyone with the link - Viewer."
        )
    video_name = str(video_item.get("name") or "video")
    ranked = []
    for item in candidates:
        score = _subtitle_name_score(video_name, str(item.get("name") or ""))
        if score >= 45:
            ranked.append((score, item))
    if not ranked:
        raise RuntimeError(f"Subtitle yang cocok tidak ditemukan di folder publik untuk {video_name}.")
    ranked.sort(key=lambda pair: (-pair[0], str(pair[1].get("name") or "").lower()))
    best = ranked[0][1]
    return {"id": str(best["id"]), "name": str(best.get("name") or "subtitle"), "video_name": video_name}


def parse_topic_options(raw: str) -> list[dict[str, Any]]:
    options: list[dict[str, Any]] = []
    for item in raw.split(","):
        item = item.strip()
        if not item or ":" not in item:
            continue
        name, value = item.rsplit(":", 1)
        try:
            thread_id = int(value.strip())
        except ValueError:
            continue
        options.append({
            "name": name.strip(),
            "thread_id": thread_id,
            "chat_id": CHANNEL_ID,
            "chat_title": "Target default",
        })
    if not options:
        options = [{
            "name": "General",
            "thread_id": DEFAULT_THREAD_ID,
            "chat_id": CHANNEL_ID,
            "chat_title": "Target default",
        }]
    return options

def load_discovered_topics() -> list[dict[str, Any]]:
    local: list[dict[str, Any]] = []
    try:
        if TOPIC_STORE_PATH.exists():
            data = json.loads(TOPIC_STORE_PATH.read_text(encoding="utf-8"))
            if isinstance(data, list):
                local = data
    except Exception:
        pass
    if not (GLOBAL_SYNC_ENABLED and cluster_store.enabled):
        return local
    remote = cluster_store.get_document("topics", [])
    merged = _merge_list_records(remote, local if GLOBAL_SYNC_BOOTSTRAP_LOCAL else [])
    if _json_fingerprint(merged) != _json_fingerprint(remote):
        merged = cluster_store.save_document("topics", merged, merge=False)
    try:
        atomic_json_write(TOPIC_STORE_PATH, merged)
    except Exception:
        pass
    return merged


def save_discovered_topics(topics: list[dict[str, Any]]) -> None:
    remote = cluster_store.get_document("topics", []) if GLOBAL_SYNC_ENABLED else []
    topics = _merge_list_records(remote, topics)
    if GLOBAL_SYNC_ENABLED:
        topics = cluster_store.save_document("topics", topics, merge=False)
    atomic_json_write(TOPIC_STORE_PATH, topics)

def get_topic_options() -> list[dict[str, Any]]:
    merged: dict[tuple[str, int], dict[str, Any]] = {}
    # Scan may discover IDs, but explicit Railway TOPIC_OPTIONS must win names.
    for item in load_discovered_topics() + parse_topic_options(TOPIC_OPTIONS_RAW):
        key = (
            str(item.get("chat_id") or CHANNEL_ID),
            int(item.get("thread_id") or 0),
        )
        merged[key] = item
    return sorted(
        merged.values(),
        key=lambda x: (
            str(x.get("chat_title") or ""),
            int(x.get("thread_id") or 0),
        ),
    )


series_store_lock = threading.Lock()

def _read_local_series_store() -> dict[str, Any]:
    try:
        if SERIES_STORE_PATH.exists():
            data = json.loads(SERIES_STORE_PATH.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
    except Exception:
        pass
    return {}


def _publish_local_series_source_once(local: dict[str, Any]) -> None:
    """Publish this Railway's legacy volume once, without making it authoritative."""
    global _global_source_published
    if not (GLOBAL_DATABASE_PUBLISH_LOCAL and GLOBAL_SYNC_BOOTSTRAP_LOCAL and cluster_store.enabled):
        return
    with _global_source_lock:
        if _global_source_published:
            return
        normalized = _normalize_series_store(local)
        if normalized:
            source_key = f"{GLOBAL_DATABASE_SOURCE_PREFIX}{cluster_store.worker_id}"
            cluster_store.save_document(source_key, normalized, merge=False)
            print(f"[GLOBAL-DB] published legacy source worker={cluster_store.worker_id} series={len(normalized)}", flush=True)
        _global_source_published = True


def _read_global_series_database(local: dict[str, Any] | None = None) -> dict[str, Any]:
    """Read the canonical Supabase database.

    Legacy Railway snapshots are used only while migration flags are enabled.
    After migration, the canonical Supabase row is the only authoritative source,
    preventing deleted/duplicate local records from being re-imported.
    """
    local = local or {}
    migration_enabled = GLOBAL_DATABASE_PUBLISH_LOCAL and GLOBAL_SYNC_BOOTSTRAP_LOCAL
    if migration_enabled:
        _publish_local_series_source_once(local)
    canonical_raw = cluster_store.get_document(GLOBAL_DATABASE_CANONICAL_KEY, {})
    canonical = _normalize_series_store(canonical_raw)
    if not cluster_store.enabled:
        return canonical or _normalize_series_store(local)
    if migration_enabled:
        sources = cluster_store.list_documents(GLOBAL_DATABASE_SOURCE_PREFIX)
        merged = _normalize_series_store(canonical, *sources.values(), local)
    else:
        merged = canonical
    # Canonicalize/deduplicate the authoritative row itself.
    if _json_fingerprint(merged) != _json_fingerprint(canonical_raw):
        cluster_store.save_document(GLOBAL_DATABASE_CANONICAL_KEY, merged, merge=False)
        merged = _normalize_series_store(cluster_store.get_document(GLOBAL_DATABASE_CANONICAL_KEY, merged))
    return merged

def load_series_store() -> dict[str, Any]:
    """Supabase canonical row is authoritative; /data is only an offline cache."""
    with series_store_lock:
        local = _read_local_series_store()
        if not (GLOBAL_SYNC_ENABLED and cluster_store.enabled):
            return _normalize_series_store(local)
        merged = _read_global_series_database(local)
        try:
            temp_path = SERIES_STORE_PATH.with_suffix(".tmp")
            temp_path.parent.mkdir(parents=True, exist_ok=True)
            temp_path.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
            temp_path.replace(SERIES_STORE_PATH)
        except Exception:
            pass
        return merged

def backup_series_store(data: dict[str, Any], reason: str = "auto") -> Path:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    safe_reason = re.sub(r"[^A-Za-z0-9_-]+", "-", reason).strip("-") or "auto"
    backup_path = BACKUP_DIR / f"telegram-series-{stamp}-{safe_reason}.json"
    backup_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    backups = sorted(BACKUP_DIR.glob("telegram-series-*.json"), key=lambda item: item.stat().st_mtime, reverse=True)
    for stale in backups[MAX_BACKUPS:]:
        stale.unlink(missing_ok=True)
    return backup_path

def save_series_store(data: dict[str, Any], reason: str = "update") -> None:
    """Atomically merge changes into the global canonical Supabase document."""
    with series_store_lock:
        SERIES_STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
        previous = _read_local_series_store()
        if previous:
            try:
                backup_series_store(previous, reason=f"before-{reason}")
            except Exception:
                pass
        normalized = _normalize_series_store(data)
        if GLOBAL_SYNC_ENABLED and cluster_store.enabled:
            remote = cluster_store.get_document(GLOBAL_DATABASE_CANONICAL_KEY, {})
            # New writes merge only with the canonical database. Legacy volume snapshots
            # are migration inputs, never permanent authoritative sources.
            normalized = _normalize_series_store(remote, normalized)
            normalized = cluster_store.save_document(GLOBAL_DATABASE_CANONICAL_KEY, normalized, merge=False)
            normalized = _normalize_series_store(cluster_store.get_document(GLOBAL_DATABASE_CANONICAL_KEY, normalized))
        temp_path = SERIES_STORE_PATH.with_suffix(".tmp")
        temp_path.write_text(json.dumps(normalized, ensure_ascii=False, indent=2), encoding="utf-8")
        temp_path.replace(SERIES_STORE_PATH)
        backup_series_store(normalized, reason=reason)

def storage_status() -> dict[str, Any]:
    persistent = str(SERIES_STORE_PATH).startswith("/data/")
    return {
        "persistent": persistent,
        "series_path": str(SERIES_STORE_PATH),
        "topic_path": str(TOPIC_STORE_PATH),
        "backup_dir": str(BACKUP_DIR),
        "warning": "" if persistent else "Penyimpanan masih memakai lokasi sementara. Pasang Railway Volume pada /data.",
    }

def telegram_message_url(
    target_chat_id: str,
    message_id: int,
) -> str:
    chat_value = str(target_chat_id).strip()

    if chat_value.startswith("@"):
        return (
            f"https://t.me/{chat_value[1:]}/{message_id}"
        )

    if chat_value.startswith("-100"):
        internal_id = chat_value[4:]
        return f"https://t.me/c/{internal_id}/{message_id}"

    raise RuntimeError(
        "Tidak dapat membuat link episode. "
        "Gunakan Chat ID supergroup -100... atau @username publik."
    )

def series_store_key(data: dict[str, Any]) -> str:
    metadata = data["metadata"]
    tmdb_id = int(data.get("tmdb_id") or 0)
    season = int(data.get("season_number") or 1)
    chat_id = str(data.get("target_chat_id") or CHANNEL_ID)
    thread_id = int(data.get("message_thread_id") or 0)
    return f"{tmdb_id}:{season}:{chat_id}:{thread_id}"

def build_episode_keyboard(
    episodes: dict[str, Any],
) -> list[list[dict[str, str]]]:
    """Susun tombol episode seperti katalog Telegram, maksimal 5 per baris.

    Tombol pertama pada setiap baris diberi ikon panah agar tampilannya mendekati
    contoh katalog episode, tetapi seluruh tombol tetap dapat diketuk langsung.
    """
    ordered = sorted(episodes, key=lambda value: int(value))
    rows: list[list[dict[str, str]]] = []
    for start in range(0, len(ordered), EPISODE_BUTTONS_PER_ROW):
        row: list[dict[str, str]] = []
        for offset, episode_key in enumerate(ordered[start:start + EPISODE_BUTTONS_PER_ROW]):
            episode = episodes[episode_key]
            prefix = "➡️ " if offset == 0 else ""
            row.append({
                "text": f"{prefix}E.{int(episode_key):02d}",
                "url": str(episode["url"]),
            })
        rows.append(row)
    return rows

def _catalog_episode_lines(series: dict[str, Any]) -> list[str]:
    """Build clickable episode rows for the Smart Catalog photo caption."""
    episodes = series.get("episodes") if isinstance(series.get("episodes"), dict) else {}
    ordered = sorted(episodes, key=lambda value: int(value))
    per_row = max(1, min(5, EPISODE_BUTTONS_PER_ROW))
    rows: list[str] = []
    for start in range(0, len(ordered), per_row):
        links: list[str] = []
        for episode_key in ordered[start:start + per_row]:
            episode = episodes.get(episode_key) or {}
            url = html.escape(str(episode.get("url") or ""), quote=True)
            label = f"E.{int(episode_key):02d}"
            links.append(f'<a href="{url}">{label}</a>' if url else label)
        rows.append("➡️ " + " | ".join(links))
    return rows


def _catalog_list(value: Any) -> str:
    if not isinstance(value, list):
        return "-"
    clean = [html.escape(str(item).lstrip("#").strip()) for item in value if str(item).strip()]
    return ", ".join(clean) or "-"


def build_series_index_caption(series: dict[str, Any]) -> str:
    """Build CineDrive v16 Smart Catalog caption.

    E01 contains title/year and TMDB details. Starting from E02, every refresh
    creates a compact new poster catalog containing only the watch instruction
    and all clickable episode links, exactly in upload order.
    """
    episodes = series.get("episodes") if isinstance(series.get("episodes"), dict) else {}
    episode_rows = _catalog_episode_lines(series)
    episode_count = len(episodes)

    if episode_count <= 1:
        title = html.escape(str(series.get("series_title") or "Serial"))
        year = html.escape(str(series.get("year") or "-"))
        rating = float(series.get("vote_average") or 0)
        votes = int(series.get("vote_count") or 0)
        release_date = html.escape(str(series.get("release_date") or "-"))
        certification = html.escape(str(series.get("certification") or "-"))
        overview = html.escape(str(series.get("overview") or "Sinopsis belum tersedia.").strip())
        lines = [
            f"<b>{title} ({year})</b>", "",
            f"⭐ Peringkat: {rating:.1f}/10 ({votes} suara)",
            f"🎭 Genre: {_catalog_list(series.get('genres'))}",
            f"📅 Rilis: {release_date}",
            f"🔞 Kategori: {certification}",
            f"🌍 Negara: {_catalog_list(series.get('countries'))}",
            f"🗣 Bahasa: {_catalog_list(series.get('languages'))}", "",
            "💬 <b>Sinopsis:</b>", overview, "",
            "👇 <b>Tap episode untuk menonton</b>",
            *episode_rows,
        ]
    else:
        lines = [
            "👇 <b>Tap episode untuk menonton</b>",
            *episode_rows,
        ]

    caption = "\n".join(lines)
    if len(caption) <= 1024:
        return caption

    # E01 may have a long TMDB overview. Shorten only the overview while keeping
    # complete HTML tags and all available episode links intact.
    if episode_count <= 1:
        fixed_tail = "\n\n👇 <b>Tap episode untuk menonton</b>\n" + "\n".join(episode_rows)
        prefix_lines = lines[:10]  # through the Sinopsis heading
        prefix = "\n".join(prefix_lines) + "\n"
        available = max(0, 1024 - len(prefix) - len(fixed_tail) - 1)
        short_overview = overview[:available].rstrip()
        if len(short_overview) < len(overview) and available >= 1:
            short_overview = short_overview[:-1].rstrip() + "…"
        return (prefix + short_overview + fixed_tail)[:1024]

    # Compact catalogs normally fit hundreds of episodes. If Telegram's photo
    # caption limit is reached, retain the newest complete rows without cutting
    # HTML anchors in the middle.
    compact = ["👇 <b>Tap episode untuk menonton</b>"]
    for row in reversed(episode_rows):
        candidate = "\n".join([compact[0], row] + compact[1:])
        if len(candidate) > 1024:
            break
        compact.insert(1, row)
    if len(compact) - 1 < len(episode_rows):
        compact.insert(1, "…")
    return "\n".join(compact)


def telegram_post(
    method: str,
    payload: dict[str, Any],
    token: str | None = None,
    try_all_bots: bool = False,
) -> dict[str, Any]:
    preferred = token or ACTIVE_BOT_TOKEN
    tokens = [preferred]
    if try_all_bots:
        tokens.extend(item for item in BOT_TOKENS if item != preferred)
    errors: list[str] = []
    for candidate in tokens:
        try:
            response = requests.post(
                telegram_api_url(candidate, method),
                data=payload,
                timeout=180,
            )
            try:
                result = response.json()
            except ValueError as exc:
                raise RuntimeError(f"Telegram {method} merespons bukan JSON.") from exc
            if response.ok and result.get("ok"):
                result["_bot"] = get_bot_identity(candidate)
                return result
            errors.append(str(result.get("description") or response.text[-800:]))
        except Exception as exc:
            errors.append(str(exc))
    raise RuntimeError(f"Telegram {method} gagal: {' | '.join(errors[-3:])}")

def create_or_update_series_index(
    data: dict[str, Any],
    episode_message_id: int,
) -> int:
    """Create a fresh Smart Catalog poster and then delete the previous catalog.

    The operation is protected by a Supabase lock per serial/season/topic so two
    Railway workers cannot publish competing catalogs. The old catalog is never
    deleted until Telegram confirms the new poster was created successfully.
    """
    metadata = data["metadata"]
    episode_number = int(data.get("episode_number") or 0)
    if not metadata.get("episode_code") or episode_number < 1:
        return 0

    target_chat_id = str(data.get("target_chat_id") or CHANNEL_ID)
    thread_id = int(data.get("message_thread_id") or 0)
    key = series_store_key(data)
    lock_key = f"smart-catalog:{key}"
    acquired = False
    lock_info: dict[str, Any] = {}

    # Brief retries handle E01/E02 completing on different workers at nearly the
    # same time without losing the newly uploaded episode from the canonical map.
    for _ in range(15):
        acquired, lock_info = cluster_store.acquire_lock(
            lock_key,
            {"episode": episode_number, "chat_id": target_chat_id, "thread_id": thread_id},
        )
        if acquired:
            break
        time.sleep(2)
    if not acquired:
        raise RuntimeError(
            f"Smart Catalog sedang diproses worker {lock_info.get('owner') or 'lain'}."
        )

    try:
        store = load_series_store()
        series = store.get(key) or {
            "tmdb_id": int(data.get("tmdb_id") or 0),
            "series_title": metadata.get("series_title"),
            "original_title": metadata.get("original_title"),
            "year": metadata.get("year"),
            "season_number": int(data.get("season_number") or 1),
            "target_chat_id": target_chat_id,
            "message_thread_id": thread_id,
            "topic_name": data.get("topic_name"),
            "poster_url": metadata.get("poster_url"),
            "vote_average": metadata.get("vote_average"),
            "vote_count": metadata.get("vote_count"),
            "release_date": metadata.get("release_date"),
            "certification": metadata.get("certification"),
            "genres": metadata.get("genres") or [],
            "countries": metadata.get("countries") or [],
            "languages": metadata.get("languages") or [],
            "directors": metadata.get("directors") or [],
            "writers": metadata.get("writers") or [],
            "cast": metadata.get("cast") or [],
            "overview": metadata.get("overview"),
            "index_message_id": 0,
            "index_type": "",
            "episodes": {},
        }

        series.setdefault("episodes", {})
        series["episodes"][str(episode_number)] = {
            "message_id": episode_message_id,
            "url": telegram_message_url(target_chat_id, episode_message_id),
            "title": metadata.get("episode_title"),
            "episode_code": metadata.get("episode_code"),
            "overview": metadata.get("overview"),
            "release_date": metadata.get("release_date"),
            "vote_average": metadata.get("vote_average"),
            "updated_at": now_ts(),
            "uploaded_by_worker": cluster_store.worker_id,
            "uploaded_by_bot_id": int(ACTIVE_BOT.get("id") or 0),
            "uploaded_by_bot_username": str(ACTIVE_BOT.get("username") or ""),
        }

        for field, fallback in (
            ("series_title", series.get("series_title")),
            ("original_title", series.get("original_title")),
            ("year", series.get("year")),
            ("poster_url", series.get("poster_url")),
            ("overview", series.get("overview")),
        ):
            series[field] = metadata.get(field) or fallback
        for field in (
            "vote_average", "vote_count", "release_date", "certification",
            "genres", "countries", "languages", "directors", "writers", "cast",
        ):
            value = metadata.get(field)
            if value not in (None, "", []):
                series[field] = value

        previous_index_id = int(series.get("index_message_id") or 0)
        caption = build_series_index_caption(series)
        payload: dict[str, Any] = {
            "chat_id": target_chat_id,
            "caption": caption,
            "parse_mode": "HTML",
        }
        if thread_id > 0:
            payload["message_thread_id"] = str(thread_id)

        poster = str(series.get("poster_url") or "").strip()
        if not poster:
            raise RuntimeError("Poster TMDB tidak tersedia; Smart Catalog membutuhkan poster.")
        payload["photo"] = poster
        result = telegram_post("sendPhoto", payload, token=CATALOG_BOT_TOKEN)
        new_index_id = int(result["result"]["message_id"])

        # Persist the new authoritative catalog before cleanup. A crash after this
        # point can leave an extra old poster, but cannot lose the new catalog.
        bot_info = result.get("_bot") or {}
        series["previous_index_message_id"] = previous_index_id
        series["index_message_id"] = new_index_id
        series["index_type"] = "photo"
        series["catalog_publish_mode"] = "new-poster-per-episode"
        series["catalog_layout"] = "e01-full-e02plus-compact"
        series["index_bot_id"] = int(bot_info.get("id") or CATALOG_BOT.get("id") or 0)
        series["index_bot_username"] = str(bot_info.get("username") or CATALOG_BOT.get("username") or "")
        series["catalog_bot_id"] = int(CATALOG_BOT.get("id") or 0)
        series["catalog_bot_username"] = str(CATALOG_BOT.get("username") or "")
        series["catalog_version"] = CLUSTER_VERSION
        series["updated_at"] = now_ts()
        series["previous_index_deleted"] = previous_index_id <= 0
        series.pop("catalog_edit_warning", None)
        series.pop("delete_warning", None)
        store[key] = series
        save_series_store(store, reason="v16-smart-catalog-published")

        if previous_index_id > 0 and previous_index_id != new_index_id:
            try:
                telegram_post(
                    "deleteMessage",
                    {"chat_id": target_chat_id, "message_id": str(previous_index_id)},
                    token=CATALOG_BOT_TOKEN,
                    try_all_bots=True,
                )
                series["previous_index_deleted"] = True
                series["previous_index_deleted_at"] = now_ts()
                series.pop("delete_warning", None)
            except Exception as exc:
                series["previous_index_deleted"] = False
                series["delete_warning"] = str(exc)
            store[key] = series
            save_series_store(store, reason="v16-smart-catalog-cleanup")

        return new_index_id
    finally:
        cluster_store.release_lock(lock_key)


queue_lock = threading.Lock()
queue_condition = threading.Condition(queue_lock)
jobs: dict[str, dict[str, Any]] = {}
pending_jobs: deque[str] = deque()
worker_started = False

PANEL_HTML = r"""
<!doctype html>
<html lang="id">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>CineDrive Studio v16 Enterprise Smart Catalog</title>

<style>
:root{
  color-scheme:dark;
  --bg:#080b16;--bg2:#11162a;--surface:rgba(20,25,47,.86);--surface2:#171d35;
  --line:rgba(148,163,184,.18);--line-strong:rgba(139,92,246,.52);
  --accent:#8b5cf6;--accent2:#06b6d4;--accent3:#ec4899;
  --text:#f8fafc;--muted:#aab3ca;--ok:#34d399;--err:#fb7185;--warn:#fbbf24;
  --shadow:0 18px 55px rgba(0,0,0,.34);--radius:20px;
}
*{box-sizing:border-box}
html{scroll-behavior:smooth}
body{
  margin:0;font-family:Inter,ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif;color:var(--text);
  background:
    radial-gradient(circle at 12% 0%,rgba(139,92,246,.20),transparent 34%),
    radial-gradient(circle at 88% 12%,rgba(6,182,212,.14),transparent 30%),
    linear-gradient(180deg,#070914 0%,#0b1020 48%,#080b16 100%);
  min-height:100vh;
}
body:before{content:"";position:fixed;inset:0;pointer-events:none;opacity:.28;background-image:linear-gradient(rgba(255,255,255,.018) 1px,transparent 1px),linear-gradient(90deg,rgba(255,255,255,.018) 1px,transparent 1px);background-size:30px 30px}
.wrap{width:min(1100px,94%);margin:28px auto 60px;position:relative;z-index:1}
.card{
  background:linear-gradient(145deg,rgba(25,31,58,.92),rgba(15,19,37,.94));
  border:1px solid var(--line);border-radius:var(--radius);box-shadow:var(--shadow);backdrop-filter:blur(14px);
  padding:22px;margin-bottom:20px;overflow:hidden;
}
.card:first-child{position:relative;border-color:rgba(139,92,246,.28)}
.card:first-child:before{content:"";position:absolute;inset:0 0 auto 0;height:4px;background:linear-gradient(90deg,var(--accent),var(--accent2),var(--accent3))}
h1{font-size:clamp(26px,4vw,42px);line-height:1.08;margin:5px 0 10px;letter-spacing:-.04em;background:linear-gradient(90deg,#fff,#c4b5fd 48%,#67e8f9);-webkit-background-clip:text;background-clip:text;color:transparent}
h2{font-size:clamp(20px,2.5vw,28px);margin:0 0 10px;letter-spacing:-.02em}
strong{color:#fff}
label{display:block;margin:16px 0 7px;color:#c8d0e4;font-size:13px;font-weight:750;letter-spacing:.02em}
input,textarea,select,button{width:100%;border-radius:13px;padding:13px 14px;font:inherit;transition:.2s ease}
input,textarea,select{background:rgba(7,10,24,.72);color:var(--text);border:1px solid var(--line);outline:none}
input::placeholder,textarea::placeholder{color:#6f7892}
input:focus,textarea:focus,select:focus{border-color:var(--accent);box-shadow:0 0 0 4px rgba(139,92,246,.14);background:#0b1022}
textarea{min-height:92px;resize:vertical;line-height:1.55}
input[type=file]{padding:10px;background:rgba(8,12,28,.62)}
input[type=checkbox]{accent-color:var(--accent);transform:translateY(1px)}
button{margin-top:15px;border:0;color:white;font-weight:800;cursor:pointer;background:linear-gradient(135deg,var(--accent),#6d5dfc);box-shadow:0 9px 24px rgba(109,93,252,.28)}
button:hover{transform:translateY(-1px);filter:brightness(1.08);box-shadow:0 13px 30px rgba(109,93,252,.36)}
button:active{transform:translateY(0) scale(.995)}
.result{display:grid;grid-template-columns:112px 1fr;gap:18px;border:1px solid var(--line);border-radius:18px;padding:16px;margin-top:15px;background:rgba(8,12,28,.48);box-shadow:inset 0 1px 0 rgba(255,255,255,.025)}
.result:hover{border-color:rgba(139,92,246,.38)}
.result img{width:112px;aspect-ratio:2/3;object-fit:cover;border-radius:13px;box-shadow:0 12px 30px rgba(0,0,0,.42)}
.job{border:1px solid var(--line);border-radius:16px;padding:16px;margin-top:13px;background:rgba(8,12,28,.55)}
.row{display:grid;grid-template-columns:1fr auto;gap:12px;align-items:center}.state{font-size:12px;font-weight:900;letter-spacing:.08em;padding:6px 9px;border-radius:999px;background:rgba(255,255,255,.06)}
.SUCCESS{color:var(--ok)}.ERROR{color:var(--err)}.DOWNLOADING,.PROCESSING,.UPLOADING,.QUEUED,.PREPARING,.READY{color:var(--warn)}
.muted{color:var(--muted);font-size:14px;line-height:1.55}.error{color:var(--err);white-space:pre-wrap;word-break:break-word}
.progress{height:11px;border-radius:999px;background:#090c18;overflow:hidden;margin-top:12px;border:1px solid rgba(255,255,255,.04)}
.progress>div{height:100%;background:linear-gradient(90deg,var(--accent),var(--accent2));box-shadow:0 0 15px rgba(6,182,212,.42)}
.batch-help{background:linear-gradient(135deg,rgba(139,92,246,.10),rgba(6,182,212,.07));border:1px solid rgba(139,92,246,.25);padding:14px 15px;border-radius:14px;margin-top:12px;line-height:1.55}
.batch-help code{color:#ddd6fe;word-break:break-all;background:rgba(0,0,0,.25);padding:2px 6px;border-radius:6px}
.series-search{margin-bottom:12px}
.series-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(255px,1fr));gap:14px;margin-top:14px}
.series-card{border:1px solid var(--line);border-radius:16px;padding:13px;background:rgba(8,12,28,.58);cursor:pointer;transition:.2s ease}
.series-card:hover{border-color:var(--accent);transform:translateY(-2px);box-shadow:0 14px 30px rgba(0,0,0,.25)}
.series-card.selected{outline:2px solid var(--accent);background:rgba(139,92,246,.12)}
.series-card img{width:76px;height:112px;object-fit:cover;border-radius:10px;float:left;margin-right:13px;box-shadow:0 10px 22px rgba(0,0,0,.35)}
.series-card .title{font-weight:850;margin-bottom:7px}.series-card .meta{font-size:13px;color:var(--muted);line-height:1.55}
.hidden{display:none!important}
.menu-nav{display:grid;grid-template-columns:repeat(3,1fr);gap:11px;margin:17px 0 2px}
.menu-nav button{margin:0;background:rgba(8,12,28,.65);border:1px solid var(--line);box-shadow:none;padding:14px 10px;min-height:54px}
.menu-nav button.active,.menu-nav button:hover{background:linear-gradient(135deg,rgba(139,92,246,.92),rgba(6,182,212,.72));border-color:transparent;transform:translateY(-1px)}
.menu-section{display:none;margin-top:20px;padding-top:20px;border-top:1px solid var(--line);animation:menuFade .22s ease}
.menu-section.active{display:block}
.menu-section>.menu-content{padding:0}
@keyframes menuFade{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:translateY(0)}}
.clearfix::after{content:"";display:block;clear:both}
::-webkit-scrollbar{width:10px}::-webkit-scrollbar-track{background:#080b16}::-webkit-scrollbar-thumb{background:#333a59;border-radius:999px;border:2px solid #080b16}
@media(min-width:760px){form:not(.result form) label+input,form label+select,form label+textarea{max-width:100%}}
@media(max-width:700px){
 .wrap{width:min(96%,680px);margin:14px auto 38px}.card{border-radius:17px;padding:17px;margin-bottom:14px}
 .result{grid-template-columns:78px 1fr;gap:12px;padding:12px}.result img{width:78px}.menu-nav{grid-template-columns:1fr}.menu-nav button{font-size:14px}
 .menu-section{margin-top:16px;padding-top:16px}.menu-section>.menu-content{padding:0}
 h1{font-size:29px}.row{grid-template-columns:1fr}.state{justify-self:start}
}


.app-nav{position:sticky;top:0;z-index:50;width:min(1100px,94%);margin:0 auto 14px;padding:10px;background:rgba(8,11,22,.92);border:1px solid var(--line);border-radius:0 0 18px 18px;backdrop-filter:blur(16px);box-shadow:0 12px 35px rgba(0,0,0,.30);display:grid;grid-template-columns:repeat(6,1fr);gap:8px}
.app-nav button{margin:0;padding:11px 8px;min-height:48px;background:rgba(20,25,47,.72);border:1px solid var(--line);box-shadow:none;font-size:13px}
.app-nav button.active,.app-nav button:hover{background:linear-gradient(135deg,var(--accent),rgba(6,182,212,.82));border-color:transparent}
.page-section{display:none;animation:menuFade .22s ease}
.page-section.active{display:block}
.mobile-nav-label{display:inline}
#queueSection{scroll-margin-top:90px}
@media(max-width:700px){
 body{padding-bottom:82px}
 .wrap{margin-top:14px}
 .app-nav{position:fixed;top:auto;bottom:0;left:0;right:0;width:100%;margin:0;padding:8px 8px calc(8px + env(safe-area-inset-bottom));border-radius:18px 18px 0 0;grid-template-columns:repeat(6,1fr)}
 .app-nav button{padding:8px 4px;min-height:56px;font-size:12px;border-radius:12px;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:2px}
 .app-nav .nav-icon{font-size:19px;line-height:1}
 .mobile-nav-label{font-size:10px}
}

.scan-restore-box{padding:16px;border:1px solid rgba(139,92,246,.32);border-radius:16px;background:rgba(10,14,35,.55);margin-bottom:18px}
.scan-restore-box h3{margin:0 0 8px}.scan-series-grid{display:grid;gap:12px;margin-top:14px}.scan-series-card{padding:14px;border:1px solid var(--line);border-radius:14px;background:rgba(8,12,30,.72)}
.scan-series-card form{margin:0}.episode-chips{display:flex;flex-wrap:wrap;gap:6px;margin:10px 0}.episode-chips span{padding:5px 8px;border-radius:999px;background:rgba(139,92,246,.18);border:1px solid rgba(139,92,246,.35);font-size:12px}.soft-line{border:0;border-top:1px solid var(--line);margin:20px 0}

.data-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px;margin:14px 0}.data-stat{padding:15px;border:1px solid var(--line);border-radius:15px;background:rgba(8,12,28,.56)}.data-stat b{display:block;font-size:24px;margin-top:5px}.action-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:10px}.action-grid form{margin:0}.action-grid button{margin-top:0}.danger{background:linear-gradient(135deg,#be123c,#ef4444)!important}.json-box{max-height:520px;overflow:auto;white-space:pre-wrap;word-break:break-word;background:#050814;border:1px solid var(--line);border-radius:14px;padding:14px;font:12px/1.55 ui-monospace,SFMono-Regular,Consolas,monospace}.backup-row{display:grid;grid-template-columns:1fr auto auto;gap:8px;align-items:center;padding:10px 0;border-bottom:1px solid var(--line)}.backup-row form{margin:0}.backup-row button{margin:0;padding:9px 12px}.watermark-note{padding:12px 14px;margin:10px 0;border-radius:13px;background:rgba(6,182,212,.08);border:1px solid rgba(6,182,212,.25);color:var(--muted);font-size:13px;line-height:1.5}.notice{padding:12px 14px;border:1px solid rgba(52,211,153,.35);background:rgba(52,211,153,.08);border-radius:13px;margin:12px 0}
.status-toolbar{display:grid;grid-template-columns:1fr auto;gap:10px;align-items:center;margin:12px 0}.status-toolbar button{margin:0;width:auto;padding:10px 15px}.status-summary{display:grid;grid-template-columns:repeat(auto-fit,minmax(145px,1fr));gap:10px;margin:14px 0}.status-stat{padding:13px;border:1px solid var(--line);border-radius:14px;background:rgba(8,12,28,.56)}.status-stat b{display:block;font-size:23px;margin-top:4px}.worker-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:10px;margin:12px 0 18px}.worker-card{padding:14px;border:1px solid var(--line);border-radius:15px;background:rgba(8,12,28,.58)}.worker-head{display:flex;align-items:center;justify-content:space-between;gap:10px}.worker-dot{width:11px;height:11px;border-radius:50%;display:inline-block;margin-right:7px;background:#ef4444;box-shadow:0 0 12px rgba(239,68,68,.55)}.worker-card.online .worker-dot{background:#22c55e;box-shadow:0 0 12px rgba(34,197,94,.65)}.worker-state{font-size:12px;font-weight:900;letter-spacing:.05em}.worker-card.online .worker-state{color:#4ade80}.worker-card.offline .worker-state{color:#f87171}.status-job{border:1px solid var(--line);border-radius:17px;padding:15px;margin:12px 0;background:rgba(8,12,28,.58)}.status-meta{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:5px 14px;margin-top:10px}.status-badge{display:inline-block;padding:5px 9px;border-radius:999px;font-size:12px;font-weight:900;letter-spacing:.05em;background:rgba(255,255,255,.06)}.status-empty{text-align:center;padding:30px 15px;border:1px dashed var(--line);border-radius:16px}.status-updated{font-size:12px;color:var(--muted)}
</style>
</head>
<body>
<nav class="app-nav" aria-label="Navigasi utama">
  <button type="button" data-page-target="searchSection"><span class="nav-icon">🔍</span><span class="mobile-nav-label">Film</span></button>
  <button type="button" data-page-target="serialSection"><span class="nav-icon">📺</span><span class="mobile-nav-label">Serial</span></button>
  <button type="button" data-page-target="queueSection"><span class="nav-icon">⏳</span><span class="mobile-nav-label">Antrean</span></button>
  <button type="button" data-page-target="statusSection"><span class="nav-icon">📊</span><span class="mobile-nav-label">Status</span></button>
  <button type="button" data-page-target="dataSection"><span class="nav-icon">🗄️</span><span class="mobile-nav-label">Data</span></button>
  <button type="button" data-page-target="homeSection"><span class="nav-icon">🏠</span><span class="mobile-nav-label">Info</span></button>
</nav>
<div class="wrap">
  <div class="card page-section" id="homeSection">
    <h1>🎬 CineDrive Studio v16 Enterprise Smart Catalog</h1>
    <p class="muted">Pilih menu di navigasi untuk mencari film, mengelola serial, atau melihat antrean tanpa perlu menggulir halaman panjang.</p>
    <div class="batch-help"><strong>Status penyimpanan:</strong> {% if storage.persistent %}<span class="SUCCESS">Permanen</span>{% else %}<span class="ERROR">Sementara</span>{% endif %}<br><span class="muted">Serial: {{ storage.series_path }}<br>Topic: {{ storage.topic_path }}<br>Backup: {{ storage.backup_dir }}</span>{% if storage.warning %}<p class="error">{{ storage.warning }}</p>{% endif %}</div>
  </div>

  <div class="card page-section active" id="searchSection">
    <h1>🎬 CineDrive Studio</h1>
    <p class="muted" style="font-size:15px;margin-top:0">Kelola film, serial, subtitle, watermark, dan publikasi Telegram dalam satu panel. Episode baru mengambil detail TMDB terbaru dan mengganti posting indeks lama secara otomatis.</p>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px">
      <form method="post" action="{{ scan_url }}">
        <button type="submit">Scan Group & Topic terbaru</button>
      </form>
      <form method="post" action="{{ test_telegram_url }}">
        <button type="submit">Tes Koneksi Bot API</button>
      </form>
    </div>
    {% if scan_message %}
      <p class="muted">{{ scan_message }}</p>
    {% endif %}
    <p class="muted">
      Kirim satu pesan baru di setiap topic terlebih dahulu. Telegram Bot API tidak menyediakan metode untuk menampilkan semua topic sekaligus; fitur ini membaca topic dari update terbaru yang diterima bot.
    </p>
    <p class="muted">
      TMDB + thumbnail + subtitle + posting utama serial dengan tombol episode.
      Saat episode baru ditambahkan, posting utama serial akan diperbarui otomatis.
    </p>

    <div class="batch-help">
      <strong>Status penyimpanan:</strong>
      {% if storage.persistent %}<span class="SUCCESS">Permanen</span>{% else %}<span class="ERROR">Sementara</span>{% endif %}<br>
      <span class="muted">Serial: {{ storage.series_path }}<br>Topic: {{ storage.topic_path }}<br>Backup: {{ storage.backup_dir }}</span>
      {% if storage.warning %}<p class="error">{{ storage.warning }}</p>{% endif %}
    </div>

    <form method="get" action="{{ panel_url }}">
      <input type="hidden" name="key" value="{{ key }}">
      <label>Cari judul TMDB</label>
      <input name="q" value="{{ query }}" placeholder="Contoh: Masters of the Universe" required>
      <button type="submit">Cari di TMDB</button>
    </form>

    {% if results %}
      <h2 style="margin-top:22px">Hasil pencarian</h2>
      {% for item in results %}
      <form method="post" action="{{ enqueue_url }}" enctype="multipart/form-data">
        <input type="hidden" name="tmdb_id" value="{{ item.id }}">
        <input type="hidden" name="media_type" value="{{ item.media_type }}">
        <div class="result">
          {% if item.poster_url %}<img src="{{ item.poster_url }}" alt="Poster">{% endif %}
          <div>
            <strong>{{ item.title }}</strong>
            <div class="muted">{{ item.year }} · {{ item.media_type }}</div>
            <p class="muted">{{ item.overview }}</p>

            {% if item.media_type == "tv" %}
            <label>Season</label>
            <input name="season_number" type="number" min="0" value="1" required>

            <label>Episode</label>
            <input name="episode_number" type="number" min="1" value="1" required>
            {% endif %}

            <label>Link Google Drive / File ID</label>
            <input name="drive_input" required placeholder="https://drive.google.com/file/d/FILE_ID/view">

            <label>Topic Telegram tujuan</label>
            <select name="message_thread_id">
              {% for topic in topic_options %}
              <option value="{{ topic.chat_id }}|{{ topic.thread_id }}">
  {{ topic.chat_title }} → {{ topic.name }}{% if topic.thread_id %} (ID {{ topic.thread_id }}){% endif %}
</option>
              {% endfor %}
            </select>

            <label>Mode subtitle</label>
            <select name="subtitle_mode">
              <option value="auto_drive">Cari otomatis dari folder Google Drive publik</option>
              <option value="auto_id">Otomatis pilih subtitle Indonesia di dalam video</option>
              <option value="upload">Upload subtitle .srt/.ass/.vtt</option>
              <option value="none">Tanpa subtitle</option>
            </select>

            <label>Link folder Google Drive publik (wajib untuk mode otomatis)</label>
            <input name="public_folder_input" placeholder="https://drive.google.com/drive/folders/FOLDER_ID">

            <label>File subtitle (hanya untuk mode Upload)</label>
            <input type="file" name="subtitle_file" accept=".srt,.ass,.ssa,.vtt">


            <label><input type="checkbox" name="watermark_enabled" style="width:auto"> Aktifkan watermark logo</label>
            <label>Mode watermark</label><select name="watermark_mode"><option value="smart_v2" selected>Smart Watermark Safe Area — tetap di sudut pilihan</option><option value="static">Statis — tetap di satu posisi</option></select>
            <label>Kecepatan gerak halus</label><select name="watermark_speed"><option value="slow">Lambat</option><option value="normal" selected>Normal</option><option value="fast">Cepat</option></select>
            <label>Sumber logo</label><select name="watermark_source"><option value="gdrive" selected>Google Drive — dapat diproses semua Railway</option><option value="upload">Upload dari perangkat — hanya worker lokal</option></select>
            <label>Link logo Google Drive / File ID</label><input name="watermark_drive_input" placeholder="https://drive.google.com/file/d/FILE_ID/view">
            <label>File logo lokal PNG/WEBP/JPG/GIF</label><input type="file" name="watermark_file" accept=".png,.webp,.jpg,.jpeg,.gif">
            <label>Posisi logo</label><select name="watermark_position"><option value="top_right">Kanan atas</option><option value="top_left">Kiri atas</option><option value="bottom_right">Kanan bawah</option><option value="bottom_left">Kiri bawah</option></select>
            <label>Ukuran logo</label><select name="watermark_size"><option value="5">Kecil (5%)</option><option value="8" selected>Sedang (8%)</option><option value="10">Besar (10%)</option><option value="15">Ekstra besar (15%)</option></select>
            <label>Transparansi logo</label><select name="watermark_opacity"><option value="20">20%</option><option value="35" selected>35%</option><option value="50">50%</option><option value="70">70%</option><option value="100">100%</option></select>


            <label>Profil kualitas video</label>
            <select name="encode_profile"><option value="telegram_1080" selected>1080p H.265 — Telegram di bawah 1,5 GB</option><option value="original">H.264 CRF — ukuran tidak dijamin</option></select>
            <label>Target ukuran (GB)</label><input name="target_size_gb" type="number" min="0.30" max="1.49" step="0.01" value="1.45">

            <label>Caption tambahan (opsional)</label>
            <textarea name="extra_caption" placeholder="Contoh: 1080p · Subtitle Indonesia"></textarea>

            <button type="submit">Tambahkan satu episode</button>
          </div>
        </div>
      </form>

      {% if item.media_type == "tv" %}
      <form method="post" action="{{ batch_enqueue_url }}" enctype="multipart/form-data">
        <input type="hidden" name="tmdb_id" value="{{ item.id }}">
        <input type="hidden" name="media_type" value="tv">
        <div class="result">
          {% if item.poster_url %}<img src="{{ item.poster_url }}" alt="Poster">{% endif %}
          <div>
            <strong>Batch episode — {{ item.title }}</strong>

            <label>Season</label>
            <input name="season_number" type="number" min="0" value="1" required>

            <label>Topic Telegram tujuan</label>
            <select name="topic_target">
              {% for topic in topic_options %}
              <option value="{{ topic.chat_id }}|{{ topic.thread_id }}">
                {{ topic.chat_title }} → {{ topic.name }}{% if topic.thread_id %} (ID {{ topic.thread_id }}){% endif %}
              </option>
              {% endfor %}
            </select>

            <label>Mode subtitle batch</label>
            <select name="batch_subtitle_mode">
              <option value="auto_drive">Cari otomatis di folder Google Drive</option>
              <option value="none">Tanpa subtitle</option>
              <option value="drive">Subtitle dari link Google Drive per episode</option>
              <option value="auto_id">Subtitle Indonesia internal di video</option>
            </select>

            <label>Link folder Google Drive publik (wajib untuk mode otomatis)</label>
            <input name="batch_public_folder_input" placeholder="https://drive.google.com/drive/folders/FOLDER_ID">

            <label>Daftar episode</label>
            <textarea name="episode_lines" required
              placeholder="1|LINK_VIDEO_EP1|LINK_SUBTITLE_EP1
2|LINK_VIDEO_EP2|LINK_SUBTITLE_EP2
3|LINK_VIDEO_EP3"></textarea>

            <div class="batch-help">
              Format setiap baris:
              <br><code>episode|link_video|link_subtitle_opsional</code>
              <br>Contoh tanpa subtitle:
              <br><code>4|https://drive.google.com/file/d/VIDEO_ID/view</code>
            </div>


            <label><input type="checkbox" name="batch_watermark_enabled" style="width:auto"> Aktifkan watermark logo</label>
            <label>Mode watermark</label><select name="batch_watermark_mode"><option value="smart_v2" selected>Smart Watermark Safe Area — tetap di sudut pilihan</option><option value="static">Statis — tetap di satu posisi</option></select>
            <label>Kecepatan gerak halus</label><select name="batch_watermark_speed"><option value="slow">Lambat</option><option value="normal" selected>Normal</option><option value="fast">Cepat</option></select>
            <label>Sumber logo</label><select name="batch_watermark_source"><option value="gdrive" selected>Google Drive — dapat diproses semua Railway</option><option value="upload">Upload dari perangkat — hanya worker lokal</option></select>
            <label>Link logo Google Drive / File ID</label><input name="batch_watermark_drive_input" placeholder="https://drive.google.com/file/d/FILE_ID/view">
            <label>File logo lokal PNG/WEBP/JPG/GIF</label><input type="file" name="batch_watermark_file" accept=".png,.webp,.jpg,.jpeg,.gif">
            <label>Posisi logo</label><select name="batch_watermark_position"><option value="top_right">Kanan atas</option><option value="top_left">Kiri atas</option><option value="bottom_right">Kanan bawah</option><option value="bottom_left">Kiri bawah</option></select>
            <label>Ukuran logo</label><select name="batch_watermark_size"><option value="5">Kecil (5%)</option><option value="8" selected>Sedang (8%)</option><option value="10">Besar (10%)</option><option value="15">Ekstra besar (15%)</option></select>
            <label>Transparansi logo</label><select name="batch_watermark_opacity"><option value="20">20%</option><option value="35" selected>35%</option><option value="50">50%</option><option value="70">70%</option><option value="100">100%</option></select>


            <label>Profil kualitas semua episode</label>
            <select name="batch_encode_profile"><option value="telegram_1080" selected>1080p H.265 — Telegram di bawah 1,5 GB</option><option value="original">H.264 CRF — ukuran tidak dijamin</option></select>
            <label>Target ukuran per episode (GB)</label><input name="batch_target_size_gb" type="number" min="0.30" max="1.49" step="0.01" value="1.45">

            <label>Caption tambahan untuk semua episode (opsional)</label>
            <textarea name="batch_extra_caption" placeholder="Contoh: 1080p · Subtitle Indonesia"></textarea>

            <button type="submit">Tambahkan semua episode ke antrean</button>
          </div>
        </div>
      </form>
      {% endif %}
      {% endfor %}
    {% elif query %}
      <p class="muted">Tidak ada hasil TMDB yang cocok.</p>
    {% endif %}
  </div>


  <div class="card page-section" id="serialSection">
    <h2>Menu Pengelolaan Serial</h2>
    <p class="muted">Pilih menu yang ingin dibuka. Hanya satu menu ditampilkan agar panel lebih ringkas di HP.</p>
    <div class="menu-nav">
      <button type="button" data-menu-target="manualMenu">✍️ Mode Manual / Hybrid</button>
      <button type="button" data-menu-target="savedMenu">➕ Tambah Episode</button>
      <button type="button" data-menu-target="restoreMenu">♻️ Pulihkan Serial</button>
    </div>

  <section class="menu-section" id="manualMenu">
    <div class="menu-content">
    <p class="muted">Gunakan jika judul tidak ditemukan di TMDB atau data TMDB ingin diganti manual.</p>
    <form method="post" action="{{ manual_enqueue_url }}" enctype="multipart/form-data">
      <label>Tipe konten</label>
      <select name="manual_media_type"><option value="movie">Film</option><option value="tv">Serial / Episode</option></select>
      <label>Judul</label><input name="manual_title" required>
      <label>Judul asli / AKA</label><input name="manual_original_title">
      <label>Tahun</label><input name="manual_year" type="number" min="1900" max="2100">
      <label>Poster URL</label><input name="manual_poster_url" placeholder="https://.../poster.jpg">
      <label>Sinopsis</label><textarea name="manual_overview" required></textarea>
      <label>Genre</label><input name="manual_genres" placeholder="Drama, Romance">
      <label>Negara</label><input name="manual_countries" placeholder="Indonesia">
      <label>Bahasa</label><input name="manual_languages" placeholder="Indonesian">
      <label>Sutradara</label><input name="manual_directors">
      <label>Penulis</label><input name="manual_writers">
      <label>Pemeran</label><textarea name="manual_cast"></textarea>
      <label>Rating</label><input name="manual_rating" type="number" step="0.1" min="0" max="10">
      <label>Jumlah pengguna rating</label><input name="manual_vote_count" type="number" min="0" value="0">
      <label>Kategori</label><input name="manual_certification" placeholder="PG-13 / TV-14">
      <label>Tanggal rilis</label><input name="manual_release_date" placeholder="12 Juli 2026">
      <label>Season (khusus serial)</label><input name="manual_season_number" type="number" min="0" value="1">
      <label>Episode (khusus serial)</label><input name="manual_episode_number" type="number" min="1" value="1">
      <label>Judul episode</label><input name="manual_episode_title" placeholder="Episode 1">
      <label>Link Google Drive / File ID video</label><input name="manual_drive_input" required>
      <label>Topic Telegram tujuan</label>
      <select name="manual_topic_target">{% for topic in topic_options %}<option value="{{ topic.chat_id }}|{{ topic.thread_id }}">{{ topic.chat_title }} → {{ topic.name }}{% if topic.thread_id %} (ID {{ topic.thread_id }}){% endif %}</option>{% endfor %}</select>
      <label>Mode subtitle</label>
      <select name="manual_subtitle_mode"><option value="auto_drive">Cari otomatis di folder Google Drive</option><option value="none">Tanpa subtitle</option><option value="auto_id">Subtitle Indonesia internal</option><option value="drive">Subtitle dari Google Drive</option></select>
      <label>Link folder Google Drive publik (wajib untuk mode otomatis)</label><input name="manual_public_folder_input" placeholder="https://drive.google.com/drive/folders/FOLDER_ID">
      <label>Link subtitle Google Drive (opsional)</label><input name="manual_subtitle_drive">

      <label><input type="checkbox" name="manual_watermark_enabled" style="width:auto"> Aktifkan watermark logo</label>
            <label>Mode watermark</label><select name="manual_watermark_mode"><option value="smart_v2" selected>Smart Watermark Safe Area — tetap di sudut pilihan</option><option value="static">Statis — tetap di satu posisi</option></select>
            <label>Kecepatan gerak halus</label><select name="manual_watermark_speed"><option value="slow">Lambat</option><option value="normal" selected>Normal</option><option value="fast">Cepat</option></select>
      <label>Sumber logo</label><select name="manual_watermark_source"><option value="gdrive" selected>Google Drive — dapat diproses semua Railway</option><option value="upload">Upload dari perangkat — hanya worker lokal</option></select>
      <label>Link logo Google Drive / File ID</label><input name="manual_watermark_drive_input" placeholder="https://drive.google.com/file/d/FILE_ID/view">
      <label>File logo lokal PNG/WEBP/JPG/GIF</label><input type="file" name="manual_watermark_file" accept=".png,.webp,.jpg,.jpeg,.gif">
      <label>Posisi logo</label><select name="manual_watermark_position"><option value="top_right">Kanan atas</option><option value="top_left">Kiri atas</option><option value="bottom_right">Kanan bawah</option><option value="bottom_left">Kiri bawah</option></select>
      <label>Ukuran logo</label><select name="manual_watermark_size"><option value="5">Kecil (5%)</option><option value="8" selected>Sedang (8%)</option><option value="10">Besar (10%)</option><option value="15">Ekstra besar (15%)</option></select>
      <label>Transparansi logo</label><select name="manual_watermark_opacity"><option value="20">20%</option><option value="35" selected>35%</option><option value="50">50%</option><option value="70">70%</option><option value="100">100%</option></select>


      <label>Profil kualitas video</label><select name="manual_encode_profile"><option value="telegram_1080" selected>1080p H.265 — Telegram di bawah 1,5 GB</option><option value="original">H.264 CRF — ukuran tidak dijamin</option></select>
      <label>Target ukuran (GB)</label><input name="manual_target_size_gb" type="number" min="0.30" max="1.49" step="0.01" value="1.45">

      <label>Caption tambahan (opsional)</label><textarea name="manual_extra_caption"></textarea>
      <button type="submit">Tambahkan Manual ke antrean</button>
    </form>
    </div>
  </section>

  <section class="menu-section" id="savedMenu">
    <div class="menu-content">
    <p class="muted">
      Cari judul serial, pilih hasilnya, lalu nomor episode berikutnya akan terisi otomatis.
    </p>

    {% if saved_series %}
      <label>Cari serial</label>
      <input id="seriesSearch" class="series-search"
             placeholder="Ketik sebagian judul serial...">

      <div id="seriesGrid" class="series-grid">
        {% for item in saved_series %}
        <div class="series-card clearfix"
             data-key="{{ item.key }}"
             data-title="{{ item.title|lower }}"
             data-next="{{ item.next_episode }}"
             data-season="{{ item.season }}"
             data-topic="{{ item.topic }}"
             onclick="selectSeries(this)">
          {% if item.poster_url %}
          <img src="{{ item.poster_url }}" alt="Poster">
          {% endif %}
          <div class="title">{{ item.title }}</div>
          <div class="meta">
            Season {{ item.season }}<br>
            {{ item.episode_count }} episode tersedia<br>
            Episode terakhir: E{{ "%02d"|format(item.last_episode) }}<br>
            Episode berikutnya: E{{ "%02d"|format(item.next_episode) }}<br>
            Topic: {{ item.topic }}
          </div>
        </div>
        {% endfor %}
      </div>

      <form id="savedEpisodeForm" method="post" enctype="multipart/form-data"
            action="{{ add_saved_episode_url }}"
            style="margin-top:18px">
        <input type="hidden" id="seriesKey" name="series_key" required>

        <label>Serial terpilih</label>
        <input id="selectedSeriesLabel" readonly
               placeholder="Pilih serial dari hasil pencarian">

        <label>Nomor episode baru</label>
        <input id="savedEpisodeNumber"
               name="saved_episode_number"
               type="number" min="1" required>

        <label>Judul episode</label>
        <input name="saved_episode_title"
               placeholder="Episode baru">

        <label>Link Google Drive video</label>
        <input name="saved_drive_input" required
               placeholder="https://drive.google.com/file/d/VIDEO_ID/view">

        <label>Mode subtitle</label>
        <select name="saved_subtitle_mode">
          <option value="auto_drive">Cari otomatis di folder Google Drive</option>
          <option value="none">Tanpa subtitle</option>
          <option value="auto_id">Subtitle Indonesia internal</option>
          <option value="drive">Subtitle dari Google Drive</option>
        </select>

        <label>Link folder Google Drive publik (wajib untuk mode otomatis)</label>
        <input name="saved_public_folder_input" placeholder="https://drive.google.com/drive/folders/FOLDER_ID">

        <label>Link subtitle Google Drive</label>
        <input name="saved_subtitle_drive"
               placeholder="https://drive.google.com/file/d/SUBTITLE_ID/view">


        <label><input type="checkbox" name="saved_watermark_enabled" style="width:auto"> Aktifkan watermark logo</label>
            <label>Mode watermark</label><select name="saved_watermark_mode"><option value="smart_v2" selected>Smart Watermark Safe Area — tetap di sudut pilihan</option><option value="static">Statis — tetap di satu posisi</option></select>
            <label>Kecepatan gerak halus</label><select name="saved_watermark_speed"><option value="slow">Lambat</option><option value="normal" selected>Normal</option><option value="fast">Cepat</option></select>
        <label>Sumber logo</label><select name="saved_watermark_source"><option value="gdrive" selected>Google Drive — dapat diproses semua Railway</option><option value="upload">Upload dari perangkat — hanya worker lokal</option></select>
        <label>Link logo Google Drive / File ID</label><input name="saved_watermark_drive_input" placeholder="https://drive.google.com/file/d/FILE_ID/view">
        <label>File logo lokal PNG/WEBP/JPG/GIF</label><input type="file" name="saved_watermark_file" accept=".png,.webp,.jpg,.jpeg,.gif">
        <label>Posisi logo</label><select name="saved_watermark_position"><option value="top_right">Kanan atas</option><option value="top_left">Kiri atas</option><option value="bottom_right">Kanan bawah</option><option value="bottom_left">Kiri bawah</option></select>
        <label>Ukuran logo</label><select name="saved_watermark_size"><option value="5">Kecil (5%)</option><option value="8" selected>Sedang (8%)</option><option value="10">Besar (10%)</option><option value="15">Ekstra besar (15%)</option></select>
        <label>Transparansi logo</label><select name="saved_watermark_opacity"><option value="20">20%</option><option value="35" selected>35%</option><option value="50">50%</option><option value="70">70%</option><option value="100">100%</option></select>


        <label>Profil kualitas video</label><select name="saved_encode_profile"><option value="telegram_1080" selected>1080p H.265 — Telegram di bawah 1,5 GB</option><option value="original">H.264 CRF — ukuran tidak dijamin</option></select>
        <label>Target ukuran (GB)</label><input name="saved_target_size_gb" type="number" min="0.30" max="1.49" step="0.01" value="1.45">

        <label>Caption tambahan</label>
        <textarea name="saved_extra_caption"
                  placeholder="Contoh: 1080p · Subtitle Indonesia"></textarea>

        <button type="submit">Tambahkan Episode Baru</button>
      </form>
    {% else %}
      <p class="muted">
        Belum ada serial tersimpan. Buat Episode 1 dan tunggu sampai SUCCESS.
      </p>
    {% endif %}
    </div>
  </section>

  <section class="menu-section" id="restoreMenu">
    <div class="menu-content">
    <div class="scan-restore-box">
      <h3>🔎 Pulihkan dari Hasil Scan Bot API</h3>
      <p class="muted">Tekan scan setelah mengirim, meneruskan, atau mengedit pesan serial/episode di Telegram. Bot API hanya dapat membaca update terbaru yang masih tersedia.</p>
      <form method="post" action="{{ scan_series_url }}">
        <button type="submit">Scan Serial dari Telegram</button>
      </form>
      {% if scan_series_results %}
        <div class="scan-series-grid">
        {% for item in scan_series_results %}
          <div class="scan-series-card">
            <strong>{{ item.title }}</strong>
            <div class="muted">Season {{ item.season }} · {{ item.episode_count }} episode</div>
            <div class="muted">{{ item.chat_title }} → {{ item.topic_name }}{% if item.thread_id %} ({{ item.thread_id }}){% endif %}</div>
            <div class="episode-chips">{% for ep in item.episode_numbers %}<span>E{{ "%02d"|format(ep) }}</span>{% endfor %}</div>
            <form method="post" action="{{ restore_scanned_series_url }}">
              <input type="hidden" name="scan_id" value="{{ item.scan_id }}">
              <button type="submit">Pulihkan Serial Ini</button>
            </form>
          </div>
        {% endfor %}
        </div>
      {% else %}
        <p class="muted">Belum ada hasil scan serial.</p>
      {% endif %}
    </div>
    <hr class="soft-line">
    <p class="muted">Atau pulihkan secara manual dengan memasukkan data posting utama dan episode.</p>
    <form method="post" action="{{ restore_series_url }}">
      <label>Judul serial</label><input name="restore_title" required>
      <label>Judul asli / AKA</label><input name="restore_original_title">
      <label>Tahun</label><input name="restore_year" placeholder="2026">
      <label>TMDB ID (opsional)</label><input name="restore_tmdb_id" type="number" min="0">
      <label>Season</label><input name="restore_season" type="number" min="0" value="1" required>
      <label>Chat ID Telegram</label><input name="restore_chat_id" value="{{ default_chat_id }}" required>
      <label>Topic / Thread ID</label><input name="restore_thread_id" type="number" min="0" value="0">
      <label>Nama topic</label><input name="restore_topic_name" placeholder="General">
      <label>Poster URL (opsional)</label><input name="restore_poster_url" placeholder="https://...">
      <label>Message ID posting utama (opsional)</label><input name="restore_index_message_id" type="number" min="0" value="0">
      <label>Tipe posting utama</label><select name="restore_index_type"><option value="photo">Photo/Poster</option><option value="text">Text</option></select>
      <label>Daftar episode</label>
      <textarea name="restore_episode_lines" required placeholder="1|12345|https://t.me/c/CHAT/12345|Judul Episode 1
2|12346|https://t.me/c/CHAT/12346|Judul Episode 2"></textarea>
      <div class="batch-help">Format: <code>episode|message_id|url_opsional|judul_opsional</code>. URL boleh dikosongkan; aplikasi akan membuat URL dari Chat ID.</div>
      <label>Sinopsis (opsional)</label><textarea name="restore_overview"></textarea>
      <button type="submit">Pulihkan Serial</button>
    </form>
    </div>
  </section>
  </div>


  <div class="card page-section" id="statusSection">
    <div class="row"><div><h2 style="margin:0">📊 Status Proses Global</h2><div class="muted">Memantau pekerjaan dari seluruh Railway dan bot Telegram.</div></div><span id="statusUpdated" class="status-updated">Belum diperbarui</span></div>
    <div class="status-toolbar"><select id="statusFilter"><option value="active">Sedang diproses</option><option value="all">Semua pekerjaan</option><option value="failed">Gagal</option><option value="success">Selesai</option></select><button type="button" id="statusRefreshButton">Refresh</button></div>
    <div id="statusSummary" class="status-summary"></div>
    <h3 style="margin-bottom:8px">🚆 Status Railway Worker</h3>
    <div id="workerStatusGrid" class="worker-grid"><p class="muted">Memuat status worker...</p></div>
    <div id="globalJobs"><p class="muted">Memuat status global...</p></div>
  </div>

  <div class="card page-section" id="dataSection">
    <h2>🗄️ Database Global Supabase</h2>
    <p class="muted">Serial, episode, topic, dan hasil scan dibaca dari database global Supabase. File Railway Volume di bawah hanya cache dan backup.</p>
    {% if data_message %}<div class="notice">{{ data_message }}</div>{% endif %}
    <div class="data-grid">
      <div class="data-stat"><span class="muted">Serial</span><b>{{ data_stats.series_count }}</b></div>
      <div class="data-stat"><span class="muted">Episode</span><b>{{ data_stats.episode_count }}</b></div>
      <div class="data-stat"><span class="muted">Topic</span><b>{{ data_stats.topic_count }}</b></div>
      <div class="data-stat"><span class="muted">Hasil scan</span><b>{{ data_stats.scan_count }}</b></div>
      <div class="data-stat"><span class="muted">Mode database</span><b style="font-size:18px">{{ data_stats.database_mode }}</b></div>
      <div class="data-stat"><span class="muted">Fingerprint</span><b style="font-size:14px">{{ data_stats.fingerprint }}</b></div>
      <div class="data-stat"><span class="muted">Backup lokal</span><b>{{ data_stats.backup_count }}</b></div>
      <div class="data-stat"><span class="muted">Ruang kosong volume</span><b style="font-size:18px">{{ data_stats.free_space }}</b></div>
    </div>
    <div class="action-grid">
      <form method="get" action="{{ export_data_url }}"><input type="hidden" name="key" value="{{ key }}"><button type="submit">📦 Export semua data ZIP</button></form>
      <form method="post" action="{{ create_backup_url }}"><button type="submit">💾 Buat backup sekarang</button></form>
      <form method="post" action="{{ clear_scan_url }}" onsubmit="return confirm('Hapus semua hasil scan global?')"><button class="danger" type="submit">🧹 Bersihkan hasil scan</button></form>
      <form method="post" action="{{ cleanup_global_url }}" onsubmit="return confirm('Gabungkan serial duplikat dan jadikan Supabase sebagai satu-satunya sumber utama?')"><button type="submit">🧬 Bersihkan serial duplikat</button></form>
    </div>
    <div class="batch-help"><strong>Cache lokal:</strong> {{ data_stats.local_series_count }} serial · {{ data_stats.local_topic_count }} topic. Cache tidak digunakan sebagai sumber utama saat GLOBAL_SYNC_BOOTSTRAP_LOCAL=0 dan GLOBAL_DATABASE_PUBLISH_LOCAL=0.</div>
    <h3>Cache & Backup Railway Volume</h3>
    {% for f in data_files %}
    <div class="job"><div class="row"><div><strong>{{ f.label }}</strong><div class="muted">{{ f.path }} · {{ f.size }} · {{ f.modified }}</div></div><div style="display:flex;gap:8px"><a href="{{ f.download_url }}" style="color:#67e8f9">Download</a></div></div>
      <details style="margin-top:10px"><summary style="cursor:pointer">Lihat isi JSON</summary><pre class="json-box">{{ f.preview }}</pre></details>
      <form method="post" action="{{ import_json_url }}" enctype="multipart/form-data"><input type="hidden" name="kind" value="{{ f.kind }}"><label>Ganti file ini dari JSON</label><input type="file" name="json_file" accept="application/json,.json" required><button type="submit" onclick="return confirm('Data lama akan dibackup lalu diganti. Lanjutkan?')">Upload & Pulihkan</button></form>
    </div>
    {% endfor %}
    <h3>Import seluruh data dari ZIP</h3>
    <form method="post" action="{{ import_zip_url }}" enctype="multipart/form-data"><input type="file" name="zip_file" accept="application/zip,.zip" required><button type="submit" onclick="return confirm('File yang cocok akan mengganti data aktif. Lanjutkan?')">📤 Import ZIP</button></form>
    <h3>Daftar backup</h3>
    {% if backup_files %}{% for b in backup_files %}<div class="backup-row"><div><strong>{{ b.name }}</strong><div class="muted">{{ b.size }} · {{ b.modified }}</div></div><form method="post" action="{{ restore_backup_url }}"><input type="hidden" name="name" value="{{ b.name }}"><button type="submit" onclick="return confirm('Pulihkan backup ini?')">Pulihkan</button></form><form method="post" action="{{ delete_backup_url }}"><input type="hidden" name="name" value="{{ b.name }}"><button class="danger" type="submit" onclick="return confirm('Hapus backup ini?')">Hapus</button></form></div>{% endfor %}{% else %}<p class="muted">Belum ada backup.</p>{% endif %}
  </div>

  <div class="card page-section" id="queueSection">
    <div class="row"><h2 style="margin:0">Antrean & status</h2><span class="muted">Maksimal {{ max_queue }}</span></div>
    <div id="jobs"><p class="muted">Memuat status...</p></div>
  </div>
</div>

<script>
const pageSections = [...document.querySelectorAll(".page-section")];
const pageButtons = [...document.querySelectorAll("[data-page-target]")];
function openMainPage(id, updateHash=true){
  const target = document.getElementById(id) ? id : "searchSection";
  pageSections.forEach(section => section.classList.toggle("active", section.id === target));
  pageButtons.forEach(button => button.classList.toggle("active", button.dataset.pageTarget === target));
  localStorage.setItem("gdriveActivePage", target);
  if(updateHash) history.replaceState(null, "", `#${target}`);
  window.scrollTo({top:0, behavior:"instant"});
  if(target === "queueSection") refreshJobs();
  if(target === "statusSection") refreshGlobalStatus();
}
pageButtons.forEach(button => button.addEventListener("click", () => openMainPage(button.dataset.pageTarget)));
const hashPage = location.hash.replace("#", "");
const savedMainPage = localStorage.getItem("gdriveActivePage");
openMainPage(document.getElementById(hashPage) ? hashPage : (document.getElementById(savedMainPage) ? savedMainPage : "searchSection"), false);
window.addEventListener("hashchange", () => {
  const target = location.hash.replace("#", "");
  if(document.getElementById(target)) openMainPage(target, false);
});

const menuSections = [...document.querySelectorAll(".menu-section")];
const menuButtons = [...document.querySelectorAll("[data-menu-target]")];
function openPanelMenu(id, scroll=false){
  menuSections.forEach(section => section.classList.toggle("active", section.id === id));
  menuButtons.forEach(button => button.classList.toggle("active", button.dataset.menuTarget === id));
  localStorage.setItem("gdriveActiveMenu", id);
  const selected = document.getElementById(id);
  if(scroll && selected) selected.scrollIntoView({behavior:"smooth", block:"start"});
}
menuButtons.forEach(button => button.addEventListener("click", () => openPanelMenu(button.dataset.menuTarget, false)));
const savedPanelMenu = localStorage.getItem("gdriveActiveMenu");
openPanelMenu(savedPanelMenu && document.getElementById(savedPanelMenu) ? savedPanelMenu : "manualMenu", false);

const statusUrl={{ status_url|tojson }};
function esc(v){return String(v??"").replaceAll("&","&amp;").replaceAll("<","&lt;").replaceAll(">","&gt;").replaceAll('"',"&quot;")}
async function refreshJobs(){
 const box=document.getElementById("jobs");
 try{
  const r=await fetch(statusUrl,{cache:"no-store"}),d=await r.json();
  if(!d.success){box.innerHTML=`<p class="error">${esc(d.error)}</p>`;return}
  if(!d.jobs.length){box.innerHTML=`<p class="muted">Belum ada film dalam antrean.</p>`;return}
  box.innerHTML=d.jobs.map(j=>{
   const pct=Math.max(0,Math.min(100,Number(j.overall_progress||0)));
   const stagePct=Math.max(0,Math.min(100,Number(j.stage_progress||0)));
   const eta=j.eta_seconds>0?` · ETA ${esc(j.eta_human)}`:"";
   const detail=j.progress_detail?`<div class="muted">${esc(j.progress_detail)}${eta}</div>`:"";
   const p=`<div class="progress"><div style="width:${pct}%"></div></div>
            <div class="muted">Total ${pct.toFixed(1)}% · Tahap ${stagePct.toFixed(1)}%</div>${detail}`;
   return `<div class="job"><div class="row"><strong>${esc(j.title)}</strong><span class="state ${esc(j.state)}">${esc(j.state)}</span></div>
   <p class="muted">${esc(j.message)}</p>${p}
   ${j.processing_worker?`<div class="muted">Worker proses: <strong>${esc(j.processing_worker)}</strong>${j.processing_on_other_worker?` · diproses di Railway lain`:""}</div>`:""}
   <div class="muted">File: ${esc(j.file_size_human||"-")}</div>
   ${j.season_number?`<div class="muted">Season ${esc(j.season_number)} · Episode ${esc(j.episode_number)}</div>`:""}
   <div class="muted">Tujuan: ${esc(j.target_chat_id||"-")} → ${esc(j.topic_name||"General")} ${j.message_thread_id?`(ID ${esc(j.message_thread_id)})`:""}</div>
   <div class="muted">Subtitle: ${esc(j.subtitle_info||"-")}</div>
   <div class="muted">Logo: ${esc(j.watermark_info||"Tanpa logo")}</div>
   <div class="muted">Encode: ${esc(j.encode_info||j.encode_profile||"-")}</div>
   ${j.message_id?`<div class="muted">Episode Message ID: ${esc(j.message_id)}</div>`:""}
   ${j.index_message_id?`<div class="muted">Posting utama serial: ${esc(j.index_message_id)}</div>`:""}
   ${j.error?`<p class="error">${esc(j.error)}</p>`:""}</div>`;
  }).join("");
 }catch(e){box.innerHTML=`<p class="error">${esc(e)}</p>`}
}

const schedulerDashboardUrl={{ scheduler_dashboard_url|tojson }};
const activeGlobalStates=new Set(["QUEUED","ASSIGNED","CLAIMED","DOWNLOADING","PROCESSING","PREPARING","READY","UPLOADING"]);
function fmtDuration(seconds){seconds=Math.max(0,Number(seconds||0));const h=Math.floor(seconds/3600),m=Math.floor((seconds%3600)/60),sec=Math.floor(seconds%60);return h?`${h}j ${m}m`:m?`${m}m ${sec}d`:`${sec}d`}
async function refreshGlobalStatus(){
 const box=document.getElementById("globalJobs"),summary=document.getElementById("statusSummary"),updated=document.getElementById("statusUpdated"),workerGrid=document.getElementById("workerStatusGrid");
 if(!box)return;
 try{
  const r=await fetch(schedulerDashboardUrl,{cache:"no-store"}),d=await r.json();
  if(!d.success){box.innerHTML=`<p class="error">${esc(d.error||"Gagal mengambil status")}</p>`;return}
  const filter=document.getElementById("statusFilter")?.value||"active";
  let rows=Array.isArray(d.jobs)?d.jobs:[];
  if(filter==="active")rows=rows.filter(j=>activeGlobalStates.has(String(j.state)));
  if(filter==="failed")rows=rows.filter(j=>String(j.state)==="ERROR");
  if(filter==="success")rows=rows.filter(j=>String(j.state)==="SUCCESS");
  summary.innerHTML=`<div class="status-stat"><span class="muted">Aktif</span><b>${esc(d.active_count)}</b></div><div class="status-stat"><span class="muted">Menunggu</span><b>${esc(d.queued_count)}</b></div><div class="status-stat"><span class="muted">Selesai</span><b>${esc(d.success_count)}</b></div><div class="status-stat"><span class="muted">Gagal</span><b>${esc(d.error_count)}</b></div><div class="status-stat"><span class="muted">Worker online</span><b>${esc(d.online_worker_count||0)}</b></div><div class="status-stat"><span class="muted">Worker offline</span><b>${esc(d.offline_worker_count||0)}</b></div>`;
  const workerRows=Array.isArray(d.worker_statuses)?d.worker_statuses:[];
  if(workerGrid){
    workerGrid.innerHTML=workerRows.length?workerRows.map(w=>{
      const state=w.online?"online":"offline";
      const age=Number(w.age_seconds||0)<60?`${Number(w.age_seconds||0)} detik lalu`:fmtDuration(Number(w.age_seconds||0))+" lalu";
      return `<div class="worker-card ${state}"><div class="worker-head"><strong><span class="worker-dot"></span>${esc(w.worker_id)}</strong><span class="worker-state">${esc(w.status)}</span></div><div class="muted" style="margin-top:8px">Versi: <strong>${esc(w.version||"-")}</strong></div><div class="muted">Terakhir aktif: <strong>${esc(age)}</strong></div><div class="muted">Job aktif: <strong>${esc(w.active_job_count||0)}</strong>${w.active_job_title?` · ${esc(w.active_job_title)}`:""}</div><div class="muted">CPU terdeteksi: <strong>${esc(w.cpu_count||"-")}</strong></div></div>`;
    }).join(""):`<div class="status-empty"><strong>Belum ada heartbeat worker.</strong><div class="muted">Pastikan Supabase dan CLUSTER_WORKER_ID sudah benar.</div></div>`;
  }
  updated.textContent=`Diperbarui ${new Date().toLocaleTimeString()}`;
  if(!rows.length){box.innerHTML=`<div class="status-empty"><strong>Tidak ada pekerjaan pada filter ini.</strong><div class="muted">Status akan muncul otomatis ketika ada proses baru.</div></div>`;return}
  box.innerHTML=rows.map(j=>{
   const pct=Math.max(0,Math.min(100,Number(j.overall_progress||j.progress||0)));
   const started=Number(j.started_at||j.created_at||0);const finished=Number(j.finished_at||0);const duration=started?fmtDuration((finished||Date.now()/1000)-started):"-";
   return `<div class="status-job"><div class="row"><div><strong>${esc(j.title||j.job_id||"Pekerjaan")}</strong>${j.episode_number?`<div class="muted">Season ${esc(j.season_number||1)} · Episode ${esc(j.episode_number)}</div>`:""}</div><span class="status-badge ${esc(j.state)}">${esc(j.state||"UNKNOWN")}</span></div><div class="progress"><div style="width:${pct}%"></div></div><div class="muted">Progres ${pct.toFixed(1)}%${j.eta_human&&j.eta_human!=="-"?` · ETA ${esc(j.eta_human)}`:""}</div><div class="status-meta"><div class="muted">Worker: <strong>${esc(j.assigned_worker||j.worker_id||"Menunggu worker")}</strong></div><div class="muted">Bot: <strong>${esc(j.bot_username||j.bot_name||"-")}</strong></div><div class="muted">Durasi: <strong>${esc(duration)}</strong></div><div class="muted">Pengirim: <strong>${esc(j.submitted_by||"-")}</strong></div><div class="muted">Topic: <strong>${esc(j.topic_name||"General")}</strong></div><div class="muted">Detail: <strong>${esc(j.progress_detail||j.message||"-")}</strong></div></div>${j.error?`<p class="error">${esc(j.error)}</p>`:""}</div>`;
  }).join("");
 }catch(e){box.innerHTML=`<p class="error">${esc(e)}</p>`}
}
document.getElementById("statusRefreshButton")?.addEventListener("click",refreshGlobalStatus);
document.getElementById("statusFilter")?.addEventListener("change",refreshGlobalStatus);

const seriesSearch = document.getElementById("seriesSearch");
if (seriesSearch) {
  seriesSearch.addEventListener("input", () => {
    const query = seriesSearch.value.trim().toLowerCase();
    document.querySelectorAll(".series-card").forEach(card => {
      const title = card.dataset.title || "";
      card.classList.toggle("hidden", !title.includes(query));
    });
  });
}

function selectSeries(card) {
  document.querySelectorAll(".series-card").forEach(item => {
    item.classList.remove("selected");
  });
  card.classList.add("selected");

  const key = card.dataset.key;
  const nextEpisode = card.dataset.next || "1";
  const season = card.dataset.season || "1";
  const topic = card.dataset.topic || "General";
  const titleNode = card.querySelector(".title");
  const title = titleNode ? titleNode.textContent.trim() : "Serial";

  document.getElementById("seriesKey").value = key;
  document.getElementById("savedEpisodeNumber").value = nextEpisode;
  document.getElementById("selectedSeriesLabel").value =
    `${title} · Season ${season} · ${topic}`;

  document.getElementById("savedEpisodeForm").scrollIntoView({
    behavior: "smooth",
    block: "start"
  });
}

refreshJobs();refreshGlobalStatus();setInterval(refreshJobs,3000);setInterval(()=>{if(document.getElementById("statusSection")?.classList.contains("active"))refreshGlobalStatus()},3000);
</script>
</body>
</html>
"""

def topic_name_from_id(thread_id: int, chat_id: str | int | None = None) -> str:
    for topic in get_topic_options():
        if int(topic.get("thread_id") or 0) != thread_id:
            continue
        if chat_id is None or str(topic.get("chat_id")) == str(chat_id):
            return str(topic.get("name") or f"Topic {thread_id}")
    return "General" if thread_id == 0 else f"Topic {thread_id}"

def telegram_method(method: str, *, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """Call Telegram using configured Local Bot API, then official API if unreachable."""
    errors: list[str] = []
    for base in telegram_api_bases():
        try:
            response = requests.get(
                telegram_api_url(ACTIVE_BOT_TOKEN, method, base=base),
                params=params or {},
                timeout=60,
            )
            try:
                data = response.json()
            except ValueError as exc:
                raise RuntimeError(
                    f"{base}: respons bukan JSON (HTTP {response.status_code})"
                ) from exc
            if not response.ok or not data.get("ok"):
                raise RuntimeError(
                    f"{base}: {data.get('description') or f'Telegram HTTP {response.status_code}'}"
                )
            data["_api_base"] = base
            return data
        except Exception as exc:
            errors.append(str(exc))
    raise RuntimeError("Semua Telegram Bot API gagal: " + " | ".join(errors[-3:]))

def test_telegram_connections() -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    for base in telegram_api_bases():
        started = time.time()
        try:
            response = requests.get(
                telegram_api_url(ACTIVE_BOT_TOKEN, "getMe", base=base),
                timeout=20,
            )
            payload = response.json()
            ok = bool(response.ok and payload.get("ok"))
            results.append({
                "base": base,
                "ok": ok,
                "latency_ms": int((time.time() - started) * 1000),
                "bot": (payload.get("result") or {}).get("username", ""),
                "error": "" if ok else str(payload.get("description") or response.text[:300]),
            })
        except Exception as exc:
            results.append({
                "base": base,
                "ok": False,
                "latency_ms": int((time.time() - started) * 1000),
                "bot": "",
                "error": str(exc),
            })
    return {"success": any(item["ok"] for item in results), "results": results}

def scan_recent_topics() -> dict[str, Any]:
    webhook = telegram_method("getWebhookInfo").get("result") or {}
    if webhook.get("url"):
        raise RuntimeError(
            "Webhook masih aktif. Hapus webhook dahulu sebelum memakai Scan Topics."
        )

    updates = telegram_method(
        "getUpdates",
        params={
            "limit": 100,
            "timeout": 0,
            "allowed_updates": json.dumps([
                "message",
                "edited_message",
                "channel_post",
                "edited_channel_post",
            ]),
        },
    ).get("result") or []

    existing = {
        (str(item.get("chat_id")), int(item.get("thread_id") or 0)): item
        for item in load_discovered_topics()
    }
    chats: dict[str, dict[str, Any]] = {}

    for update in updates:
        message = (
            update.get("message")
            or update.get("edited_message")
            or update.get("channel_post")
            or update.get("edited_channel_post")
        )
        if not message:
            continue

        chat = message.get("chat") or {}
        chat_id = chat.get("id")
        if chat_id is None:
            continue

        chat_key = str(chat_id)
        chat_title = (
            chat.get("title")
            or chat.get("username")
            or chat.get("first_name")
            or chat_key
        )
        chats[chat_key] = {
            "chat_id": chat_id,
            "chat_title": chat_title,
            "chat_type": chat.get("type"),
        }

        thread_id = int(message.get("message_thread_id") or 0)
        created = message.get("forum_topic_created") or {}
        edited = message.get("forum_topic_edited") or {}
        topic_name = (
            created.get("name")
            or edited.get("name")
            or ("General" if thread_id == 0 else f"Topic {thread_id}")
        )

        key = (chat_key, thread_id)
        old = existing.get(key) or {}
        if (
            old.get("name")
            and not created.get("name")
            and not edited.get("name")
            and topic_name.startswith("Topic ")
        ):
            topic_name = old["name"]

        existing[key] = {
            "chat_id": chat_id,
            "chat_title": chat_title,
            "chat_type": chat.get("type"),
            "thread_id": thread_id,
            "name": topic_name,
            "last_message_id": message.get("message_id"),
            "last_seen_at": int(message.get("date") or now_ts()),
        }

    topics = list(existing.values())
    save_discovered_topics(topics)

    return {
        "updates_count": len(updates),
        "topics_count": len(topics),
        "chats": list(chats.values()),
        "topics": topics,
    }



def load_scan_results() -> list[dict[str, Any]]:
    local: list[dict[str, Any]] = []
    try:
        if SCAN_STORE_PATH.exists():
            data = json.loads(SCAN_STORE_PATH.read_text(encoding="utf-8"))
            if isinstance(data, list):
                local = data
    except Exception:
        pass
    if not (GLOBAL_SYNC_ENABLED and cluster_store.enabled):
        return local
    remote = cluster_store.get_document("scan_results", [])
    merged = _merge_list_records(remote, local if GLOBAL_SYNC_BOOTSTRAP_LOCAL else [])
    if _json_fingerprint(merged) != _json_fingerprint(remote):
        merged = cluster_store.save_document("scan_results", merged, merge=False)
    try:
        atomic_json_write(SCAN_STORE_PATH, merged)
    except Exception:
        pass
    return merged


def save_scan_results(items: list[dict[str, Any]]) -> None:
    remote = cluster_store.get_document("scan_results", []) if GLOBAL_SYNC_ENABLED else []
    items = _merge_list_records(remote, items)
    if GLOBAL_SYNC_ENABLED:
        items = cluster_store.save_document("scan_results", items, merge=False)
    atomic_json_write(SCAN_STORE_PATH, items)

def _telegram_message_from_update(update: dict[str, Any]) -> dict[str, Any] | None:
    return (
        update.get("message")
        or update.get("edited_message")
        or update.get("channel_post")
        or update.get("edited_channel_post")
    )


def _message_text(message: dict[str, Any]) -> str:
    return str(message.get("caption") or message.get("text") or "").strip()


def _clean_scanned_series_title(value: str) -> str:
    value = re.sub(r"^[^A-Za-z0-9À-ÿ]+", "", value.strip())
    value = re.sub(r"^(?:Serial|Judul)\s*:\s*", "", value, flags=re.I)
    value = re.sub(r"\s+", " ", value).strip(" -–—:|")
    return value or "Serial Telegram"


def _message_id_from_url(url: str) -> int:
    match = re.search(r"/(\d+)(?:\?.*)?$", str(url or ""))
    return int(match.group(1)) if match else 0


def scan_telegram_series() -> dict[str, Any]:
    webhook = telegram_method("getWebhookInfo").get("result") or {}
    if webhook.get("url"):
        raise RuntimeError("Webhook masih aktif. Hapus webhook dahulu sebelum memakai Scan Bot API.")

    updates = telegram_method(
        "getUpdates",
        params={
            "limit": 100,
            "timeout": 0,
            "allowed_updates": json.dumps([
                "message", "edited_message", "channel_post", "edited_channel_post"
            ]),
        },
    ).get("result") or []

    groups: dict[str, dict[str, Any]] = {}

    def ensure_group(title: str, season: int, chat_id: str, thread_id: int, chat_title: str) -> dict[str, Any]:
        normalized = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-") or "serial"
        key = f"{normalized}:{season}:{chat_id}:{thread_id}"
        item = groups.get(key)
        if item is None:
            item = {
                "scan_id": hashlib.sha256(key.encode()).hexdigest()[:16],
                "title": title,
                "original_title": title,
                "year": "-",
                "season": season,
                "chat_id": chat_id,
                "chat_title": chat_title,
                "thread_id": thread_id,
                "topic_name": topic_name_from_id(thread_id, chat_id),
                "poster_url": None,
                "index_message_id": 0,
                "index_type": "photo",
                "overview": "Dipulihkan otomatis dari hasil Scan Bot API Telegram.",
                "episodes": {},
                "last_seen_at": 0,
            }
            groups[key] = item
        return item

    for update in updates:
        message = _telegram_message_from_update(update)
        if not message:
            continue
        chat = message.get("chat") or {}
        chat_id_raw = chat.get("id")
        if chat_id_raw is None:
            continue
        chat_id = str(chat_id_raw)
        chat_title = str(chat.get("title") or chat.get("username") or chat.get("first_name") or chat_id)
        thread_id = int(message.get("message_thread_id") or 0)
        message_id = int(message.get("message_id") or 0)
        text = _message_text(message)
        timestamp = int(message.get("date") or now_ts())

        # Episode posted as video/document with SxxExx in caption/title.
        episode_match = re.search(r"(?i)\bS(\d{1,2})\s*E(\d{1,3})\b", text)
        if episode_match:
            season = int(episode_match.group(1))
            episode_number = int(episode_match.group(2))
            before = text[:episode_match.start()].splitlines()[0] if text[:episode_match.start()] else ""
            title = _clean_scanned_series_title(before)
            after = text[episode_match.end():].splitlines()[0].strip(" -–—:|")
            episode_title = after or f"Episode {episode_number}"
            item = ensure_group(title, season, chat_id, thread_id, chat_title)
            try:
                url = telegram_message_url(chat_id, message_id)
            except Exception:
                url = ""
            item["episodes"][str(episode_number)] = {
                "message_id": message_id,
                "url": url,
                "title": episode_title,
                "episode_code": f"S{season:02d}E{episode_number:02d}",
                "updated_at": timestamp,
                "restored": True,
                "scan_source": "message",
            }
            item["last_seen_at"] = max(item["last_seen_at"], timestamp)

        # Main serial post: infer title/season and read its inline episode buttons.
        keyboard = (message.get("reply_markup") or {}).get("inline_keyboard") or []
        button_episodes: list[tuple[int, str, int]] = []
        for row in keyboard:
            for button in row or []:
                label = str(button.get("text") or "")
                url = str(button.get("url") or "")
                ep_match = re.search(r"(?i)(?:E(?:P)?\.?\s*)(\d{1,3})", label)
                if ep_match and url:
                    button_episodes.append((int(ep_match.group(1)), url, _message_id_from_url(url)))
        if button_episodes or "tap episode" in text.lower() or "episode tersedia" in text.lower():
            season_match = re.search(r"(?i)\bSeason\s+(\d{1,2})\b", text)
            season = int(season_match.group(1)) if season_match else 1
            title_match = re.search(r"(?m)^🎬\s*(.+?)(?:\s*\((\d{4})\))?\s*$", text)
            if not title_match:
                title_match = re.search(r"(?m)^(?:📺\s*Serial\s*:\s*)?(.+)$", text)
            title = _clean_scanned_series_title(title_match.group(1) if title_match else "Serial Telegram")
            item = ensure_group(title, season, chat_id, thread_id, chat_title)
            item["index_message_id"] = message_id
            item["index_type"] = "photo" if message.get("photo") else "text"
            if message.get("photo"):
                item["poster_file_id"] = str((message.get("photo") or [])[-1].get("file_id") or "")
            year_match = re.search(r"\b(19|20)\d{2}\b", text)
            if year_match:
                item["year"] = year_match.group(0)
            overview_match = re.search(r"(?is)💬\s*Sinopsis\s*:\s*(.+?)(?:\n\n👇|$)", text)
            if overview_match:
                item["overview"] = overview_match.group(1).strip()
            for episode_number, url, episode_message_id in button_episodes:
                item["episodes"].setdefault(str(episode_number), {
                    "message_id": episode_message_id,
                    "url": url,
                    "title": f"Episode {episode_number}",
                    "episode_code": f"S{season:02d}E{episode_number:02d}",
                    "updated_at": timestamp,
                    "restored": True,
                    "scan_source": "index_button",
                })
            item["last_seen_at"] = max(item["last_seen_at"], timestamp)

    results = []
    for item in groups.values():
        episodes = item.get("episodes") or {}
        if not episodes:
            continue
        item["episode_count"] = len(episodes)
        item["episode_numbers"] = sorted(int(x) for x in episodes if str(x).isdigit())
        results.append(item)
    results.sort(key=lambda x: (-int(x.get("last_seen_at") or 0), x.get("title", "").lower()))
    save_scan_results(results)
    return {"updates_count": len(updates), "series_count": len(results), "results": results}


def stable_manual_series_id(title: str) -> int:
    normalized = re.sub(r"\s+", " ", title.strip().lower())
    return int(hashlib.sha256(normalized.encode()).hexdigest()[:12], 16)

def saved_series_options() -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []

    for key, series in load_series_store().items():
        if not isinstance(series, dict):
            continue

        episodes = series.get("episodes") or {}
        episode_numbers = sorted(
            int(value)
            for value in episodes.keys()
            if str(value).isdigit()
        )
        last_episode = episode_numbers[-1] if episode_numbers else 0
        next_episode = last_episode + 1 if last_episode else 1

        items.append({
            "key": key,
            "title": str(
                series.get("series_title")
                or series.get("original_title")
                or "Serial"
            ),
            "season": int(series.get("season_number") or 1),
            "topic": str(series.get("topic_name") or "General"),
            "episode_count": len(episodes),
            "last_episode": last_episode,
            "next_episode": next_episode,
            "poster_url": series.get("poster_url"),
            "updated_at": int(series.get("updated_at") or 0),
        })

    return sorted(
        items,
        key=lambda item: (
            -item["updated_at"],
            item["title"].lower(),
            item["season"],
        ),
    )

def metadata_from_saved_series(series: dict[str, Any], episode_number: int, episode_title: str) -> dict[str, Any]:
    title=str(series.get("series_title") or series.get("original_title") or "Serial")
    season=int(series.get("season_number") or 1)
    ep_title=episode_title.strip() or f"Episode {episode_number}"
    code=f"S{season:02d}E{episode_number:02d}"
    return {"title":f"{title} {code} - {ep_title}","series_title":title,"episode_title":ep_title,"episode_code":code,"season_number":season,"episode_number":episode_number,"original_title":str(series.get("original_title") or title),"year":str(series.get("year") or "-"),"media_type":"TV Episode","runtime":"-","certification":str(series.get("certification") or "-"),"vote_average":series.get("vote_average") or 0,"vote_count":int(series.get("vote_count") or 0),"release_date":str(series.get("release_date") or "-"),"genres":list(series.get("genres") or []),"countries":list(series.get("countries") or []),"languages":list(series.get("languages") or []),"directors":list(series.get("directors") or []),"writers":list(series.get("writers") or []),"cast":list(series.get("cast") or []),"overview":str(series.get("overview") or "Sinopsis belum tersedia."),"poster_url":series.get("poster_url"),"manual":bool(series.get("manual"))}

def split_manual_list(value: str) -> list[str]:
    return [part.strip() for part in value.split(",") if part.strip()]

def manual_hashtags(value: str) -> list[str]:
    return [normalize_hashtag(part) for part in split_manual_list(value) if normalize_hashtag(part)]

def build_manual_metadata(form: Any) -> dict[str, Any]:
    media_type = str(form.get("manual_media_type") or "movie")
    title = str(form.get("manual_title") or "").strip()
    original_title = str(form.get("manual_original_title") or title).strip()
    year = str(form.get("manual_year") or "-").strip()
    overview = str(form.get("manual_overview") or "Sinopsis belum tersedia.").strip()
    try: rating = round(float(str(form.get("manual_rating") or "0")), 1)
    except ValueError: rating = 0.0
    try: vote_count = int(str(form.get("manual_vote_count") or "0"))
    except ValueError: vote_count = 0
    metadata = {
        "title": title, "original_title": original_title or title, "year": year or "-",
        "media_type": "Movie" if media_type == "movie" else "TV Episode", "runtime": "-",
        "certification": str(form.get("manual_certification") or "-").strip() or "-",
        "vote_average": rating, "vote_count": vote_count,
        "release_date": str(form.get("manual_release_date") or "-").strip() or "-",
        "genres": manual_hashtags(str(form.get("manual_genres") or "")),
        "countries": manual_hashtags(str(form.get("manual_countries") or "")),
        "languages": manual_hashtags(str(form.get("manual_languages") or "")),
        "directors": split_manual_list(str(form.get("manual_directors") or "")),
        "writers": split_manual_list(str(form.get("manual_writers") or "")),
        "cast": split_manual_list(str(form.get("manual_cast") or "")),
        "overview": overview,
        "poster_url": str(form.get("manual_poster_url") or "").strip() or None,
        "manual": True,
    }
    if media_type == "tv":
        season_number = int(form.get("manual_season_number") or "1")
        episode_number = int(form.get("manual_episode_number") or "1")
        episode_title = str(form.get("manual_episode_title") or f"Episode {episode_number}").strip()
        episode_code = f"S{season_number:02d}E{episode_number:02d}"
        metadata.update({"series_title": title,"episode_title": episode_title,"episode_code": episode_code,"season_number": season_number,"episode_number": episode_number,"title": f"{title} {episode_code} - {episode_title}"})
    return metadata

def now_ts() -> int:
    return int(time.time())

def human_size(value: int) -> str:
    size = float(value or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.2f} {unit}"
        size /= 1024
    return f"{value} B"

def human_time(seconds: float | int) -> str:
    seconds = max(0, int(seconds or 0))
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}" if hours else f"{minutes:02d}:{secs:02d}"

def progress_values(stage: str, stage_pct: float) -> tuple[float, float]:
    stage_pct = max(0.0, min(100.0, float(stage_pct)))
    mapping = {
        "QUEUED": (0.0, 0.0), "DOWNLOADING": (0.0, 25.0),
        "PROCESSING": (25.0, 78.0), "PREPARING": (25.0, 78.0), "READY": (78.0, 80.0), "UPLOADING": (80.0, 99.0),
        "SUCCESS": (100.0, 100.0), "ERROR": (0.0, 0.0),
    }
    start, end = mapping.get(stage, (0.0, 100.0))
    overall = start if start == end else start + ((end - start) * stage_pct / 100.0)
    return round(stage_pct, 1), round(overall, 1)

def update_progress(job_id: str, stage: str, stage_pct: float, *, detail: str = "", eta_seconds: float = 0, message: str | None = None, **extra: Any) -> None:
    stage_value, overall_value = progress_values(stage, stage_pct)
    payload: dict[str, Any] = {
        "state": stage, "stage_progress": stage_value,
        "overall_progress": overall_value, "progress_detail": detail,
        "eta_seconds": max(0, int(eta_seconds or 0)),
        "eta_human": human_time(eta_seconds) if eta_seconds else "-",
    }
    if message is not None:
        payload["message"] = message
    payload.update(extra)
    set_job(job_id, **payload)

def authorized() -> bool:
    return (request.args.get("key") or request.headers.get("X-Secret-Key")) == SECRET_KEY

def extract_drive_file_id(value: str) -> str:
    value = value.strip()
    if re.fullmatch(r"[A-Za-z0-9_-]{20,}", value):
        return value
    for pattern in (r"/file/d/([A-Za-z0-9_-]+)", r"[?&]id=([A-Za-z0-9_-]+)", r"/d/([A-Za-z0-9_-]+)"):
        match = re.search(pattern, value)
        if match:
            return match.group(1)
    raise ValueError("Link Google Drive atau File ID tidak valid.")

def tmdb_get(path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    query = dict(params or {})
    query["api_key"] = TMDB_API_KEY
    query.setdefault("language", TMDB_LANGUAGE)
    response = requests.get(f"https://api.themoviedb.org/3{path}", params=query, timeout=30)
    response.raise_for_status()
    return response.json()

def poster_url(path: str | None) -> str | None:
    return f"{TMDB_IMAGE_BASE}{path}" if path else None

def search_tmdb(query: str) -> list[dict[str, Any]]:
    data = tmdb_get("/search/multi", {"query": query, "include_adult": "false", "page": 1})
    results = []
    for item in data.get("results", []):
        media_type = item.get("media_type")
        if media_type not in {"movie", "tv"}:
            continue
        title = item.get("title") or item.get("name") or "Tanpa judul"
        date = item.get("release_date") or item.get("first_air_date") or ""
        results.append({
            "id": item["id"], "media_type": media_type, "title": title,
            "year": date[:4] if date else "-", "overview": item.get("overview") or "Tanpa sinopsis.",
            "poster_url": poster_url(item.get("poster_path")),
        })
    return results[:10]

def normalize_hashtag(value: str) -> str:
    value = re.sub(r"\s+", "_", value.strip().replace("&", "and"))
    value = re.sub(r"[^A-Za-z0-9_]", "", value)
    return f"#{value}" if value else ""

def format_date_id(date_str: str) -> str:
    if not date_str:
        return "-"
    try:
        year, month, day = date_str.split("-")
        months = ["Januari","Februari","Maret","April","Mei","Juni","Juli","Agustus","September","Oktober","November","Desember"]
        return f"{int(day)} {months[int(month)-1]} {year}"
    except Exception:
        return date_str

def build_metadata(tmdb_id: int, media_type: str) -> dict[str, Any]:
    detail = tmdb_get(f"/{media_type}/{tmdb_id}", {"append_to_response": "credits,release_dates,content_ratings"})
    title = detail.get("title") or detail.get("name") or "Tanpa judul"
    original_title = detail.get("original_title") or detail.get("original_name") or title
    release_date = detail.get("release_date") or detail.get("first_air_date") or ""
    runtime = detail.get("runtime") or ((detail.get("episode_run_time") or ["-"])[0])
    credits = detail.get("credits") or {}
    crew, cast = credits.get("crew") or [], credits.get("cast") or []
    directors = [x["name"] for x in crew if x.get("job") in {"Director","Series Director"}][:3]
    writers = [x["name"] for x in crew if x.get("job") in {"Writer","Screenplay","Story","Teleplay"}][:6]
    certification = "-"
    source = detail.get("release_dates", {}).get("results", []) if media_type == "movie" else detail.get("content_ratings", {}).get("results", [])
    for row in source:
        if row.get("iso_3166_1") == "US":
            if media_type == "movie":
                for rel in row.get("release_dates", []):
                    if rel.get("certification"):
                        certification = rel["certification"]; break
            elif row.get("rating"):
                certification = row["rating"]
            if certification != "-":
                break
    return {
        "title": title, "original_title": original_title,
        "year": release_date[:4] if release_date else "-",
        "media_type": "Movie" if media_type == "movie" else "TV",
        "runtime": runtime, "certification": certification,
        "vote_average": round(float(detail.get("vote_average") or 0), 1),
        "vote_count": int(detail.get("vote_count") or 0),
        "release_date": format_date_id(release_date),
        "genres": [normalize_hashtag(x.get("name","")) for x in detail.get("genres", []) if normalize_hashtag(x.get("name",""))],
        "countries": [normalize_hashtag(x.get("name","")) for x in detail.get("production_countries", []) if normalize_hashtag(x.get("name",""))],
        "languages": [normalize_hashtag(x.get("english_name") or x.get("name","")) for x in detail.get("spoken_languages", []) if normalize_hashtag(x.get("english_name") or x.get("name",""))],
        "directors": directors, "writers": writers,
        "cast": [x["name"] for x in cast[:8]],
        "overview": detail.get("overview") or "Sinopsis belum tersedia.",
        "poster_url": poster_url(detail.get("poster_path")),
    }


def build_episode_metadata(
    tv_id: int,
    season_number: int,
    episode_number: int,
) -> dict[str, Any]:
    series = tmdb_get(
        f"/tv/{tv_id}",
        {"append_to_response": "credits,content_ratings"},
    )
    episode = tmdb_get(
        f"/tv/{tv_id}/season/{season_number}/episode/{episode_number}",
        {"append_to_response": "credits"},
    )

    series_title = series.get("name") or "Tanpa judul"
    original_title = series.get("original_name") or series_title
    episode_title = episode.get("name") or f"Episode {episode_number}"
    air_date = episode.get("air_date") or ""
    runtime = episode.get("runtime") or (
        (series.get("episode_run_time") or ["-"])[0]
    )

    series_credits = series.get("credits") or {}
    episode_credits = episode.get("credits") or {}
    crew = episode_credits.get("crew") or series_credits.get("crew") or []
    cast = episode_credits.get("cast") or series_credits.get("cast") or []

    directors = [
        x["name"] for x in crew
        if x.get("job") in {"Director", "Series Director"}
    ][:3]
    writers = [
        x["name"] for x in crew
        if x.get("job") in {"Writer", "Screenplay", "Story", "Teleplay"}
    ][:6]

    certification = "-"
    ratings = series.get("content_ratings", {}).get("results", [])
    for rating in ratings:
        if rating.get("iso_3166_1") == "US" and rating.get("rating"):
            certification = rating["rating"]
            break

    episode_code = f"S{season_number:02d}E{episode_number:02d}"

    return {
        "title": f"{series_title} {episode_code} - {episode_title}",
        "series_title": series_title,
        "episode_title": episode_title,
        "episode_code": episode_code,
        "season_number": season_number,
        "episode_number": episode_number,
        "original_title": original_title,
        "year": air_date[:4] if air_date else "-",
        "media_type": "TV Episode",
        "runtime": runtime,
        "certification": certification,
        "vote_average": round(float(episode.get("vote_average") or 0), 1),
        "vote_count": int(episode.get("vote_count") or 0),
        "release_date": format_date_id(air_date),
        "genres": [
            normalize_hashtag(x.get("name", ""))
            for x in series.get("genres", [])
            if normalize_hashtag(x.get("name", ""))
        ],
        "countries": [
            normalize_hashtag(x.get("name", ""))
            for x in series.get("production_countries", [])
            if normalize_hashtag(x.get("name", ""))
        ],
        "languages": [
            normalize_hashtag(x.get("english_name") or x.get("name", ""))
            for x in series.get("spoken_languages", [])
            if normalize_hashtag(x.get("english_name") or x.get("name", ""))
        ],
        "directors": directors,
        "writers": writers,
        "cast": [x["name"] for x in cast[:8]],
        "overview": episode.get("overview") or series.get("overview") or "Sinopsis belum tersedia.",
        "poster_url": poster_url(episode.get("still_path") or series.get("poster_path")),
    }

def build_caption(meta: dict[str, Any], extra: str = "") -> str:
    if meta.get("episode_code"):
        lines = [
            f"📺 Serial: {meta['series_title']}",
            f"🎬 Episode: {meta['episode_code']} - {meta['episode_title']}",
            f"📢 AKA: {meta['original_title']}",
            "",
        ]
    else:
        lines = [
            f"🎬 Judul: {meta['title']} [{meta['year']}] ({meta['media_type']})",
            f"📢 AKA: {meta['original_title']}",
            "",
        ]

    lines.extend([
        f"Durasi: {meta['runtime']} menit",
        f"Kategori: {meta['certification']}",
        f"Peringkat: {meta['vote_average']}⭐ dari {meta['vote_count']} pengguna",
        f"Rilis: {meta['release_date']}",
        f"Genre: {', '.join(meta['genres']) or '-'}",
        f"Negara: {', '.join(meta['countries']) or '-'}",
        f"Bahasa: {', '.join(meta['languages']) or '-'}", "",
        "👱 Info Cast:",
        f"Sutradara: {', '.join(meta['directors']) or '-'}",
        f"Penulis: {', '.join(meta['writers']) or '-'}",
        f"Pemeran: {', '.join(meta['cast']) or '-'}", "",
        "💬 Sinopsis:", meta["overview"],
    ])
    if extra.strip():
        lines.extend(["", extra.strip()])
    return "\n".join(lines)[:4000]

def drive_url(file_id: str) -> str:
    return f"https://drive.usercontent.google.com/download?id={quote(file_id)}&export=download&confirm=t"

def set_job(job_id: str, **updates: Any) -> None:
    with queue_lock:
        if job_id in jobs:
            jobs[job_id].update(updates)

def get_jobs_snapshot() -> list[dict[str, Any]]:
    with queue_lock:
        ordered = sorted(jobs.values(), key=lambda x: x["created_at"], reverse=True)
        out = []
        for item in ordered:
            data = dict(item)
            data["downloaded_human"] = human_size(data.get("downloaded_bytes", 0))
            data["file_size_human"] = human_size(data.get("file_size_bytes", 0))
            data.setdefault("stage_progress", 0.0)
            data.setdefault("overall_progress", 0.0)
            data.setdefault("progress_detail", "")
            data.setdefault("eta_seconds", 0)
            data.setdefault("eta_human", "-")
            out.append(data)
        return out

def download_file(job_id: str, file_id: str, destination: Path) -> None:
    header_file = destination.parent / "headers.txt"
    cmd = ["curl","--fail","--location","--retry","3","--retry-delay","3","--connect-timeout","30","--max-time","0","--dump-header",str(header_file),"--user-agent","Mozilla/5.0 Chrome/126.0","--output",str(destination),drive_url(file_id)]
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    total = 0
    started = time.monotonic()
    while proc.poll() is None:
        if header_file.exists():
            matches = re.findall(r"(?im)^content-length:\s*(\d+)\s*$", header_file.read_text(encoding="utf-8", errors="ignore"))
            if matches:
                total = int(matches[-1])
        downloaded = destination.stat().st_size if destination.exists() else 0
        elapsed = max(0.1, time.monotonic() - started)
        speed = downloaded / elapsed
        pct = (downloaded * 100 / total) if total > 0 else 0
        eta = ((total - downloaded) / speed) if total > downloaded and speed > 0 else 0
        detail = f"{human_size(downloaded)} / {human_size(total)} · {human_size(int(speed))}/s" if total > 0 else f"{human_size(downloaded)} · {human_size(int(speed))}/s"
        update_progress(job_id, "DOWNLOADING", pct, detail=detail, eta_seconds=eta, message="Mengunduh video dari Google Drive.", downloaded_bytes=downloaded, total_bytes=total)
        time.sleep(1)
    stderr = proc.stderr.read() if proc.stderr else ""
    if proc.returncode != 0:
        raise RuntimeError(f"Download Google Drive gagal: {stderr[-1000:]}")
    if not destination.exists() or destination.stat().st_size <= 0:
        raise RuntimeError("File hasil download kosong.")
    with destination.open("rb") as handle:
        prefix = handle.read(512).lower()
    if b"<html" in prefix or b"<!doctype html" in prefix:
        raise RuntimeError("Google Drive mengirim halaman HTML. Pastikan file dapat diakses oleh siapa saja yang memiliki link.")
    size = destination.stat().st_size
    update_progress(job_id, "DOWNLOADING", 100, detail=f"{human_size(size)} selesai", message="Download selesai.", downloaded_bytes=size, total_bytes=max(total, size))

def download_drive_asset(file_id: str, destination: Path) -> None:
    command = [
        "curl", "--fail", "--location",
        "--retry", "3", "--retry-delay", "3",
        "--connect-timeout", "30", "--max-time", "0",
        "--user-agent", "Mozilla/5.0 Chrome/126.0",
        "--output", str(destination),
        drive_url(file_id),
    ]
    result = subprocess.run(
        command,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Download file Google Drive gagal: {result.stderr[-1000:]}"
        )
    if not destination.exists() or destination.stat().st_size <= 0:
        raise RuntimeError("File Google Drive hasil download kosong.")
    with destination.open("rb") as handle:
        prefix = handle.read(256).lower()
    if b"<html" in prefix or b"<!doctype html" in prefix:
        raise RuntimeError(
            "Google Drive mengirim halaman HTML, bukan file. "
            "Pastikan akses file publik."
        )

def ffprobe_streams(video_path: Path) -> list[dict[str, Any]]:
    proc = subprocess.run(
        ["ffprobe","-v","error","-show_streams","-of","json",str(video_path)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffprobe gagal: {proc.stderr[-1000:]}")
    return json.loads(proc.stdout).get("streams", [])

def choose_subtitle_stream(streams: list[dict[str, Any]]) -> tuple[int | None, str]:
    subtitles = [s for s in streams if s.get("codec_type") == "subtitle"]
    if not subtitles:
        return None, "Tidak ada subtitle internal"
    preferred = {"ind","id","indo","indonesian","bahasa indonesia"}
    for pos, stream in enumerate(subtitles):
        tags = stream.get("tags") or {}
        lang = str(tags.get("language") or "").lower()
        title = str(tags.get("title") or "").lower()
        if lang in preferred or any(word in title for word in preferred):
            return pos, f"Subtitle internal Indonesia ({lang or title or 'track'})"
    return 0, "Subtitle internal pertama (Indonesia tidak terdeteksi)"

def extract_internal_subtitle(video_path: Path, subtitle_pos: int, out_path: Path) -> None:
    proc = subprocess.run(
        ["ffmpeg","-y","-i",str(video_path),"-map",f"0:s:{subtitle_pos}",str(out_path)],
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True
    )
    if proc.returncode != 0 or not out_path.exists():
        raise RuntimeError(f"Ekstrak subtitle gagal: {proc.stderr[-1200:]}")

def ffmpeg_escape(path: Path) -> str:
    return str(path).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")

def ffprobe_duration(video_path: Path) -> float:
    proc = subprocess.run(
        ["ffprobe","-v","error","-show_entries","format=duration","-of","default=noprint_wrappers=1:nokey=1",str(video_path)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffprobe durasi gagal: {proc.stderr[-1000:]}")
    try:
        return max(0.0, float(proc.stdout.strip()))
    except ValueError as exc:
        raise RuntimeError("Durasi video tidak dapat dibaca.") from exc


def ffprobe_video_size(video_path: Path) -> tuple[int, int]:
    proc = subprocess.run(
        [
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=width,height",
            "-of", "csv=p=0:s=x", str(video_path),
        ],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffprobe resolusi gagal: {proc.stderr[-1000:]}")
    try:
        width_text, height_text = proc.stdout.strip().split("x", 1)
        return max(2, int(width_text)), max(2, int(height_text))
    except (ValueError, TypeError) as exc:
        raise RuntimeError("Resolusi video tidak dapat dibaca.") from exc


def detect_active_video_area(video_path: Path, duration: float) -> tuple[float, float, float, float]:
    """Return active picture bounds as normalized x, y, width, height.

    FFmpeg cropdetect is sampled at several positions. The most frequently
    detected crop is used. If detection is uncertain, the entire frame is
    treated as active video so encoding can continue safely.
    """
    frame_w, frame_h = ffprobe_video_size(video_path)
    samples = [0.0]
    if duration > 20:
        samples = [max(0.0, duration * ratio) for ratio in (0.08, 0.25, 0.50, 0.75, 0.92)]

    detections: list[tuple[int, int, int, int]] = []
    crop_pattern = re.compile(r"crop=(\d+):(\d+):(\d+):(\d+)")
    for start in samples:
        proc = subprocess.run(
            [
                "ffmpeg", "-hide_banner", "-ss", f"{start:.3f}",
                "-i", str(video_path), "-t", "2.5",
                "-vf", "cropdetect=24:16:0", "-an", "-f", "null", "-",
            ],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
        )
        matches = crop_pattern.findall(proc.stderr or "")
        if matches:
            w, h, x, y = map(int, matches[-1])
            if w >= frame_w * 0.45 and h >= frame_h * 0.45:
                detections.append((w, h, x, y))

    if not detections:
        return 0.0, 0.0, 1.0, 1.0

    # Group nearly identical detections because cropdetect rounds to blocks.
    grouped: dict[tuple[int, int, int, int], int] = {}
    for w, h, x, y in detections:
        key = (round(w / 8) * 8, round(h / 8) * 8, round(x / 8) * 8, round(y / 8) * 8)
        grouped[key] = grouped.get(key, 0) + 1
    crop_w, crop_h, crop_x, crop_y = max(
        grouped, key=lambda item: (grouped[item], item[0] * item[1])
    )

    crop_x = max(0, min(frame_w - 2, crop_x))
    crop_y = max(0, min(frame_h - 2, crop_y))
    crop_w = max(2, min(frame_w - crop_x, crop_w))
    crop_h = max(2, min(frame_h - crop_y, crop_h))
    return (crop_x / frame_w, crop_y / frame_h, crop_w / frame_w, crop_h / frame_h)

WATERMARK_EXTENSIONS = {".png", ".webp", ".jpg", ".jpeg", ".gif"}
WATERMARK_POSITIONS = {
    "top_right": "main_w-overlay_w-20:20",
    "top_left": "20:20",
    "bottom_right": "main_w-overlay_w-20:main_h-overlay_h-20",
    "bottom_left": "20:main_h-overlay_h-20",
}

def _watermark_common_config(field_name: str) -> dict[str, Any]:
    try:
        width_pct = max(3, min(25, int(request.form.get(field_name + "_size") or "8")))
        opacity_pct = max(10, min(100, int(request.form.get(field_name + "_opacity") or "35")))
    except ValueError as exc:
        raise ValueError("Ukuran atau transparansi logo tidak valid.") from exc
    position = str(request.form.get(field_name + "_position") or "top_right")
    if position not in WATERMARK_POSITIONS:
        raise ValueError("Posisi logo tidak valid.")
    mode = str(request.form.get(field_name + "_mode") or "smart_v2")
    if mode not in {"static", "smart_v2"}:
        mode = "smart_v2"
    speed = str(request.form.get(field_name + "_speed") or "normal")
    if speed not in {"slow", "normal", "fast"}:
        speed = "normal"
    return {
        "watermark_position": position, "watermark_mode": mode,
        "watermark_speed": speed, "watermark_size": width_pct,
        "watermark_opacity": opacity_pct,
    }

def materialize_global_watermark(job: dict[str, Any], work_dir: Path) -> dict[str, Any]:
    """Download a portable watermark asset when the job uses Google Drive."""
    if not job.get("watermark_enabled"):
        return job
    source = str(job.get("watermark_source") or "upload")
    if source != "gdrive":
        return job
    file_id = str(job.get("watermark_drive_file_id") or "").strip()
    if not file_id:
        raise RuntimeError("File ID logo Google Drive tidak tersedia.")
    destination = work_dir / "watermark-global.png"
    download_drive_asset(file_id, destination)
    if not destination.exists() or destination.stat().st_size < 32:
        raise RuntimeError("Logo Google Drive gagal diunduh.")
    job["watermark_path"] = str(destination)
    job["watermark_info"] = (
        f"Logo Google Drive · {'Smart Safe Area' if job.get('watermark_mode') == 'smart_v2' else 'Statis Safe Area'} "
        f"· {job.get('watermark_size', 8)}% · opacity {job.get('watermark_opacity', 35)}%"
    )
    return job

def save_watermark_upload(field_name: str, work_dir: Path) -> dict[str, Any]:
    enabled = str(request.form.get(field_name + "_enabled") or "") in {"1", "on", "true", "yes"}
    if not enabled:
        return {
            "watermark_enabled": False, "watermark_path": "",
            "watermark_source": "none", "watermark_drive_file_id": "",
            "watermark_info": "Tanpa logo",
        }

    common = _watermark_common_config(field_name)
    source = str(request.form.get(field_name + "_source") or "upload").strip().lower()
    if source not in {"upload", "gdrive"}:
        source = "upload"

    if source == "gdrive":
        drive_input = str(request.form.get(field_name + "_drive_input") or "").strip()
        if not drive_input:
            raise ValueError("Watermark Google Drive dipilih tetapi link/File ID logo masih kosong.")
        file_id = extract_drive_file_id(drive_input)
        return {
            "watermark_enabled": True, "watermark_path": "",
            "watermark_source": "gdrive", "watermark_drive_file_id": file_id,
            "watermark_info": (
                f"Logo Google Drive · {'Smart Safe Area' if common['watermark_mode'] == 'smart_v2' else 'Statis Safe Area'} "
                f"· {common['watermark_size']}% · opacity {common['watermark_opacity']}%"
            ),
            **common,
        }

    upload = request.files.get(field_name + "_file")
    if not upload or not upload.filename:
        raise ValueError("Watermark upload dipilih tetapi file logo belum dipilih.")
    ext = Path(upload.filename).suffix.lower()
    if ext not in WATERMARK_EXTENSIONS:
        raise ValueError("Format logo harus PNG, WEBP, JPG, JPEG, atau GIF.")
    destination = work_dir / f"watermark{ext}"
    upload.save(destination)
    return {
        "watermark_enabled": True, "watermark_path": str(destination),
        "watermark_source": "upload", "watermark_drive_file_id": "",
        "watermark_info": (
            f"Logo {upload.filename} · {'Smart Safe Area' if common['watermark_mode'] == 'smart_v2' else 'Statis Safe Area'} "
            f"· {common['watermark_size']}% · opacity {common['watermark_opacity']}%"
        ),
        **common,
    }

def copy_watermark_config(source: dict[str, Any], work_dir: Path) -> dict[str, Any]:
    result = dict(source)
    source_path = Path(str(source.get("watermark_path") or ""))
    if source.get("watermark_enabled") and source_path.exists():
        destination = work_dir / source_path.name
        shutil.copy2(source_path, destination)
        result["watermark_path"] = str(destination)
    return result

def parse_encode_config(prefix: str = "") -> dict[str, Any]:
    field = f"{prefix}encode_profile" if prefix else "encode_profile"
    profile = str(request.form.get(field) or "telegram_1080").strip()
    if profile not in {"telegram_1080", "original"}:
        profile = "telegram_1080"
    target_field = f"{prefix}target_size_gb" if prefix else "target_size_gb"
    try:
        target_gb = float(request.form.get(target_field) or TELEGRAM_TARGET_GB)
    except ValueError:
        target_gb = TELEGRAM_TARGET_GB
    target_gb = max(0.30, min(1.49, target_gb))
    return {"encode_profile": profile, "target_size_gb": target_gb}


def calculate_target_video_kbps(duration: float, target_gb: float, audio_kbps: int = TELEGRAM_AUDIO_KBPS) -> int:
    if duration <= 0:
        raise RuntimeError("Durasi video tidak dapat dibaca untuk menghitung target ukuran.")
    target_bytes = target_gb * 1024 ** 3
    usable_bits = target_bytes * 8 * 0.965  # ruang aman untuk container/metadata
    total_kbps = usable_bits / duration / 1000
    return max(350, int(total_kbps - audio_kbps))

def process_video(
    job_id: str,
    input_path: Path,
    output_path: Path,
    thumb_path: Path,
    subtitle_path: Path | None,
    watermark: dict[str, Any] | None = None,
) -> None:
    duration = ffprobe_duration(input_path)
    watermark = watermark or {}
    watermark_path = Path(str(watermark.get("watermark_path") or ""))
    use_watermark = bool(watermark.get("watermark_enabled")) and watermark_path.exists()
    profile = str(watermark.get("encode_profile") or "telegram_1080")
    target_gb = float(watermark.get("target_size_gb") or TELEGRAM_TARGET_GB)

    base_cmd = ["ffmpeg", "-y", "-i", str(input_path)]
    if use_watermark:
        base_cmd += ["-stream_loop", "-1", "-i", str(watermark_path)]

    filters: list[str] = []
    if subtitle_path:
        filters.append(f"[0:v]subtitles='{ffmpeg_escape(subtitle_path)}'[base0]")
    else:
        filters.append("[0:v]null[base0]")
    filters.append("[base0]scale=w='min(1920,iw)':h='min(1080,ih)':force_original_aspect_ratio=decrease,scale=trunc(iw/2)*2:trunc(ih/2)*2[base]")

    output_label = "base"
    if use_watermark:
        active_x, active_y, active_w, active_h = detect_active_video_area(input_path, duration)
        width = max(3, min(25, int(watermark.get("watermark_size") or 8))) / 100
        opacity = max(10, min(100, int(watermark.get("watermark_opacity") or 35))) / 100
        selected_position = str(watermark.get("watermark_position") or "top_right")
        mode = str(watermark.get("watermark_mode") or "smart_v2")
        speed = str(watermark.get("watermark_speed") or "normal")
        periods = {"slow": (83, 107), "normal": (53, 71), "fast": (31, 43)}
        period_x, period_y = periods.get(speed, periods["normal"])

        # Bounds are calculated from the detected picture area, not the full
        # encoded frame. This keeps the logo away from cinematic black bars.
        left = f"main_w*{active_x:.6f}"
        top = f"main_h*{active_y:.6f}"
        right = f"main_w*{active_x + active_w:.6f}"
        bottom = f"main_h*{active_y + active_h:.6f}"
        margin = "max(18,min(main_w,main_h)*0.018)"

        if selected_position == "top_left":
            base_x, base_y = f"{left}+{margin}", f"{top}+{margin}"
            dx_sign, dy_sign = 1, 1
        elif selected_position == "bottom_left":
            base_x, base_y = f"{left}+{margin}", f"{bottom}-overlay_h-{margin}"
            dx_sign, dy_sign = 1, -1
        elif selected_position == "bottom_right":
            base_x, base_y = f"{right}-overlay_w-{margin}", f"{bottom}-overlay_h-{margin}"
            dx_sign, dy_sign = -1, -1
        else:
            base_x, base_y = f"{right}-overlay_w-{margin}", f"{top}+{margin}"
            dx_sign, dy_sign = -1, 1

        if mode == "smart_v2":
            # Subtle movement remains around the selected corner. It never
            # alternates corners and is clamped inside the active picture.
            motion = "max(6,min(main_w,main_h)*0.012)"
            raw_x = f"({base_x})+({dx_sign})*({motion})*(0.5+0.5*sin(2*PI*t/{period_x}))"
            raw_y = f"({base_y})+({dy_sign})*({motion})*(0.5+0.5*sin(2*PI*t/{period_y}+PI/2))"
            x_expr = f"max({left}+{margin},min({right}-overlay_w-{margin},{raw_x}))"
            y_expr = f"max({top}+{margin},min({bottom}-overlay_h-{margin},{raw_y}))"
            overlay_expr = f"x='{x_expr}':y='{y_expr}':eval=frame:shortest=1"
        else:
            x_expr = f"max({left}+{margin},min({right}-overlay_w-{margin},{base_x}))"
            y_expr = f"max({top}+{margin},min({bottom}-overlay_h-{margin},{base_y}))"
            overlay_expr = f"x='{x_expr}':y='{y_expr}':eval=init:shortest=1"

        filters.extend([
            f"[1:v][base]scale2ref=w=main_w*{active_w * width:.6f}:h=-1[logo][base2]",
            f"[logo]format=rgba,colorchannelmixer=aa={opacity:.3f}[wm]",
            f"[base2][wm]overlay={overlay_expr}[vout]",
        ])
        set_job(
            job_id,
            watermark_info=(
                f"{watermark.get('watermark_info') or 'Logo'} · Safe area "
                f"{active_w * 100:.1f}%×{active_h * 100:.1f}% · sudut tetap"
            ),
        )
        output_label = "vout"

    common = base_cmd + ["-filter_complex", ";".join(filters), "-map", f"[{output_label}]", "-map", "0:a?"]
    video_kbps = calculate_target_video_kbps(duration, target_gb) if profile == "telegram_1080" else 0
    maxrate = max(video_kbps, int(video_kbps * 1.08)) if video_kbps else 0
    bufsize = maxrate * 2 if maxrate else 0

    def run_ffmpeg(codec: str, fallback: bool = False) -> tuple[int, str]:
        output_path.unlink(missing_ok=True)
        log_path = output_path.parent / ("ffmpeg-fallback.log" if fallback else "ffmpeg.log")
        cmd = list(common)
        if codec == "libx265":
            turbo_params = (
                "rc-lookahead=10:bframes=2:ref=2:subme=1:me=hex:"
                "aq-mode=1:rd=2:psy-rd=1.0"
                if TELEGRAM_X265_TURBO else ""
            )
            x265_params = (
                f"pools={TELEGRAM_X265_THREADS}:"
                f"frame-threads={TELEGRAM_X265_FRAME_THREADS}:"
                f"wpp={1 if TELEGRAM_X265_WPP else 0}:"
                f"{turbo_params + ':' if turbo_params else ''}"
                "log-level=error"
            )
            set_job(job_id, encode_info=(
                f"H.265 Turbo 1080p · preset {TELEGRAM_X265_PRESET} · target {target_gb:.2f} GB · "
                f"video {video_kbps} kbps · CPU {CPU_COUNT} · threads "
                f"{TELEGRAM_X265_THREADS}/{TELEGRAM_X265_FRAME_THREADS} · WPP "
                f"{'aktif' if TELEGRAM_X265_WPP else 'mati'}"
            ))
            cmd += [
                "-c:v", "libx265", "-preset", TELEGRAM_X265_PRESET,
                "-threads", str(TELEGRAM_X265_THREADS),
                "-x265-params", x265_params,
                "-b:v", f"{video_kbps}k", "-maxrate", f"{maxrate}k", "-bufsize", f"{bufsize}k",
                "-tag:v", "hvc1", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", f"{TELEGRAM_AUDIO_KBPS}k",
            ]
        elif profile == "telegram_1080":
            set_job(job_id, encode_info=f"H.264 fallback 1080p · target {target_gb:.2f} GB · video {video_kbps} kbps")
            cmd += [
                "-c:v", "libx264", "-preset", FFMPEG_PRESET,
                "-threads", str(TELEGRAM_X265_THREADS),
                "-b:v", f"{video_kbps}k", "-maxrate", f"{maxrate}k", "-bufsize", f"{bufsize}k",
                "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", f"{TELEGRAM_AUDIO_KBPS}k",
            ]
        else:
            set_job(job_id, encode_info=f"H.264 CRF {FFMPEG_CRF}")
            cmd += ["-c:v", "libx264", "-preset", FFMPEG_PRESET, "-crf", FFMPEG_CRF, "-c:a", "aac", "-b:a", "128k"]

        cmd += ["-movflags", "+faststart", "-progress", "pipe:1", "-nostats", str(output_path)]
        started = time.monotonic()
        with log_path.open("w", encoding="utf-8", errors="replace") as log_file:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=log_file, text=True, bufsize=1)
            assert proc.stdout is not None
            for raw_line in proc.stdout:
                line = raw_line.strip()
                if not line.startswith("out_time_ms="):
                    continue
                try:
                    processed = int(line.split("=", 1)[1]) / 1_000_000
                except ValueError:
                    continue
                pct = (processed * 100 / duration) if duration > 0 else 0
                elapsed = max(0.1, time.monotonic() - started)
                speed_ratio = processed / elapsed if processed > 0 else 0
                eta = ((duration - processed) / speed_ratio) if duration > processed and speed_ratio > 0 else 0
                mode_text = "H.264 fallback" if fallback else ("H.265" if codec == "libx265" else "H.264")
                update_progress(
                    job_id, "PROCESSING", pct,
                    detail=f"{human_time(processed)} / {human_time(duration)} · {speed_ratio:.2f}x",
                    eta_seconds=eta,
                    message=f"Mengompres 1080p dengan {mode_text}{' Turbo' if codec == 'libx265' and TELEGRAM_X265_TURBO else ''}.",
                    processed_seconds=processed,
                    duration_seconds=duration,
                )
            return_code = proc.wait()
        try:
            log_text = log_path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            log_text = "Log FFmpeg tidak dapat dibaca."
        return return_code, log_text

    if profile == "telegram_1080":
        requested_codec = "libx265" if TELEGRAM_VIDEO_CODEC == "libx265" else "libx264"
    else:
        requested_codec = "libx264"

    return_code, ffmpeg_log = run_ffmpeg(requested_codec)
    if return_code != 0 and requested_codec == "libx265" and TELEGRAM_FALLBACK_H264:
        set_job(job_id, message="H.265 gagal. Mencoba ulang dengan H.264 yang lebih ringan.")
        update_progress(job_id, "PROCESSING", 0, detail="Fallback otomatis H.264", message="H.265 gagal; mencoba H.264.")
        first_error = ffmpeg_log[-1800:]
        return_code, fallback_log = run_ffmpeg("libx264", fallback=True)
        if return_code != 0:
            raise RuntimeError(
                f"FFmpeg H.265 gagal, lalu fallback H.264 juga gagal (kode {return_code}).\n"
                f"H.265:\n{first_error}\n\nH.264:\n{fallback_log[-2200:]}"
            )
    elif return_code != 0:
        hint = " Proses kemungkinan dihentikan karena RAM/CPU." if return_code in {-9, 137} else ""
        raise RuntimeError(f"FFmpeg video gagal (kode {return_code}).{hint}\n{ffmpeg_log[-3000:]}")

    final_size = output_path.stat().st_size if output_path.exists() else 0
    if profile == "telegram_1080" and final_size > int(1.5 * 1024 ** 3):
        raise RuntimeError(f"Hasil encode {human_size(final_size)} masih melewati 1,5 GB. Turunkan target ukuran, misalnya 1,40 GB.")
    set_job(job_id, output_size_bytes=final_size, file_size_bytes=final_size)
    update_progress(job_id, "PROCESSING", 98, detail=f"Hasil {human_size(final_size)}", message="Membuat thumbnail video.")
    thumb = subprocess.run(["ffmpeg", "-y", "-ss", "00:00:05", "-i", str(output_path), "-frames:v", "1", "-vf", "scale=640:-2", "-q:v", "3", str(thumb_path)], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    if thumb.returncode != 0 or not thumb_path.exists():
        raise RuntimeError(f"FFmpeg thumbnail gagal: {thumb.stderr[-1200:]}")
    update_progress(job_id, "PROCESSING", 100, detail=f"Selesai · {human_size(final_size)}", message="Pemrosesan video selesai.")

def send_poster(meta: dict[str, Any], caption: str, target_chat_id: str, message_thread_id: int) -> None:
    if not meta.get("poster_url"):
        return
    payload = {
        "chat_id": target_chat_id,
        "photo": meta["poster_url"],
        "caption": caption[:1024],
    }
    if message_thread_id > 0:
        payload["message_thread_id"] = str(message_thread_id)
    response = requests.post(
        telegram_api_url(ACTIVE_BOT_TOKEN, "sendPhoto"),
        data=payload,
        timeout=120,
    )
    data = response.json()
    if not response.ok or not data.get("ok"):
        raise RuntimeError(f"Kirim poster gagal: {data.get('description', response.text)}")

def upload_video(job_id: str, video_path: Path, thumb_path: Path, caption: str, target_chat_id: str, message_thread_id: int) -> dict[str, Any]:
    video_file = video_path.open("rb")
    thumb_file = thumb_path.open("rb")
    try:
        fields: dict[str, Any] = {
            "chat_id": target_chat_id,
            "caption": caption[:1024],
            "supports_streaming": "true",
            "video": (video_path.name, video_file, "video/mp4"),
            "thumbnail": (thumb_path.name, thumb_file, "image/jpeg"),
        }
        if message_thread_id > 0:
            fields["message_thread_id"] = str(message_thread_id)
        encoder = MultipartEncoder(fields=fields)
        started = time.monotonic()
        last_update = [0.0]
        def callback(monitor: MultipartEncoderMonitor) -> None:
            now = time.monotonic()
            if now - last_update[0] < 0.5 and monitor.bytes_read < monitor.len:
                return
            last_update[0] = now
            elapsed = max(0.1, now - started)
            uploaded, total = monitor.bytes_read, monitor.len
            speed = uploaded / elapsed
            pct = uploaded * 100 / total if total else 0
            eta = ((total - uploaded) / speed) if total > uploaded and speed > 0 else 0
            update_progress(job_id, "UPLOADING", pct, detail=f"{human_size(uploaded)} / {human_size(total)} · {human_size(int(speed))}/s", eta_seconds=eta, message="Mengunggah video ke Telegram.", uploaded_bytes=uploaded, upload_total_bytes=total)
        monitor = MultipartEncoderMonitor(encoder, callback)
        response = requests.post(
            telegram_api_url(ACTIVE_BOT_TOKEN, "sendVideo"),
            data=monitor,
            headers={"Content-Type": monitor.content_type},
            timeout=(30, 7200),
        )
        try:
            result = response.json()
        except ValueError:
            result = {}
        if not response.ok or not result.get("ok"):
            raise RuntimeError(f"Upload Telegram gagal: {result.get('description') or response.text[-1200:]}")
        update_progress(job_id, "UPLOADING", 100, detail=f"{human_size(monitor.len)} berhasil diunggah.", message="Upload Telegram selesai.", uploaded_bytes=monitor.len, upload_total_bytes=monitor.len)
        return result
    finally:
        video_file.close()
        thumb_file.close()


def _scheduler_payload(job: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """Create a portable job payload. Uploaded subtitle/logo files force local execution."""
    uploaded_subtitle = str(job.get("uploaded_subtitle") or "")
    watermark_path = str(job.get("watermark_path") or "")
    watermark_source = str(job.get("watermark_source") or "upload")
    local_watermark = bool(job.get("watermark_enabled") and watermark_path and watermark_source != "gdrive")
    local_only = bool(uploaded_subtitle or local_watermark)
    skip = {
        "work_dir", "uploaded_subtitle", "watermark_path", "state", "message", "error",
        "started_at", "finished_at", "downloaded_bytes", "total_bytes", "file_size_bytes",
        "message_id", "index_message_id", "stage_progress", "overall_progress",
        "progress_detail", "eta_seconds", "eta_human", "scheduler_payload",
    }
    payload = {k: v for k, v in job.items() if k not in skip}
    payload["uploaded_subtitle"] = ""
    payload["watermark_path"] = ""
    return payload, local_only


def _parse_cluster_timestamp(value: Any) -> float:
    raw = str(value or "").strip()
    if not raw:
        return 0.0
    try:
        return time.mktime(time.strptime(raw[:19], "%Y-%m-%dT%H:%M:%S"))
    except Exception:
        try:
            return float(raw)
        except (TypeError, ValueError):
            return 0.0


def _scheduler_worker_health() -> dict[str, dict[str, Any]]:
    """Return worker heartbeat state; old Supabase rows are not treated as online."""
    now = time.time()
    health: dict[str, dict[str, Any]] = {}
    try:
        rows = cluster_store.workers()
    except Exception:
        rows = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        worker_id = str(row.get("worker_id") or row.get("updated_by") or "").strip()
        if not worker_id:
            continue
        stamp = _parse_cluster_timestamp(row.get("last_seen") or row.get("updated_at"))
        age = max(0.0, now - stamp) if stamp else float("inf")
        health[worker_id] = {
            "worker_id": worker_id,
            "last_seen": row.get("last_seen") or row.get("updated_at"),
            "age_seconds": int(age) if age != float("inf") else None,
            "online": age <= V161_WORKER_OFFLINE_SECONDS,
        }
    health[cluster_store.worker_id] = {
        "worker_id": cluster_store.worker_id,
        "last_seen": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "age_seconds": 0,
        "online": True,
    }
    return health


def _scheduler_worker_ids() -> list[str]:
    """Only return workers whose heartbeat is still fresh."""
    return sorted(wid for wid, info in _scheduler_worker_health().items() if info.get("online"))

def _scheduler_choose_worker() -> str:
    workers = _scheduler_worker_ids()
    counts = {wid: 0 for wid in workers}
    try:
        for item in cluster_store.enterprise_jobs():
            if str(item.get("state")) not in {"QUEUED", "DOWNLOADING", "PROCESSING", "PREPARING", "READY", "UPLOADING"}:
                continue
            assigned = str(item.get("assigned_worker") or item.get("worker_id") or "")
            if assigned in counts:
                counts[assigned] += 1
    except Exception:
        pass
    return min(workers, key=lambda wid: (counts.get(wid, 0), wid != cluster_store.worker_id, wid))


def _scheduler_local_active_count() -> int:
    """Return jobs already claimed/running on this Railway worker."""
    active = {"CLAIMED", "DOWNLOADING", "PROCESSING", "PREPARING", "READY", "UPLOADING"}
    with queue_lock:
        return sum(
            1 for item in jobs.values()
            if str(item.get("assigned_worker") or item.get("worker_id") or "") == cluster_store.worker_id
            and (
                str(item.get("state") or "") in active
                or (
                    str(item.get("state") or "") == "QUEUED"
                    and str(item.get("scheduler_status") or "") == "CLAIMED"
                )
            )
        )


def _scheduler_submit_locked(job_id: str) -> None:
    """Publish a job to the shared queue.

    Portable Google Drive jobs are intentionally left unassigned so any idle
    Railway can atomically claim them. Jobs containing uploaded local files stay
    pinned to the Railway that received those files.
    """
    job = jobs[job_id]
    payload, local_only = _scheduler_payload(job)
    job["submitted_by"] = cluster_store.worker_id
    job["preferred_worker"] = cluster_store.worker_id
    job["scheduler_local_only"] = local_only
    job["scheduler_payload"] = payload

    if SCHEDULER_ENABLED and ENTERPRISE_CLUSTER_ENABLED and cluster_store.enabled and not local_only:
        job["assigned_worker"] = ""
        job["worker_id"] = ""
        job["scheduler_status"] = "UNASSIGNED"
        job["message"] = "Menunggu worker Railway yang kosong."
        job["progress_detail"] = "Antrean global belum diklaim worker."
        cluster_store.publish_enterprise_job(dict(job))
        # Keep the lightweight local record for dashboard visibility, but do not
        # put it in the local processing deque until this worker wins the claim.
        shutil.rmtree(Path(str(job.get("work_dir") or "")), ignore_errors=True)
        job["work_dir"] = ""
        return

    job["assigned_worker"] = cluster_store.worker_id
    job["worker_id"] = cluster_store.worker_id
    job["scheduler_status"] = "PINNED_LOCAL" if local_only else "LOCAL"
    job["message"] = "Menunggu giliran di worker lokal."
    cluster_store.publish_enterprise_job(dict(job))
    pending_jobs.append(job_id)


def _scheduler_series_identity(job: dict[str, Any]) -> str:
    """Stable identity used to serialize episodes from the same series/season/topic."""
    episode = int(job.get("episode_number") or 0)
    if episode < 1:
        return ""
    title = str(
        job.get("tmdb_id")
        or job.get("saved_series_key")
        or (job.get("scheduler_payload") or {}).get("saved_series_key")
        or job.get("title")
        or "series"
    ).strip().lower()
    # Remove the trailing SxxExx / Episode suffix when a title is used as fallback.
    title = re.sub(r"\bs\d{1,3}e\d{1,4}\b.*$", "", title, flags=re.I).strip()
    return "|".join([
        str(job.get("target_chat_id") or CHANNEL_ID),
        str(job.get("message_thread_id") or 0),
        title,
        str(int(job.get("season_number") or 1)),
    ])


def _scheduler_created_value(job: dict[str, Any]) -> float:
    raw = job.get("created_at") or 0
    try:
        return float(raw)
    except (TypeError, ValueError):
        return 0.0


def _scheduler_latest_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep only the newest job for each serial/season/episode identity.

    Older SUCCESS/ERROR duplicates must never block a newly submitted episode.
    Film jobs and jobs without a stable episode identity remain distinct by job id.
    """
    latest: dict[str, dict[str, Any]] = {}
    for row in rows:
        identity = _scheduler_series_identity(row)
        episode = int(row.get("episode_number") or 0)
        if identity and episode > 0:
            key = f"serial:{identity}:episode:{episode}"
        else:
            key = f"job:{row.get('id') or row.get('item_key') or id(row)}"
        current = latest.get(key)
        if current is None or _scheduler_created_value(row) >= _scheduler_created_value(current):
            latest[key] = row
    return list(latest.values())

def _scheduler_series_ready(candidate: dict[str, Any], rows: list[dict[str, Any]]) -> bool:
    """Strict sequential dispatch for serial episodes only.

    A serial episode may be claimed only after every lower-numbered episode from
    the same series, season, chat, and topic has reached SUCCESS. Film jobs do
    not have an episode number, so they remain globally parallel.
    """
    rows = _scheduler_latest_rows(rows)
    identity = _scheduler_series_identity(candidate)
    if not identity:
        return True
    if not (SERIES_SEQUENTIAL_SCHEDULER or SMART_PIPELINE_SCHEDULER):
        return True
    candidate_id = str(candidate.get("id") or "")
    candidate_episode = int(candidate.get("episode_number") or 0)
    for other in rows:
        if str(other.get("id") or "") == candidate_id:
            continue
        if _scheduler_series_identity(other) != identity:
            continue
        other_episode = int(other.get("episode_number") or 0)
        if not (0 < other_episode < candidate_episode):
            continue
        if str(other.get("state") or "") != "SUCCESS":
            return False
    return True

def _scheduler_claim_remote_jobs() -> None:
    if not (SCHEDULER_ENABLED and ENTERPRISE_CLUSTER_ENABLED and cluster_store.enabled):
        return
    if _scheduler_local_active_count() >= SCHEDULER_MAX_JOBS_PER_WORKER:
        return

    rows = _scheduler_latest_rows(cluster_store.enterprise_jobs())
    candidates = [row for row in rows if str(row.get("state") or "") == "QUEUED"]
    candidates.sort(key=lambda row: (
        _scheduler_created_value(row),
        int(row.get("season_number") or 0),
        int(row.get("episode_number") or 0),
        str(row.get("id") or ""),
    ))

    for remote in candidates:
        if _scheduler_local_active_count() >= SCHEDULER_MAX_JOBS_PER_WORKER:
            break
        assigned = str(remote.get("assigned_worker") or "").strip()
        if assigned and assigned != cluster_store.worker_id:
            continue
        if bool(remote.get("scheduler_local_only")) and assigned != cluster_store.worker_id:
            continue
        job_id = str(remote.get("id") or "")
        payload = remote.get("scheduler_payload")
        if not job_id or not isinstance(payload, dict):
            continue
        if not _scheduler_series_ready(remote, rows):
            continue

        series_identity = _scheduler_series_identity(remote)
        series_lock_key = "scheduler-series:" + hashlib.sha256(series_identity.encode("utf-8")).hexdigest()[:32] if series_identity else ""
        series_locked = False
        try:
            if series_lock_key:
                series_locked, _ = cluster_store.acquire_lock(series_lock_key, {
                    "job_id": job_id, "series_identity": series_identity,
                })
                if not series_locked:
                    continue
                # Re-read after obtaining the per-series dispatch mutex so a second
                # Railway cannot claim the next episode from a stale queue snapshot.
                fresh_rows = cluster_store.enterprise_jobs()
                fresh = next((row for row in fresh_rows if str(row.get("id") or "") == job_id), None)
                if not fresh or str(fresh.get("state") or "") != "QUEUED" or not _scheduler_series_ready(fresh, fresh_rows):
                    continue
                remote = fresh
                payload = remote.get("scheduler_payload")
                if not isinstance(payload, dict):
                    continue

            claimed, _ = cluster_store.acquire_lock(f"scheduler-claim:{job_id}", {
                "job_id": job_id, "assigned_worker": cluster_store.worker_id,
            })
            if not claimed:
                continue

            claimed_remote = dict(remote)
            claimed_remote.update({
                "assigned_worker": cluster_store.worker_id,
                "worker_id": cluster_store.worker_id,
                "scheduler_status": "CLAIMED",
                "message": f"Episode sebelumnya berhasil; diproses oleh {cluster_store.worker_id}.",
                "claimed_at": now_ts(),
                "series_sequential": bool(series_identity),
            })
            cluster_store.publish_enterprise_job(claimed_remote)

            work_dir = Path(tempfile.mkdtemp(prefix=f"cinedrive-v14-{job_id}-"))
            restored = dict(payload)
            restored.update({
                "id": job_id, "work_dir": str(work_dir), "state": "QUEUED",
                "message": f"Diambil oleh scheduler {cluster_store.worker_id}.",
                "error": None, "started_at": None, "finished_at": None,
                "downloaded_bytes": 0, "total_bytes": 0, "file_size_bytes": 0,
                "message_id": None, "stage_progress": 0.0, "overall_progress": 0.0,
                "progress_detail": "Diklaim setelah semua episode sebelumnya SUCCESS; menunggu proses lokal.", "eta_seconds": 0, "eta_human": "-",
                "submitted_by": remote.get("submitted_by"), "preferred_worker": remote.get("preferred_worker"),
                "assigned_worker": cluster_store.worker_id, "worker_id": cluster_store.worker_id,
                "scheduler_status": "CLAIMED", "scheduler_payload": payload,
                "scheduler_local_only": False, "series_sequential": bool(series_identity),
            })
            with queue_condition:
                jobs[job_id] = restored
                pending_jobs.append(job_id)
                queue_condition.notify()
                SCHEDULER_WAKE_EVENT.set()
        finally:
            if series_locked and series_lock_key:
                cluster_store.release_lock(series_lock_key)

def _scheduler_force_release_job_locks(remote: dict[str, Any], offline_worker: str) -> None:
    """Remove locks owned by an offline worker so another Railway can claim the job."""
    if not cluster_store.enabled:
        return
    job_id = str(remote.get("id") or "")
    keys = [f"scheduler-claim:{job_id}"] if job_id else []
    payload = remote.get("scheduler_payload") if isinstance(remote.get("scheduler_payload"), dict) else remote
    identity_parts = [
        str(payload.get("target_chat_id") or remote.get("target_chat_id") or CHANNEL_ID),
        str(payload.get("message_thread_id") or remote.get("message_thread_id") or 0),
        str(payload.get("tmdb_id") or payload.get("file_id") or payload.get("title") or job_id),
        str(payload.get("season_number") or remote.get("season_number") or 0),
        str(payload.get("episode_number") or remote.get("episode_number") or 0),
    ]
    keys.append("media:" + hashlib.sha256("|".join(identity_parts).encode("utf-8")).hexdigest()[:32])
    for lock_key in keys:
        try:
            response = requests.delete(
                cluster_store._endpoint("cinedrive_cluster"),
                headers=cluster_store._headers("return=minimal"),
                params={
                    "namespace": f"eq.{cluster_store.namespace}",
                    "record_type": "eq.lock",
                    "record_key": f"eq.{lock_key}",
                    "updated_by": f"eq.{offline_worker}",
                },
                timeout=20,
            )
            response.raise_for_status()
        except Exception as exc:
            cluster_store.last_error = f"failover release {lock_key}: {exc}"


def _scheduler_reassign_orphaned_queued_jobs() -> int:
    """Requeue portable jobs assigned to a worker whose heartbeat expired.

    v16.1 also recovers jobs that were DOWNLOADING/PROCESSING/UPLOADING. Such jobs
    restart from the original Google Drive source on an idle worker. Local-upload
    jobs cannot be moved because their assets exist only on the failed Railway.
    """
    if not (V13_QUEUE_FAILOVER_ENABLED and V161_FAILOVER_ENABLED and SCHEDULER_ENABLED and cluster_store.enabled):
        return 0
    health = _scheduler_worker_health()
    changed = 0
    transferable_states = {"QUEUED"} | (V161_FAILOVER_STATES if V161_FAILOVER_PROCESSING_JOBS else set())
    for remote in _scheduler_latest_rows(cluster_store.enterprise_jobs()):
        state = str(remote.get("state") or "")
        if state not in transferable_states:
            continue
        assigned = str(remote.get("assigned_worker") or remote.get("worker_id") or "").strip()
        if not assigned or assigned == cluster_store.worker_id:
            continue
        info = health.get(assigned)
        if info and info.get("online"):
            continue
        age = (info or {}).get("age_seconds")
        if age is not None and age < (V161_WORKER_OFFLINE_SECONDS + V161_FAILOVER_GRACE_SECONDS):
            continue
        payload = remote.get("scheduler_payload")
        if not isinstance(payload, dict):
            continue
        if bool(remote.get("scheduler_local_only")):
            failed = dict(remote)
            failed.update({
                "state": "ERROR",
                "scheduler_status": "FAILOVER_BLOCKED_LOCAL_ASSET",
                "message": f"Worker {assigned} offline; job tidak dapat dialihkan karena memakai file upload lokal.",
                "error": "Failover membutuhkan sumber Google Drive yang dapat diakses worker lain.",
                "failover_from": assigned,
                "failover_at": now_ts(),
                "finished_at": now_ts(),
            })
            cluster_store.publish_enterprise_job(failed)
            changed += 1
            continue
        _scheduler_force_release_job_locks(remote, assigned)
        recovered = dict(remote)
        recovered.update({
            "state": "QUEUED",
            "assigned_worker": "",
            "worker_id": "",
            "scheduler_status": "FAILOVER_REQUEUED",
            "message": f"Worker {assigned} offline; tugas dikembalikan ke antrean global.",
            "progress_detail": f"Failover otomatis dari {assigned}; menunggu Railway idle.",
            "error": None,
            "started_at": None,
            "finished_at": None,
            "claimed_at": None,
            "stage_progress": 0.0,
            "overall_progress": 0.0,
            "failover_from": assigned,
            "failover_at": now_ts(),
            "failover_count": int(remote.get("failover_count") or 0) + 1,
            "last_failed_state": state,
        })
        cluster_store.publish_enterprise_job(recovered)
        print(f"[FAILOVER] job={remote.get('id')} from={assigned} state={state} requeued", flush=True)
        changed += 1
        SCHEDULER_WAKE_EVENT.set()
    return changed


def _v15_delete_cluster_rows(params: dict[str, str]) -> int:
    if not cluster_store.enabled:
        return 0
    response = requests.delete(
        cluster_store._endpoint("cinedrive_cluster"),
        headers=cluster_store._headers("return=representation"),
        params=params,
        timeout=30,
    )
    response.raise_for_status()
    try:
        rows = response.json()
        return len(rows) if isinstance(rows, list) else 0
    except ValueError:
        return 0


def _v15_cleanup_stale_records() -> dict[str, int]:
    """Delete expired claims and old terminal job history.

    The newest canonical episode row is retained by the scheduler even before
    cleanup, so this maintenance is safe and mainly keeps Supabase compact.
    """
    result = {"claims": 0, "success": 0, "error": 0}
    if not (V15_AUTO_CLEANUP_ENABLED and cluster_store.enabled):
        return result
    now = time.time()
    claim_cutoff = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - SCHEDULER_CLAIM_TTL_SECONDS))
    success_cutoff = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - V15_SUCCESS_RETENTION_SECONDS))
    error_cutoff = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - V15_ERROR_RETENTION_SECONDS))
    try:
        result["claims"] = _v15_delete_cluster_rows({
            "namespace": f"eq.{cluster_store.namespace}",
            "record_type": "eq.lock", "record_key": "like.scheduler-claim:*",
            "updated_at": f"lt.{claim_cutoff}",
        })
        # PostgREST JSON path filters are supported through data->>state.
        result["success"] = _v15_delete_cluster_rows({
            "namespace": f"eq.{cluster_store.namespace}", "bucket": "eq.documents",
            "item_key": "like.enterprise-job:*", "data->>state": "eq.SUCCESS",
            "updated_at": f"lt.{success_cutoff}",
        })
        result["error"] = _v15_delete_cluster_rows({
            "namespace": f"eq.{cluster_store.namespace}", "bucket": "eq.documents",
            "item_key": "like.enterprise-job:*", "data->>state": "eq.ERROR",
            "updated_at": f"lt.{error_cutoff}",
        })
    except Exception as exc:
        cluster_store.last_error = f"v15-cleanup: {exc}"
    return result


def v15_maintenance_worker() -> None:
    while True:
        _v15_cleanup_stale_records()
        time.sleep(V15_CLEANUP_INTERVAL_SECONDS)


def scheduler_worker() -> None:
    while True:
        try:
            _scheduler_reassign_orphaned_queued_jobs()
            _scheduler_claim_remote_jobs()
        except Exception as exc:
            cluster_store.last_error = f"scheduler: {exc}"
        # Event-assisted wakeup for local submissions, with polling fallback for
        # changes made by other Railway workers.
        SCHEDULER_WAKE_EVENT.wait(timeout=SCHEDULER_POLL_SECONDS)
        SCHEDULER_WAKE_EVENT.clear()

def _pipeline_shared_rows() -> list[dict[str, Any]]:
    try:
        return _scheduler_latest_rows(cluster_store.enterprise_jobs())
    except Exception as exc:
        cluster_store.last_error = f"pipeline-read: {exc}"
        return []

def _pipeline_lower_blockers(data: dict[str, Any], job_id: str) -> list[dict[str, Any]]:
    identity = _scheduler_series_identity(data)
    episode = int(data.get("episode_number") or 0)
    if not identity or episode < 1:
        return []
    blockers: list[dict[str, Any]] = []
    for row in _pipeline_shared_rows():
        if str(row.get("id") or "") == job_id:
            continue
        if _scheduler_series_identity(row) != identity:
            continue
        other_episode = int(row.get("episode_number") or 0)
        if not (0 < other_episode < episode):
            continue
        state = str(row.get("state") or "")
        if state != "SUCCESS":
            blockers.append(row)
    blockers.sort(key=lambda row: int(row.get("episode_number") or 0))
    return blockers

def _pipeline_wait_for_upload_turn(job_id: str, data: dict[str, Any]) -> None:
    """Keep an encoded serial episode READY until all lower episodes succeed."""
    if not SMART_PIPELINE_SCHEDULER or not _scheduler_series_identity(data):
        return
    started = time.monotonic()
    while True:
        blockers = _pipeline_lower_blockers(data, job_id)
        if not blockers:
            update_progress(job_id, "READY", 100, message="Giliran upload tersedia.", detail="Episode sebelumnya sudah berhasil; memulai upload Telegram.")
            with queue_lock:
                snap = dict(jobs.get(job_id) or data)
            cluster_store.publish_enterprise_job(snap)
            return
        first = blockers[0]
        ep = int(first.get("episode_number") or 0)
        state = str(first.get("state") or "QUEUED")
        worker = str(first.get("assigned_worker") or first.get("worker_id") or "worker lain")
        if state == "ERROR":
            message = f"Menunggu E{ep:02d} diperbaiki sebelum upload."
        else:
            message = f"Encode selesai; menunggu E{ep:02d} berhasil di {worker}."
        update_progress(job_id, "READY", 50, message=message, detail=f"READY · urutan upload serial dijaga · E{ep:02d}={state}")
        with queue_lock:
            snap = dict(jobs.get(job_id) or data)
        snap.update({"pipeline_status": "READY_WAITING", "waiting_for_episode": ep, "waiting_for_job": first.get("id")})
        cluster_store.publish_enterprise_job(snap)
        if time.monotonic() - started > SMART_PIPELINE_WAIT_TIMEOUT_SECONDS:
            raise RuntimeError(f"Timeout menunggu urutan upload. Episode E{ep:02d} belum berhasil.")
        time.sleep(SMART_PIPELINE_POLL_SECONDS)

def upload_video_with_retry(*args: Any, **kwargs: Any) -> dict[str, Any]:
    last_error: Exception | None = None
    for attempt in range(1, V15_UPLOAD_RETRIES + 1):
        try:
            return upload_video(*args, **kwargs)
        except Exception as exc:
            last_error = exc
            if attempt >= V15_UPLOAD_RETRIES:
                break
            delay = min(30, 3 * attempt)
            print(f"[UPLOAD] attempt {attempt}/{V15_UPLOAD_RETRIES} failed: {exc}; retry in {delay}s", flush=True)
            time.sleep(delay)
    raise RuntimeError(f"Upload Telegram gagal setelah {V15_UPLOAD_RETRIES} percobaan: {last_error}")


def process_job(job_id: str) -> None:
    with queue_lock:
        data = dict(jobs[job_id])
    identity_parts = [
        str(data.get("target_chat_id") or CHANNEL_ID), str(data.get("message_thread_id") or 0),
        str(data.get("tmdb_id") or data.get("file_id") or data.get("title") or job_id),
        str(data.get("season_number") or 0), str(data.get("episode_number") or 0),
    ]
    enterprise_lock_key = "media:" + hashlib.sha256("|".join(identity_parts).encode("utf-8")).hexdigest()[:32]
    lock_ok, lock_info = cluster_store.acquire_lock(enterprise_lock_key, {
        "job_id": job_id, "title": data.get("title"), "episode_number": data.get("episode_number"),
    })
    if not lock_ok:
        owner = str(lock_info.get("owner") or "worker lain")
        set_job(job_id, state="ERROR", message=f"Dibatalkan: konten sedang diproses oleh {owner}.",
                error=f"Distributed lock aktif pada {owner}", finished_at=now_ts(), progress_detail="Mencegah encode/upload ganda.")
        with queue_lock:
            snapshot = dict(jobs.get(job_id) or data)
        cluster_store.publish_enterprise_job(snapshot)
        shutil.rmtree(Path(data["work_dir"]), ignore_errors=True)
        return
    work_dir = Path(data["work_dir"])
    input_path = work_dir / "input.mp4"
    output_path = work_dir / "output.mp4"
    thumb_path = work_dir / "thumbnail.jpg"
    subtitle_path: Path | None = None
    try:
        update_progress(job_id, "DOWNLOADING", 0, message="Mulai mengunduh dari Google Drive.", started_at=now_ts(), error=None)
        download_file(job_id, data["file_id"], input_path)
        size = input_path.stat().st_size
        update_progress(job_id, "PROCESSING", 0, message="Mendeteksi subtitle dan menyiapkan FFmpeg.", file_size_bytes=size, downloaded_bytes=size)

        mode = data["subtitle_mode"]
        if mode == "auto_drive":
            match = find_matching_subtitle(data["file_id"], str(data.get("public_folder_id") or ""))
            subtitle_ext = Path(match["name"]).suffix.lower() or ".srt"
            subtitle_path = work_dir / f"subtitle-auto{subtitle_ext}"
            download_drive_asset(match["id"], subtitle_path)
            subtitle_info = f"Subtitle otomatis Google Drive: {match['name']}"
        elif mode == "upload":
            uploaded = Path(data["uploaded_subtitle"])
            if not uploaded.exists():
                raise RuntimeError("File subtitle upload tidak ditemukan.")
            subtitle_path = uploaded
            subtitle_info = f"Subtitle upload: {uploaded.name}"
        elif mode == "drive":
            subtitle_file_id = str(data.get("subtitle_drive_file_id") or "")
            if not subtitle_file_id:
                raise RuntimeError("Link subtitle Google Drive belum diisi.")
            subtitle_path = work_dir / "subtitle-drive.srt"
            download_drive_asset(subtitle_file_id, subtitle_path)
            subtitle_info = "Subtitle dari Google Drive"
        elif mode == "auto_id":
            pos, subtitle_info = choose_subtitle_stream(ffprobe_streams(input_path))
            if pos is not None:
                subtitle_path = work_dir / "internal.srt"
                extract_internal_subtitle(input_path, pos, subtitle_path)
        else:
            subtitle_info = "Tanpa subtitle"

        data = materialize_global_watermark(data, work_dir)
        with queue_lock:
            if job_id in jobs:
                jobs[job_id].update({
                    "watermark_path": data.get("watermark_path", ""),
                    "watermark_info": data.get("watermark_info", "Tanpa logo"),
                })
        set_job(job_id, subtitle_info=subtitle_info)
        update_progress(job_id, "PROCESSING", 1, message=f"Memproses video. {subtitle_info}", detail=f"{subtitle_info} · {data.get('watermark_info', 'Tanpa logo')}")
        process_video(job_id, input_path, output_path, thumb_path, subtitle_path, data)

        # Serial episodes may be prepared concurrently on different Railway workers.
        # Only the Telegram upload is released in episode order. Films bypass this gate.
        _pipeline_wait_for_upload_turn(job_id, data)

        full_caption = build_caption(
            data["metadata"],
            data.get("extra_caption", ""),
        )
        is_episode = bool(
            data["metadata"].get("episode_code")
        )

        target_chat_id = str(data.get("target_chat_id") or CHANNEL_ID)
        target_thread_id = int(data.get("message_thread_id") or 0)

        if not is_episode:
            update_progress(
                job_id,
                "UPLOADING",
                0,
                message="Mengirim poster dan detail TMDB.",
                detail="Membuat posting informasi film.",
            )
            send_poster(
                data["metadata"],
                full_caption,
                target_chat_id,
                target_thread_id,
            )
            video_caption = str(
                data["metadata"].get("title")
                or data["metadata"].get("original_title")
                or "Film"
            )[:1024]
        else:
            video_caption = full_caption

        update_progress(
            job_id,
            "UPLOADING",
            1,
            message=("Mengunggah video film." if not is_episode else "Mengunggah episode ke Telegram."),
            detail="Memulai koneksi upload.",
        )
        result = upload_video_with_retry(
            job_id,
            output_path,
            thumb_path,
            video_caption,
            target_chat_id,
            target_thread_id,
        )

        episode_message_id = int(
            result["result"]["message_id"]
        )
        index_message_id = 0

        catalog_warning = ""
        if is_episode:
            update_progress(
                job_id,
                "UPLOADING",
                100,
                message="Video episode berhasil; memperbarui katalog serial.",
                detail="Menyimpan message_id sebelum pembaruan katalog.",
                message_id=episode_message_id,
            )
            try:
                index_message_id = create_or_update_series_index(data, episode_message_id)
            except Exception as catalog_exc:
                # The episode upload itself is authoritative. A catalog failure is
                # recorded as a warning and can be repaired without blocking E+1.
                catalog_warning = str(catalog_exc)
                print(f"[CATALOG] warning job={job_id}: {catalog_exc}", flush=True)

        update_progress(
            job_id,
            "SUCCESS",
            100,
            message=(
                ("Episode berhasil dikirim; katalog perlu diperbaiki." if catalog_warning else "Episode berhasil dikirim dan katalog serial diperbarui.")
                if is_episode
                else "Poster dan video berhasil dikirim."
            ),
            detail=(f"Selesai dengan peringatan katalog: {catalog_warning}" if catalog_warning else "Selesai."),
            message_id=episode_message_id,
            index_message_id=index_message_id,
            finished_at=now_ts(),
        )
    except Exception as exc:
        set_job(job_id, state="ERROR", message="Proses gagal.", error=str(exc), finished_at=now_ts(), progress_detail="Proses berhenti karena error.")
    finally:
        with queue_lock:
            snapshot = dict(jobs.get(job_id) or data)
        cluster_store.publish_enterprise_job(snapshot)
        cluster_store.release_lock(enterprise_lock_key)
        shutil.rmtree(work_dir, ignore_errors=True)

def queue_worker() -> None:
    while True:
        with queue_condition:
            while not pending_jobs:
                queue_condition.wait()
            job_id = pending_jobs.popleft()
        process_job(job_id)

def ensure_worker_started() -> None:
    global worker_started
    with queue_lock:
        if not worker_started:
            threading.Thread(target=queue_worker, daemon=True, name="video-queue-worker").start()
            worker_started = True

ensure_worker_started()
threading.Thread(target=scheduler_worker, daemon=True, name="enterprise-scheduler-worker").start()
threading.Thread(target=v15_maintenance_worker, daemon=True, name="v15-maintenance-worker").start()


def human_bytes(value: int) -> str:
    size = float(max(0, value))
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return f"{size:.1f} TB"

def data_file_map() -> dict[str, Path]:
    return {"series": SERIES_STORE_PATH, "topics": TOPIC_STORE_PATH, "scan": SCAN_STORE_PATH}

def safe_json_read(path: Path, default: Any) -> Any:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return default

def atomic_json_write(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)

def create_full_backup(reason: str = "manual") -> Path:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    out = BACKUP_DIR / f"all-data-{stamp}-{reason}.zip"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for kind, path in data_file_map().items():
            if path.exists(): zf.write(path, arcname=path.name)
    return out

def data_management_context(key: str) -> dict[str, Any]:
    # Global Supabase data is authoritative; local files are shown only as cache/backup.
    series = load_series_store()
    topics = load_discovered_topics()
    scans = load_scan_results()
    local_series = safe_json_read(SERIES_STORE_PATH, {})
    local_topics = safe_json_read(TOPIC_STORE_PATH, [])
    episode_count = sum(len((item or {}).get("episodes") or {}) for item in series.values()) if isinstance(series, dict) else 0
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    backups = sorted([p for p in BACKUP_DIR.iterdir() if p.is_file()], key=lambda p:p.stat().st_mtime, reverse=True)
    try: free = human_bytes(shutil.disk_usage(SERIES_STORE_PATH.parent).free)
    except Exception: free = "Tidak tersedia"
    labels={"series":"telegram-series.json","topics":"telegram-topics.json","scan":"telegram-scan-results.json"}
    files=[]
    for kind,path in data_file_map().items():
        raw = path.read_text(encoding="utf-8") if path.exists() else ("{}" if kind=="series" else "[]")
        try: preview=json.dumps(json.loads(raw), ensure_ascii=False, indent=2)
        except Exception: preview=raw
        st=path.stat() if path.exists() else None
        files.append({"kind":kind,"label":labels[kind],"path":str(path),"size":human_bytes(st.st_size if st else 0),"modified":time.strftime("%d-%m-%Y %H:%M:%S",time.localtime(st.st_mtime)) if st else "Belum dibuat","preview":preview[:300000],"download_url":url_for("download_data_file",kind=kind,key=key)})
    backup_items=[]
    for p in backups[:100]:
        st=p.stat(); backup_items.append({"name":p.name,"size":human_bytes(st.st_size),"modified":time.strftime("%d-%m-%Y %H:%M:%S",time.localtime(st.st_mtime))})
    return {
        "data_stats": {
            "series_count": len(series) if isinstance(series, dict) else 0,
            "episode_count": episode_count,
            "topic_count": len(topics) if isinstance(topics, list) else 0,
            "scan_count": len(scans) if isinstance(scans, list) else 0,
            "backup_count": len(backups),
            "free_space": free,
            "database_mode": "Supabase Global" if cluster_store.enabled else "Cache Lokal",
            "fingerprint": _json_fingerprint(series)[:16],
            "local_series_count": len(local_series) if isinstance(local_series, dict) else 0,
            "local_topic_count": len(local_topics) if isinstance(local_topics, list) else 0,
        },
        "data_files": files,
        "backup_files": backup_items,
    }



LANDING_HTML = r"""
<!doctype html>
<html lang="id">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="theme-color" content="#070910">
<title>CINEMAXX1 · CineDrive Studio v10.6.2.2 · Smart Watermark Safe Area</title>
<style>
:root{color-scheme:dark;--bg:#06070b;--panel:rgba(15,17,24,.78);--line:rgba(255,255,255,.11);--gold:#f7c75f;--gold2:#fff1ad;--text:#fff;--muted:#a8acb8;--ok:#43e39f}
*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;min-height:100vh;font-family:Inter,ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif;color:var(--text);background:#06070b;overflow-x:hidden}
body:before{content:"";position:fixed;inset:0;background:radial-gradient(circle at 18% 10%,rgba(247,199,95,.17),transparent 30%),radial-gradient(circle at 82% 22%,rgba(112,71,255,.16),transparent 31%),linear-gradient(180deg,rgba(0,0,0,.12),#06070b 72%);pointer-events:none}
.grid{position:fixed;inset:0;opacity:.18;background-image:linear-gradient(rgba(255,255,255,.035) 1px,transparent 1px),linear-gradient(90deg,rgba(255,255,255,.035) 1px,transparent 1px);background-size:38px 38px;mask-image:linear-gradient(to bottom,#000,transparent 80%)}
.shell{position:relative;z-index:1;width:min(1120px,92%);margin:auto;padding:26px 0 42px}
.top{display:flex;align-items:center;justify-content:space-between;gap:16px}.brand{display:flex;align-items:center;gap:12px;font-weight:900;letter-spacing:.08em}.logo{width:45px;height:45px;border-radius:14px;display:grid;place-items:center;background:linear-gradient(145deg,var(--gold2),#bc7925);color:#160e03;box-shadow:0 10px 30px rgba(247,199,95,.23);font-size:23px}.badge{padding:8px 12px;border:1px solid rgba(67,227,159,.24);background:rgba(67,227,159,.08);border-radius:999px;color:#91f4c8;font-size:12px;font-weight:800}.dot{display:inline-block;width:7px;height:7px;border-radius:50%;background:var(--ok);box-shadow:0 0 12px var(--ok);margin-right:7px}
.hero{min-height:66vh;display:grid;grid-template-columns:1.2fr .8fr;align-items:center;gap:46px;padding:70px 0 44px}.eyebrow{color:var(--gold);font-size:12px;font-weight:900;letter-spacing:.2em;text-transform:uppercase}.hero h1{font-size:clamp(45px,8vw,88px);line-height:.93;letter-spacing:-.065em;margin:16px 0 22px;max-width:760px}.hero h1 span{background:linear-gradient(100deg,#fff 10%,var(--gold2) 55%,#d79b36);background-clip:text;-webkit-background-clip:text;color:transparent}.lead{font-size:clamp(16px,2vw,20px);line-height:1.7;color:var(--muted);max-width:680px;margin:0 0 30px}
.actions{display:flex;flex-wrap:wrap;gap:12px}.btn{display:inline-flex;align-items:center;justify-content:center;gap:9px;min-height:50px;padding:0 19px;border-radius:14px;text-decoration:none;font-weight:850;border:1px solid var(--line);color:#fff;background:rgba(255,255,255,.05);transition:.2s}.btn:hover{transform:translateY(-2px);border-color:rgba(247,199,95,.5)}.btn.primary{color:#1d1303;border:0;background:linear-gradient(135deg,var(--gold2),#d58b26);box-shadow:0 13px 35px rgba(213,139,38,.22)}
.login{padding:23px;border:1px solid var(--line);border-radius:24px;background:linear-gradient(145deg,rgba(24,25,33,.9),rgba(10,11,16,.83));box-shadow:0 30px 80px rgba(0,0,0,.35);backdrop-filter:blur(18px)}.login h2{margin:0 0 7px;font-size:23px}.login p{color:var(--muted);font-size:14px;line-height:1.55;margin:0 0 18px}.login label{display:block;font-size:12px;font-weight:800;color:#d7d8de;margin:0 0 7px}.login input{width:100%;border:1px solid var(--line);background:#090a0f;color:#fff;padding:14px;border-radius:13px;outline:none;font:inherit}.login input:focus{border-color:var(--gold);box-shadow:0 0 0 4px rgba(247,199,95,.09)}.login button{width:100%;margin-top:12px;border:0;border-radius:13px;padding:14px;font:inherit;font-weight:900;color:#1a1103;background:linear-gradient(135deg,var(--gold2),#d58b26);cursor:pointer}.tiny{margin-top:13px!important;font-size:11px!important;color:#737784!important}
.stats{display:grid;grid-template-columns:repeat(4,1fr);gap:13px;margin:18px 0 44px}.stat{padding:18px;border:1px solid var(--line);border-radius:18px;background:rgba(255,255,255,.035);backdrop-filter:blur(10px)}.stat b{display:block;font-size:26px;margin-top:7px}.stat span{font-size:12px;color:var(--muted);font-weight:750}.features{display:grid;grid-template-columns:repeat(3,1fr);gap:14px}.feature{padding:22px;border-radius:20px;border:1px solid var(--line);background:rgba(255,255,255,.03)}.feature i{font-style:normal;font-size:26px}.feature h3{margin:13px 0 7px}.feature p{margin:0;color:var(--muted);line-height:1.6;font-size:14px}.foot{display:flex;justify-content:space-between;gap:20px;color:#737784;font-size:12px;margin-top:44px;padding-top:22px;border-top:1px solid var(--line)}
@media(max-width:820px){.hero{grid-template-columns:1fr;padding:52px 0 30px;gap:28px}.hero h1{font-size:55px}.stats{grid-template-columns:repeat(2,1fr)}.features{grid-template-columns:1fr}.top .brand-name{font-size:13px}}
@media(max-width:480px){.shell{width:91%}.hero h1{font-size:46px}.stats{gap:9px}.stat{padding:14px}.stat b{font-size:22px}.foot{flex-direction:column}.badge{font-size:10px;padding:7px 9px}}
</style>
</head>
<body><div class="grid"></div><main class="shell">
<header class="top"><div class="brand"><div class="logo">🎬</div><div class="brand-name">CINEMAXX1</div></div><div class="badge"><span class="dot"></span>SERVER ONLINE</div></header>
<section class="hero"><div><div class="eyebrow">Google Drive → Telegram</div><h1>CineDrive <span>Studio</span></h1><p class="lead">Kelola film, serial, subtitle, watermark, encoding 1080p, antrean, dan publikasi Telegram dari satu dashboard.</p><div class="actions"><a class="btn primary" href="#login">Buka Panel Admin →</a><a class="btn" href="/health">Status API</a></div></div>
<div class="login" id="login"><h2>Masuk ke panel</h2><p>Masukkan SECRET_KEY Railway untuk membuka dashboard pengelolaan.</p><form method="get" action="/panel"><label>SECRET KEY</label><input type="password" name="key" autocomplete="current-password" placeholder="Masukkan kunci akses" required><button type="submit">Masuk ke Dashboard</button></form><p class="tiny">Kunci dipakai untuk autentikasi panel dan tidak disimpan oleh halaman ini.</p></div></section>
<section class="stats"><div class="stat"><span>SERIAL TERSIMPAN</span><b>{{ stats.series }}</b></div><div class="stat"><span>TOTAL EPISODE</span><b>{{ stats.episodes }}</b></div><div class="stat"><span>ANTREAN AKTIF</span><b>{{ stats.active_jobs }}</b></div><div class="stat"><span>VERSI APLIKASI</span><b>10.6</b></div></section>
<section class="features"><article class="feature"><i>🎞️</i><h3>Encoding Telegram</h3><p>H.265 hemat ukuran dengan fallback H.264 dan target hasil di bawah 1,5 GB.</p></article><article class="feature"><i>📺</i><h3>Pengelolaan Serial</h3><p>Tambah episode, perbarui posting utama, pulihkan data, dan kelola tombol episode.</p></article><article class="feature"><i>🗄️</i><h3>Data Permanen</h3><p>Backup, ekspor, impor, dan pemulihan data yang tersimpan pada Railway Volume.</p></article></section>
<footer class="foot"><span>© 2026 CINEMAXX1</span><span>CineDrive Studio v10.6.2.2 · Smart Watermark Safe Area · Railway</span></footer>
</main></body></html>
"""

@app.get("/")
def home():
    series = load_series_store()
    series_count = len(series) if isinstance(series, dict) else 0
    episode_count = sum(
        len((item or {}).get("episodes") or {})
        for item in series.values()
    ) if isinstance(series, dict) else 0
    with queue_lock:
        active_jobs = sum(
            1 for item in jobs.values()
            if item.get("state") in {"QUEUED", "DOWNLOADING", "PROCESSING", "PREPARING", "READY", "UPLOADING"}
        )
    return render_template_string(
        LANDING_HTML,
        stats={
            "series": series_count,
            "episodes": episode_count,
            "active_jobs": active_jobs,
        },
    )

@app.get("/bot-status")
def bot_status():
    bots = []
    for index, token in enumerate(BOT_TOKENS, start=1):
        identity = get_bot_identity(token)
        bots.append({
            "index": index,
            "active": token == ACTIVE_BOT_TOKEN,
            "id": identity.get("id", 0),
            "username": identity.get("username", ""),
            "first_name": identity.get("first_name", ""),
            "error": identity.get("error", ""),
        })
    return jsonify({
        "success": True,
        "version": CLUSTER_VERSION,
        "worker_id": cluster_store.worker_id,
        "configured_bot_count": len(BOT_TOKENS),
        "active_bot_index": ACTIVE_BOT_INDEX + 1,
        "active_bot": get_bot_identity(ACTIVE_BOT_TOKEN),
        "bots": bots,
    })


@app.get("/health")
def health():
    return jsonify({"success": True, "status": "ok", "version": CLUSTER_VERSION, "cluster_enabled": cluster_store.enabled, "active_bot": get_bot_identity(ACTIVE_BOT_TOKEN), "configured_bot_count": len(BOT_TOKENS)})

@app.get("/global-sync-status")
def global_sync_status():
    series = load_series_store()
    topics = load_discovered_topics()
    scans = load_scan_results()
    episode_count = sum(len(item.get("episodes") or {}) for item in series.values() if isinstance(item, dict))
    return jsonify({
        "success": True,
        "version": CLUSTER_VERSION,
        "global_sync_enabled": GLOBAL_SYNC_ENABLED and cluster_store.enabled,
        "namespace": cluster_store.namespace,
        "worker_id": cluster_store.worker_id,
        "series_count": len(series),
        "episode_count": episode_count,
        "topic_count": len(topics),
        "scan_count": len(scans),
        "series_fingerprint": _json_fingerprint(series),
        "database_mode": "supabase-canonical" if cluster_store.enabled else "local-fallback",
        "canonical_key": GLOBAL_DATABASE_CANONICAL_KEY,
        "legacy_source_count": len(cluster_store.list_documents(GLOBAL_DATABASE_SOURCE_PREFIX)) if cluster_store.enabled else 0,
        "last_error": cluster_store.last_error,
    })


@app.post("/global-sync")
def global_sync_now():
    series = load_series_store()
    topics = load_discovered_topics()
    scans = load_scan_results()
    return jsonify({
        "success": True,
        "version": CLUSTER_VERSION,
        "namespace": cluster_store.namespace,
        "worker_id": cluster_store.worker_id,
        "series_count": len(series),
        "topic_count": len(topics),
        "scan_count": len(scans),
        "fingerprint": _json_fingerprint(series),
    })


@app.post("/global-database-converge")
def global_database_converge():
    if not authorized():
        return jsonify({"success": False, "error": "Unauthorized"}), 401
    series = _read_global_series_database(_read_local_series_store())
    return jsonify({
        "success": True,
        "version": CLUSTER_VERSION,
        "namespace": cluster_store.namespace,
        "worker_id": cluster_store.worker_id,
        "series_count": len(series),
        "episode_count": sum(len(x.get("episodes") or {}) for x in series.values() if isinstance(x, dict)),
        "series_fingerprint": _json_fingerprint(series),
        "legacy_source_count": len(cluster_store.list_documents(GLOBAL_DATABASE_SOURCE_PREFIX)),
    })


@app.post("/global-database-cleanup")
def cleanup_global_database():
    if not authorized():
        return jsonify({"success": False, "error": "Unauthorized"}), 401
    try:
        canonical_raw = cluster_store.get_document(GLOBAL_DATABASE_CANONICAL_KEY, {}) if cluster_store.enabled else _read_local_series_store()
        cleaned = _normalize_series_store(canonical_raw)
        if cluster_store.enabled:
            cluster_store.save_document(GLOBAL_DATABASE_CANONICAL_KEY, cleaned, merge=False)
            cleaned = _normalize_series_store(cluster_store.get_document(GLOBAL_DATABASE_CANONICAL_KEY, cleaned))
        atomic_json_write(SERIES_STORE_PATH, cleaned)
        msg = f"Database global dibersihkan: {len(cleaned)} serial, fingerprint {_json_fingerprint(cleaned)[:16]}."
    except Exception as exc:
        msg = f"Pembersihan database gagal: {exc}"
    return redirect(url_for("panel", key=request.args.get("key", ""), data_message=msg) + "#dataSection")


@app.get("/scheduler-dashboard-data")
def scheduler_dashboard_data():
    if not authorized():
        return jsonify({"success": False, "error": "Unauthorized"}), 401
    local = get_jobs_snapshot()
    shared = cluster_store.enterprise_jobs() if cluster_store.enabled else []
    merged: dict[str, dict[str, Any]] = {}
    for item in shared + local:
        if not isinstance(item, dict):
            continue
        job_id = str(item.get("id") or item.get("job_id") or "")
        if not job_id:
            continue
        previous = merged.get(job_id) or {}
        # Prefer the record with the newest timestamps/progress, while preserving useful fields.
        candidate = dict(previous)
        candidate.update({k: v for k, v in item.items() if v not in (None, "")})
        merged[job_id] = candidate
    rows = list(merged.values())
    rows.sort(key=lambda x: float(x.get("finished_at") or x.get("started_at") or x.get("created_at") or x.get("updated_at") or 0), reverse=True)
    active_states = {"QUEUED", "ASSIGNED", "CLAIMED", "DOWNLOADING", "PROCESSING", "UPLOADING"}
    for item in rows:
        item.setdefault("job_id", item.get("id"))
        item.setdefault("assigned_worker", item.get("worker_id"))
        item.setdefault("bot_username", (item.get("bot_identity") or {}).get("username") if isinstance(item.get("bot_identity"), dict) else "")
    worker_rows = cluster_store.workers() if cluster_store.enabled else []
    worker_statuses: list[dict[str, Any]] = []
    now_epoch = time.time()
    online_threshold = max(60, int(os.getenv("WORKER_OFFLINE_SECONDS", "90") or "90"))
    for raw_worker in worker_rows:
        if not isinstance(raw_worker, dict):
            continue
        worker = dict(raw_worker)
        worker_id = str(worker.get("worker_id") or worker.get("updated_by") or "").strip()
        last_seen = str(worker.get("last_seen") or worker.get("updated_at") or "")
        last_seen_epoch = 0.0
        try:
            last_seen_epoch = time.mktime(time.strptime(last_seen[:19], "%Y-%m-%dT%H:%M:%S"))
        except Exception:
            last_seen_epoch = 0.0
        age_seconds = int(max(0, now_epoch - last_seen_epoch)) if last_seen_epoch else 999999
        online = age_seconds <= online_threshold
        current_jobs = [
            job for job in rows
            if str(job.get("assigned_worker") or job.get("worker_id") or "") == worker_id
            and str(job.get("state") or "") in active_states
        ]
        worker_statuses.append({
            "worker_id": worker_id or "unknown-worker",
            "hostname": worker.get("hostname") or "-",
            "version": worker.get("version") or "-",
            "last_seen": last_seen,
            "age_seconds": age_seconds,
            "online": online,
            "status": "ONLINE" if online else "OFFLINE",
            "active_job_count": len(current_jobs),
            "active_job_title": str((current_jobs[0] if current_jobs else {}).get("title") or ""),
            "cpu_count": ((worker.get("metadata") or {}).get("cpu_count") if isinstance(worker.get("metadata"), dict) else None),
        })
    worker_statuses.sort(key=lambda item: (not item["online"], item["worker_id"]))
    return jsonify({
        "success": True, "version": CLUSTER_VERSION, "namespace": cluster_store.namespace,
        "worker_id": cluster_store.worker_id,
        "worker_count": len(worker_statuses),
        "online_worker_count": sum(1 for item in worker_statuses if item["online"]),
        "offline_worker_count": sum(1 for item in worker_statuses if not item["online"]),
        "worker_offline_seconds": online_threshold,
        "worker_statuses": worker_statuses,
        "active_count": sum(1 for x in rows if str(x.get("state")) in active_states),
        "queued_count": sum(1 for x in rows if str(x.get("state")) in {"QUEUED", "ASSIGNED", "CLAIMED"}),
        "success_count": sum(1 for x in rows if str(x.get("state")) == "SUCCESS"),
        "error_count": sum(1 for x in rows if str(x.get("state")) == "ERROR"),
        "jobs": rows[:100], "last_error": cluster_store.last_error,
    })


@app.get("/v14-status")
def v14_status():
    rows = cluster_store.enterprise_jobs() if cluster_store.enabled else []
    pipeline_states = {"DOWNLOADING", "PROCESSING", "PREPARING", "READY", "UPLOADING"}
    return jsonify({
        "success": True,
        "version": "14.2.0",
        "worker_id": cluster_store.worker_id,
        "smart_pipeline_scheduler": SMART_PIPELINE_SCHEDULER,
        "serial_pipeline_only": True,
        "film_scheduler_mode": "global-parallel",
        "pipeline_poll_seconds": SMART_PIPELINE_POLL_SECONDS,
        "active_pipeline_jobs": [row for row in rows if str(row.get("state") or "") in pipeline_states][:50],
        "ready_count": sum(1 for row in rows if str(row.get("state") or "") == "READY"),
        "last_error": cluster_store.last_error,
    })

@app.get("/scheduler-status")
def scheduler_status():
    shared = cluster_store.enterprise_jobs() if cluster_store.enabled else []
    active_states = {"QUEUED", "DOWNLOADING", "PROCESSING", "PREPARING", "READY", "UPLOADING"}
    workers = _scheduler_worker_ids()
    load = {wid: 0 for wid in workers}
    for item in shared:
        if str(item.get("state")) in active_states:
            wid = str(item.get("assigned_worker") or item.get("worker_id") or "")
            if wid:
                load[wid] = load.get(wid, 0) + 1
    return jsonify({
        "success": True, "version": CLUSTER_VERSION,
        "scheduler_enabled": SCHEDULER_ENABLED and ENTERPRISE_CLUSTER_ENABLED and cluster_store.enabled,
        "series_sequential_scheduler": SERIES_SEQUENTIAL_SCHEDULER,
        "smart_pipeline_scheduler": SMART_PIPELINE_SCHEDULER,
        "pipeline_poll_seconds": SMART_PIPELINE_POLL_SECONDS,
        "namespace": cluster_store.namespace, "worker_id": cluster_store.worker_id,
        "poll_seconds": SCHEDULER_POLL_SECONDS, "max_jobs_per_worker": SCHEDULER_MAX_JOBS_PER_WORKER,
        "workers": workers, "worker_load": load,
        "queued_jobs": [j for j in shared if str(j.get("state")) == "QUEUED"][:50],
        "active_jobs": [j for j in shared if str(j.get("state")) in active_states][:50],
        "last_error": cluster_store.last_error,
    })


@app.get("/enterprise-status")
def enterprise_status():
    local_jobs = get_jobs_snapshot()
    shared_jobs = cluster_store.enterprise_jobs()
    workers = cluster_store.workers() if cluster_store.enabled else []
    active_states = {"QUEUED", "DOWNLOADING", "PROCESSING", "PREPARING", "READY", "UPLOADING"}
    return jsonify({
        "success": True,
        "version": CLUSTER_VERSION,
        "enterprise_cluster_enabled": ENTERPRISE_CLUSTER_ENABLED and cluster_store.enabled,
        "namespace": cluster_store.namespace,
        "worker_id": cluster_store.worker_id,
        "configured_bot_count": len(BOT_TOKENS),
        "active_bot": get_bot_identity(ACTIVE_BOT_TOKEN),
        "worker_count": len(workers),
        "local_job_count": len(local_jobs),
        "shared_job_count": len(shared_jobs),
        "active_shared_jobs": [j for j in shared_jobs if str(j.get("state")) in active_states],
        "recent_shared_jobs": shared_jobs[:25],
        "lock_ttl_seconds": ENTERPRISE_LOCK_TTL_SECONDS,
        "last_error": cluster_store.last_error,
    })

@app.get("/v13-status")
def v13_status():
    shared_jobs = cluster_store.enterprise_jobs() if cluster_store.enabled else []
    active_states = {"QUEUED", "DOWNLOADING", "PROCESSING", "PREPARING", "READY", "UPLOADING"}
    canonical = load_series_store()
    return jsonify({
        "success": True, "version": CLUSTER_VERSION,
        "mode": "enterprise-canonical-scheduler",
        "namespace": cluster_store.namespace, "worker_id": cluster_store.worker_id,
        "worker_count": len(_scheduler_worker_ids()),
        "active_bot": get_bot_identity(ACTIVE_BOT_TOKEN),
        "catalog_bot": get_bot_identity(CATALOG_BOT_TOKEN),
        "configured_bot_count": len(BOT_TOKENS),
        "series_count": len(canonical),
        "episode_count": sum(len((x.get("episodes") or {})) for x in canonical.values() if isinstance(x, dict)),
        "series_fingerprint": _json_fingerprint(canonical),
        "scheduler_enabled": SCHEDULER_ENABLED and cluster_store.enabled,
        "queue_failover_enabled": V13_QUEUE_FAILOVER_ENABLED,
        "active_jobs": [j for j in shared_jobs if str(j.get("state")) in active_states][:V13_MAX_RECENT_JOBS],
        "recent_jobs": shared_jobs[:V13_MAX_RECENT_JOBS],
        "last_error": cluster_store.last_error,
    })


@app.get("/cluster-status")
def cluster_status():
    return jsonify(cluster_store.status())

@app.get("/v16.1-status")
@app.get("/v16-status")
@app.get("/v15-status")
def v15_status():
    rows = _scheduler_latest_rows(cluster_store.enterprise_jobs()) if cluster_store.enabled else []
    return jsonify({
        "success": True,
        "version": CLUSTER_VERSION,
        "mode": "enterprise-stable-event-assisted",
        "worker_id": cluster_store.worker_id,
        "worker_count": len(_scheduler_worker_ids()),
        "scheduler_enabled": SCHEDULER_ENABLED,
        "series_sequential": SERIES_SEQUENTIAL_SCHEDULER,
        "smart_pipeline": SMART_PIPELINE_SCHEDULER,
        "event_assisted": True,
        "auto_recovery": V15_AUTO_RECOVERY_ENABLED,
        "automatic_failover": V161_FAILOVER_ENABLED,
        "failover_processing_jobs": V161_FAILOVER_PROCESSING_JOBS,
        "worker_offline_seconds": V161_WORKER_OFFLINE_SECONDS,
        "failover_grace_seconds": V161_FAILOVER_GRACE_SECONDS,
        "auto_cleanup": V15_AUTO_CLEANUP_ENABLED,
        "upload_retries": V15_UPLOAD_RETRIES,
        "canonical_job_count": len(rows),
        "queued_count": sum(1 for row in rows if str(row.get("state") or "") == "QUEUED"),
        "active_count": sum(1 for row in rows if str(row.get("state") or "") in {"CLAIMED", "DOWNLOADING", "PROCESSING", "PREPARING", "READY", "UPLOADING"}),
        "central_config": CENTRAL_CONFIG_STATUS,
        "last_error": cluster_store.last_error,
    })


@app.route("/cluster-heartbeat", methods=["GET", "POST"])
def cluster_heartbeat():
    ok = cluster_store.heartbeat()
    status = cluster_store.status()
    status["heartbeat_requested"] = True
    status["heartbeat_ok"] = ok and bool(status.get("heartbeat_ok"))
    return jsonify(status), (200 if status["heartbeat_ok"] else 503)

@app.get("/cluster-workers")
def cluster_workers():
    status = cluster_store.status()
    return jsonify({"success": True, "enabled": status["enabled"], "namespace": status["namespace"], "workers": status["workers"]})

@app.post("/cluster-sync")
def cluster_sync():
    if not authorized():
        return jsonify({"success": False, "error": "Unauthorized"}), 401
    # Membaca data remote lalu menulis cache lokal melalui fungsi penyimpanan normal.
    series = load_series_store()
    topics = load_discovered_topics()
    scans = load_scan_results()
    save_series_store(series, reason="cluster-sync")
    save_discovered_topics(topics)
    save_scan_results(scans)
    return jsonify({"success": True, "series": len(series), "topics": len(topics), "scan_results": len(scans), "cluster": cluster_store.status()})

@app.get("/panel")
def panel():
    if not authorized():
        return jsonify({"success": False, "error": "Unauthorized"}), 401
    key = request.args.get("key", "")
    query = request.args.get("q", "").strip()
    results = search_tmdb(query) if query else []
    return render_template_string(
        PANEL_HTML,
        key=key,
        query=query,
        results=results,
        panel_url=url_for("panel"),
        enqueue_url=url_for("enqueue", key=key),
        batch_enqueue_url=url_for("batch_enqueue", key=key),
        manual_enqueue_url=url_for("manual_enqueue", key=key),
        add_saved_episode_url=url_for("add_saved_episode", key=key),
        restore_series_url=url_for("restore_series", key=key),
        scan_series_url=url_for("scan_series_bot_api", key=key),
        restore_scanned_series_url=url_for("restore_scanned_series", key=key),
        scan_series_results=load_scan_results(),
        saved_series=saved_series_options(),
        storage=storage_status(),
        default_chat_id=CHANNEL_ID,
        scan_url=url_for("scan_topics", key=key),
        test_telegram_url=url_for("test_telegram_api", key=key),
        status_url=url_for("api_jobs", key=key),
        scheduler_dashboard_url=url_for("scheduler_dashboard_data", key=key),
        max_queue=MAX_QUEUE,
        topic_options=get_topic_options(),
        scan_message=request.args.get("scan_message", ""),
        data_message=request.args.get("data_message", ""),
        export_data_url=url_for("export_all_data", key=key),
        create_backup_url=url_for("create_data_backup", key=key),
        clear_scan_url=url_for("clear_scan_data", key=key),
        import_json_url=url_for("import_json_data", key=key),
        import_zip_url=url_for("import_zip_data", key=key),
        restore_backup_url=url_for("restore_data_backup", key=key),
        delete_backup_url=url_for("delete_data_backup", key=key),
        cleanup_global_url=url_for("cleanup_global_database", key=key),
        **data_management_context(key),
    )


@app.get("/data/download/<kind>")
def download_data_file(kind: str):
    if not authorized(): return jsonify({"success":False,"error":"Unauthorized"}),401
    path=data_file_map().get(kind)
    if not path: return jsonify({"success":False,"error":"Jenis file tidak valid."}),400
    if not path.exists(): atomic_json_write(path, {} if kind=="series" else [])
    return send_file(path, as_attachment=True, download_name=path.name, mimetype="application/json")

@app.get("/data/export")
def export_all_data():
    if not authorized(): return jsonify({"success":False,"error":"Unauthorized"}),401
    memory=io.BytesIO()
    with zipfile.ZipFile(memory,"w",zipfile.ZIP_DEFLATED) as zf:
        for _,path in data_file_map().items():
            if path.exists(): zf.write(path,arcname=path.name)
        if BACKUP_DIR.exists():
            for p in BACKUP_DIR.iterdir():
                if p.is_file(): zf.write(p,arcname=f"backups/{p.name}")
    memory.seek(0)
    return send_file(memory,as_attachment=True,download_name=f"cinedrive-data-{time.strftime('%Y%m%d-%H%M%S')}.zip",mimetype="application/zip")

@app.post("/data/backup")
def create_data_backup():
    if not authorized(): return jsonify({"success":False,"error":"Unauthorized"}),401
    path=create_full_backup("manual")
    return redirect(url_for("panel",key=request.args.get("key",""),data_message=f"Backup dibuat: {path.name}")+"#dataSection")

@app.post("/data/import-json")
def import_json_data():
    if not authorized(): return jsonify({"success":False,"error":"Unauthorized"}),401
    kind=request.form.get("kind",""); target=data_file_map().get(kind); upload=request.files.get("json_file")
    if not target or not upload or not upload.filename: return jsonify({"success":False,"error":"File atau jenis data tidak valid."}),400
    try:
        raw=upload.read(16*1024*1024+1)
        if len(raw)>16*1024*1024: raise ValueError("File JSON melebihi 16 MB.")
        data=json.loads(raw.decode("utf-8-sig"))
        if kind=="series" and not isinstance(data,dict): raise ValueError("Data series harus berupa object JSON.")
        if kind in {"topics","scan"} and not isinstance(data,list): raise ValueError("Data topic/scan harus berupa array JSON.")
        create_full_backup("before-import")
        if kind == "series":
            save_series_store(data, reason="import-global")
        elif kind == "topics":
            save_discovered_topics(data)
        else:
            save_scan_results(data)
        msg=f"{target.name} berhasil dipulihkan ke database global."
    except Exception as exc: msg=f"Import gagal: {exc}"
    return redirect(url_for("panel",key=request.args.get("key",""),data_message=msg)+"#dataSection")

@app.post("/data/import-zip")
def import_zip_data():
    if not authorized(): return jsonify({"success":False,"error":"Unauthorized"}),401
    upload=request.files.get("zip_file")
    if not upload or not upload.filename: return jsonify({"success":False,"error":"Pilih file ZIP."}),400
    try:
        raw=upload.read(64*1024*1024+1)
        if len(raw)>64*1024*1024: raise ValueError("ZIP melebihi 64 MB.")
        create_full_backup("before-zip-import")
        names={p.name:(kind,p) for kind,p in data_file_map().items()}
        restored=[]
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            for info in zf.infolist():
                base=Path(info.filename).name
                if base not in names or info.is_dir(): continue
                if info.file_size>16*1024*1024: raise ValueError(f"{base} terlalu besar.")
                kind,target=names[base]; data=json.loads(zf.read(info).decode("utf-8-sig"))
                if kind=="series" and not isinstance(data,dict): raise ValueError(f"{base} tidak valid.")
                if kind!="series" and not isinstance(data,list): raise ValueError(f"{base} tidak valid.")
                atomic_json_write(target,data); restored.append(base)
        if not restored: raise ValueError("ZIP tidak berisi file data yang dikenali.")
        msg="Berhasil memulihkan: "+", ".join(restored)
    except Exception as exc: msg=f"Import ZIP gagal: {exc}"
    return redirect(url_for("panel",key=request.args.get("key",""),data_message=msg)+"#dataSection")

@app.post("/data/clear-scan")
def clear_scan_data():
    if not authorized(): return jsonify({"success":False,"error":"Unauthorized"}),401
    create_full_backup("before-clear-scan"); save_scan_results([])
    return redirect(url_for("panel",key=request.args.get("key",""),data_message="Hasil scan berhasil dibersihkan.")+"#dataSection")

def safe_backup_path(name: str) -> Path:
    if not name or Path(name).name!=name: raise ValueError("Nama backup tidak valid.")
    path=(BACKUP_DIR/name).resolve(); root=BACKUP_DIR.resolve()
    if root not in path.parents: raise ValueError("Path backup tidak valid.")
    return path

@app.post("/data/restore-backup")
def restore_data_backup():
    if not authorized(): return jsonify({"success":False,"error":"Unauthorized"}),401
    try:
        path=safe_backup_path(request.form.get("name",""))
        if not path.exists(): raise ValueError("Backup tidak ditemukan.")
        create_full_backup("before-restore")
        if path.suffix.lower()==".zip":
            with zipfile.ZipFile(path) as zf:
                names={p.name:(kind,p) for kind,p in data_file_map().items()}
                restored=[]
                for info in zf.infolist():
                    base=Path(info.filename).name
                    if base in names and not info.is_dir():
                        kind,target=names[base]; data=json.loads(zf.read(info).decode("utf-8-sig")); atomic_json_write(target,data); restored.append(base)
            if not restored: raise ValueError("ZIP tidak berisi data yang dikenali.")
        elif path.name.startswith("telegram-series-"):
            atomic_json_write(SERIES_STORE_PATH,json.loads(path.read_text(encoding="utf-8")))
        else: raise ValueError("Jenis backup tidak didukung.")
        msg=f"Backup {path.name} berhasil dipulihkan."
    except Exception as exc: msg=f"Restore gagal: {exc}"
    return redirect(url_for("panel",key=request.args.get("key",""),data_message=msg)+"#dataSection")

@app.post("/data/delete-backup")
def delete_data_backup():
    if not authorized(): return jsonify({"success":False,"error":"Unauthorized"}),401
    try:
        path=safe_backup_path(request.form.get("name","")); path.unlink(missing_ok=True); msg=f"Backup {path.name} dihapus."
    except Exception as exc: msg=f"Hapus backup gagal: {exc}"
    return redirect(url_for("panel",key=request.args.get("key",""),data_message=msg)+"#dataSection")

@app.post("/test-telegram-api")
def test_telegram_api():
    if not authorized():
        return jsonify({"success": False, "error": "Unauthorized"}), 401
    key = request.args.get("key", "")
    result = test_telegram_connections()
    parts = []
    for item in result["results"]:
        status = "OK" if item["ok"] else "GAGAL"
        detail = f"@{item['bot']}" if item.get("bot") else item.get("error", "")
        parts.append(f"{status} {item['base']} ({item['latency_ms']} ms) {detail}".strip())
    message = "Tes Bot API: " + " | ".join(parts)
    return redirect(url_for("panel", key=key, scan_message=message))

@app.post("/scan-topics")
def scan_topics():
    if not authorized():
        return jsonify({"success": False, "error": "Unauthorized"}), 401

    key = request.args.get("key", "")
    try:
        result = scan_recent_topics()
        message = (
            f"Scan selesai: {result['updates_count']} update dibaca, "
            f"{result['topics_count']} tujuan/topic tersimpan."
        )
    except Exception as exc:
        message = f"Scan gagal: {exc}"

    return redirect(
        url_for("panel", key=key, scan_message=message)
    )



def parse_batch_episode_lines(
    raw: str,
    subtitle_mode: str,
) -> list[dict[str, Any]]:
    episodes: list[dict[str, Any]] = []
    seen: set[int] = set()

    for line_number, raw_line in enumerate(raw.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue

        parts = [part.strip() for part in line.split("|")]
        if len(parts) < 2:
            raise ValueError(
                f"Baris {line_number}: format harus episode|link_video|link_subtitle."
            )

        try:
            episode_number = int(parts[0])
        except ValueError as exc:
            raise ValueError(
                f"Baris {line_number}: nomor episode tidak valid."
            ) from exc

        if episode_number < 1:
            raise ValueError(
                f"Baris {line_number}: episode minimal 1."
            )
        if episode_number in seen:
            raise ValueError(
                f"Episode {episode_number} ditulis lebih dari sekali."
            )

        video_file_id = extract_drive_file_id(parts[1])
        subtitle_file_id = ""

        if len(parts) >= 3 and parts[2]:
            subtitle_file_id = extract_drive_file_id(parts[2])

        if subtitle_mode == "drive" and not subtitle_file_id:
            raise ValueError(
                f"Baris {line_number}: mode subtitle Drive memerlukan link subtitle."
            )

        episodes.append({
            "episode_number": episode_number,
            "video_file_id": video_file_id,
            "subtitle_file_id": subtitle_file_id,
        })
        seen.add(episode_number)

    if not episodes:
        raise ValueError("Daftar episode masih kosong.")

    return episodes


@app.post("/batch-enqueue")
def batch_enqueue():
    if not authorized():
        return jsonify({"success": False, "error": "Unauthorized"}), 401

    try:
        tmdb_id = int(request.form.get("tmdb_id", "0"))
        season_number = int(request.form.get("season_number", "1") or "1")
        subtitle_mode = request.form.get(
            "batch_subtitle_mode", "none"
        ).strip()
        extra_caption = request.form.get(
            "batch_extra_caption", ""
        ).strip()
        episode_lines = request.form.get(
            "episode_lines", ""
        )

        if season_number < 0:
            raise ValueError("Season tidak valid.")
        if subtitle_mode not in {"none", "drive", "auto_id", "auto_drive"}:
            raise ValueError("Mode subtitle batch tidak valid.")

        topic_target = request.form.get(
            "topic_target",
            f"{CHANNEL_ID}|{DEFAULT_THREAD_ID}",
        )
        if "|" in topic_target:
            target_chat_id, thread_value = topic_target.rsplit("|", 1)
        else:
            target_chat_id, thread_value = CHANNEL_ID, topic_target
        message_thread_id = int(thread_value or "0")

        public_folder_input = str(request.form.get("batch_public_folder_input") or "").strip()
        public_folder_id = extract_drive_folder_id(public_folder_input) if subtitle_mode == "auto_drive" else ""

        batch_items = parse_batch_episode_lines(
            episode_lines,
            subtitle_mode,
        )
        batch_logo_dir = Path(tempfile.mkdtemp(prefix="watermark-batch-v10-6-2-2-"))
        try:
            batch_watermark = save_watermark_upload("batch_watermark", batch_logo_dir)
        except Exception:
            shutil.rmtree(batch_logo_dir, ignore_errors=True)
            raise

        with queue_condition:
            active_count = sum(
                1 for item in jobs.values()
                if item["state"] in {
                    "QUEUED", "DOWNLOADING",
                    "PROCESSING", "UPLOADING",
                }
            )

            if active_count + len(batch_items) > MAX_QUEUE:
                raise ValueError(
                    f"Antrean tidak cukup. Aktif {active_count}, "
                    f"akan ditambah {len(batch_items)}, "
                    f"maksimal {MAX_QUEUE}."
                )

            created_ids: list[str] = []

            for item in batch_items:
                episode_number = item["episode_number"]
                metadata = build_episode_metadata(
                    tmdb_id,
                    season_number,
                    episode_number,
                )

                job_id = uuid.uuid4().hex[:12]
                work_dir = Path(
                    tempfile.mkdtemp(
                        prefix=f"drive-telegram-v10-6-2-2-{job_id}-"
                    )
                )

                watermark_config = copy_watermark_config(batch_watermark, work_dir)
                jobs[job_id] = {
                    "id": job_id,
                    "file_id": item["video_file_id"],
                    "title": metadata["title"],
                    "metadata": metadata,
                    "tmdb_id": tmdb_id,
                    "season_number": season_number,
                    "episode_number": episode_number,
                    "target_chat_id": target_chat_id,
                    "message_thread_id": message_thread_id,
                    "topic_name": topic_name_from_id(
                        message_thread_id,
                        target_chat_id,
                    ),
                    "extra_caption": extra_caption,
                    "subtitle_mode": subtitle_mode,
                    "uploaded_subtitle": "",
                    "subtitle_drive_file_id": item["subtitle_file_id"],
                    "public_folder_id": public_folder_id,
                    "subtitle_info": "Menunggu pemeriksaan",
                    "work_dir": str(work_dir),
                    "state": "QUEUED",
                    "message": "Menunggu giliran.",
                    "created_at": now_ts(),
                    "started_at": None,
                    "finished_at": None,
                    "downloaded_bytes": 0,
                    "total_bytes": 0,
                    "file_size_bytes": 0,
                    "message_id": None,
                    "error": None,
                    "stage_progress": 0.0,
                    "overall_progress": 0.0,
                    "progress_detail": "Menunggu giliran.",
                    "eta_seconds": 0,
                    "eta_human": "-",
                    "batch_id": "",
                    **watermark_config,
                    **parse_encode_config("batch_"),
                }
                _scheduler_submit_locked(job_id)
                created_ids.append(job_id)

            batch_id = uuid.uuid4().hex[:10]
            for job_id in created_ids:
                jobs[job_id]["batch_id"] = batch_id

            queue_condition.notify_all()
        shutil.rmtree(batch_logo_dir, ignore_errors=True)

        key = request.args.get("key", "")
        return redirect(
            url_for(
                "panel",
                key=key,
                scan_message=(
                    f"Batch {batch_id}: "
                    f"{len(created_ids)} episode masuk antrean."
                ),
            )
        )

    except Exception as exc:
        key = request.args.get("key", "")
        return redirect(
            url_for(
                "panel",
                key=key,
                scan_message=f"Batch gagal: {exc}",
            )
        )



@app.post("/scan-series-bot-api")
def scan_series_bot_api():
    if not authorized():
        return jsonify({"success": False, "error": "Unauthorized"}), 401
    key = request.args.get("key", "")
    try:
        result = scan_telegram_series()
        message = (
            f"Scan Bot API selesai: {result['updates_count']} update dibaca, "
            f"{result['series_count']} serial ditemukan."
        )
    except Exception as exc:
        message = f"Scan Bot API gagal: {exc}"
    return redirect(url_for("panel", key=key, scan_message=message) + "#restoreMenu")


@app.post("/restore-scanned-series")
def restore_scanned_series():
    if not authorized():
        return jsonify({"success": False, "error": "Unauthorized"}), 401
    key = request.args.get("key", "")
    try:
        scan_id = str(request.form.get("scan_id") or "").strip()
        scanned = next((x for x in load_scan_results() if str(x.get("scan_id")) == scan_id), None)
        if not scanned:
            raise ValueError("Hasil scan tidak ditemukan. Jalankan scan ulang.")
        title = str(scanned.get("title") or "Serial Telegram")
        season = int(scanned.get("season") or 1)
        chat_id = str(scanned.get("chat_id") or CHANNEL_ID)
        thread_id = int(scanned.get("thread_id") or 0)
        tmdb_id = stable_manual_series_id(title)
        series = {
            "tmdb_id": tmdb_id,
            "series_title": title,
            "original_title": str(scanned.get("original_title") or title),
            "year": str(scanned.get("year") or "-"),
            "season_number": season,
            "target_chat_id": chat_id,
            "message_thread_id": thread_id,
            "topic_name": str(scanned.get("topic_name") or topic_name_from_id(thread_id, chat_id)),
            "poster_url": scanned.get("poster_url"),
            "poster_file_id": scanned.get("poster_file_id"),
            "vote_average": 0,
            "vote_count": 0,
            "release_date": "-",
            "certification": "-",
            "genres": [], "countries": [], "languages": [],
            "directors": [], "writers": [], "cast": [],
            "overview": str(scanned.get("overview") or "Dipulihkan dari Scan Bot API Telegram."),
            "index_message_id": int(scanned.get("index_message_id") or 0),
            "index_type": str(scanned.get("index_type") or "photo"),
            "episodes": dict(scanned.get("episodes") or {}),
            "manual": True,
            "restored_from_bot_api": True,
            "restored_at": now_ts(),
            "updated_at": now_ts(),
        }
        if not series["episodes"]:
            raise ValueError("Hasil scan tidak memiliki episode.")
        store = load_series_store()
        restored_key = f"{tmdb_id}:{season}:{chat_id}:{thread_id}"
        existing = store.get(restored_key)
        if isinstance(existing, dict):
            merged_episodes = dict(existing.get("episodes") or {})
            merged_episodes.update(series["episodes"])
            series["episodes"] = merged_episodes
            if not series["index_message_id"]:
                series["index_message_id"] = int(existing.get("index_message_id") or 0)
                series["index_type"] = str(existing.get("index_type") or series["index_type"])
        store[restored_key] = series
        save_series_store(store, reason="restore-bot-api-scan")
        return redirect(url_for("panel", key=key, scan_message=f"Serial '{title}' berhasil dipulihkan dari scan dengan {len(series['episodes'])} episode.") + "#savedMenu")
    except Exception as exc:
        return redirect(url_for("panel", key=key, scan_message=f"Pemulihan hasil scan gagal: {exc}") + "#restoreMenu")


@app.post("/restore-series")
def restore_series():
    if not authorized():
        return jsonify({"success": False, "error": "Unauthorized"}), 401
    key = request.args.get("key", "")
    try:
        title = str(request.form.get("restore_title") or "").strip()
        if not title:
            raise ValueError("Judul serial wajib diisi.")
        original_title = str(request.form.get("restore_original_title") or title).strip() or title
        year = str(request.form.get("restore_year") or "-").strip() or "-"
        season = int(request.form.get("restore_season") or 1)
        chat_id = str(request.form.get("restore_chat_id") or CHANNEL_ID).strip()
        thread_id = int(request.form.get("restore_thread_id") or 0)
        topic_name = str(request.form.get("restore_topic_name") or topic_name_from_id(thread_id, chat_id)).strip()
        tmdb_raw = str(request.form.get("restore_tmdb_id") or "").strip()
        tmdb_id = int(tmdb_raw) if tmdb_raw else stable_manual_series_id(title)
        index_message_id = int(request.form.get("restore_index_message_id") or 0)
        index_type = str(request.form.get("restore_index_type") or "photo")
        poster_url = str(request.form.get("restore_poster_url") or "").strip() or None
        overview = str(request.form.get("restore_overview") or "Sinopsis belum tersedia.").strip()
        episodes: dict[str, Any] = {}
        for line_number, raw in enumerate(str(request.form.get("restore_episode_lines") or "").splitlines(), 1):
            raw = raw.strip()
            if not raw:
                continue
            parts = [part.strip() for part in raw.split("|", 3)]
            if len(parts) < 2:
                raise ValueError(f"Baris episode {line_number} tidak valid.")
            episode_number = int(parts[0])
            message_id = int(parts[1])
            url = parts[2] if len(parts) >= 3 and parts[2] else telegram_message_url(chat_id, message_id)
            episode_title = parts[3] if len(parts) >= 4 and parts[3] else f"Episode {episode_number}"
            episodes[str(episode_number)] = {
                "message_id": message_id,
                "url": url,
                "title": episode_title,
                "episode_code": f"S{season:02d}E{episode_number:02d}",
                "updated_at": now_ts(),
                "restored": True,
            }
        if not episodes:
            raise ValueError("Masukkan minimal satu episode.")
        series = {
            "tmdb_id": tmdb_id,
            "series_title": title,
            "original_title": original_title,
            "year": year,
            "season_number": season,
            "target_chat_id": chat_id,
            "message_thread_id": thread_id,
            "topic_name": topic_name,
            "poster_url": poster_url,
            "vote_average": 0,
            "vote_count": 0,
            "release_date": "-",
            "certification": "-",
            "genres": [], "countries": [], "languages": [],
            "directors": [], "writers": [], "cast": [],
            "overview": overview,
            "index_message_id": index_message_id,
            "index_type": index_type,
            "episodes": episodes,
            "manual": True,
            "restored_at": now_ts(),
            "updated_at": now_ts(),
        }
        store = load_series_store()
        restored_key = f"{tmdb_id}:{season}:{chat_id}:{thread_id}"
        store[restored_key] = series
        save_series_store(store, reason="restore")
        return redirect(url_for("panel", key=key, scan_message=f"Serial '{title}' berhasil dipulihkan dengan {len(episodes)} episode."))
    except Exception as exc:
        return redirect(url_for("panel", key=key, scan_message=f"Pemulihan serial gagal: {exc}"))


@app.post("/add-saved-episode")
def add_saved_episode():
    if not authorized(): return jsonify({"success":False,"error":"Unauthorized"}),401
    key=request.args.get("key","")
    try:
        series_key=str(request.form.get("series_key") or "").strip(); ep=int(request.form.get("saved_episode_number") or 0)
        if ep<1: raise ValueError("Nomor episode harus minimal 1")
        store=load_series_store(); series=store.get(series_key)
        if not isinstance(series,dict): raise ValueError("Serial tidak ditemukan")
        if str(ep) in (series.get("episodes") or {}): raise ValueError(f"Episode {ep} sudah tersimpan")
        video_id=extract_drive_file_id(str(request.form.get("saved_drive_input") or ""))
        mode=str(request.form.get("saved_subtitle_mode") or "none")
        if mode not in {"none","auto_id","drive","auto_drive"}: raise ValueError("Mode subtitle tidak valid")
        sub_id=""
        if mode=="drive": sub_id=extract_drive_file_id(str(request.form.get("saved_subtitle_drive") or ""))
        public_folder_input=str(request.form.get("saved_public_folder_input") or "").strip()
        public_folder_id=extract_drive_folder_id(public_folder_input) if mode=="auto_drive" else ""
        tmdb_id = int(series.get("tmdb_id") or 0)
        season_number = int(series.get("season_number") or 1)
        if tmdb_id > 0 and not bool(series.get("manual")):
            try:
                meta = build_episode_metadata(tmdb_id, season_number, ep)
            except Exception:
                meta = metadata_from_saved_series(series, ep, str(request.form.get("saved_episode_title") or ""))
        else:
            meta = metadata_from_saved_series(series, ep, str(request.form.get("saved_episode_title") or ""))
        with queue_condition:
            active=sum(1 for i in jobs.values() if i["state"] in {"QUEUED","DOWNLOADING","PROCESSING","PREPARING","READY","UPLOADING"})
            if active>=MAX_QUEUE: raise ValueError(f"Antrean penuh. Maksimal {MAX_QUEUE}")
            jid=uuid.uuid4().hex[:12]; wd=Path(tempfile.mkdtemp(prefix=f"drive-telegram-v10-6-2-2-{jid}-"))
            watermark_config=save_watermark_upload("saved_watermark",wd)
            chat=str(series.get("target_chat_id") or CHANNEL_ID); thread=int(series.get("message_thread_id") or 0)
            jobs[jid]={"id":jid,"file_id":video_id,"title":meta["title"],"metadata":meta,"tmdb_id":int(series.get("tmdb_id") or 0),"season_number":int(series.get("season_number") or 1),"episode_number":ep,"target_chat_id":chat,"message_thread_id":thread,"topic_name":str(series.get("topic_name") or topic_name_from_id(thread,chat)),"extra_caption":str(request.form.get("saved_extra_caption") or "").strip(),"subtitle_mode":mode,"uploaded_subtitle":"","subtitle_drive_file_id":sub_id,"public_folder_id":public_folder_id,"subtitle_info":"Menunggu pemeriksaan","work_dir":str(wd),"state":"QUEUED","message":"Menunggu giliran.","created_at":now_ts(),"started_at":None,"finished_at":None,"downloaded_bytes":0,"total_bytes":0,"file_size_bytes":0,"message_id":None,"error":None,"stage_progress":0.0,"overall_progress":0.0,"progress_detail":"Menunggu giliran.","eta_seconds":0,"eta_human":"-","manual_mode":bool(series.get("manual")),"saved_series_key":series_key,**watermark_config,**parse_encode_config("saved_")}
            _scheduler_submit_locked(jid); queue_condition.notify()
        return redirect(url_for("panel", key=key, scan_message=f"{meta['episode_code']} ditambahkan ke {meta['series_title']}") + "#queueSection")
    except Exception as exc:
        return redirect(url_for("panel", key=key, scan_message=f"Tambah episode gagal: {exc}") + "#serialSection")


@app.post("/manual-enqueue")
def manual_enqueue():
    if not authorized():
        return jsonify({"success": False, "error": "Unauthorized"}), 401
    key = request.args.get("key", "")
    try:
        metadata = build_manual_metadata(request.form)
        if not metadata.get("title"): raise ValueError("Judul wajib diisi.")
        file_id = extract_drive_file_id(str(request.form.get("manual_drive_input") or ""))
        topic_target = str(request.form.get("manual_topic_target", f"{CHANNEL_ID}|{DEFAULT_THREAD_ID}"))
        if "|" in topic_target: target_chat_id, thread_value = topic_target.rsplit("|", 1)
        else: target_chat_id, thread_value = CHANNEL_ID, topic_target
        message_thread_id = int(thread_value or "0")
        subtitle_mode = str(request.form.get("manual_subtitle_mode") or "none")
        if subtitle_mode not in {"none", "auto_id", "drive", "auto_drive"}: raise ValueError("Mode subtitle tidak valid.")
        subtitle_drive_file_id = ""
        public_folder_input = str(request.form.get("manual_public_folder_input") or "").strip()
        public_folder_id = extract_drive_folder_id(public_folder_input) if subtitle_mode == "auto_drive" else ""
        subtitle_drive = str(request.form.get("manual_subtitle_drive") or "").strip()
        if subtitle_mode == "drive":
            if not subtitle_drive: raise ValueError("Mode subtitle Drive memerlukan link subtitle.")
            subtitle_drive_file_id = extract_drive_file_id(subtitle_drive)
        with queue_condition:
            active_count = sum(1 for item in jobs.values() if item["state"] in {"QUEUED","DOWNLOADING","PROCESSING","PREPARING","READY","UPLOADING"})
            if active_count >= MAX_QUEUE: raise ValueError(f"Antrean penuh. Maksimal {MAX_QUEUE}.")
            job_id = uuid.uuid4().hex[:12]
            work_dir = Path(tempfile.mkdtemp(prefix=f"drive-telegram-v10-6-2-2-{job_id}-"))
            watermark_config = save_watermark_upload("manual_watermark", work_dir)
            manual_media_type = str(request.form.get("manual_media_type") or "movie")
            season_number = int(request.form.get("manual_season_number") or "1") if manual_media_type == "tv" else None
            episode_number = int(request.form.get("manual_episode_number") or "1") if manual_media_type == "tv" else None
            manual_tmdb_id = stable_manual_series_id(str(metadata.get("series_title") or metadata.get("title") or ""))
            jobs[job_id] = {
                "id": job_id,"file_id": file_id,"title": metadata["title"],"metadata": metadata,"tmdb_id": manual_tmdb_id,
                "season_number": season_number,"episode_number": episode_number,"target_chat_id": target_chat_id,
                "message_thread_id": message_thread_id,"topic_name": topic_name_from_id(message_thread_id,target_chat_id),
                "extra_caption": str(request.form.get("manual_extra_caption") or "").strip(),"subtitle_mode": subtitle_mode,
                "uploaded_subtitle": "","subtitle_drive_file_id": subtitle_drive_file_id,"public_folder_id": public_folder_id,"subtitle_info": "Menunggu pemeriksaan",
                "work_dir": str(work_dir),"state": "QUEUED","message": "Menunggu giliran.","created_at": now_ts(),
                "started_at": None,"finished_at": None,"downloaded_bytes": 0,"total_bytes": 0,"file_size_bytes": 0,
                "message_id": None,"error": None,"stage_progress": 0.0,"overall_progress": 0.0,
                "progress_detail": "Menunggu giliran.","eta_seconds": 0,"eta_human": "-","manual_mode": True,
                **watermark_config,
                **parse_encode_config("manual_"),
            }
            _scheduler_submit_locked(job_id)
            queue_condition.notify()
        return redirect(url_for("panel", key=key, scan_message=f"Konten manual '{metadata['title']}' ditambahkan ke antrean."))
    except Exception as exc:
        return redirect(url_for("panel", key=key, scan_message=f"Manual gagal: {exc}"))


@app.post("/enqueue")
def enqueue():
    if not authorized():
        return jsonify({"success": False, "error": "Unauthorized"}), 401
    drive_input = request.form.get("drive_input", "").strip()
    tmdb_id = int(request.form.get("tmdb_id", "0"))
    media_type = request.form.get("media_type", "").strip()
    subtitle_mode = request.form.get("subtitle_mode", "auto_drive").strip()
    extra_caption = request.form.get("extra_caption", "").strip()
    season_number = int(request.form.get("season_number", "1") or "1")
    episode_number = int(request.form.get("episode_number", "1") or "1")
    topic_target = request.form.get(
        "message_thread_id",
        f"{CHANNEL_ID}|{DEFAULT_THREAD_ID}",
    )
    if "|" in topic_target:
        target_chat_id, thread_value = topic_target.rsplit("|", 1)
    else:
        target_chat_id, thread_value = CHANNEL_ID, topic_target
    message_thread_id = int(thread_value or "0")

    if media_type not in {"movie","tv"} or subtitle_mode not in {"auto_id","upload","none","auto_drive"}:
        return jsonify({"success": False, "error": "Pilihan tidak valid."}), 400

    try:
        file_id = extract_drive_file_id(drive_input)
        public_folder_input = str(request.form.get("public_folder_input") or "").strip()
        public_folder_id = extract_drive_folder_id(public_folder_input) if subtitle_mode == "auto_drive" else ""
        if media_type == "tv":
            if season_number < 0 or episode_number < 1:
                raise ValueError("Season atau episode tidak valid.")
            metadata = build_episode_metadata(
                tmdb_id,
                season_number,
                episode_number,
            )
        else:
            metadata = build_metadata(tmdb_id, media_type)
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 400

    with queue_condition:
        active = sum(1 for item in jobs.values() if item["state"] in {"QUEUED","DOWNLOADING","PROCESSING","PREPARING","READY","UPLOADING"})
        if active >= MAX_QUEUE:
            return jsonify({"success": False, "error": f"Antrean penuh. Maksimal {MAX_QUEUE} pekerjaan."}), 429

        job_id = uuid.uuid4().hex[:12]
        work_dir = Path(tempfile.mkdtemp(prefix=f"drive-telegram-v10-6-2-2-{job_id}-"))
        try:
            watermark_config = save_watermark_upload("watermark", work_dir)
        except Exception:
            shutil.rmtree(work_dir, ignore_errors=True)
            raise
        uploaded_subtitle = ""

        if subtitle_mode == "upload":
            file = request.files.get("subtitle_file")
            if not file or not file.filename:
                shutil.rmtree(work_dir, ignore_errors=True)
                return jsonify({"success": False, "error": "Pilih file subtitle terlebih dahulu."}), 400
            ext = Path(file.filename).suffix.lower()
            if ext not in {".srt",".ass",".ssa",".vtt"}:
                shutil.rmtree(work_dir, ignore_errors=True)
                return jsonify({"success": False, "error": "Format subtitle harus .srt, .ass, .ssa, atau .vtt."}), 400
            subtitle_dest = work_dir / f"subtitle{ext}"
            file.save(subtitle_dest)
            uploaded_subtitle = str(subtitle_dest)

        jobs[job_id] = {
            "id": job_id, "file_id": file_id, "title": metadata["title"], "metadata": metadata,
            "tmdb_id": tmdb_id,
            "season_number": season_number if media_type == "tv" else None,
            "episode_number": episode_number if media_type == "tv" else None,
            "target_chat_id": target_chat_id,
            "message_thread_id": message_thread_id,
            "topic_name": topic_name_from_id(message_thread_id, target_chat_id),
            "extra_caption": extra_caption, "subtitle_mode": subtitle_mode,
            "uploaded_subtitle": uploaded_subtitle, "subtitle_drive_file_id": "", "public_folder_id": public_folder_id, "subtitle_info": "Menunggu pemeriksaan",
            "work_dir": str(work_dir), "state": "QUEUED", "message": "Menunggu giliran.",
            "created_at": now_ts(), "started_at": None, "finished_at": None,
            "downloaded_bytes": 0, "total_bytes": 0, "file_size_bytes": 0,
            "message_id": None, "error": None, "stage_progress": 0.0, "overall_progress": 0.0, "progress_detail": "Menunggu giliran.", "eta_seconds": 0, "eta_human": "-",
            **watermark_config,
            **parse_encode_config(),
        }
        _scheduler_submit_locked(job_id)
        queue_condition.notify()

    return redirect(url_for("panel", key=request.args.get("key", "")))

@app.get("/api/series/search")
def api_series_search():
    if not authorized():
        return jsonify({
            "success": False,
            "error": "Unauthorized",
        }), 401

    query = request.args.get("q", "").strip().lower()
    items = saved_series_options()

    if query:
        items = [
            item
            for item in items
            if query in item["title"].lower()
        ]

    return jsonify({
        "success": True,
        "results": items,
        "count": len(items),
    })


@app.get("/api/series")
def api_series():
    if not authorized():
        return jsonify({
            "success": False,
            "error": "Unauthorized",
        }), 401

    return jsonify({
        "success": True,
        "series": load_series_store(),
    })


def _panel_global_jobs() -> list[dict[str, Any]]:
    """Merge local submission cards with the authoritative shared job state.

    A job submitted through this panel may be claimed by another Railway. The
    lightweight local record therefore must not remain QUEUED when Supabase says
    the same job is already PROCESSING/UPLOADING/SUCCESS on another worker.
    """
    local_rows = get_jobs_snapshot()
    shared_rows = cluster_store.enterprise_jobs() if cluster_store.enabled else []
    shared_by_id = {
        str(item.get("id") or item.get("job_id") or ""): dict(item)
        for item in shared_rows
        if isinstance(item, dict) and str(item.get("id") or item.get("job_id") or "")
    }

    merged_rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    authoritative_fields = {
        "state", "message", "error", "overall_progress", "stage_progress",
        "progress_detail", "eta_seconds", "eta_human", "started_at",
        "finished_at", "assigned_worker", "worker_id", "scheduler_status",
        "bot", "message_id", "index_message_id", "file_size_bytes",
    }
    for local in local_rows:
        row = dict(local)
        job_id = str(row.get("id") or row.get("job_id") or "")
        remote = shared_by_id.get(job_id)
        if remote:
            for field in authoritative_fields:
                if field in remote and remote.get(field) is not None:
                    row[field] = remote.get(field)
            # Fill metadata that may exist only in one side.
            for field, value in remote.items():
                if field not in row or row.get(field) in (None, "", 0, "-"):
                    row[field] = value
        worker = str(row.get("assigned_worker") or row.get("worker_id") or "")
        row["processing_on_other_worker"] = bool(
            worker and worker != cluster_store.worker_id
            and str(row.get("state") or "") not in {"QUEUED", "SUCCESS", "ERROR"}
        )
        row["current_panel_worker"] = cluster_store.worker_id
        row["processing_worker"] = worker
        if row["processing_on_other_worker"]:
            row["message"] = (
                f"Sedang diproses di {worker}. "
                + str(row.get("message") or "")
            ).strip()
        merged_rows.append(row)
        seen.add(job_id)

    # Also show jobs created from another panel when they are active globally.
    active_states = {"QUEUED", "ASSIGNED", "CLAIMED", "DOWNLOADING", "PROCESSING", "PREPARING", "READY", "UPLOADING"}
    for remote in shared_rows:
        job_id = str(remote.get("id") or remote.get("job_id") or "")
        if not job_id or job_id in seen or str(remote.get("state") or "") not in active_states:
            continue
        row = dict(remote)
        worker = str(row.get("assigned_worker") or row.get("worker_id") or "")
        row["processing_on_other_worker"] = bool(worker and worker != cluster_store.worker_id)
        row["current_panel_worker"] = cluster_store.worker_id
        row["processing_worker"] = worker
        merged_rows.append(row)

    merged_rows.sort(
        key=lambda item: float(item.get("finished_at") or item.get("started_at") or item.get("created_at") or 0),
        reverse=True,
    )
    return merged_rows[:MAX_QUEUE]


@app.get("/api/jobs")
def api_jobs():
    if not authorized():
        return jsonify({"success": False, "error": "Unauthorized"}), 401
    return jsonify({
        "success": True,
        "worker_id": cluster_store.worker_id,
        "jobs": _panel_global_jobs(),
    })


def _global_sync_loop() -> None:
    # Menyatukan cache lama dari setiap Railway, lalu menjaga cache lokal mengikuti Supabase.
    time.sleep(2)
    while True:
        try:
            if GLOBAL_SYNC_ENABLED and cluster_store.enabled:
                load_series_store()
                load_discovered_topics()
                load_scan_results()
                print(
                    f"[GLOBAL-SYNC] OK worker={cluster_store.worker_id} namespace={cluster_store.namespace}",
                    flush=True,
                )
        except Exception as exc:
            print(f"[GLOBAL-SYNC] ERROR: {exc}", flush=True)
        time.sleep(GLOBAL_SYNC_INTERVAL)


if GLOBAL_SYNC_ENABLED and cluster_store.enabled:
    threading.Thread(target=_global_sync_loop, name="global-sync", daemon=True).start()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "8080")))
