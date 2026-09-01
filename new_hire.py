"""
CLI Interface for Creating New Hires in SAP SuccessFactors Employee Central.
Powered by the Metadata Engine and HireService.

Usage:
    python new_hire.py --start-date 2026-08-01 --position 3000803 --dry-run
    python new_hire.py --start-date 2026-08-01 --position 3000803 --first-name Jane --last-name Doe --live
"""

import argparse
import json
import sys

from sf_client import SFClient
from hire_service import HireService


def main():
    parser = argparse.ArgumentParser(description="Create a new hire in SF Employee Central")
    parser.add_argument("--start-date",             required=True, help="Start date YYYY-MM-DD")
    parser.add_argument("--position",               required=True, help="Position external code")
    parser.add_argument("--person-id",              help="Explicit personIdExternal / userId (optional)")
    parser.add_argument("--first-name",             required=True, help="Candidate first name")
    parser.add_argument("--last-name",              required=True, help="Candidate last name")
    parser.add_argument("--gender",                 default="M", help="Gender (M/F/D)")
    parser.add_argument("--salutation",             default="Mr", help="Salutation (e.g. Mr, Ms, Dr or option ID)")
    parser.add_argument("--marital-status",         default="Single", help="Marital status (Single/Married)")
    parser.add_argument("--nationality",            default="USA", help="Nationality country code (default: USA)")
    parser.add_argument("--second-nationality",     default="CAN", help="Second nationality (default: CAN)")
    parser.add_argument("--suffix",                 default="III", help="Suffix (e.g. Jr, Sr, III or option ID)")
    parser.add_argument("--email",                  help="Email address")
    parser.add_argument("--ssn",                    help="National ID / SSN")
    parser.add_argument("--dob",                    default="1993-08-08", help="Date of Birth YYYY-MM-DD")
    
    # Org overrides
    parser.add_argument("--company",                help="Legal entity / company code (overrides position lookup)")
    parser.add_argument("--business-unit",          help="Business unit code")
    parser.add_argument("--division",               help="Division code")
    parser.add_argument("--department",             help="Department code")
    parser.add_argument("--job-code",               help="Job code")
    parser.add_argument("--job-title",              help="Job title override")
    parser.add_argument("--cost-center",            help="Cost center code")
    parser.add_argument("--location",               help="Location code")
    parser.add_argument("--pay-scale-type",          help="Pay scale type code (e.g. USA/US1)")
    parser.add_argument("--pay-scale-area",          help="Pay scale area code (e.g. USA/US2)")
    parser.add_argument("--custom-json",            help="JSON string with additional custom field overrides")
    
    parser.add_argument("--dry-run", action="store_true", default=False,
                        help="Print payload without calling the API")
    parser.add_argument("--live", action="store_true",
                        help="Actually POST to the API (required for live creation)")
    args = parser.parse_args()

    user_inputs = {
        "start_date":            args.start_date,
        "position":              args.position,
        "person_id":             args.person_id,
        "first_name":            args.first_name,
        "last_name":             args.last_name,
        "gender":                args.gender,
        "salutation":            args.salutation,
        "marital_status":        args.marital_status,
        "nationality":           args.nationality,
        "second_nationality":    args.second_nationality,
        "suffix":                args.suffix,
        "email":                 args.email,
        "ssn":                   args.ssn,
        "dob":                   args.dob,
        "company":               args.company,
        "businessUnit":          args.business_unit,
        "division":              args.division,
        "department":            args.department,
        "jobCode":               args.job_code,
        "jobTitle":              args.job_title,
        "costCenter":            args.cost_center,
        "location":              args.location,
        "payScaleType":          args.pay_scale_type,
        "payScaleArea":          args.pay_scale_area,
    }

    # Filter out None values
    user_inputs = {k: v for k, v in user_inputs.items() if v is not None}

    if args.custom_json:
        try:
            extra = json.loads(args.custom_json)
            user_inputs.update(extra)
        except Exception as e:
            print(f"Error parsing --custom-json: {e}")
            sys.exit(1)

    dry_run = not args.live
    service = HireService()

    print(f"\nProcessing hire request (Mode: {'DRY RUN' if dry_run else 'LIVE'}) ...")
    res = service.hire_employee(user_inputs, dry_run=dry_run)

    if dry_run:
        print("\n[DRY RUN PLAN GENERATED]")
        print(json.dumps(res, indent=2))
        print("\nTo execute this hire live against SuccessFactors, re-run with --live.")
    else:
        print("\n=======================================================")
        print(" SUCCESS: New employee created in SAP SuccessFactors!")
        print(f" Person ID / User ID : {res['person_id']}")
        print(f" Name                : {res['full_name']}")
        print(f" Position            : {res['position']}")
        print(f" Start Date          : {res['start_date']}")
        print(f" Email               : {res['email']}")
        print(f" Execution Steps     : {json.dumps(res['steps'])}")
        print("=======================================================\n")


if __name__ == "__main__":
    main()
