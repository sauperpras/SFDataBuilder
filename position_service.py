"""
Position Service for SAP SuccessFactors Employee Central.
Handles Position lookups, organizational defaults resolution, and Position cloning.
"""

import json
from typing import Any, Dict, List, Optional
from sf_client import SFClient


_POSITION_ORG_FIELDS = (
    "company", "businessUnit", "division", "department",
    "location", "jobCode", "jobTitle", "costCenter",
    "payScaleType", "payScaleArea", "payGrade"
)


class PositionService:
    def __init__(self, client: SFClient):
        self.client = client

    def get_position(self, code: str) -> Optional[Dict[str, Any]]:
        """Query a Position by its external code."""
        try:
            res = self.client.get("Position", params={"$filter": f"code eq '{code}'"})
            rows = res.get("d", {}).get("results", [])
            return rows[0] if rows else None
        except Exception:
            return None

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
        target_code: str,
        title_suffix: str = "(Copy)",
        overrides: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Clone an existing Position into a new Position code, inheriting all populated attributes.
        """
        src = self.get_position(source_code)
        if not src:
            raise ValueError(f"Source Position '{source_code}' not found.")

        overrides = overrides or {}

        # Exclude read-only, system, and derived properties
        exclude = {
            "__metadata", "createdDateTime", "createdDate", "createdBy",
            "lastModifiedDateTime", "lastModifiedDate", "lastModifiedBy", "lastModifiedDateWithTZ",
            "mdfSystemRecordId", "mdfSystemEntityId", "mdfSystemVersionId",
            "mdfSystemRecordStatus", "mdfSystemOptimisticLockUUID", "mdfSystemObjectType",
            "legacyPositionId", "incumbent",
            "effectiveEndDate", "regularTemporary", "employeeClass", "jobLevel",
            "type", "transactionSequence", "jobTitle", "payGrade", "positionTitle", "description"
        }

        clean_src = {}
        for k, v in src.items():
            if k in exclude or isinstance(v, dict) or v is None:
                continue
            if k.startswith("externalName_") and k != "externalName_defaultValue":
                continue
            clean_src[k] = v

        base_title = src.get("externalName_defaultValue") or "Position"
        new_title = f"{base_title} {title_suffix}".strip()

        payload = {
            "__metadata": {"uri": "Position"},
            **clean_src,
            "code": target_code,
            "externalName_defaultValue": new_title,
            "vacant": True,
            "cust_employeeClass": src.get("cust_employeeClass") or "EC_01",
            "cust_workerType": src.get("cust_workerType") or "WT_01",
            "cust_employmentType": src.get("cust_employmentType") or "ET_01",
            "cust_payScaleType": src.get("cust_payScaleType") or "USA/US1",
            "cust_payScaleArea": src.get("cust_payScaleArea") or "USA/US2",
            "cust_payScaleGroup": src.get("cust_payScaleGroup") or "USA/PROD/PLANT/TECH",
            "cust_payScaleLevel": src.get("cust_payScaleLevel") or "USA/PROD/PLANT/TECH/L1",
            "standardHours": str(src.get("standardHours") or "40.0"),
            "cust_workingDaysPerWeek": str(src.get("cust_workingDaysPerWeek") or "5.0"),
        }

        payload.update(overrides)

        res = self.client.deep_upsert(payload)
        return {
            "status": "SUCCESS",
            "code": target_code,
            "title": payload["externalName_defaultValue"],
            "company": payload.get("company"),
            "department": payload.get("department"),
            "jobCode": payload.get("jobCode"),
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
