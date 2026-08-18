"""
Thin HTTP client for SAP SuccessFactors OData v2.
Uses HTTP Basic Auth (username@company:password).
"""

import json
import os
import uuid
import requests
from requests.auth import HTTPBasicAuth


def _load_config(path: str = "config.local.json") -> dict:
    with open(path) as f:
        return json.load(f)


class SFClient:
    def __init__(self, config_path: str = "config.local.json"):
        cfg = _load_config(config_path)
        self.api_url = cfg["SF_API_URL"].rstrip("/")
        # SF Basic Auth format: username@company:password
        username = f"{cfg['SF_USERNAME']}"
        password = cfg["SF_PASSWORD"]
        self.auth = HTTPBasicAuth(username, password)
        self.session = requests.Session()
        self.session.auth = self.auth
        self.session.headers.update({
            "Accept": "application/json",
            "Content-Type": "application/json",
            "successfactors-sourcetype": "odata",
        })
        self._fetch_csrf_token()

    def _fetch_csrf_token(self):
        """Fetch and cache a CSRF token required by SAP for all write operations."""
        try:
            url = f"{self.api_url}/odata/v2/"
            resp = self.session.get(url, headers={"X-CSRF-Token": "Fetch"}, timeout=10)
            token = resp.headers.get("X-CSRF-Token")
            if token:
                self.session.headers["X-CSRF-Token"] = token
        except Exception:
            pass  # token unavailable; proceed without it

    def get(self, entity: str, params: dict = None) -> dict:
        url = f"{self.api_url}/odata/v2/{entity}"
        resp = self.session.get(url, params=params)
        resp.raise_for_status()
        return resp.json()

    def post(self, entity: str, payload: dict) -> dict:
        url = f"{self.api_url}/odata/v2/{entity}"
        resp = self.session.post(url, json=payload)
        if not resp.ok:
            raise Exception(f"POST {entity} → {resp.status_code}: {resp.text}")
        return resp.json()

    def upsert(self, key_path: str, payload: dict) -> dict:
        """PUT to a key-based entity URL — creates if absent, updates if present."""
        url = f"{self.api_url}/odata/v2/{key_path}"
        resp = self.session.put(url, json=payload)
        if not resp.ok:
            raise Exception(f"PUT {key_path} → {resp.status_code}: {resp.text}")
        if resp.status_code == 204 or not resp.text.strip():
            return {}
        return resp.json()

    def batch(self, operations: list, print_request: bool = False) -> str:
        """
        Execute multiple POST operations in a single OData $batch request.
        All operations share one changeset so they execute as a single transaction.
        Each operation: {"entity": "EntityName", "payload": {...}}
        Returns the raw multipart response text.
        """
        batch_id = f"batch_create_employee_{uuid.uuid4().hex[:8]}"
        cs_id = "changeset_single_transaction"
        csrf = self.session.headers.get("X-CSRF-Token", "")

        inner_headers = [
            "Content-Type: application/json",
            "successfactors-sourcetype: odata",
        ]
        if csrf:
            inner_headers.append(f"X-CSRF-Token: {csrf}")

        parts = [
            f"--{batch_id}",
            f"Content-Type: multipart/mixed; boundary={cs_id}",
            "",
        ]

        for op in operations:
            payload = {"__metadata": {"uri": op["entity"]}, **op["payload"]}
            parts += [
                f"--{cs_id}",
                "Content-Type: application/http",
                "Content-Transfer-Encoding: binary",
                "",
                f"POST {op['entity']} HTTP/1.1",
            ] + inner_headers + [
                "",
                json.dumps(payload),
                "",
            ]

        parts += [
            f"--{cs_id}--",
            "",
            f"--{batch_id}--",
            "",
        ]

        body = "\r\n".join(parts)

        if print_request:
            print("\n--- BATCH REQUEST BODY ---")
            print(body)
            print("--- END BATCH REQUEST BODY ---\n")

        url = f"{self.api_url}/odata/v2/$batch"
        resp = self.session.post(
            url,
            data=body.encode("utf-8"),
            headers={"Content-Type": f"multipart/mixed; boundary={batch_id}"},
        )
        if not resp.ok:
            raise Exception(f"$batch → {resp.status_code}: {resp.text}")
        return resp.text

    def metadata(self) -> str:
        """Fetch raw $metadata XML for entity discovery."""
        url = f"{self.api_url}/odata/v2/$metadata"
        resp = self.session.get(url, headers={"Accept": "application/xml"})
        resp.raise_for_status()
        return resp.text

    def check_position(self, position_id: str) -> dict:
        """Look up a Position record by externalCode (direct key access)."""
        return self.get(f"Position('{position_id}')")
