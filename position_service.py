"""
Position Service for SAP SuccessFactors Employee Central.
Contract-first position lookup, organizational defaults resolution,
schema-driven position cloning in the 4000000 range, and live position updating.
"""

import json
from typing import Any, Dict, List, Optional
from sf_client import SFClient
from metadata_engine import MetadataEngine


_POSITION_ORG_FIELDS = (
    "company", "businessUnit", "division", "department",
    "location", "jobCode", "jobTitle", "costCenter",
    "payScaleType", "payScaleArea", "payGrade"
)


class PositionService:
    def __init__(self, client: SFClient, meta_engine: Optional[MetadataEngine] = None):
        self.client = client
        self.meta_engine = meta_engine or MetadataEngine(client)

    def get_position(self, code: str) -> Optional[Dict[str, Any]]:
        """Query a Position by its external code."""
        try:
            res = self.client.get("Position", params={"$filter": f"code eq '{code}'"})
            rows = res.get("d", {}).get("results", [])
            return rows[0] if rows else None
        except Exception:
            return None

    def get_next_position_code(self, base_range: int = 4000001) -> str:
        """
        Dynamically find the next available numeric position code in the 4000000 range.
        Checks iteratively to ensure we don't accidentally overwrite existing positions.
        """
        curr = base_range
        while True:
            if not self.get_position(str(curr)):
                return str(curr)
            curr += 1

    def get_org_defaults(self, position_code: str) -> Dict[str, Any]:
        """
        Extract organizational and job defaults from a position record for EmpJob.
        """
        pos = self.get_position(position_code)
        if not pos:
            return {}

        defaults = {f: pos[f] for f in _POSITION_ORG_FIELDS if pos.get(f) is not None}

        # Fallback for jobTitle if blank
        if not defaults.get("jobTitle"):
            title = pos.get("jobTitle") or pos.get("positionTitle") or pos.get("externalName_defaultValue")
            if title:
                defaults["jobTitle"] = title.replace(" (Copy)", "").strip()

        # Map custom pay scale fields if standard ones are null
        if not defaults.get("payScaleType") and pos.get("cust_payScaleType"):
            defaults["payScaleType"] = pos.get("cust_payScaleType")
        if not defaults.get("payScaleArea") and pos.get("cust_payScaleArea"):
            defaults["payScaleArea"] = pos.get("cust_payScaleArea")

        return defaults

    def clone_position(
        self,
        source_code: str,
        target_code: Optional[str] = None,
        title_suffix: str = "(Copy)",
        overrides: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Clone an existing Position into a new Position code (defaults to next in 4000000 range)
        using schema-driven pre-flight validation.
        """
        src = self.get_position(source_code)
        if not src:
            raise ValueError(f"Source Position '{source_code}' not found in SuccessFactors.")

        # If no target code specified, allocate next code in the 4000000 range
        if not target_code:
            target_code = self.get_next_position_code(4000001)

        overrides = overrides or {}

        base_title = src.get("externalName_defaultValue") or src.get("positionTitle") or "Position"
        new_title = f"{base_title} {title_suffix}".strip()

        # Build raw payload by copying populated source attributes
        raw_payload = {
            "__metadata": {"uri": "Position"},
            **{k: v for k, v in src.items() if not isinstance(v, dict) and v is not None and not k.startswith("externalName_")},
            "code": target_code,
            "externalName_defaultValue": new_title,
            "vacant": True,
            "targetFTE": src.get("targetFTE") or 1.0,
            "standardHours": str(src.get("standardHours") or "40.0"),
            "cust_workingDaysPerWeek": str(src.get("cust_workingDaysPerWeek") or "5.0"),
            "cust_employeeClass": src.get("cust_employeeClass") or "EC_01",
            "cust_workerType": src.get("cust_workerType") or "WT_01",
            "cust_employmentType": src.get("cust_employmentType") or "ET_01",
            "cust_payScaleType": src.get("cust_payScaleType") or "USA/US1",
            "cust_payScaleArea": src.get("cust_payScaleArea") or "USA/US2",
            "cust_payScaleGroup": src.get("cust_payScaleGroup") or "USA/PROD/PLANT/TECH",
            "cust_payScaleLevel": src.get("cust_payScaleLevel") or "USA/PROD/PLANT/TECH/L1",
        }

        raw_payload.update(overrides)

        # Pre-flight schema validation: strips non-upsertable system attributes and coerces types
        clean_payload = self.meta_engine.validate_payload("Position", raw_payload)

        res = self.client.deep_upsert(clean_payload)
        return {
            "status": "SUCCESS",
            "code": target_code,
            "title": clean_payload.get("externalName_defaultValue", new_title),
            "company": clean_payload.get("company"),
            "department": clean_payload.get("department"),
            "jobCode": clean_payload.get("jobCode"),
            "raw_response": res
        }

    def update_position(self, code: str, updates: Dict[str, Any]) -> Dict[str, Any]:
        """
        Update fields on an existing Position with schema-driven validation.
        """
        existing = self.get_position(code)
        if not existing:
            raise ValueError(f"Position '{code}' not found in SuccessFactors.")

        # Build update payload
        raw_payload = {
            "__metadata": {"uri": "Position"},
            **{k: v for k, v in existing.items() if not isinstance(v, dict) and v is not None and not k.startswith("externalName_")},
            "code": code,
            **updates
        }

        clean_payload = self.meta_engine.validate_payload("Position", raw_payload)
        res = self.client.deep_upsert(clean_payload)
        return {
            "status": "UPDATED",
            "code": code,
            "updated_fields": list(updates.keys()),
            "raw_response": res
        }

    def list_positions(self, top: int = 20, filter_query: Optional[str] = None) -> List[Dict[str, Any]]:
        """List positions with optional filter."""
        params = {
            "$top": str(top),
            "$select": "code,externalName_defaultValue,company,department,jobCode,vacant,effectiveStatus"
        }
        if filter_query:
            params["$filter"] = filter_query

        res = self.client.get("Position", params=params)
        return res.get("d", {}).get("results", [])
