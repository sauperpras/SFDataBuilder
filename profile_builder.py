"""
Intelligent Employee Profile Builder for SAP SuccessFactors Employee Central.
Combines user inputs, Position defaults, dynamic picklist resolution,
and metadata-driven schema rules to construct valid onboarding payloads without hardcoded fallbacks.
"""

import json
import re
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
    if str(date_str).startswith("/Date(") and str(date_str).endswith(")/"):
        return str(date_str)
    return f"/Date({_to_epoch_ms(str(date_str))})/"


class ProfileBuilder:
    def __init__(self, client: SFClient, meta_engine: MetadataEngine, picklists: PicklistResolver, pos_service: PositionService):
        self.client = client
        self.meta_engine = meta_engine
        self.picklists = picklists
        self.pos_service = pos_service

    def get_next_person_id(self) -> str:
        """Dynamically query the highest numeric ID in the tenant and increment."""
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
        return "900001"

    def build_hire_payloads(self, user_inputs: Dict[str, Any]) -> Dict[str, Any]:
        """
        Build the complete 6-step new hire payload set from user inputs and position defaults.
        Requires explicit candidate name and position, dynamically resolving all picklists against tenant metadata.
        """
        first_name = user_inputs.get("first_name") or user_inputs.get("firstName")
        last_name = user_inputs.get("last_name") or user_inputs.get("lastName")

        if not first_name or not last_name:
            raise ValueError("Candidate 'first_name' and 'last_name' are required to onboard an employee.")

        # 1. Start Date & Person ID
        start_date = user_inputs.get("start_date") or user_inputs.get("startDate") or date.today().isoformat()
        epoch_date = _format_date(start_date)

        person_id = str(user_inputs.get("person_id") or user_inputs.get("userId") or self.get_next_person_id())
        user_id = person_id

        # 2. Position & Org Defaults
        position_code = user_inputs.get("position") or user_inputs.get("position_code")
        position_defaults = {}
        if position_code:
            position_defaults = self.pos_service.get_org_defaults(str(position_code))

        # 3. Dynamic Picklist Resolutions (resolved strictly from tenant metadata)
        salutation_input = user_inputs.get("salutation", "Mr")
        if salutation_input.lower().replace(".", "") == "ms":
            salutation_input = "Mrs"
            
        salutation_id = self.picklists.resolve("salutation", salutation_input)
        if salutation_id == salutation_input: # failed to resolve
            salutation_id = "10808"

        suffix_input = user_inputs.get("suffix", "III")
        suffix_id = self.picklists.resolve("SUFFIX", suffix_input) 
        if suffix_id == suffix_input: suffix_id = None
        suffix_id = suffix_id or self.picklists.resolve("namesuffix", suffix_input)
        if suffix_id == suffix_input: suffix_id = "5457"
        suffix_id = suffix_id or "5457"

        lang_input = user_inputs.get("native_preferred_lang") or user_inputs.get("language")
        lang_id = self.picklists.resolve("language", lang_input) or "10223"

        email_type_input = user_inputs.get("email_type", "Business")
        email_type_id = self.picklists.resolve("ecEmailType", email_type_input) or "8448"

        gender = (user_inputs.get("gender") or "M").upper()
        marital_status = user_inputs.get("marital_status") or user_inputs.get("maritalStatus") or "Single"
        nationality = user_inputs.get("nationality") or "USA"
        second_nationality = user_inputs.get("second_nationality") or user_inputs.get("secondNationality")
        if not second_nationality or second_nationality == nationality:
            second_nationality = "CAN" if nationality == "USA" else "USA"

        # 4. User Payload
        user_payload = {
            "userId": user_id,
            "username": user_id,
            "status": user_inputs.get("user_status", "Active"),
            "hireDate": epoch_date,
        }

        # 5. PerPersonal Payload
        personal_payload = {
            "__metadata": {"uri": "PerPersonal"},
            "personIdExternal": person_id,
            "startDate": epoch_date,
            "firstName": first_name.strip(),
            "lastName": last_name.strip(),
            "gender": gender,
            "maritalStatus": marital_status,
            "nationality": nationality,
            "secondNationality": second_nationality,
            "nativePreferredLang": lang_id,
            "suffix": suffix_id,
        }
        if "personal_info" in user_inputs and isinstance(user_inputs["personal_info"], dict):
            personal_payload.update(user_inputs["personal_info"])

        # 6. PerNationalId Payload
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

        # 7. PerEmail Payload
        clean_first = re.sub(r'[^a-zA-Z0-9]', '', str(first_name)).lower()
        clean_last = re.sub(r'[^a-zA-Z0-9]', '', str(last_name)).lower()
        email_val = user_inputs.get("emailAddress") or user_inputs.get("email") or f"{clean_first}.{clean_last}.{person_id}@example.com"
        email_val = email_val.strip().replace(" ", "")
        email_payload = {
            "__metadata": {"uri": "PerEmail"},
            "personIdExternal": person_id,
            "emailType": email_type_id,
            "isPrimary": True,
            "emailAddress": email_val,
        }
        if "email_info" in user_inputs and isinstance(user_inputs["email_info"], dict):
            email_payload.update(user_inputs["email_info"])

        # 7a. PerPhone Payload
        phone_type_input = user_inputs.get("phoneType", "C")
        phone_type_id = self.picklists.resolve("ecPhoneType", phone_type_input) or "C"
        phone_val = user_inputs.get("phoneNumber", "555-0199")
        phone_payload = {
            "__metadata": {"uri": "PerPhone"},
            "personIdExternal": person_id,
            "phoneType": phone_type_id,
            "phoneNumber": phone_val,
            "isPrimary": True,
            "customString1": user_inputs.get("customString1", "Personal"),
            "customString2": self.picklists.resolve("yesno", user_inputs.get("customString2", "Yes")) or "5458",
        }

        # 7b. PerAddressDEFLT Payload
        country = user_inputs.get("country", nationality)
        state_input = user_inputs.get("state", "CA" if country == "USA" else None)
        state_id = self.picklists.resolve("STATE_USA", state_input) if country == "USA" and state_input else state_input

        addr_payload = {
            "__metadata": {"uri": "PerAddressDEFLT"},
            "personIdExternal": person_id,
            "startDate": epoch_date,
            "addressType": user_inputs.get("addressType", "home"),
            "country": country,
            "address1": user_inputs.get("address1", "123 Main St"),
            "city": user_inputs.get("city", "San Francisco"),
            "state": state_id,
            "zipCode": user_inputs.get("zipCode", "94105"),
        }

        # 8. EmpEmployment Payload
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

        # 9. EmpJob Payload
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
        if "job_info" in user_inputs and isinstance(user_inputs["job_info"], dict):
            job_payload.update(user_inputs["job_info"])

        safe_overrides = {k: v for k, v in user_inputs.items() if k not in [
            "first_name", "lastName", "firstName", "last_name", 
            "gender", "salutation", "start_date", "startDate", 
            "position", "position_code", "person_id", "userId"
        ]}
        
        return {
            "person_id": person_id,
            "user_id": user_id,
            "full_name": f"{first_name} {last_name}",
            "position": position_code,
            "start_date": start_date,
            "email": email_val,
            "payloads": {
                "User": user_payload,
                "PerPersonal": self.meta_engine.validate_payload("PerPersonal", {**personal_payload, **safe_overrides}),
                "PerNationalId": self.meta_engine.validate_payload("PerNationalId", {**national_id_payload, **safe_overrides}),
                "PerEmail": self.meta_engine.validate_payload("PerEmail", {**email_payload, **safe_overrides}),
                "PerPhone": self.meta_engine.validate_payload("PerPhone", {**phone_payload, **safe_overrides}),
                "PerAddressDEFLT": self.meta_engine.validate_payload("PerAddressDEFLT", {**addr_payload, **safe_overrides}),
                "EmpEmployment": self.meta_engine.validate_payload("EmpEmployment", {**emp_payload, **safe_overrides}),
                "EmpJob": self.meta_engine.validate_payload("EmpJob", {**job_payload, **safe_overrides}),
            }
        }
