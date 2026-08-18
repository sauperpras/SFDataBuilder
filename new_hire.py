"""
New hire creation for SAP SuccessFactors Employee Central.

Usage:
    python new_hire.py --start-date 2026-08-01 --position 12345 --dry-run

With --dry-run the payload is printed but NOT posted to SF.
"""

import argparse
import json
import sys
from datetime import date

from sf_client import SFClient


# ---------------------------------------------------------------------------
# Payload builder
# ---------------------------------------------------------------------------

_POSITION_ORG_FIELDS = ("company", "businessUnit", "division", "department", "location", "jobCode",
                        "payScaleType", "payScaleArea")


def fetch_position_defaults(client, position_id: str) -> dict:
    """
    Query MDF Position by code to get org fields required by EmpJob.
    Field names map 1:1 from Position to jobInfoNav (company, businessUnit, etc.).
    """
    result = client.get("Position", params={
        "$filter": f"code eq '{position_id}'",
        "$select": "code,company,businessUnit,division,department,location,jobCode,payScaleType,payScaleArea",
    })
    rows = result.get("d", {}).get("results", [])
    if not rows:
        print(f"  WARNING: No Position found with code={position_id} — jobInfoNav will have no org fields.")
        return {}
    pos = rows[0]
    defaults = {f: pos[f] for f in _POSITION_ORG_FIELDS if pos.get(f)}
    return defaults


def build_new_hire_payload(person_id: str, user_id: str, start_date: str, position_id: str,
                           position_defaults: dict = None) -> dict:
    epoch = f"/Date({_to_epoch_ms(start_date)})/"
    return {
        "__metadata": {"uri": "EmpEmployment"},
        "personIdExternal": person_id,
        "userId": user_id,
        "assignmentIdExternal": person_id,
        "assignmentClass": "ST",
        "isContingentWorker": False,
        "startDate": epoch,
        "originalStartDate": epoch,
        "firstDateWorked": epoch,
        "personNav": {
            "personIdExternal": person_id,
            "personalInfoNav": {
                "results": [
                    {
                        "personIdExternal": person_id,
                        "startDate": epoch,
                        "firstName": "New",             # placeholder
                        "lastName": "Hire",             # placeholder
                        "gender": "M",                  # placeholder
                    }
                ]
            },
            "emailNav": {
                "results": [
                    {
                        "personIdExternal": person_id,
                        "emailType": "8448",            # Business — verify with your instance
                        "isPrimary": True,
                        "emailAddress": "newhire@example.com",  # placeholder
                    }
                ]
            },
        },
        "jobInfoNav": {
            "results": [
                {
                    "userId": user_id,
                    "startDate": epoch,
                    "position": position_id,
                    "seqNumber": "1",
                    "eventReason": "HIRNEW",
                    **(position_defaults or {}),
                }
            ]
        },
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _to_epoch_ms(date_str: str) -> int:
    """Convert YYYY-MM-DD to milliseconds since epoch (SF OData /Date(...)/ format)."""
    d = date.fromisoformat(date_str)
    epoch = date(1970, 1, 1)
    return int((d - epoch).total_seconds() * 1000)


ID_START = 80_000_000


def _next_person_id(client) -> str:
    """
    Find the highest userId >= 80000000 in SF and return the next available ID.
    Falls back to 80000000 if none exist yet.
    """
    result = client.get("User", params={
        "$filter": "userId ge '80000000' and userId le '99999999'",
        "$orderby": "userId desc",
        "$top": "10",
        "$select": "userId",
    })
    rows = result.get("d", {}).get("results", [])
    numeric = [int(r["userId"]) for r in rows if r["userId"].isdigit()]
    if numeric:
        return str(max(numeric) + 1)
    return str(ID_START)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def create_new_hire(start_date: str, position_id: str, dry_run: bool = True,
                    overrides: dict = None):
    client = SFClient()

    person_id = _next_person_id(client)
    user_id = person_id
    print(f"\n[1/4] Generated personIdExternal={person_id}, userId={user_id}")

    print(f"\n[2/4] Fetching org defaults from position {position_id} ...")
    position_defaults = fetch_position_defaults(client, position_id)
    if overrides:
        position_defaults.update(overrides)
    print(f"  Org fields: {json.dumps(position_defaults)}")

    user_payload = {
        "userId": user_id,
        "username": user_id,
        "status": "Active",
        "hireDate": f"/Date({_to_epoch_ms(start_date)})/",
    }

    emp_payload = build_new_hire_payload(person_id, user_id, start_date, position_id, position_defaults)

    print("\n[3/5] Step 1 payload — POST User:")
    print(json.dumps(user_payload, indent=4))
    print("\n[4/5] Step 2 payload — POST upsert (EmpEmployment deep insert):")
    print(json.dumps(emp_payload, indent=4))

    if dry_run:
        print("\n[DRY RUN] Payloads shown above — nothing was sent to SuccessFactors.")
        return

    print("\n[5/5] Step 1: Creating User ...")
    user_result = client.post("User", user_payload)
    print(json.dumps(user_result, indent=4) if user_result else "(204 No Content)")

    print("\n       Step 2: Creating EmpEmployment (deep upsert) ...")
    emp_result = client.deep_upsert(emp_payload)
    print(json.dumps(emp_result, indent=4) if emp_result else "(204 No Content)")

    print(f"\nNew hire created. personIdExternal={person_id}, userId={user_id}")


def main():
    parser = argparse.ArgumentParser(description="Create a new hire in SF Employee Central")
    parser.add_argument("--start-date",     required=True, help="Start date YYYY-MM-DD")
    parser.add_argument("--position",       required=True, help="Position external code")
    parser.add_argument("--company",        help="Legal entity / company code (overrides position lookup)")
    parser.add_argument("--business-unit",  help="Business unit code")
    parser.add_argument("--division",       help="Division code")
    parser.add_argument("--department",     help="Department code")
    parser.add_argument("--job-code",       help="Job code")
    parser.add_argument("--cost-center",    help="Cost center code")
    parser.add_argument("--location",       help="Location code")
    parser.add_argument("--employee-class",  help="Employee class picklist ID")
    parser.add_argument("--pay-scale-type",  default="US1", help="Pay scale type code (default: US1)")
    parser.add_argument("--pay-scale-area",  default="US2", help="Pay scale area code (default: US2)")
    parser.add_argument("--dry-run", action="store_true", default=True,
                        help="Print payload without calling the API (default: True)")
    parser.add_argument("--live", action="store_true",
                        help="Actually POST to the API (disables dry-run)")
    args = parser.parse_args()

    overrides = {k: v for k, v in {
        "company":       args.company,
        "businessUnit":  args.business_unit,
        "division":      args.division,
        "department":    args.department,
        "jobCode":       args.job_code,
        "costCenter":    args.cost_center,
        "location":      args.location,
        "employeeClass": args.employee_class,
        "payScaleType":  args.pay_scale_type,
        "payScaleArea":  args.pay_scale_area,
    }.items() if v is not None}

    dry_run = not args.live
    create_new_hire(start_date=args.start_date, position_id=args.position,
                    dry_run=dry_run, overrides=overrides)


if __name__ == "__main__":
    main()
