
"""CineDrive v11 Cluster storage using Supabase REST.

The cluster synchronizes small JSON metadata (series, topics, scan results and
worker heartbeats). Large video/subtitle/logo files remain local to each worker.
"""
from __future__ import annotations

import json
import os
import socket
import threading
import time
import uuid
from typing import Any

import requests


class ClusterStore:
    def __init__(self) -> None:
        self.url = os.getenv("SUPABASE_URL", "").rstrip("/")
        self.key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
        self.namespace = os.getenv("CLUSTER_NAMESPACE", "default").strip() or "default"
        self.worker_id = os.getenv(
            "CLUSTER_WORKER_ID",
            f"{socket.gethostname()}-{uuid.uuid4().hex[:6]}",
        ).strip()
        self.enabled = bool(self.url and self.key)
        self.timeout = max(5, int(os.getenv("CLUSTER_HTTP_TIMEOUT", "20")))
        self._heartbeat_started = False
        self._heartbeat_guard = threading.Lock()

    @property
    def headers(self) -> dict[str, str]:
        return {
            "apikey": self.key,
            "Authorization": f"Bearer {self.key}",
            "Content-Type": "application/json",
            "Prefer": "resolution=merge-duplicates,return=representation",
        }

    def _endpoint(self, table: str = "cinedrive_cluster") -> str:
        return f"{self.url}/rest/v1/{table}"

    def get_json(self, bucket: str, key: str, default: Any) -> Any:
        if not self.enabled:
            return default
        response = requests.get(
            self._endpoint(),
            headers=self.headers,
            params={
                "namespace": f"eq.{self.namespace}",
                "bucket": f"eq.{bucket}",
                "item_key": f"eq.{key}",
                "select": "value",
                "limit": "1",
            },
            timeout=self.timeout,
        )
        response.raise_for_status()
        rows = response.json()
        if not rows:
            return default
        value = rows[0].get("value", default)
        return value

    def put_json(self, bucket: str, key: str, value: Any) -> None:
        if not self.enabled:
            return
        payload = {
            "namespace": self.namespace,
            "bucket": bucket,
            "item_key": key,
            "value": value,
            "worker_id": self.worker_id,
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        response = requests.post(
            self._endpoint(),
            headers=self.headers,
            params={"on_conflict": "namespace,bucket,item_key"},
            json=payload,
            timeout=self.timeout,
        )
        response.raise_for_status()

    def merge_dict(self, bucket: str, key: str, incoming: dict[str, Any]) -> dict[str, Any]:
        """Best-effort conflict-safe merge for dictionary documents.

        It retries after reading the latest document. For episode maps this
        prevents one Railway worker from blindly replacing another worker's data.
        """
        if not self.enabled:
            return incoming
        last_error: Exception | None = None
        for _ in range(3):
            try:
                current = self.get_json(bucket, key, {})
                if not isinstance(current, dict):
                    current = {}
                merged = self._deep_merge(current, incoming)
                self.put_json(bucket, key, merged)
                return merged
            except Exception as exc:
                last_error = exc
                time.sleep(0.35)
        if last_error:
            raise last_error
        return incoming

    @classmethod
    def _deep_merge(cls, old: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
        result = dict(old)
        for key, value in new.items():
            if isinstance(value, dict) and isinstance(result.get(key), dict):
                result[key] = cls._deep_merge(result[key], value)
            else:
                result[key] = value
        return result

    def heartbeat(self, extra: dict[str, Any] | None = None) -> None:
        if not self.enabled:
            return
        payload = {
            "worker_id": self.worker_id,
            "last_seen": int(time.time()),
            "hostname": socket.gethostname(),
            "status": "online",
        }
        if extra:
            payload.update(extra)
        self.put_json("workers", self.worker_id, payload)

    def list_workers(self) -> list[dict[str, Any]]:
        if not self.enabled:
            return []
        response = requests.get(
            self._endpoint(),
            headers=self.headers,
            params={
                "namespace": f"eq.{self.namespace}",
                "bucket": "eq.workers",
                "select": "item_key,value,updated_at",
                "order": "updated_at.desc",
                "limit": "100",
            },
            timeout=self.timeout,
        )
        response.raise_for_status()
        rows = response.json()
        result = []
        now = int(time.time())
        for row in rows:
            value = row.get("value") if isinstance(row.get("value"), dict) else {}
            last_seen = int(value.get("last_seen") or 0)
            value["online"] = bool(last_seen and now - last_seen <= 120)
            result.append(value)
        return result

    def start_heartbeat(self, interval: int = 45) -> None:
        if not self.enabled:
            return
        with self._heartbeat_guard:
            if self._heartbeat_started:
                return
            self._heartbeat_started = True

        def loop() -> None:
            while True:
                try:
                    self.heartbeat()
                except Exception:
                    pass
                time.sleep(max(15, interval))

        threading.Thread(target=loop, name="cluster-heartbeat", daemon=True).start()


cluster_store = ClusterStore()
