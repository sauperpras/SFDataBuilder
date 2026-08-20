"""
SAP SuccessFactors Model Context Protocol (MCP) Server.
Exposes metadata-driven tools for employee onboarding, position management,
picklist resolution, and generic OData operations.
"""

import json
from typing import Any, Dict, Optional
from mcp.server import MCPServer

from sf_client import SFClient
from metadata_engine import MetadataEngine, PicklistResolver
from position_service import PositionService
from hire_service import HireService


mcp = MCPServer("sap-successfactors")

# Initialize core services
_client = SFClient()
_meta_engine = MetadataEngine(_client)
_picklists = PicklistResolver(_client)
_pos_service = PositionService(_client)
_hire_service = HireService(_client)


@mcp.tool()
def sf_hire_employee(inputs: Dict[str, Any], dry_run: bool = False) -> str:
    """
    Create a new employee profile in SAP SuccessFactors Employee Central.
    
    Accepts arbitrary user inputs (e.g. first_name, last_name, position, start_date,
    gender, marital_status, email, ssn, department, custom fields, etc.).
    Automatically resolves human-readable values to picklist IDs, applies Position org
    defaults, and fills in sensible compliant defaults for any omitted fields.
    
    Args:
        inputs: Dictionary of employee attributes and overrides.
        dry_run: If True, returns the generated validation plan without writing to SF.
    """
    try:
        res = _hire_service.hire_employee(inputs, dry_run=dry_run)
        return json.dumps(res, indent=2)
    except Exception as e:
        return json.dumps({"status": "ERROR", "message": str(e)}, indent=2)


@mcp.tool()
def sf_clone_position(
    source_code: str,
    target_code: str,
    title_suffix: str = "(Copy)",
    overrides: Optional[Dict[str, Any]] = None,
) -> str:
    """
    Clone an existing Position into a new code, inheriting all org and custom attributes.
    
    Args:
        source_code: External code of the existing source position (e.g. '3000803').
        target_code: External code for the new position (e.g. '3000803_COPY').
        title_suffix: Suffix appended to the position title (default: '(Copy)').
        overrides: Optional dictionary of attributes to override on the cloned position.
    """
    try:
        res = _pos_service.clone_position(
            source_code=source_code,
            target_code=target_code,
            title_suffix=title_suffix,
            overrides=overrides,
        )
        return json.dumps(res, indent=2)
    except Exception as e:
        return json.dumps({"status": "ERROR", "message": str(e)}, indent=2)


@mcp.tool()
def sf_get_position(position_code: str) -> str:
    """
    Retrieve full details and organizational hierarchy for a Position.
    
    Args:
        position_code: External code of the position (e.g. '3000803').
    """
    try:
        pos = _pos_service.get_position(position_code)
        if not pos:
            return json.dumps({"status": "NOT_FOUND", "message": f"Position '{position_code}' not found."}, indent=2)
        clean = {k: v for k, v in pos.items() if not isinstance(v, dict)}
        return json.dumps(clean, indent=2)
    except Exception as e:
        return json.dumps({"status": "ERROR", "message": str(e)}, indent=2)


@mcp.tool()
def sf_list_positions(limit: int = 10, filter_query: Optional[str] = None) -> str:
    """
    List positions in the tenant with optional OData filter.
    
    Args:
        limit: Maximum number of positions to return (default: 10).
        filter_query: OData $filter string (e.g. "company eq '1710' and vacant eq true").
    """
    try:
        positions = _pos_service.list_positions(top=limit, filter_query=filter_query)
        clean = [
            {k: v for k, v in p.items() if not isinstance(v, dict)}
            for p in positions
        ]
        return json.dumps(clean, indent=2)
    except Exception as e:
        return json.dumps({"status": "ERROR", "message": str(e)}, indent=2)


@mcp.tool()
def sf_search_picklist(picklist_id: str, query: str = "") -> str:
    """
    Search options and inspect values for a SuccessFactors picklist.
    
    Args:
        picklist_id: Picklist ID (e.g. 'salutation', 'SUFFIX', 'ecEmailType', 'Gender', 'ecMaritalStatus').
        query: Optional search term to filter options by label or ID.
    """
    try:
        opts = _picklists.search(picklist_id, query)
        return json.dumps(opts, indent=2)
    except Exception as e:
        return json.dumps({"status": "ERROR", "message": str(e)}, indent=2)


@mcp.tool()
def sf_get_schema(entity_name: str) -> str:
    """
    Inspect the metadata schema of an entity, including properties, data types, nullability, and navigations.
    
    Args:
        entity_name: Entity name (e.g. 'User', 'PerPersonal', 'EmpJob', 'EmpEmployment', 'Position').
    """
    try:
        schema = _meta_engine.get_schema(entity_name)
        if not schema:
            return json.dumps({"status": "NOT_FOUND", "message": f"Entity '{entity_name}' not found in metadata."}, indent=2)
        
        return json.dumps({
            "name": schema.name,
            "key_properties": schema.key_properties,
            "properties": {
                p.name: {
                    "type": p.type,
                    "nullable": p.nullable,
                    "creatable": p.creatable,
                    "updatable": p.updatable,
                    "max_length": p.max_length,
                    "label": p.label,
                }
                for p in schema.properties.values()
            },
            "navigation_properties": [
                {"name": n.name, "to_role": n.to_role}
                for n in schema.nav_properties.values()
            ]
        }, indent=2)
    except Exception as e:
        return json.dumps({"status": "ERROR", "message": str(e)}, indent=2)


@mcp.tool()
def sf_query(
    entity: str,
    filter: Optional[str] = None,
    select: Optional[str] = None,
    top: int = 10,
    expand: Optional[str] = None,
) -> str:
    """
    Execute a generic OData query on any SAP SuccessFactors entity.
    
    Args:
        entity: OData entity name (e.g. 'User', 'EmpJob', 'Position', 'FODepartment').
        filter: OData $filter clause (e.g. "userId eq '900154'").
        select: Comma-separated field list for $select.
        top: Max records to return for $top (default: 10).
        expand: Navigation properties for $expand.
    """
    try:
        params = {"$top": str(top)}
        if filter:
            params["$filter"] = filter
        if select:
            params["$select"] = select
        if expand:
            params["$expand"] = expand

        res = _client.get(entity, params=params)
        records = res.get("d", {}).get("results", [])
        if not records and "d" in res and not isinstance(res["d"], list):
            records = [res["d"]]

        clean = [
            {k: v for k, v in r.items() if not isinstance(v, dict)}
            for r in records
        ]
        return json.dumps(clean, indent=2)
    except Exception as e:
        return json.dumps({"status": "ERROR", "message": str(e)}, indent=2)


@mcp.tool()
def sf_upsert_entity(entity: str, payload: Dict[str, Any]) -> str:
    """
    Execute a direct deep upsert for an entity with pre-flight schema validation.
    
    Args:
        entity: Target entity name (e.g. 'PerPersonal', 'EmpJob', 'Position').
        payload: Entity payload dictionary.
    """
    try:
        if "__metadata" not in payload:
            payload["__metadata"] = {"uri": entity}
        validated = _meta_engine.validate_payload(entity, payload)
        res = _client.deep_upsert(validated)
        return json.dumps(res, indent=2)
    except Exception as e:
        return json.dumps({"status": "ERROR", "message": str(e)}, indent=2)


if __name__ == "__main__":
    import anyio
    anyio.run(mcp.run_stdio_async)
