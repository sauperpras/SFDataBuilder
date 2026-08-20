"""
New hire creation for SAP SuccessFactors Employee Central.

Usage:
    python new_hire.py --start-date 2026-08-01 --position 3000803 --pay-scale-type "USA/US1" --pay-scale-area "USA/US2" --dry-run
    python new_hire.py --start-date 2026-08-01 --position 3000803 --pay-scale-type "USA/US1" --pay-scale-area "USA/US2" --live

With --dry-run the payloads are printed but NOT posted to SF.
With --live the new hire is created across User, PerPersonal, PerNationalId, PerEmail, EmpEmployment, and EmpJob.
"""

import argparse
import json
import sys
from datetime import date

from sf_client import SFClient


# ---------------------------------------------------------------------------
# Payload builders & Helpers
# ---------------------------------------------------------------------------

_POSITION_ORG_FIELDS = ("company", "businessUnit", "division", "department", "location", "jobCode", "jobTitle")


def fetch_position_defaults(client: SFClient, position_id: str) -> dict:
    """
    Query MDF Position by code to get org fields required by EmpJob.
    Field names map 1:1 from Position to jobInfoNav (company, businessUnit, etc.).
    """
    try:
        result = client.get("Position", params={
            "$filter": f"code eq '{position_id}'",
            "$select": "code,company,businessUnit,division,department,location,jobCode,jobTitle,positionTitle,externalName_defaultValue",
        })
        rows = result.get("d", {}).get("results", [])
        if not rows:
            print(f"  WARNING: No Position found with code={position_id} — org fields must be supplied via CLI args.")
            return {}
        pos = rows[0]
        defaults = {f: pos[f] for f in _POSITION_ORG_FIELDS if pos.get(f)}
        if not defaults.get("jobTitle"):
            title = pos.get("jobTitle") or pos.get("positionTitle") or pos.get("externalName_defaultValue")
            if title:
                defaults["jobTitle"] = title.replace(" (Copy)", "")
        return defaults
    except Exception as e:
        print(f"  WARNING: Position query failed ({e}) — org fields must be supplied via CLI args.")
        return {}


def _to_epoch_ms(date_str: str) -> int:
    """Convert YYYY-MM-DD to milliseconds since epoch (SF OData /Date(...)/ format)."""
    d = date.fromisoformat(date_str)
    epoch = date(1970, 1, 1)
    return int((d - epoch).total_seconds() * 1000)


ID_START = 900_000


def _next_person_id(client: SFClient) -> str:
    """
    Find the highest numeric personIdExternal / userId in SF and return the next available ID.
    """
    all_numeric = []
    for entity, field in [("User", "userId"), ("EmpEmployment", "personIdExternal")]:
        try:
            result = client.get(entity, params={
                "$filter": f"{field} ge '100000' and {field} le '999999'",
                "$orderby": f"{field} desc",
                "$top": "20",
                "$select": field,
            })
            rows = result.get("d", {}).get("results", [])
            for r in rows:
                val = r.get(field, "")
                if val.isdigit():
                    all_numeric.append(int(val))
        except Exception:
            pass
    if all_numeric:
        return str(max(all_numeric) + 1)
    return str(ID_START)


# ---------------------------------------------------------------------------
# Main Creation Workflow
# ---------------------------------------------------------------------------

def create_new_hire(start_date: str, position_id: str, dry_run: bool = True,
                    overrides: dict = None, personal_data: dict = None):
    client = SFClient()
    overrides = overrides or {}
    personal_data = personal_data or {}

    epoch = f"/Date({_to_epoch_ms(start_date)})/"
    dob_epoch = f"/Date({_to_epoch_ms(personal_data.get('dob', '1993-08-08'))})/"

    person_id = personal_data.get("person_id") or _next_person_id(client)
    user_id = person_id
    print(f"\n[1/7] Target personIdExternal={person_id}, userId={user_id}")

    print(f"\n[2/7] Fetching org defaults from position {position_id} ...")
    position_defaults = fetch_position_defaults(client, position_id)
    if overrides:
        position_defaults.update(overrides)
    print(f"  Org fields: {json.dumps(position_defaults)}")

    # 1. User
    user_payload = {
        "userId": user_id,
        "username": user_id,
        "status": "Active",
        "hireDate": epoch,
    }

    # 2. PerPersonal
    personal_payload = {
        "__metadata": {"uri": "PerPersonal"},
        "personIdExternal": person_id,
        "startDate": epoch,
        "firstName": personal_data.get("first_name", "Alex"),
        "lastName": personal_data.get("last_name", "Taylor"),
        "gender": personal_data.get("gender", "M"),
        "salutation": personal_data.get("salutation", "10808"),
        "maritalStatus": personal_data.get("marital_status", "Single"),
        "nationality": personal_data.get("nationality", "USA"),
        "secondNationality": personal_data.get("second_nationality", "CAN"),
        "nativePreferredLang": personal_data.get("native_preferred_lang", "10223"),
        "suffix": personal_data.get("suffix", "5457"),
    }

    # 3. PerNationalId
    last4 = str(person_id)[-4:]
    national_id_val = personal_data.get("national_id") or f"900-15-{last4}"
    national_id_payload = {
        "__metadata": {"uri": "PerNationalId"},
        "personIdExternal": person_id,
        "country": personal_data.get("nationality", "USA"),
        "cardType": "ssn",
        "nationalId": national_id_val,
        "isPrimary": True,
        "customString1": "Yes",
    }

    # 4. PerEmail
    email_val = personal_data.get("email") or f"{personal_payload['firstName'].lower()}.{personal_payload['lastName'].lower()}.{person_id}@example.com"
    email_payload = {
        "__metadata": {"uri": "PerEmail"},
        "personIdExternal": person_id,
        "emailType": "8448",  # Business
        "isPrimary": True,
        "emailAddress": email_val,
    }

    # 5. EmpEmployment
    emp_payload = {
        "__metadata": {"uri": "EmpEmployment"},
        "personIdExternal": person_id,
        "userId": user_id,
        "isContingentWorker": False,
        "startDate": epoch,
        "originalStartDate": epoch,
        "firstDateWorked": epoch,
        "seniorityDate": epoch,
        "serviceDate": epoch,
        "benefitsEligibilityStartDate": epoch,
    }

    # 6. EmpJob
    job_payload = {
        "__metadata": {"uri": "EmpJob"},
        "userId": user_id,
        "startDate": epoch,
        "position": position_id,
        "seqNumber": "1",
        "eventReason": "HIRNEW",
        "isFulltimeEmployee": True,
        "standardHours": "40",
        **position_defaults,
    }

    print("\n[3/7] Step 1 payload — POST User:")
    print(json.dumps(user_payload, indent=4))
    print("\n[4/7] Step 2 payload — POST upsert (PerPersonal):")
    print(json.dumps(personal_payload, indent=4))
    print("\n[5/7] Step 3 payload — POST upsert (PerNationalId):")
    print(json.dumps(national_id_payload, indent=4))
    print("\n[6/7] Step 4 payload — POST upsert (PerEmail):")
    print(json.dumps(email_payload, indent=4))
    print("\n[7/7] Step 5 & 6 payloads — POST upsert (EmpEmployment & EmpJob):")
    print(json.dumps(emp_payload, indent=4))
    print(json.dumps(job_payload, indent=4))

    if dry_run:
        print("\n[DRY RUN] Payloads displayed above — nothing was sent to SuccessFactors.")
        return

    print("\n>>> Executing Live Creation on SuccessFactors <<<")

    print("\n[1/6] Creating User ...")
    try:
        user_result = client.post("User", user_payload)
        print("  [OK] User created.")
    except Exception as e:
        if "User already exists" in str(e):
            print("  [OK] User already exists — proceeding.")
        else:
            raise

    print("\n[2/6] Upserting PerPersonal ...")
    client.deep_upsert(personal_payload)
    print("  [OK] PerPersonal created.")

    print("\n[3/6] Upserting PerNationalId ...")
    client.deep_upsert(national_id_payload)
    print("  [OK] PerNationalId created.")

    print("\n[4/6] Upserting PerEmail ...")
    client.deep_upsert(email_payload)
    print("  [OK] PerEmail created.")

    print("\n[5/6] Upserting EmpEmployment ...")
    client.deep_upsert(emp_payload)
    print("  [OK] EmpEmployment created.")

    print("\n[6/6] Upserting EmpJob ...")
    client.deep_upsert(job_payload)
    print("  [OK] EmpJob created.")

    print(f"\n=======================================================")
    print(f" SUCCESS: New employee created in SAP SuccessFactors!")
    print(f" Person ID / User ID : {person_id}")
    print(f" Name                : {personal_payload['firstName']} {personal_payload['lastName']}")
    print(f" Position            : {position_id}")
    print(f" Start Date          : {start_date}")
    print(f" Email               : {email_val}")
    print(f"=======================================================\n")


def main():
    parser = argparse.ArgumentParser(description="Create a new hire in SF Employee Central")
    parser.add_argument("--start-date",             required=True, help="Start date YYYY-MM-DD")
    parser.add_argument("--position",               required=True, help="Position external code")
    parser.add_argument("--person-id",              help="Explicit personIdExternal / userId (optional)")
    parser.add_argument("--first-name",             default="Alex", help="First name (default: Alex)")
    parser.add_argument("--last-name",              default="Taylor", help="Last name (default: Taylor)")
    parser.add_argument("--gender",                 default="M", help="Gender (M/F/D)")
    parser.add_argument("--salutation",             default="10808", help="Salutation picklist ID (default: 10808 for MR)")
    parser.add_argument("--marital-status",         default="Single", help="Marital status (default: Single)")
    parser.add_argument("--nationality",            default="USA", help="Nationality country code (default: USA)")
    parser.add_argument("--second-nationality",     default="CAN", help="Second nationality (default: CAN)")
    parser.add_argument("--native-preferred-lang",  default="10223", help="Native preferred language ID (default: 10223 for en_US)")
    parser.add_argument("--suffix",                 default="5457", help="Suffix picklist ID (default: 5457 for III)")
    parser.add_argument("--email",                  help="Email address")
    parser.add_argument("--ssn",                    help="National ID / SSN")
    parser.add_argument("--dob",                    default="1993-08-08", help="Date of Birth YYYY-MM-DD")
    
    # Org overrides
    parser.add_argument("--company",                help="Legal entity / company code (overrides position lookup)")
    parser.add_argument("--business-unit",          help="Business unit code")
    parser.add_argument("--division",               help="Division code")
    parser.add_argument("--department",             help="Department code")
    parser.add_argument("--job-code",               help="Job code")
    parser.add_argument("--cost-center",            help="Cost center code")
    parser.add_argument("--location",               help="Location code")
    parser.add_argument("--employee-class",          help="Employee class picklist ID")
    parser.add_argument("--pay-scale-type",          required=True, help="Pay scale type code (e.g. USA/US1)")
    parser.add_argument("--pay-scale-area",          required=True, help="Pay scale area code (e.g. USA/US2)")
    
    parser.add_argument("--dry-run", action="store_true", default=False,
                        help="Print payload without calling the API")
    parser.add_argument("--live", action="store_true",
                        help="Actually POST to the API (required for live creation)")
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

    personal_data = {
        "person_id":             args.person_id,
        "first_name":            args.first_name,
        "last_name":             args.last_name,
        "gender":                args.gender,
        "salutation":            args.salutation,
        "marital_status":        args.marital_status,
        "nationality":           args.nationality,
        "second_nationality":    args.second_nationality,
        "native_preferred_lang": args.native_preferred_lang,
        "suffix":                args.suffix,
        "email":                 args.email,
        "national_id":           args.ssn,
        "dob":                   args.dob,
    }

    dry_run = not args.live
    create_new_hire(start_date=args.start_date, position_id=args.position,
                    dry_run=dry_run, overrides=overrides, personal_data=personal_data)


if __name__ == "__main__":
    main()
