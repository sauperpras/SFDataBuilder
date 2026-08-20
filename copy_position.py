"""
Utility script to clone/copy a Position in SAP SuccessFactors Employee Central.

Usage:
    python copy_position.py --source 3000803 --target 3000803_COPY
"""

import argparse
import json
import sys
from sf_client import SFClient


def copy_position(source_code: str, target_code: str, title_suffix: str = "(Copy)"):
    client = SFClient()

    print(f"Fetching source position '{source_code}' ...")
    res = client.get("Position", params={"$filter": f"code eq '{source_code}'"})
    results = res.get("d", {}).get("results", [])
    if not results:
        raise Exception(f"Source position '{source_code}' not found.")

    src = results[0]

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
        "cust_employeeClass": "EC_01",
        "cust_workerType": "WT_01",
        "cust_employmentType": "ET_01",
        "cust_payScaleType": "USA/US1",
        "cust_payScaleArea": "USA/US2",
        "cust_payScaleGroup": "USA/PROD/PLANT/TECH",
        "cust_payScaleLevel": "USA/PROD/PLANT/TECH/L1",
        "standardHours": "40.0",
        "cust_workingDaysPerWeek": "5.0",
    }

    print(f"\nUpserting Position '{target_code}' ...")
    client.deep_upsert(payload)
    print(f"SUCCESS: Position '{target_code}' created as a clone of '{source_code}'.")
    return payload


def main():
    parser = argparse.ArgumentParser(description="Clone an existing Position in SF Employee Central")
    parser.add_argument("--source", required=True, help="Source Position external code (e.g. 3000803)")
    parser.add_argument("--target", required=True, help="Target Position external code (e.g. 3000803_COPY)")
    parser.add_argument("--suffix", default="(Copy)", help="Title suffix (default: '(Copy)')")
    args = parser.parse_args()

    copy_position(source_code=args.source, target_code=args.target, title_suffix=args.suffix)


if __name__ == "__main__":
    main()
