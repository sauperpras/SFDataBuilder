"""
New hire creation for SAP SuccessFactors Employee Central.

Usage:
    python new_hire.py --start-date 2026-08-01 --position 12345 --dry-run

With --dry-run the payload is printed but NOT posted to SF.
"""

import argparse
import json
import sys
import uuid
from datetime import date

from sf_client import SFClient


# ---------------------------------------------------------------------------
# Payload builders
# Minimum required fields are based on standard EC OData v2 new-hire flow.
# Adjust once the OData dictionary is reviewed for this specific instance.
# ---------------------------------------------------------------------------

def build_per_person(person_id: str) -> dict:
    return {
        "personIdExternal": person_id,
        "dateOfBirth": "/Date(0)/",     # placeholder — update with real DOB
        "countryOfBirth": "US",         # placeholder
    }


def build_per_personal(person_id: str, start_date: str) -> dict:
    return {
        "personIdExternal": person_id,
        "startDate": f"/Date({_to_epoch_ms(start_date)})/",
        "firstName": "New",             # placeholder
        "lastName": "Hire",             # placeholder
        "gender": "M",                  # placeholder
    }


def build_per_email(person_id: str, start_date: str) -> dict:
    return {
        "personIdExternal": person_id,
        "startDate": f"/Date({_to_epoch_ms(start_date)})/",
        "emailType": "8448",            # Business — verify with your instance
        "isPrimary": True,
        "emailAddress": "newhire@example.com",  # placeholder
    }


def build_emp_employment(person_id: str, user_id: str, start_date: str) -> dict:
    return {
        "personIdExternal": person_id,
        "userId": user_id,
        "startDate": f"/Date({_to_epoch_ms(start_date)})/",
        "originalStartDate": f"/Date({_to_epoch_ms(start_date)})/",
        "seniorityDate": f"/Date({_to_epoch_ms(start_date)})/",
        "employmentType": "Employee",   # verify with your instance picklist
    }


def build_emp_job(user_id: str, start_date: str, position_id: str) -> dict:
    return {
        "userId": user_id,
        "startDate": f"/Date({_to_epoch_ms(start_date)})/",
        "position": position_id,
        "seqNumber": 1,
        "emplStatus": "A",              # Active — verify picklist value
        "employeeType": "Regular",      # verify with your instance
        # Fields below are typically derived from Position in SF EC;
        # leaving them commented so SF can default them from the position.
        # "company": "...",
        # "department": "...",
        # "jobCode": "...",
        # "costCenter": "...",
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _to_epoch_ms(date_str: str) -> int:
    """Convert YYYY-MM-DD to milliseconds since epoch (SF OData /Date(...)/ format)."""
    d = date.fromisoformat(date_str)
    epoch = date(1970, 1, 1)
    return int((d - epoch).total_seconds() * 1000)


def _new_person_id() -> str:
    """Generate a unique external person ID. Replace with your ID scheme."""
    return f"NH-{uuid.uuid4().hex[:8].upper()}"


def _new_user_id(person_id: str) -> str:
    """Derive a userId from the personIdExternal. Adjust to your convention."""
    return person_id


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def create_new_hire(start_date: str, position_id: str, dry_run: bool = True):
    client = SFClient()

    # Step 0: validate the position exists
    print(f"\n[1/5] Looking up position {position_id} ...")
    if not dry_run:
        pos_result = client.check_position(position_id)
        positions = pos_result.get("d", {}).get("results", [])
        if not positions:
            print(f"ERROR: Position '{position_id}' not found in the system.")
            sys.exit(1)
        print(f"      Found: {positions[0]}")
    else:
        print("      (dry-run: skipping position lookup)")

    person_id = _new_person_id()
    user_id = _new_user_id(person_id)
    print(f"\n[2/5] Generated personIdExternal={person_id}, userId={user_id}")

    payloads = {
        "PerPerson":      build_per_person(person_id),
        "PerPersonal":    build_per_personal(person_id, start_date),
        "PerEmail":       build_per_email(person_id, start_date),
        "EmpEmployment":  build_emp_employment(person_id, user_id, start_date),
        "EmpJob":         build_emp_job(user_id, start_date, position_id),
    }

    print("\n[3/5] Payloads:")
    print(json.dumps(payloads, indent=2))

    if dry_run:
        print("\n[DRY RUN] Payloads shown above — nothing was sent to SuccessFactors.")
        return

    # Step 4: post in order (PerPerson must exist before PerPersonal/EmpEmployment)
    print("\n[4/5] Posting entities ...")
    for entity, payload in payloads.items():
        print(f"  POST {entity} ...", end=" ")
        result = client.post(entity, payload)
        print(f"OK  →  {result.get('d', {}).get('personIdExternal') or result.get('d', {}).get('userId', '')}")

    print(f"\n[5/5] New hire created. personIdExternal={person_id}, userId={user_id}")


def main():
    parser = argparse.ArgumentParser(description="Create a new hire in SF Employee Central")
    parser.add_argument("--start-date", required=True, help="Start date YYYY-MM-DD")
    parser.add_argument("--position",   required=True, help="Position external code")
    parser.add_argument("--dry-run", action="store_true", default=True,
                        help="Print payload without calling the API (default: True)")
    parser.add_argument("--live", action="store_true",
                        help="Actually POST to the API (disables dry-run)")
    args = parser.parse_args()

    dry_run = not args.live
    create_new_hire(start_date=args.start_date, position_id=args.position, dry_run=dry_run)


if __name__ == "__main__":
    main()
