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
# ---------------------------------------------------------------------------

def build_user(user_id: str) -> dict:
    return {
        "userId": user_id,
        "username": user_id,
        "status": "Active",
    }


def build_per_person(person_id: str) -> dict:
    return {
        "personIdExternal": person_id,
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
        "eventReason": "HIRNEW",
        # Fields below are typically derived from Position in SF EC.
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
    print(f"\n[1/3] Generated personIdExternal={person_id}, userId={user_id}")

    # Order matches the sample batch: User → PerPerson → EmpEmployment → EmpJob → PerPersonal → PerEmail
    operations = [
        {"entity": "User",          "payload": build_user(user_id)},
        {"entity": "PerPerson",     "payload": build_per_person(person_id)},
        {"entity": "EmpEmployment", "payload": build_emp_employment(person_id, user_id, start_date)},
        {"entity": "EmpJob",        "payload": build_emp_job(user_id, start_date, position_id)},
        {"entity": "PerPersonal",   "payload": build_per_personal(person_id, start_date)},
        {"entity": "PerEmail",      "payload": build_per_email(person_id, start_date)},
    ]

    print("\n[2/3] Batch operations:")
    for op in operations:
        print(f"  POST {op['entity']}")
        print(f"  {json.dumps(op['payload'], indent=4)}")

    if dry_run:
        print("\n[DRY RUN] Operations shown above — nothing was sent to SuccessFactors.")
        return

    print("\n[3/3] Sending $batch request ...")
    response_text = client.batch(operations)
    print(response_text)

    # Check for errors in the batch response
    if '"error"' in response_text or 'HTTP/1.1 4' in response_text or 'HTTP/1.1 5' in response_text:
        print("\nWARNING: One or more batch operations may have failed — check response above.")
    else:
        print(f"\nNew hire created. personIdExternal={person_id}, userId={user_id}")


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
