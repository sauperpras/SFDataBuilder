"""
Metadata Engine & Dynamic Picklist Resolver for SAP SuccessFactors OData v2.
Parses $metadata EDM XML, discovers schemas, validates payloads pre-flight,
and resolves human-readable values to exact picklist option IDs with caching.
"""

import json
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from sf_client import SFClient


EDM_NS = "{http://schemas.microsoft.com/ado/2008/09/edm}"
SAP_NS = "{http://www.sap.com/Protocols/SAPData}"


@dataclass
class EntityProperty:
    name: str
    type: str
    nullable: bool = True
    creatable: bool = True
    updatable: bool = True
    max_length: Optional[int] = None
    label: Optional[str] = None


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

    def is_creatable(self, prop_name: str) -> bool:
        prop = self.properties.get(prop_name)
        return prop.creatable if prop else False


class MetadataEngine:
    def __init__(self, client: SFClient):
        self.client = client
        self._schemas: Dict[str, EntitySchema] = {}
        self._load_metadata()

    def _load_metadata(self):
        """Parse OData v2 EDM XML and build entity schemas."""
        xml_content = self.client.metadata()
        root = ET.fromstring(xml_content)

        for et in root.iter(f"{EDM_NS}EntityType"):
            name = et.attrib.get("Name", "")
            if not name:
                continue

            schema = EntitySchema(name=name)

            # Keys
            key_elem = et.find(f"{EDM_NS}Key")
            if key_elem is not None:
                for prop_ref in key_elem.findall(f"{EDM_NS}PropertyRef"):
                    schema.key_properties.append(prop_ref.attrib.get("Name", ""))

            # Properties
            for prop in et.findall(f"{EDM_NS}Property"):
                pname = prop.attrib.get("Name", "")
                ptype = prop.attrib.get("Type", "Edm.String")
                nullable = prop.attrib.get("Nullable", "true").lower() == "true"
                creatable = prop.attrib.get(f"{SAP_NS}creatable", "true").lower() == "true"
                updatable = prop.attrib.get(f"{SAP_NS}updatable", "true").lower() == "true"
                max_len_str = prop.attrib.get("MaxLength")
                max_len = int(max_len_str) if max_len_str and max_len_str.isdigit() else None
                label = prop.attrib.get(f"{SAP_NS}label")

                schema.properties[pname] = EntityProperty(
                    name=pname,
                    type=ptype,
                    nullable=nullable,
                    creatable=creatable,
                    updatable=updatable,
                    max_length=max_len,
                    label=label,
                )

            # Navigations
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
        Validate and sanitize payload against the entity schema before API execution.
        Strips properties that are definitively marked as non-creatable or not present in schema.
        """
        schema = self.get_schema(entity_name)
        if not schema:
            return payload

        sanitized = {}
        for k, v in payload.items():
            if k == "__metadata":
                sanitized[k] = v
                continue

            if k in schema.properties:
                prop = schema.properties[k]
                if is_upsert and not prop.creatable and not prop.updatable:
                    continue  # Strip non-writable property
                sanitized[k] = v
            elif k in schema.nav_properties:
                sanitized[k] = v
            else:
                # Custom fields or dynamic MDF properties
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
            res = self.client.get(f"Picklist('{picklist_id}')", params={"$expand": "picklistOptions"})
            options = res.get("d", {}).get("picklistOptions", {}).get("results", [])
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
        except Exception:
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
            return val_str

        # 1. Exact ID match
        for opt in options:
            if opt["id"] == val_str:
                return opt["id"]

        # 2. Exact externalCode match (case-insensitive)
        for opt in options:
            if opt["externalCode"].lower() == val_str.lower():
                return opt["id"] if opt["id"] != "None" else opt["externalCode"]

        # 3. Partial match on externalCode
        for opt in options:
            if val_str.lower() in opt["externalCode"].lower() or opt["externalCode"].lower() in val_str.lower():
                return opt["id"]

        return default or val_str

    def search(self, picklist_id: str, query: str = "") -> List[Dict[str, Any]]:
        """Search options within a picklist."""
        options = self.get_picklist_options(picklist_id)
        if not query:
            return options
        q = query.lower()
        return [opt for opt in options if q in opt["id"].lower() or q in opt["externalCode"].lower()]
