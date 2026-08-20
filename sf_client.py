"""
Thin HTTP client for SAP SuccessFactors OData v2.
Supports configuration via config.local.json, .env, or environment variables.
"""

import json
import os
import uuid
from pathlib import Path
import requests
from requests.auth import HTTPBasicAuth


def _load_config(path: str = "config.local.json") -> dict:
    config = {}
    config_file = Path(path)
    if config_file.exists():
        try:
            with open(config_file) as f:
                config = json.load(f)
        except Exception as e:
            print(f"Warning: could not parse {path}: {e}")

    # Fallback to environment variables
    api_url = os.getenv("SF_API_URL", config.get("SF_API_URL", ""))
    username = os.getenv("SF_USERNAME", config.get("SF_USERNAME", ""))
    password = os.getenv("SF_PASSWORD", config.get("SF_PASSWORD", ""))

    if not api_url or not username or not password:
        raise ValueError(
            "Missing SF credentials. Provide SF_API_URL, SF_USERNAME, SF_PASSWORD "
            f"via environment variables or {path}."
        )

    return {
        "SF_API_URL": api_url,
        "SF_USERNAME": username,
        "SF_PASSWORD": password,
    }


class SFClient:
    def __init__(self, config_path: str = "config.local.json"):
        cfg = _load_config(config_path)
        self.api_url = cfg["SF_API_URL"].rstrip("/")
        username = cfg["SF_USERNAME"]
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
            pass

    def get(self, entity: str, params: dict = None) -> dict:
        url = f"{self.api_url}/odata/v2/{entity}"
        resp = self.session.get(url, params=params)
        resp.raise_for_status()
        return resp.json()

    def post(self, entity: str, payload: dict) -> dict:
        url = f"{self.api_url}/odata/v2/{entity}"
        resp = self.session.post(url, json=payload)
        if not resp.ok:
            raise Exception(f"POST {entity} -> {resp.status_code}: {resp.text}")
        if resp.status_code == 204 or not resp.text.strip():
            return {}
        return resp.json()

    def _check_upsert_response(self, data: dict, operation_name: str = "upsert"):
        """Recursively check for 'ERROR' status in upsert response and raise Exception if found."""
        def extract_errors(item):
            errors = []
            if isinstance(item, dict):
                if item.get("status") == "ERROR":
                    msg = item.get("message")
                    if msg:
                        errors.append(msg)
                for inline in item.get("inlineResults", []) or []:
                    for sub in inline.get("results", []) or []:
                        errors.extend(extract_errors(sub))
            elif isinstance(item, list):
                for sub in item:
                    errors.extend(extract_errors(sub))
            return errors

        if isinstance(data, dict) and "d" in data:
            errs = extract_errors(data["d"])
            if errs:
                raise Exception(f"{operation_name} failed with error(s):\n" + "\n".join(f"  - {e}" for e in errs))

    def upsert(self, key_path: str, payload: dict) -> dict:
        """PUT to a key-based entity URL — creates if absent, updates if present."""
        url = f"{self.api_url}/odata/v2/{key_path}"
        resp = self.session.put(url, json=payload)
        if not resp.ok:
            raise Exception(f"PUT {key_path} -> {resp.status_code}: {resp.text}")
        if resp.status_code == 204 or not resp.text.strip():
            return {}
        data = resp.json()
        self._check_upsert_response(data, operation_name=f"PUT {key_path}")
        return data

    def deep_upsert(self, payload: dict, params: dict = None) -> dict:
        """POST to /odata/v2/upsert with a deep-insert payload."""
        url = f"{self.api_url}/odata/v2/upsert"
        resp = self.session.post(url, json=payload, params=params)
        if not resp.ok:
            raise Exception(f"POST upsert -> {resp.status_code}: {resp.text}")
        if resp.status_code == 204 or not resp.text.strip():
            return {}
        data = resp.json()
        entity_name = payload.get("__metadata", {}).get("uri", "Entity")
        self._check_upsert_response(data, operation_name=f"Upsert {entity_name}")
        return data

    def metadata(self, use_cache: bool = True) -> str:
        """Fetch raw $metadata XML with disk caching support."""
        cache_path = Path(".sf_metadata.xml")
        if use_cache and cache_path.exists():
            return cache_path.read_text(encoding="utf-8")

        url = f"{self.api_url}/odata/v2/$metadata"
        resp = self.session.get(url, headers={"Accept": "application/xml"})
        resp.raise_for_status()
        xml_text = resp.text
        try:
            cache_path.write_text(xml_text, encoding="utf-8")
        except Exception:
            pass
        return xml_text
