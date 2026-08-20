"""
Automated Test Suite for Metadata Engine, Dynamic Picklist Resolver,
Profile Builder, Position Service, and FastMCP Server Tools.
"""

import json
from sf_client import SFClient
from metadata_engine import MetadataEngine, PicklistResolver
from position_service import PositionService
from profile_builder import ProfileBuilder
from hire_service import HireService
import server


def test_metadata_engine():
    print("\n=== 1. Testing Metadata Engine ===")
    client = SFClient()
    engine = MetadataEngine(client)

    schema_empjob = engine.get_schema("EmpJob")
    assert schema_empjob is not None, "EmpJob schema should exist"
    assert "userId" in schema_empjob.properties, "userId property should exist on EmpJob"
    assert "startDate" in schema_empjob.properties, "startDate property should exist on EmpJob"
    print(f"  [OK] EmpJob schema loaded with {len(schema_empjob.properties)} properties.")

    schema_pos = engine.get_schema("Position")
    assert schema_pos is not None, "Position schema should exist"
    assert "code" in schema_pos.properties, "code property should exist on Position"
    print(f"  [OK] Position schema loaded with {len(schema_pos.properties)} properties.")


def test_picklist_resolver():
    print("\n=== 2. Testing Dynamic Picklist Resolver ===")
    client = SFClient()
    resolver = PicklistResolver(client)

    sal_mr = resolver.resolve("salutation", "Mr")
    sal_ms = resolver.resolve("salutation", "Ms")
    suf_iii = resolver.resolve("SUFFIX", "III")
    email_biz = resolver.resolve("ecEmailType", "Business")

    print(f"  Resolved 'Mr' in salutation: {sal_mr}")
    print(f"  Resolved 'Ms' in salutation: {sal_ms}")
    print(f"  Resolved 'III' in SUFFIX: {suf_iii}")
    print(f"  Resolved 'Business' in ecEmailType: {email_biz}")

    assert sal_mr is not None and sal_mr != "Mr", "Should resolve Mr to picklist ID"
    assert suf_iii is not None, "Should resolve III"
    print("  [OK] Picklist dynamic resolution verified.")


def test_profile_builder_and_dry_run():
    print("\n=== 3. Testing Profile Builder & Dry Run ===")
    client = SFClient()
    service = HireService(client)

    inputs = {
        "start_date": "2026-08-01",
        "position": "3000803",
        "first_name": "TestUser",
        "last_name": "Engine",
        "gender": "F",
        "salutation": "Ms",
        "marital_status": "Single",
        "nationality": "USA",
    }

    res = service.hire_employee(inputs, dry_run=True)
    assert res["status"] == "DRY_RUN", "Dry run status should be DRY_RUN"
    payloads = res["plan"]
    assert "User" in payloads
    assert "PerPersonal" in payloads
    assert "PerNationalId" in payloads
    assert "PerEmail" in payloads
    assert "EmpEmployment" in payloads
    assert "EmpJob" in payloads

    # Verify dynamic picklist resolution was applied inside payloads
    assert payloads["PerPersonal"]["salutation"] == "10810", f"Expected 10810 for Ms, got {payloads['PerPersonal']['salutation']}"
    assert payloads["EmpJob"]["company"] == "1710", "Should inherit Company 1710 from position"
    assert payloads["EmpJob"]["jobTitle"] == "Senior Designer", "Should inherit Job Title from position"
    print("  [OK] Profile Builder generated complete valid plan with position defaults & dynamic picklists.")


def test_mcp_server_tools():
    print("\n=== 4. Testing MCP Server Tool Functions ===")
    # 1. Test sf_get_schema
    schema_res = json.loads(server.sf_get_schema("PerPersonal"))
    assert schema_res.get("name") == "PerPersonal"
    print(f"  [OK] sf_get_schema tool returned valid schema for PerPersonal.")

    # 2. Test sf_search_picklist
    pick_res = json.loads(server.sf_search_picklist("salutation", "Mr"))
    assert len(pick_res) > 0
    print(f"  [OK] sf_search_picklist tool returned {len(pick_res)} options.")

    # 3. Test sf_get_position
    pos_res = json.loads(server.sf_get_position("3000803"))
    assert pos_res.get("code") == "3000803"
    print(f"  [OK] sf_get_position tool returned valid data for 3000803.")

    # 4. Test sf_hire_employee dry-run
    hire_res = json.loads(server.sf_hire_employee({
        "start_date": "2026-08-01",
        "position": "3000803",
        "first_name": "MCPTest",
        "last_name": "Agent",
    }, dry_run=True))
    assert hire_res.get("status") == "DRY_RUN"
    print(f"  [OK] sf_hire_employee MCP tool dry-run successful.")


def main():
    test_metadata_engine()
    test_picklist_resolver()
    test_profile_builder_and_dry_run()
    test_mcp_server_tools()
    print("\n=======================================================")
    print(" ALL TESTS PASSED SUCCESSFULLY!")
    print("=======================================================\n")


if __name__ == "__main__":
    main()
