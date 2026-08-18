"""
Thin HTTP client for SAP SuccessFactors OData v2.
Uses HTTP Basic Auth (username@company:password).
"""

import json
import os
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
        })

    def get(self, entity: str, params: dict = None) -> dict:
        url = f"{self.api_url}/odata/v2/{entity}"
        resp = self.session.get(url, params=params)
        resp.raise_for_status()
        return resp.json()

    def post(self, entity: str, payload: dict) -> dict:
        url = f"{self.api_url}/odata/v2/{entity}"
        resp = self.session.post(url, json=payload)
        resp.raise_for_status()
        return resp.json()

    def metadata(self) -> str:
        """Fetch raw $metadata XML for entity discovery."""
        url = f"{self.api_url}/odata/v2/$metadata"
        resp = self.session.get(url, headers={"Accept": "application/xml"})
        resp.raise_for_status()
        return resp.text

    def check_position(self, position_id: str) -> dict:
        """Look up a Position record by externalCode."""
        return self.get("Position", params={
            "$filter": f"externalCode eq '{position_id}'",
            "$top": "1",
        })
