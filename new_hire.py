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
        "countryOfBirth": "USA",        # 3-letter ISO code as used in this instance
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
    epoch = f"/Date({_to_epoch_ms(start_date)})/"
    return {
        "personIdExternal": person_id,
        "userId": user_id,
        "assignmentIdExternal": person_id,
        "assignmentClass": "ST",
        "isContingentWorker": False,
        "isECRecord": True,
        "startDate": epoch,
        "originalStartDate": epoch,
        "seniorityDate": epoch,
        "serviceDate": epoch,
        "firstDateWorked": epoch,
        "benefitsEligibilityStartDate": epoch,
    }


def build_emp_job(user_id: str, start_date: str, position_id: str) -> dict:
    return {
        "userId": user_id,
        "startDate": f"/Date({_to_epoch_ms(start_date)})/",
        "position": position_id,
        "seqNumber": "1",
        "emplStatus": "4595",           # Active picklist ID — verify with your instance
        "eventReason": "HIRNEW",        # New hire event reason
        # Fields below are typically derived from Position in SF EC;
        # leaving them commented so SF can default them from the position.
        # "company": "...",
        # "businessUnit": "...",
        # "division": "...",
        # "department": "...",
        # "jobCode": "...",
        # "costCenter": "...",
        # "location": "...",
        # "employeeClass": "...",       # picklist ID
        # "employmentType": "...",      # picklist ID
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _to_epoch_ms(date_str: str) -> int:
    """Convert YYYY-MM-DD to milliseconds since epoch (SF OData /Date(...)/ format)."""
    d = date.fromisoformat(date_str)
    epoch = date(1970, 1, 1)
    return int((d - epoch).total_seconds() * 1000)


def _sf_datetime(date_str: str) -> str:
    """Convert YYYY-MM-DD to SF OData key datetime literal: datetime'YYYY-MM-DDTHH:MM:SS'."""
    return f"datetime'{date_str}T00:00:00'"


def _entity_key(entity: str, payload: dict, start_date: str) -> str:
    """Return the OData key path for upsert PUT (e.g. PerPerson('id'))."""
    pid = payload.get("personIdExternal", "")
    uid = payload.get("userId", "")
    dt  = _sf_datetime(start_date)
    keys = {
        "PerPerson":     f"PerPerson('{pid}')",
        "PerPersonal":   f"PerPersonal(personIdExternal='{pid}',startDate={dt})",
        "PerEmail":      f"PerEmail(personIdExternal='{pid}',startDate={dt},emailType='{payload.get('emailType', '')}')",
        "EmpEmployment": f"EmpEmployment(personIdExternal='{pid}',userId='{uid}')",
        "EmpJob":        f"EmpJob(seqNumber=1L,startDate={dt},userId='{uid}')",
    }
    return keys[entity]


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

    person_id = _new_person_id()
    user_id = _new_user_id(person_id)
    print(f"\n[1/4] Generated personIdExternal={person_id}, userId={user_id}")

    # PerPerson is auto-created by SF when PerPersonal is upserted; do not write it directly.
    payloads = {
        "PerPersonal":    build_per_personal(person_id, start_date),
        "PerEmail":       build_per_email(person_id, start_date),
        "EmpEmployment":  build_emp_employment(person_id, user_id, start_date),
        "EmpJob":         build_emp_job(user_id, start_date, position_id),
    }

    print("\n[2/4] Payloads:")
    print(json.dumps(payloads, indent=2))

    if dry_run:
        print("\n[DRY RUN] Payloads shown above — nothing was sent to SuccessFactors.")
        return

    # Upsert in order (PerPerson must exist before PerPersonal/EmpEmployment)
    print("\n[3/4] Upserting entities ...")
    for entity, payload in payloads.items():
        key_path = _entity_key(entity, payload, start_date)
        print(f"  PUT {key_path} ...", end=" ")
        result = client.upsert(key_path, payload)
        ref = result.get('d', {}).get('personIdExternal') or result.get('d', {}).get('userId', '') or "(204 No Content)"
        print(f"OK  →  {ref}")

    print(f"\n[4/4] New hire created. personIdExternal={person_id}, userId={user_id}")


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
