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

def build_new_hire_payload(person_id: str, user_id: str, start_date: str, position_id: str) -> dict:
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
                    "emplStatus": "4595",               # Active picklist ID — verify with your instance
                    "eventReason": "HIRNEW",
                }
            ]
        },
        "userNav": {
            "userId": user_id,
            "username": user_id,
            "status": "Active",
            "hireDate": epoch,
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

def create_new_hire(start_date: str, position_id: str, dry_run: bool = True):
    client = SFClient()

    person_id = _next_person_id(client)
    user_id = person_id
    print(f"\n[1/3] Generated personIdExternal={person_id}, userId={user_id}")

    payload = build_new_hire_payload(person_id, user_id, start_date, position_id)

    print("\n[2/3] Payload:")
    print(json.dumps(payload, indent=4))

    if dry_run:
        print("\n[DRY RUN] Payload shown above — nothing was sent to SuccessFactors.")
        return

    print("\n[3/3] Sending POST to upsert?purgeType=full ...")
    result = client.deep_upsert(payload)
    print(json.dumps(result, indent=4) if result else "(empty response — likely 204 No Content)")

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
