"""
Metadata Engine & Dynamic Picklist Resolver for SAP SuccessFactors OData v2.
Parses $metadata EDM XML with full SAP SuccessFactors annotations, discovers schemas,
enforces strict contract-first pre-flight validation, and resolves dynamic picklists.
"""

import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from sf_client import SFClient


EDM_NS = "{http://schemas.microsoft.com/ado/2008/09/edm}"
SF_SAP_NS = "{http://www.successfactors.com/edm/sap}"
SAP_STD_NS = "{http://www.sap.com/Protocols/SAPData}"


def _get_sap_attr(element: ET.Element, attr_name: str, default: Optional[str] = None) -> Optional[str]:
    """Helper to retrieve SAP metadata attribute regardless of namespace variant."""
    for ns in [SF_SAP_NS, SAP_STD_NS]:
        val = element.attrib.get(f"{ns}{attr_name}")
        if val is not None:
            return val
    return element.attrib.get(attr_name, default)


def _to_epoch_ms(date_str: str) -> int:
    """Convert YYYY-MM-DD to milliseconds since epoch (SF OData /Date(...)/ format)."""
    d = date.fromisoformat(str(date_str).split("T")[0])
    epoch = date(1970, 1, 1)
    return int((d - epoch).total_seconds() * 1000)


def _format_date(date_str: str) -> str:
    """Format date string to SF /Date(ms)/."""
    if str(date_str).startswith("/Date(") and str(date_str).endswith(")/"):
        return str(date_str)
    return f"/Date({_to_epoch_ms(str(date_str))})/"


@dataclass
class EntityProperty:
    name: str
    type: str
    nullable: bool = True
    required: bool = False
    creatable: bool = True
    updatable: bool = True
    upsertable: bool = True
    visible: bool = True
    max_length: Optional[int] = None
    picklist_id: Optional[str] = None
    display_format: Optional[str] = None
    label: Optional[str] = None

    @property
    def is_writable(self) -> bool:
        """True if field can be written to via POST or Upsert."""
        return self.upsertable or self.creatable or self.updatable


@dataclass
class NavigationProperty:
    name: str
    relationship: str
    from_role: Optional[str] = None
    to_role: Optional[str] = None


@dataclass
class EntitySchema:
    name: str
    properties: Dict[str, EntityProperty] = field(default_factory=dict)
    nav_properties: Dict[str, NavigationProperty] = field(default_factory=dict)
    key_properties: List[str] = field(default_factory=list)

    def get_required_properties(self) -> List[str]:
        """List of property names that are strictly required by SuccessFactors."""
        return [p.name for p in self.properties.values() if p.required]

    def get_writable_properties(self) -> List[str]:
        """List of property names that can be sent in upsert/create payloads."""
        return [p.name for p in self.properties.values() if p.is_writable]

    def is_writable(self, prop_name: str) -> bool:
        prop = self.properties.get(prop_name)
        return prop.is_writable if prop else True


class MetadataEngine:
    def __init__(self, client: SFClient):
        self.client = client
        self._schemas: Dict[str, EntitySchema] = {}
        self._load_metadata()

    def _load_metadata(self):
        """Parse OData v2 EDM XML with full SuccessFactors annotations."""
        xml_content = self.client.metadata()
        root = ET.fromstring(xml_content)

        for et in root.iter(f"{EDM_NS}EntityType"):
            name = et.attrib.get("Name", "")
            if not name:
                continue

            schema = EntitySchema(name=name)

            # 1. Primary Keys
            key_elem = et.find(f"{EDM_NS}Key")
            if key_elem is not None:
                for prop_ref in key_elem.findall(f"{EDM_NS}PropertyRef"):
                    schema.key_properties.append(prop_ref.attrib.get("Name", ""))

            # 2. Properties & SAP Annotations
            for prop in et.findall(f"{EDM_NS}Property"):
                pname = prop.attrib.get("Name", "")
                ptype = prop.attrib.get("Type", "Edm.String")
                nullable = prop.attrib.get("Nullable", "true").lower() == "true"
                
                req_val = _get_sap_attr(prop, "required", "false").lower() == "true"
                required = req_val or not nullable

                creatable = _get_sap_attr(prop, "creatable", "true").lower() == "true"
                updatable = _get_sap_attr(prop, "updatable", "true").lower() == "true"
                upsertable = _get_sap_attr(prop, "upsertable", "true").lower() == "true"
                visible = _get_sap_attr(prop, "visible", "true").lower() == "true"

                max_len_str = prop.attrib.get("MaxLength")
                max_len = int(max_len_str) if max_len_str and max_len_str.isdigit() else None

                picklist_id = _get_sap_attr(prop, "picklist")
                display_format = _get_sap_attr(prop, "display-format")
                label = _get_sap_attr(prop, "label")

                schema.properties[pname] = EntityProperty(
                    name=pname,
                    type=ptype,
                    nullable=nullable,
                    required=required,
                    creatable=creatable,
                    updatable=updatable,
                    upsertable=upsertable,
                    visible=visible,
                    max_length=max_len,
                    picklist_id=picklist_id,
                    display_format=display_format,
                    label=label,
                )

            # 3. Navigation Properties
            for nav in et.findall(f"{EDM_NS}NavigationProperty"):
                nname = nav.attrib.get("Name", "")
                rel = nav.attrib.get("Relationship", "")
                to_role = nav.attrib.get("ToRole")
                from_role = nav.attrib.get("FromRole")
                schema.nav_properties[nname] = NavigationProperty(
                    name=nname,
                    relationship=rel,
                    from_role=from_role,
                    to_role=to_role,
                )

            self._schemas[name] = schema

    def get_schema(self, entity_name: str) -> Optional[EntitySchema]:
        return self._schemas.get(entity_name)

    def get_all_entities(self) -> List[str]:
        return sorted(self._schemas.keys())

    def validate_payload(self, entity_name: str, payload: Dict[str, Any], is_upsert: bool = True) -> Dict[str, Any]:
        """
        Strict Contract-First Validation:
        1. Strips all read-only, non-upsertable system properties (e.g. mdfSystem*, createdDateTime, lastModified*).
        2. Formats and coerces data types (Edm.DateTime -> /Date(...)/, Edm.Boolean -> bool).
        3. Enforces string length limits and strips null values on optional fields.
        """
        schema = self.get_schema(entity_name)
        if not schema:
            return payload

        sanitized: Dict[str, Any] = {}

        # Preserve metadata header
        if "__metadata" in payload:
            sanitized["__metadata"] = payload["__metadata"]
        else:
            sanitized["__metadata"] = {"uri": entity_name}

        # Known system properties that must never be written
        system_readonly_prefixes = ("mdfSystem", "lastModified", "createdDate", "createdBy")

        for k, v in payload.items():
            if k == "__metadata":
                continue

            # Strip system fields
            if any(k.startswith(pfx) for pfx in system_readonly_prefixes):
                continue

            if k in schema.properties:
                prop = schema.properties[k]
                
                # Check if writable
                if not prop.is_writable:
                    continue  # Strip non-writable property

                # Handle None values (omit optional nulls to avoid SF validation conflicts)
                if v is None:
                    if prop.required:
                        pass  # Let required field handling or default take over
                    else:
                        continue

                # Type coercion & formatting
                val = v
                if prop.type in ("Edm.DateTime", "Edm.DateTimeOffset") or prop.display_format == "Date":
                    if val is not None and not str(val).startswith("/Date("):
                        val = _format_date(str(val))
                elif prop.type == "Edm.Boolean":
                    if isinstance(val, str):
                        val = val.lower() in ("true", "1", "yes", "y")
                    elif isinstance(val, (int, float)):
                        val = bool(val)
                elif prop.type == "Edm.String" and prop.max_length and isinstance(val, str):
                    if len(val) > prop.max_length:
                        val = val[:prop.max_length]

                sanitized[k] = val

            elif k in schema.nav_properties:
                sanitized[k] = v
            elif k.startswith("cust_") or k.startswith("customString"):
                # Retain custom fields
                sanitized[k] = v

        return sanitized


class PicklistResolver:
    def __init__(self, client: SFClient, cache_file: str = ".sf_picklists.json"):
        self.client = client
        p = Path(cache_file)
        if not p.is_absolute():
            base_dir = Path(__file__).resolve().parent
            p = base_dir / cache_file
        self.cache_file = p
        self._cache: Dict[str, List[Dict[str, Any]]] = self._load_cache()

    def _load_cache(self) -> Dict[str, List[Dict[str, Any]]]:
        if self.cache_file.exists():
            try:
                return json.loads(self.cache_file.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {}

    def _save_cache(self):
        try:
            self.cache_file.write_text(json.dumps(self._cache, indent=2), encoding="utf-8")
        except Exception:
            pass

    def get_picklist_options(self, picklist_id: str, force_refresh: bool = False) -> List[Dict[str, Any]]:
        """Fetch and cache options for a given picklist."""
        if not force_refresh and picklist_id in self._cache:
            return self._cache[picklist_id]

        try:
            # 1. Try legacy Picklist API
            res = self.client.get(f"Picklist('{picklist_id}')", params={"$expand": "picklistOptions"})
            options = res.get("d", {}).get("picklistOptions", {}).get("results", [])
            if options:
                clean_opts = [
                    {
                        "id": str(opt.get("id")),
                        "externalCode": str(opt.get("externalCode") or ""),
                        "status": opt.get("status"),
                    }
                    for opt in options
                ]
                self._cache[picklist_id] = clean_opts
                self._save_cache()
                return clean_opts

            # 2. Try MDF PickListV2 API fallback
            mdf_res = self.client.get("PickListValueV2", params={"$filter": f"PickListV2_id eq '{picklist_id}' and status eq 'A'"})
            mdf_options = mdf_res.get("d", {}).get("results", [])
            if mdf_options:
                clean_opts = [
                    {
                        "id": str(opt.get("externalCode")),
                        "externalCode": str(opt.get("externalCode") or ""),
                        "status": opt.get("status"),
                        "label": opt.get("label_defaultValue", opt.get("externalCode")),
                    }
                    for opt in mdf_options
                ]
                self._cache[picklist_id] = clean_opts
                self._save_cache()
                return clean_opts

        except Exception:
            pass

        return []

    def resolve(self, picklist_id: str, value: Any, default: Optional[str] = None) -> Optional[str]:
        """
        Resolve human-readable label, code, or existing ID to a valid picklist option ID / code.
        """
        if value is None:
            return default

        val_str = str(value).strip()
        options = self.get_picklist_options(picklist_id)
        if not options:
            return default or val_str

        # 1. Exact ID match
        for opt in options:
            if opt["id"] == val_str:
                return opt["id"]

        # 2. Exact externalCode match (case-insensitive)
        for opt in options:
            if opt.get("externalCode", "").lower() == val_str.lower():
                return opt["id"] if opt["id"] != "None" else opt["externalCode"]

        # 3. Label match
        for opt in options:
            if opt.get("label", "").lower() == val_str.lower():
                return opt["id"]

        # 4. Partial match on externalCode or label
        for opt in options:
            if val_str.lower() in opt.get("externalCode", "").lower() or val_str.lower() in opt.get("label", "").lower():
                return opt["id"]

        return default or val_str

    def search(self, picklist_id: str, query: str = "") -> List[Dict[str, Any]]:
        """Search options within a picklist."""
        options = self.get_picklist_options(picklist_id)
        if not query:
            return options
        q = query.lower()
        return [
            opt for opt in options
            if q in opt.get("id", "").lower() or q in opt.get("externalCode", "").lower() or q in opt.get("label", "").lower()
        ]
