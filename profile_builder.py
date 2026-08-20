"""
Intelligent Employee Profile Builder for SAP SuccessFactors Employee Central.
Combines flexible user inputs, Position defaults, dynamic picklist resolution,
and metadata-driven sensible defaults to construct valid, complete onboarding payloads.
"""

import json
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

from sf_client import SFClient
from metadata_engine import MetadataEngine, PicklistResolver
from position_service import PositionService


def _to_epoch_ms(date_str: str) -> int:
    """Convert YYYY-MM-DD to milliseconds since epoch (SF OData /Date(...)/ format)."""
    d = date.fromisoformat(str(date_str).split("T")[0])
    epoch = date(1970, 1, 1)
    return int((d - epoch).total_seconds() * 1000)


def _format_date(date_str: str) -> str:
    """Format ISO date to /Date(ms)/."""
    return f"/Date({_to_epoch_ms(date_str)})/"


class ProfileBuilder:
    def __init__(self, client: SFClient, meta_engine: MetadataEngine, picklists: PicklistResolver, pos_service: PositionService):
        self.client = client
        self.meta_engine = meta_engine
        self.picklists = picklists
        self.pos_service = pos_service

    def get_next_person_id(self) -> str:
        """Dynamically find the highest numeric ID in the tenant and increment."""
        all_numeric = []
        for entity, field in [("User", "userId"), ("EmpEmployment", "personIdExternal")]:
            try:
                result = self.client.get(entity, params={
                    "$filter": f"{field} ge '100000' and {field} le '999999'",
                    "$orderby": f"{field} desc",
                    "$top": "20",
                    "$select": field,
                })
                rows = result.get("d", {}).get("results", [])
                for r in rows:
                    val = r.get(field, "")
                    if str(val).isdigit():
                        all_numeric.append(int(val))
            except Exception:
                pass

        if all_numeric:
            return str(max(all_numeric) + 1)
        return "900000"

    def build_hire_payloads(self, user_inputs: Dict[str, Any]) -> Dict[str, Any]:
        """
        Build the complete 6-step new hire payload set from user inputs and position defaults.
        """
        # 1. Start Date & Person ID
        start_date = user_inputs.get("start_date") or user_inputs.get("startDate") or "2026-08-01"
        epoch_date = _format_date(start_date)

        person_id = str(user_inputs.get("person_id") or user_inputs.get("userId") or self.get_next_person_id())
        user_id = person_id

        # 2. Position & Org Defaults
        position_code = user_inputs.get("position") or user_inputs.get("position_code")
        position_defaults = {}
        if position_code:
            position_defaults = self.pos_service.get_org_defaults(str(position_code))

        # 3. Dynamic Picklist Resolutions
        salutation_input = user_inputs.get("salutation", "Mr")
        salutation_id = self.picklists.resolve("salutation", salutation_input, default="10808")

        suffix_input = user_inputs.get("suffix", "III")
        suffix_id = self.picklists.resolve("SUFFIX", suffix_input, default="5457")

        lang_input = user_inputs.get("native_preferred_lang") or user_inputs.get("language") or "10223"
        lang_id = self.picklists.resolve("language", lang_input, default="10223")

        email_type_input = user_inputs.get("email_type", "Business")
        email_type_id = self.picklists.resolve("ecEmailType", email_type_input, default="8448")

        # 4. Personal Info
        first_name = user_inputs.get("first_name") or user_inputs.get("firstName") or "Alex"
        last_name = user_inputs.get("last_name") or user_inputs.get("lastName") or "Taylor"
        gender = (user_inputs.get("gender") or "M").upper()
        marital_status = user_inputs.get("marital_status") or user_inputs.get("maritalStatus") or "Single"
        nationality = user_inputs.get("nationality") or "USA"
        second_nationality = user_inputs.get("second_nationality") or user_inputs.get("secondNationality") or "CAN"

        # 5. User Payload
        user_payload = {
            "userId": user_id,
            "username": user_id,
            "status": user_inputs.get("user_status", "Active"),
            "hireDate": epoch_date,
        }

        # 6. PerPersonal Payload
        personal_payload = {
            "__metadata": {"uri": "PerPersonal"},
            "personIdExternal": person_id,
            "startDate": epoch_date,
            "firstName": first_name,
            "lastName": last_name,
            "gender": gender,
            "salutation": salutation_id,
            "maritalStatus": marital_status,
            "nationality": nationality,
            "secondNationality": second_nationality,
            "nativePreferredLang": lang_id,
            "suffix": suffix_id,
        }
        if "personal_info" in user_inputs and isinstance(user_inputs["personal_info"], dict):
            personal_payload.update(user_inputs["personal_info"])

        # 7. PerNationalId Payload
        last4 = str(person_id)[-4:]
        ssn_val = user_inputs.get("ssn") or user_inputs.get("national_id") or f"900-15-{last4}"
        national_id_payload = {
            "__metadata": {"uri": "PerNationalId"},
            "personIdExternal": person_id,
            "country": nationality,
            "cardType": user_inputs.get("card_type", "ssn"),
            "nationalId": ssn_val,
            "isPrimary": True,
            "customString1": "Yes",
        }
        if "national_id_info" in user_inputs and isinstance(user_inputs["national_id_info"], dict):
            national_id_payload.update(user_inputs["national_id_info"])

        # 8. PerEmail Payload
        email_val = user_inputs.get("email") or f"{first_name.lower()}.{last_name.lower()}.{person_id}@example.com"
        email_payload = {
            "__metadata": {"uri": "PerEmail"},
            "personIdExternal": person_id,
            "emailType": email_type_id,
            "isPrimary": True,
            "emailAddress": email_val,
        }
        if "email_info" in user_inputs and isinstance(user_inputs["email_info"], dict):
            email_payload.update(user_inputs["email_info"])

        # 9. EmpEmployment Payload
        emp_payload = {
            "__metadata": {"uri": "EmpEmployment"},
            "personIdExternal": person_id,
            "userId": user_id,
            "isContingentWorker": user_inputs.get("is_contingent", False),
            "startDate": epoch_date,
            "originalStartDate": epoch_date,
            "firstDateWorked": epoch_date,
            "seniorityDate": epoch_date,
            "serviceDate": epoch_date,
            "benefitsEligibilityStartDate": epoch_date,
        }
        if "employment_info" in user_inputs and isinstance(user_inputs["employment_info"], dict):
            emp_payload.update(user_inputs["employment_info"])

        # 10. EmpJob Payload
        job_fields = {**position_defaults}
        # Direct org overrides
        for k in ["company", "businessUnit", "division", "department", "location", "jobCode", "jobTitle", "costCenter", "payScaleType", "payScaleArea"]:
            if user_inputs.get(k) is not None:
                job_fields[k] = user_inputs[k]

        job_payload = {
            "__metadata": {"uri": "EmpJob"},
            "userId": user_id,
            "startDate": epoch_date,
            "position": str(position_code) if position_code else None,
            "seqNumber": "1",
            "eventReason": user_inputs.get("event_reason", "HIRNEW"),
            "isFulltimeEmployee": user_inputs.get("is_fulltime", True),
            "standardHours": str(user_inputs.get("standard_hours", "40")),
            **job_fields,
        }
        # Clean null values in job_payload
        job_payload = {k: v for k, v in job_payload.items() if v is not None}
        if "job_info" in user_inputs and isinstance(user_inputs["job_info"], dict):
            job_payload.update(user_inputs["job_info"])

        return {
            "person_id": person_id,
            "user_id": user_id,
            "full_name": f"{first_name} {last_name}",
            "position": position_code,
            "start_date": start_date,
            "email": email_val,
            "payloads": {
                "User": user_payload,
                "PerPersonal": self.meta_engine.validate_payload("PerPersonal", personal_payload),
                "PerNationalId": self.meta_engine.validate_payload("PerNationalId", national_id_payload),
                "PerEmail": self.meta_engine.validate_payload("PerEmail", email_payload),
                "EmpEmployment": self.meta_engine.validate_payload("EmpEmployment", emp_payload),
                "EmpJob": self.meta_engine.validate_payload("EmpJob", job_payload),
            }
        }
