"""
Employee Hire Service for SAP SuccessFactors Employee Central.
Orchestrates the 6-step transactional employee onboarding flow with pre-flight validation.
"""

import json
from typing import Any, Dict, Optional

from sf_client import SFClient
from metadata_engine import MetadataEngine, PicklistResolver
from position_service import PositionService
from profile_builder import ProfileBuilder


class HireService:
    def __init__(self, client: Optional[SFClient] = None):
        self.client = client or SFClient()
        self.meta_engine = MetadataEngine(self.client)
        self.picklists = PicklistResolver(self.client)
        self.pos_service = PositionService(self.client)
        self.builder = ProfileBuilder(self.client, self.meta_engine, self.picklists, self.pos_service)

    def hire_employee(self, user_inputs: Dict[str, Any], dry_run: bool = False) -> Dict[str, Any]:
        """
        Execute full new hire workflow with smart defaults and optional custom overrides.
        """
        plan = self.builder.build_hire_payloads(user_inputs)
        payloads = plan["payloads"]

        result = {
            "status": "DRY_RUN" if dry_run else "SUCCESS",
            "person_id": plan["person_id"],
            "user_id": plan["user_id"],
            "full_name": plan["full_name"],
            "position": plan["position"],
            "start_date": plan["start_date"],
            "email": plan["email"],
            "steps": {},
        }

        if dry_run:
            result["plan"] = payloads
            return result

        # Step 1: User
        try:
            user_res = self.client.post("User", payloads["User"])
            result["steps"]["User"] = "CREATED"
        except Exception as e:
            if "User already exists" in str(e):
                result["steps"]["User"] = "ALREADY_EXISTS"
            else:
                raise Exception(f"Step 1 (User) failed: {e}")

        # Step 2: PerPersonal
        try:
            self.client.deep_upsert(payloads["PerPersonal"])
            result["steps"]["PerPersonal"] = "UPSERTED"
        except Exception as e:
            raise Exception(f"Step 2 (PerPersonal) failed: {e}")

        # Step 3: PerNationalId
        try:
            self.client.deep_upsert(payloads["PerNationalId"])
            result["steps"]["PerNationalId"] = "UPSERTED"
        except Exception as e:
            raise Exception(f"Step 3 (PerNationalId) failed: {e}")

        # Step 4: PerEmail
        try:
            self.client.deep_upsert(payloads["PerEmail"])
            result["steps"]["PerEmail"] = "UPSERTED"
        except Exception as e:
            raise Exception(f"Step 4 (PerEmail) failed: {e}")

        # Step 4a: PerPhone
        try:
            self.client.deep_upsert(payloads["PerPhone"])
            result["steps"]["PerPhone"] = "UPSERTED"
        except Exception as e:
            raise Exception(f"Step 4a (PerPhone) failed: {e}")

        # Step 4b: PerAddressDEFLT
        try:
            self.client.deep_upsert(payloads["PerAddressDEFLT"])
            result["steps"]["PerAddressDEFLT"] = "UPSERTED"
        except Exception as e:
            raise Exception(f"Step 4b (PerAddressDEFLT) failed: {e}")

        # Step 5: EmpEmployment
        try:
            self.client.deep_upsert(payloads["EmpEmployment"])
            result["steps"]["EmpEmployment"] = "UPSERTED"
        except Exception as e:
            raise Exception(f"Step 5 (EmpEmployment) failed: {e}")

        # Step 6: EmpJob
        try:
            self.client.deep_upsert(payloads["EmpJob"])
            result["steps"]["EmpJob"] = "UPSERTED"
        except Exception as e:
            raise Exception(f"Step 6 (EmpJob) failed: {e}")

        return result
