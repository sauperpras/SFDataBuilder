"""
SFDataBuilder - Interactive Web Application with AI Copilot
Modern web UI and AI Chat Assistant for SAP SuccessFactors Employee Central.
"""

import json
import os
import re
import subprocess
import sys
import traceback
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse
from starlette.routing import Route
import uvicorn

from sf_client import SFClient
from metadata_engine import MetadataEngine, PicklistResolver
from position_service import PositionService
from hire_service import HireService

# Initialize backend services
client = SFClient()
meta_engine = MetadataEngine(client)
picklists = PicklistResolver(client)
pos_service = PositionService(client)
hire_service = HireService(client)


# ==============================================================================
# AGENTIC AI COPILOT ENGINE
# ==============================================================================

_MALE_NAMES = [
    "Liam", "Noah", "Lucas", "Ethan", "Alexander", "Daniel", "Marcus", "Arjun",
    "Mateo", "Kenji", "Gabriel", "Sebastian", "Julian", "Leo", "Aiden",
    "John", "Tom", "Bruce", "Keanu", "David", "Michael", "James", "Robert",
    "William", "Richard", "Joseph", "Thomas", "Charles", "Christopher"
]

_FEMALE_NAMES = [
    "Emma", "Olivia", "Sophia", "Mia", "Isabella", "Ava", "Elena", "Priya",
    "Camila", "Yuki", "Chloe", "Harper", "Amara", "Freja", "Zara",
    "Sarah", "Jane", "Elizabeth", "Sandra", "Ashley", "Emily", "Jessica",
    "Mary", "Jennifer", "Linda", "Barbara", "Susan", "Karen", "Nancy"
]

_LAST_NAMES = [
    "Vance", "Chen", "Martinez", "Dubois", "Tanaka", "Jensen", "Patel",
    "Rossi", "Miller", "Becker", "Wright", "Gomez", "Novak", "Kim",
    "Silva", "Brooks", "Nakamura", "Larsson", "Fischer", "Alvarez"
]

def _generate_random_candidate(gender: Optional[str] = None) -> Tuple[str, str, str, str]:
    """
    Generate a random candidate name, gender (M/F), and appropriate salutation (Mr/Ms).
    """
    import random
    if not gender:
        gender = random.choice(["M", "F"])
    else:
        gender = gender.upper()

    if gender == "F":
        first = random.choice(_FEMALE_NAMES)
        salutation = "Ms"
    else:
        first = random.choice(_MALE_NAMES)
        salutation = "Mr"

    last = random.choice(_LAST_NAMES)
    return first, last, gender, salutation


def detect_gender_and_salutation(first_name: str, text: str = "") -> Tuple[str, str]:
    """
    Baseline rules for gender & salutation deduction based on honorifics and first names.
    """
    text_lower = text.lower()
    fn_lower = first_name.lower() if first_name else ""

    # Rule 1: Explicit Honorifics
    if any(h in text_lower for h in ["ms.", "ms ", "mrs.", "mrs ", "miss "]):
        return "F", "Ms"
    if any(h in text_lower for h in ["mr.", "mr "]):
        return "M", "Mr"
    if any(h in text_lower for h in ["dr.", "dr "]):
        female_set = {n.lower() for n in _FEMALE_NAMES}
        g = "F" if fn_lower in female_set else "M"
        return g, "Dr"

    # Rule 2: Lexicon matching
    female_set = {n.lower() for n in _FEMALE_NAMES}
    male_set = {n.lower() for n in _MALE_NAMES}

    if fn_lower in female_set:
        return "F", "Ms"
    if fn_lower in male_set:
        return "M", "Mr"

    # Baseline default
    return "M", "Mr"


class AICopilotEngine:
    """
    Intelligent agent that processes natural language requests, extracts intents & entities,
    proposes onboarding plans for user confirmation, and orchestrates SuccessFactors operations.
    """

    def __init__(self, meta_engine: Optional[MetadataEngine] = None, picklists: Optional[PicklistResolver] = None, client: Optional[SFClient] = None):
        if not client:
            client = SFClient()
        if not meta_engine:
            meta_engine = MetadataEngine(client)
        if not picklists:
            picklists = PicklistResolver(client)
            
        self.pos_svc = PositionService(client, meta_engine)
        self.hire_svc = HireService(client)
        self.meta = meta_engine
        self.picks = picklists
        self.sf_client = client
        self.pending_proposal: Optional[Dict[str, Any]] = None
        self.last_created_position: str = "4000001"

    def process_message(self, user_text: str, conversation_history: List[Dict[str, str]]) -> Dict[str, Any]:
        text = user_text.strip()
        lower = text.lower()

        # 0. User Confirmation of Pending Proposal
        confirm_words = ["yes", "confirm", "proceed", "go ahead", "do it", "sure", "ok", "y", "execute", "accept"]
        if any(lower == w or lower.startswith(w + " ") for w in confirm_words) and self.pending_proposal:
            return self._execute_pending_proposal()

        # 0b. User says 'use name <Name>' to change proposal name and confirm
        if self.pending_proposal and any(k in lower for k in ["use name", "name is", "change name to", "named"]):
            fn, ln = self._extract_person_name(text)
            if fn and ln:
                self.pending_proposal["first_name"] = fn
                self.pending_proposal["last_name"] = ln
                gen, sal = detect_gender_and_salutation(fn, text)
                self.pending_proposal["gender"] = gen
                self.pending_proposal["salutation"] = sal
                return self._execute_pending_proposal()

        # 1. Combined: Clone position AND hire employee
        if ("clone" in lower or "copy" in lower) and ("hire" in lower or "onboard" in lower):
            return self._handle_clone_and_hire(text)

        # 2. Clone/Copy position only
        if ("clone" in lower or "copy" in lower or "duplicate" in lower) and "position" in lower:
            return self._handle_clone_position(text)

        # 3. Update Existing Position
        # e.g., "update position 4000001 title to Principal Architect" or "set position 4000001 vacant to false"
        if ("update" in lower or "change" in lower or "set" in lower) and "position" in lower and any(w in lower for w in ["title", "vacant", "hours", "fte", "status", "to"]):
            return self._handle_update_position(text)

        # 4. Update Existing Employee
        # e.g., "update employee 900181 email to test@example.com" or "update user 900181"
        if ("update" in lower or "change" in lower or "modify" in lower) and any(w in lower for w in ["employee", "user", "person"]) and re.search(r'\b\d{5,7}\b', text):
            return self._handle_update_employee(text)
            
        # 4b. Create position from scratch
        if "create" in lower and "position" in lower:
            return self._handle_create_position(text)

        # 4. Hire/Onboard employee only
        if "hire" in lower or "onboard" in lower or "new employee" in lower:
            return self._handle_hire_employee(text)

        # 4. Position lookup / inspection
        if ("position" in lower or "pos" in lower) and any(w in lower for w in ["check", "get", "show", "inspect", "details", "lookup", "info", "what is"]):
            return self._handle_get_position(text)

        # 5. List positions
        if "position" in lower and any(w in lower for w in ["list", "top", "all", "available"]):
            return self._handle_list_positions(text)

        # 6. OData Query / Employee lookup
        if any(w in lower for w in ["user", "employee", "person", "empjob", "query", "find", "search"]) and re.search(r'\b\d{5,7}\b', text):
            return self._handle_query_employee(text)

        # 7. Picklist search
        if "picklist" in lower or "options for" in lower:
            return self._handle_picklist_query(text)

        # 8. Schema / Metadata questions
        if "schema" in lower or "fields in" in lower or "entity" in lower:
            return self._handle_schema_query(text)

        # 9. General explanation / fallback
        return self._handle_general_qa(text)

    def _extract_person_name(self, text: str) -> Tuple[Optional[str], Optional[str]]:
        stopwords = {
            'a', 'an', 'the', 'new', 'employee', 'candidate', 'person', 'someone',
            'hire', 'hired', 'hiring', 'onboard', 'onboarding', 'position', 'positions',
            'code', 'named', 'called', 'into', 'to', 'for', 'in', 'at', 'as', 'that',
            'this', 'copy', 'clone', 'duplicate', 'create', 'and', 'with', 'by', 'of',
            'use', 'using', 'name', 'names', 'is', 'change', 'set'
        }

        # Rule 1: Explicit 'named <First> <Last>', 'use name <First> <Last>', 'name is <First> <Last>' (highest priority)
        m_named = re.search(r'\b(?:named|called|name\s+is|use\s+name|change\s+name\s+to|set\s+name\s+to)\s+(?:as\s+)?(?:mr\.?|ms\.?|mrs\.?|dr\.?)?\s*([A-Za-z]+)\s+([A-Za-z]+)', text, re.IGNORECASE)
        if m_named:
            f, l = m_named.group(1).strip().capitalize(), m_named.group(2).strip().capitalize()
            if f.lower() not in stopwords and l.lower() not in stopwords:
                return f, l

        # Rule 2: Clean structural phrases, position codes, filler nouns, and command prefixes
        cleaned = text
        cleaned = re.sub(r'\b(?:use\s+name|change\s+name\s+to|name\s+is|set\s+name\s+to|named|called)\b', '', cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r'\b(?:into|to|for|in|at)\s+(?:that\s+position|position\s+[A-Za-z0-9_-]+|code\s+[A-Za-z0-9_-]+)\b', '', cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r'\b(?:position|pos|code)\s+[A-Za-z0-9_-]+\b', '', cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r'\b300\d{4}(?:_[A-Za-z0-9]+)?\b', '', cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r'\b(?:a\s+new\s+employee|an\s+employee|a\s+candidate|a\s+person|someone|new\s+hire|an\s+individual)\b', '', cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r'\b(?:as\s+(?:an?|the)?\s+[A-Za-z\s]+|starting\s+\S+|with\s+start\s+date\s+\S+)\b', '', cleaned, flags=re.IGNORECASE)

        # Rule 3: Look for 'hire/onboard <First> <Last>' in cleaned text
        m_hire = re.search(r'\b(?:hire|onboard)\s+(?:mr\.?|ms\.?|mrs\.?|dr\.?)?\s*([A-Za-z]+)\s+([A-Za-z]+)', cleaned, re.IGNORECASE)
        if m_hire:
            f, l = m_hire.group(1).strip().capitalize(), m_hire.group(2).strip().capitalize()
            if f.lower() not in stopwords and l.lower() not in stopwords:
                return f, l

        # Rule 4: Extract remaining valid alphabetic non-stopword tokens
        words = [w.strip('.,!?\"\'') for w in cleaned.split() if w.strip('.,!?\"\'')]
        valid = [w for w in words if w.lower() not in stopwords and w.isalpha()]
        if len(valid) >= 2:
            return valid[0].capitalize(), valid[1].capitalize()

        return None, None

    def _extract_position_code(self, text: str, default: Optional[str] = None) -> Optional[str]:
        m = re.search(r'\b(4\d{6}|300\d{4}(?:_[A-Za-z0-9]+)?)\b', text)
        if m:
            return m.group(1)
            
        if "that position" in text.lower() or "this position" in text.lower() or "the position" in text.lower():
            return self.last_created_position
            
        m2 = re.search(r'(?:position|pos|code)\s+([A-Za-z0-9_-]+)', text, re.IGNORECASE)
        if m2:
            val = m2.group(1)
            if val.lower() not in {"and", "to", "into", "for", "with", "is", "a", "an", "the", "that", "this"}:
                return val
                
        return default or self.last_created_position

    def _handle_clone_and_hire(self, text: str) -> Dict[str, Any]:
        source_code = self._extract_position_code(text, default="3000383")
        target_code = self.pos_svc.get_next_position_code(4000001)
        first_name, last_name = self._extract_person_name(text)

        # If name is not specified, propose a candidate name and ask for confirmation
        if not first_name or not last_name:
            lower = text.lower()
            extra_overrides = {}
            
            # Natural Language Semantic Aliases
            if "full time" in lower or "full-time" in lower:
                extra_overrides["isFulltimeEmployee"] = True
                extra_overrides["standardHours"] = "40.0"
            elif "part time" in lower or "part-time" in lower:
                extra_overrides["isFulltimeEmployee"] = False
                extra_overrides["standardHours"] = "20.0"
                
            if "contractor" in lower or "contingent" in lower:
                extra_overrides["isContingentWorker"] = True
                
            for entity in ["EmpJob", "PerPersonal", "PerEmail", "PerPhone", "PerAddressDEFLT"]:
                schema = self.meta.get_schema(entity)
                if schema:
                    for prop_name in schema.properties.keys():
                        if prop_name.startswith("mdf") or prop_name in ["userId", "personIdExternal", "firstName", "lastName", "gender", "salutation", "position"]:
                            continue
                        m = re.search(fr'\b{prop_name.lower()}\s+([a-zA-Z0-9_.-]+)', lower)
                        if m:
                            extra_overrides[prop_name] = m.group(1)
                            
            first_name, last_name, gender, salutation = _generate_random_candidate()
            self.pending_proposal = {
                "action": "clone_and_hire",
                "source_code": source_code,
                "target_code": target_code,
                "first_name": first_name,
                "last_name": last_name,
                "gender": gender,
                "salutation": salutation,
                "start_date": date.today().isoformat(),
                "extra_overrides": extra_overrides
            }
            return {
                "response": f"""### 📋 Proposed Onboarding Plan (Awaiting Confirmation)

I am going to execute this onboarding plan:
- **Candidate Name:** **{first_name} {last_name}** *(suggested)*
- **Gender & Salutation:** `{gender}` ({salutation}.)
- **Source Position:** `{source_code}`
- **New Position Code:** `{target_code}` *(4000000 range)*
- **Start Date:** `{date.today().isoformat()}`

👉 **Click "Confirm & Execute Live" below** or reply **"Yes"** to proceed *(or reply with a different name like "Use name Keanu Reeves")*.""",
                "card": {
                    "type": "confirmation_proposal",
                    "data": self.pending_proposal
                }
            }

        # If name was explicitly given, detect gender and execute directly
        self.pending_proposal = None
        gender, salutation = detect_gender_and_salutation(first_name, text)
        steps_log = []

        # Step A: Clone position
        try:
            src_pos = self.pos_svc.get_position(source_code)
            if not src_pos:
                return {
                    "response": f"⚠️ **Source Position `{source_code}` was not found in SuccessFactors.** Please verify the position code.",
                    "card": {"type": "error", "message": f"Position {source_code} not found."}
                }

            existing_target = self.pos_svc.get_position(target_code)
            if existing_target:
                steps_log.append(f"Target position `{target_code}` already exists; using existing clone.")
            else:
                clone_result = self.pos_svc.clone_position(source_code=source_code, target_code=target_code)
                steps_log.append(f"Cloned Position `{source_code}` ➔ `{target_code}` (*{clone_result.get('title')}*).")
        except Exception as e:
            return {
                "response": f"❌ **Failed to clone position `{source_code}`:** {str(e)}",
                "card": {"type": "error", "message": str(e)}
            }

        # Step B: Hire employee into the target position
        start_date = date.today().isoformat()
        
        lower = text.lower()
        extra_overrides = {}
        
        # Natural Language Semantic Aliases
        if "full time" in lower or "full-time" in lower:
            extra_overrides["isFulltimeEmployee"] = True
            extra_overrides["standardHours"] = "40.0"
        elif "part time" in lower or "part-time" in lower:
            extra_overrides["isFulltimeEmployee"] = False
            extra_overrides["standardHours"] = "20.0"
            
        if "contractor" in lower or "contingent" in lower:
            extra_overrides["isContingentWorker"] = True
            
        for entity in ["EmpJob", "PerPersonal", "PerEmail", "PerPhone", "PerAddressDEFLT"]:
            schema = self.meta.get_schema(entity)
            if schema:
                for prop_name in schema.properties.keys():
                    if prop_name.startswith("mdf") or prop_name in ["userId", "personIdExternal", "firstName", "lastName", "gender", "salutation", "position"]:
                        continue
                    m = re.search(fr'\b{prop_name.lower()}\s+([a-zA-Z0-9_.-]+)', lower)
                    if m:
                        extra_overrides[prop_name] = m.group(1)

        hire_inputs = {
            "first_name": first_name,
            "last_name": last_name,
            "gender": gender,
            "salutation": salutation,
            "position": target_code,
            "start_date": start_date,
            **extra_overrides
        }

        try:
            hire_res = self.hire_svc.hire_employee(hire_inputs, dry_run=False)
            steps_log.append(f"Executed 6-step onboarding for **{hire_res['full_name']}** (Person ID: `{hire_res['person_id']}`).")

            response_md = f"""### 🎉 Successfully Cloned Position & Onboarded Employee!

**1. Position Cloned (4000000 Range):**
- **Source:** `{source_code}`
- **New Position Code:** `{target_code}`
- **Status:** `Vacant = True`

**2. Employee Onboarded:**
- **Name:** {hire_res['full_name']} ({salutation}.)
- **Gender:** `{gender}`
- **User ID / Person ID:** `{hire_res['person_id']}`
- **Assigned Position:** `{target_code}`
- **Email:** `{hire_res['email']}`
- **Start Date:** `{hire_res['start_date']}`

**3. Transactional Steps:**
- `User`: **{hire_res['steps'].get('User', 'OK')}**
- `PerPersonal`: **{hire_res['steps'].get('PerPersonal', 'OK')}**
- `PerNationalId`: **{hire_res['steps'].get('PerNationalId', 'OK')}**
- `PerEmail`: **{hire_res['steps'].get('PerEmail', 'OK')}**
- `PerPhone`: **{hire_res['steps'].get('PerPhone', 'OK')}**
- `PerAddressDEFLT`: **{hire_res['steps'].get('PerAddressDEFLT', 'OK')}**
- `EmpEmployment`: **{hire_res['steps'].get('EmpEmployment', 'OK')}**
- `EmpJob`: **{hire_res['steps'].get('EmpJob', 'OK')}**
"""
            return {
                "response": response_md,
                "card": {
                    "type": "hire_success",
                    "data": hire_res,
                    "position": target_code,
                }
            }
        except Exception as e:
            return {
                "response": f"⚠️ Position `{target_code}` was ready, but onboarding failed: {str(e)}",
                "card": {"type": "error", "message": str(e)}
            }

    def _execute_pending_proposal(self) -> Dict[str, Any]:
        """Execute a previously proposed plan upon user confirmation."""
        if not self.pending_proposal:
            return {"response": "No pending plan to confirm. What would you like me to do?"}

        prop = self.pending_proposal
        self.pending_proposal = None

        if prop.get("action") == "clone_and_hire":
            source_code = prop["source_code"]
            target_code = prop["target_code"]
            first_name = prop["first_name"]
            last_name = prop["last_name"]
            gender = prop.get("gender") or "M"
            salutation = prop.get("salutation") or "Mr"
            start_date = prop.get("start_date") or date.today().isoformat()
            extra = prop.get("extra_overrides") or {}

            # Step A: Clone position if needed
            existing_target = self.pos_svc.get_position(target_code)
            if not existing_target:
                self.pos_svc.clone_position(source_code=source_code, target_code=target_code)

            # Step B: Hire
            hire_inputs = {
                "first_name": first_name,
                "last_name": last_name,
                "gender": gender,
                "salutation": salutation,
                "position": target_code,
                "start_date": start_date,
                **extra
            }
            try:
                hire_res = self.hire_svc.hire_employee(hire_inputs, dry_run=False)
                return {
                    "response": f"""### 🎉 Successfully Confirmed & Executed!

**1. Position Cloned (4000000 Range):**
- **Source:** `{source_code}` ➔ **Target:** `{target_code}`

**2. Employee Onboarded:**
- **Name:** {hire_res['full_name']} ({salutation}.)
- **Gender:** `{gender}`
- **User ID / Person ID:** `{hire_res['person_id']}`
- **Assigned Position:** `{target_code}`
- **Email:** `{hire_res['email']}`
- **Start Date:** `{hire_res['start_date']}`

**3. Transactional Steps:**
- `User`: **{hire_res['steps'].get('User', 'OK')}**
- `PerPersonal`: **{hire_res['steps'].get('PerPersonal', 'OK')}**
- `PerNationalId`: **{hire_res['steps'].get('PerNationalId', 'OK')}**
- `PerEmail`: **{hire_res['steps'].get('PerEmail', 'OK')}**
- `EmpEmployment`: **{hire_res['steps'].get('EmpEmployment', 'OK')}**
- `EmpJob`: **{hire_res['steps'].get('EmpJob', 'OK')}**
""",
                    "card": {"type": "hire_success", "data": hire_res, "position": target_code}
                }
            except Exception as e:
                return {
                    "response": f"❌ Execution failed: {str(e)}",
                    "card": {"type": "error", "message": str(e)}
                }

        elif prop.get("action") == "hire":
            position_code = prop["position_code"]
            first_name = prop["first_name"]
            last_name = prop["last_name"]
            gender = prop.get("gender") or "M"
            salutation = prop.get("salutation") or "Mr"
            start_date = prop.get("start_date") or date.today().isoformat()
            extra = prop.get("extra_overrides") or {}
            hire_inputs = {
                "first_name": first_name,
                "last_name": last_name,
                "gender": gender,
                "salutation": salutation,
                "position": position_code,
                "start_date": start_date,
                **extra
            }
            try:
                hire_res = self.hire_svc.hire_employee(hire_inputs, dry_run=False)
                return {
                    "response": f"""### 👤 Successfully Confirmed & Onboarded!
- **Name:** {hire_res['full_name']} ({salutation}.)
- **Gender:** `{gender}`
- **User ID / Person ID:** `{hire_res['person_id']}`
- **Position:** `{hire_res['position']}`
- **Email:** `{hire_res['email']}`
- **Start Date:** `{hire_res['start_date']}`
""",
                    "card": {"type": "hire_success", "data": hire_res}
                }
            except Exception as e:
                return {"response": f"❌ Hiring failed: {str(e)}"}

        return {"response": "Proposal executed."}

    def _handle_clone_position(self, text: str) -> Dict[str, Any]:
        source_code = self._extract_position_code(text, default="3000383")
        target_code = None
        
        # Look for explicit target code like 'to 4000005' or 'as 4000005'
        match = re.search(r'(?:to|into|as)\s+([0-9_]{5,10})', text, re.IGNORECASE)
        if match:
            target_code = match.group(1)
        else:
            target_code = self.pos_svc.get_next_position_code(4000001)

        overrides = {}
        m_title = re.search(r'(?:title|name)(?:.*?)(?:to|as|is)\s+([^,.;]+)', text, re.IGNORECASE)
        if m_title:
            overrides["externalName_defaultValue"] = m_title.group(1).strip()

        try:
            res = self.pos_svc.clone_position(source_code=source_code, target_code=target_code, overrides=overrides)
            self.last_created_position = target_code
            return {
                "response": f"""### ✅ Position Cloned Successfully into 4000000 Range!
- **Source Code:** `{source_code}`
- **New Target Code:** `{target_code}`
- **Title:** {res.get('title')}
- **Company:** `{res.get('company')}`
- **Department:** `{res.get('department')}`
- **Job Code:** `{res.get('jobCode')}`
""",
                "card": {"type": "position_cloned", "data": res}
            }
        except Exception as e:
            return {
                "response": f"❌ **Error cloning position:** {str(e)}",
                "card": {"type": "error", "message": str(e)}
            }

    def _handle_update_position(self, text: str) -> Dict[str, Any]:
        """Update fields on an existing Position."""
        pos_code = self._extract_position_code(text)
        if not pos_code:
            return {"response": "⚠️ Please specify which position code you want to update (e.g., 'Update position 4000001 title to Principal Architect')."}

        updates: Dict[str, Any] = {}

        # Title update
        m_title = re.search(r'(?:title|name)\s+(?:to|as)\s+([^,.;]+)', text, re.IGNORECASE)
        if m_title:
            updates["externalName_defaultValue"] = m_title.group(1).strip()

        # Vacant update
        if "vacant" in text.lower():
            if any(w in text.lower() for w in ["false", "no", "filled", "occupied"]):
                updates["vacant"] = False
            elif any(w in text.lower() for w in ["true", "yes", "open"]):
                updates["vacant"] = True

        # Standard hours update
        m_hours = re.search(r'(?:standard\s+hours|hours)\s+(?:to|as)?\s*(\d+(?:\.\d+)?)', text, re.IGNORECASE)
        if m_hours:
            updates["standardHours"] = str(m_hours.group(1))

        if not updates:
            return {"response": f"⚠️ Found position `{pos_code}`, but could not determine which fields to update. You can specify `title to <New Title>`, `vacant to true/false`, or `hours to <Hours>`."}

        try:
            res = self.pos_svc.update_position(pos_code, updates)
            return {
                "response": f"""### ✅ Position `{pos_code}` Successfully Updated!
- **Position Code:** `{pos_code}`
- **Updated Fields:** {', '.join(f'`{k}`' for k in updates.keys())}
- **Values Applied:** `{json.dumps(updates)}`
""",
                "card": {"type": "position_updated", "data": res}
            }
        except Exception as e:
            return {"response": f"❌ Failed to update position `{pos_code}`: {str(e)}"}

    def _handle_update_employee(self, text: str) -> Dict[str, Any]:
        """Update fields on an existing Employee (EmpJob, PerPersonal, PerEmail, etc)."""
        m_id = re.search(r'\b(\d{5,7})\b', text)
        if not m_id:
            return {"response": "⚠️ Please specify the User ID / Person ID of the employee to update (e.g. 'Update employee 900181 email to test@example.com')."}

        person_id = m_id.group(1)
        lower = text.lower()
        
        # Build dynamic overrides
        extra_overrides = {}
        if "full time" in lower or "full-time" in lower:
            extra_overrides["isFulltimeEmployee"] = True
            extra_overrides["standardHours"] = "40.0"
        elif "part time" in lower or "part-time" in lower:
            extra_overrides["isFulltimeEmployee"] = False
            extra_overrides["standardHours"] = "20.0"
            
        if "temporary" in lower or "contractor" in lower:
            extra_overrides["regularTemp"] = self.picks.resolve("regular-temp", "T") or "3613"
        elif "regular employee" in lower:
            extra_overrides["regularTemp"] = self.picks.resolve("regular-temp", "R") or "3612"

        # Explicit email regex
        m_email = re.search(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b', text)
        if m_email:
            extra_overrides["emailAddress"] = m_email.group(0).strip()
            
        entities = ["EmpJob", "PerPersonal", "PerEmail", "PerPhone", "PerAddressDEFLT"]
        entity_updates = {e: {} for e in entities}
        
        for entity in entities:
            schema = self.meta.get_schema(entity)
            if schema:
                for prop_name in schema.properties.keys():
                    if prop_name.startswith("mdf") or prop_name in ["userId", "personIdExternal", "firstName", "lastName", "gender", "salutation", "position"]:
                        continue
                    if prop_name in extra_overrides:
                        entity_updates[entity][prop_name] = extra_overrides[prop_name]
                    else:
                        m = re.search(fr'\b{prop_name.lower()}\s+(?:to\s+)?([a-zA-Z0-9_.-]+)', lower)
                        if m:
                            val = m.group(1)
                            # Picklist resolution
                            if schema.properties[prop_name].picklist_id:
                                picklist_id = schema.properties[prop_name].picklist_id
                                resolved = self.picks.resolve(picklist_id, val)
                                val = resolved if resolved else val
                            entity_updates[entity][prop_name] = val
                            
        updates_done = []
        for entity, updates in entity_updates.items():
            if not updates:
                continue
                
            try:
                # Fetch existing latest record
                filter_field = "userId" if entity == "EmpJob" else "personIdExternal"
                res = self.sf_client.get(entity, params={"$filter": f"{filter_field} eq '{person_id}'"})
                rows = res.get("d", {}).get("results", [])
                
                # If record doesn't exist, we skip or could insert, but for safety let's use the first row found
                if rows:
                    latest = rows[0]
                    # We only need the primary keys to upsert an update
                    payload = {"__metadata": {"uri": entity}, filter_field: person_id}
                    if "startDate" in latest:
                        payload["startDate"] = latest["startDate"]
                    if "seqNumber" in latest:
                        payload["seqNumber"] = str(latest["seqNumber"])
                    if "phoneType" in latest and entity == "PerPhone":
                        payload["phoneType"] = latest["phoneType"]
                    if "addressType" in latest and entity == "PerAddressDEFLT":
                        payload["addressType"] = latest["addressType"]
                    if "emailType" in latest and entity == "PerEmail":
                        payload["emailType"] = latest["emailType"]
                        
                    payload.update(updates)
                    self.sf_client.deep_upsert(payload)
                    fields = ", ".join(updates.keys())
                    updates_done.append(f"`{entity}`: {fields}")
            except Exception as e:
                return {"response": f"❌ Failed to update `{entity}` for employee `{person_id}`: {str(e)}"}

        if not updates_done:
            return {"response": f"⚠️ Employee `{person_id}` identified, but could not parse target update fields (e.g. emailAddress, regularTemp, isFulltimeEmployee)."}

        return {
            "response": f"""### ✅ Employee `{person_id}` Successfully Updated!
- **Employee ID:** `{person_id}`
- **Entities Updated:**
  - """ + "\n  - ".join(updates_done),
            "card": {"type": "employee_updated", "person_id": person_id, "changes": updates_done}
        }

    def _handle_create_position(self, text: str) -> Dict[str, Any]:
        """Creates a new position using a template approach and applies overrides."""
        lower = text.lower()
        overrides = {}
        schema = self.meta.get_schema("Position")
        
        # 1. Dynamically extract any matching property from the prompt
        if schema:
            for prop_name in schema.properties.keys():
                if prop_name.startswith("mdf") or prop_name in ["code"]:
                    continue
                # Look for simple key-value pairs (e.g., 'department 50150016', 'costcenter 2260')
                m = re.search(fr'\b{prop_name.lower()}\s+([a-zA-Z0-9_.-]+)', lower)
                if m:
                    overrides[prop_name] = m.group(1)

        # 2. Extract title explicitly as it might have spaces
        title = "New Position"
        m_title = re.search(r'title\s+(?:is\s+)?([a-zA-Z\s]+?)(?=\s+(?:company|department|costcenter|location|businessunit|division|jobcode)|$)', lower)
        if m_title:
            title = m_title.group(1).strip().title()
        overrides["externalName_defaultValue"] = title

        # 3. Auto-resolve missing org fields if company is present
        company = overrides.get("company")
        if company:
            overrides["company"] = company.upper()
            for entity, field in [('FOBusinessUnit', 'businessUnit'), ('FODivision', 'division'), ('FODepartment', 'department'), ('FOCostCenter', 'costCenter'), ('FOLocation', 'location')]:
                if field not in overrides:  # Only auto-resolve if user didn't explicitly provide it
                    try:
                        res = self.sf_client.get(entity, params={'$top': 1, '$filter': f"company eq '{company.upper()}'"})
                        items = res.get('d', {}).get('results', [])
                        if items:
                            overrides[field] = items[0].get('externalCode')
                    except:
                        pass


        try:
            target_code = self.pos_svc.get_next_position_code(4000001)
            # Use 3000383 as the default template position
            self.pos_svc.clone_position(source_code="3000383", target_code=target_code, title_suffix="", overrides=overrides)
            
            created = self.pos_svc.get_position(target_code)
            self.last_created_position = target_code
            
            md = f"### ✨ Position Created\n\n"
            md += f"**Target Code:** `{target_code}`\n\n"
            md += f"**Title:** {created.get('externalName_defaultValue', title)}\n\n"
            md += f"**Company:** {created.get('company', company)}\n\n"
            md += f"**Status:** Vacant (True)\n\n"
            
            return {
                "response": f"I have created a new position (`{target_code}`) using our standard template.",
                "card": {
                    "type": "create_success",
                    "markdown": md,
                    "data": {"target_code": target_code}
                }
            }
        except Exception as e:
            return {
                "response": f"⚠️ Failed to create position: {str(e)}"
            }

    def _handle_hire_employee(self, text: str) -> Dict[str, Any]:
        first_name, last_name = self._extract_person_name(text)
        position_code = self._extract_position_code(text, default="4000001")

        # Dynamically extract optional overrides for EmpJob and PerPersonal
        lower = text.lower()
        extra_overrides = {}
        
        # Natural Language Semantic Aliases
        if "full time" in lower or "full-time" in lower:
            extra_overrides["isFulltimeEmployee"] = True
            extra_overrides["standardHours"] = "40.0"
        elif "part time" in lower or "part-time" in lower:
            extra_overrides["isFulltimeEmployee"] = False
            extra_overrides["standardHours"] = "20.0"
            
        if "contractor" in lower or "contingent" in lower:
            extra_overrides["isContingentWorker"] = True
            
        for entity in ["EmpJob", "PerPersonal", "PerEmail", "PerPhone", "PerAddressDEFLT"]:
            schema = self.meta.get_schema(entity)
            if schema:
                for prop_name in schema.properties.keys():
                    if prop_name.startswith("mdf") or prop_name in ["userId", "personIdExternal", "firstName", "lastName", "gender", "salutation", "position"]:
                        continue
                    m = re.search(fr'\b{prop_name.lower()}\s+([a-zA-Z0-9_.-]+)', lower)
                    if m:
                        extra_overrides[prop_name] = m.group(1)

        # Extract start date
        start_date = date.today().isoformat()
        m_date = re.search(r'(?:start(?:ing)?\s+(?:on\s+)?|start_date\s+)(1st\s+sept|sept\s+1st|september\s+1(?:st)?|1\s+sept|09-01|2026-09-01)', lower)
        if m_date:
            start_date = "2026-09-01"

        # If name is not specified, propose a candidate name and ask for confirmation
        if not first_name or not last_name:
            first_name, last_name, gender, salutation = _generate_random_candidate()
            self.pending_proposal = {
                "action": "hire",
                "position_code": position_code,
                "first_name": first_name,
                "last_name": last_name,
                "gender": gender,
                "salutation": salutation,
                "start_date": start_date,
                "extra_overrides": extra_overrides
            }
            return {
                "response": f"""### 📋 Proposed Hire Plan (Awaiting Confirmation)

I am going to onboard this employee:
- **Candidate Name:** **{first_name} {last_name}** *(suggested)*
- **Gender & Salutation:** `{gender}` ({salutation}.)
- **Position:** `{position_code}`
- **Start Date:** `{start_date}`

👉 **Click "Confirm & Execute Live" below** or reply **"Yes"** to proceed *(or reply with a different name like "Use name Sarah Connor")*.""",
                "card": {
                    "type": "confirmation_proposal",
                    "data": self.pending_proposal
                }
            }

        gender, salutation = detect_gender_and_salutation(first_name, text)
        hire_inputs = {
            "first_name": first_name,
            "last_name": last_name,
            "gender": gender,
            "salutation": salutation,
            "position": position_code,
            "start_date": start_date,
            **extra_overrides
        }

        try:
            res = self.hire_svc.hire_employee(hire_inputs, dry_run=False)
            return {
                "response": f"""### 👤 New Employee Successfully Hired!
- **Name:** {res['full_name']}
- **Person ID / User ID:** `{res['person_id']}`
- **Position:** `{res['position']}`
- **Email:** `{res['email']}`
- **Start Date:** `{res['start_date']}`

**Onboarding Entities Processed:**
`User` ✅ | `PerPersonal` ✅ | `PerNationalId` ✅ | `PerEmail` ✅ | `EmpEmployment` ✅ | `EmpJob` ✅
""",
                "card": {"type": "hire_success", "data": res}
            }
        except Exception as e:
            return {
                "response": f"❌ **Hiring failed:** {str(e)}",
                "card": {"type": "error", "message": str(e)}
            }

    def _handle_get_position(self, text: str) -> Dict[str, Any]:
        pos_match = re.search(r'\b([A-Za-z0-9_]{4,20})\b', text)
        codes = [w for w in text.split() if any(c.isdigit() for c in w)]
        code = codes[0] if codes else "3000383"

        pos = self.pos_svc.get_position(code)
        if not pos:
            return {
                "response": f"🔍 Position `{code}` was not found in SuccessFactors.",
                "card": {"type": "not_found", "code": code}
            }

        title = pos.get("externalName_defaultValue") or pos.get("code")
        self.last_created_position = code
        return {
            "response": f"""### 🏢 Position `{code}` Details
- **Title:** **{title}**
- **Company:** `{pos.get('company')}`
- **Department:** `{pos.get('department')}`
- **Job Code:** `{pos.get('jobCode')}`
- **Cost Center:** `{pos.get('costCenter')}`
- **Location:** `{pos.get('location')}`
- **Vacant:** `{'Yes' if pos.get('vacant') else 'No'}`
- **Standard Hours:** `{pos.get('standardHours', '40.0')}`
""",
            "card": {"type": "position_details", "data": {k: v for k, v in pos.items() if not isinstance(v, dict)}}
        }

    def _handle_list_positions(self, text: str) -> Dict[str, Any]:
        try:
            positions = self.pos_svc.list_positions(top=8)
            lines = [f"- **`{p['code']}`** — {p.get('externalName_defaultValue', 'Untitled')} *(Company: {p.get('company')})*" for p in positions]
            return {
                "response": f"### 📋 Top Positions in SuccessFactors:\n" + "\n".join(lines),
                "card": {"type": "position_list", "data": positions}
            }
        except Exception as e:
            return {"response": f"Error listing positions: {str(e)}", "card": {"type": "error", "message": str(e)}}

    def _handle_query_employee(self, text: str) -> Dict[str, Any]:
        id_match = re.search(r'\b(\d{5,7})\b', text)
        if not id_match:
            return {"response": "Please specify a numeric User ID or Person ID (e.g. `900170`)."}

        user_id = id_match.group(1)
        try:
            res = self.sf_client.get("EmpJob", params={"$filter": f"userId eq '{user_id}'"})
            records = res.get("d", {}).get("results", [])
            if not records:
                return {"response": f"🔍 No EmpJob record found for User ID `{user_id}`."}
            r = records[0]
            return {
                "response": f"""### 👤 Employee Profile (`{user_id}`)
- **Job Title:** {r.get('jobTitle', 'N/A')}
- **Position:** `{r.get('position', 'N/A')}`
- **Company:** `{r.get('company', 'N/A')}`
- **Department:** `{r.get('department', 'N/A')}`
- **Event Reason:** `{r.get('eventReason', 'N/A')}`
- **Start Date:** `{r.get('startDate', 'N/A')}`
""",
                "card": {"type": "employee_details", "data": {k: v for k, v in r.items() if not isinstance(v, dict)}}
            }
        except Exception as e:
            return {"response": f"Query failed: {str(e)}"}

    def _handle_picklist_query(self, text: str) -> Dict[str, Any]:
        # Match common picklist IDs
        known = ["salutation", "SUFFIX", "ecEmailType", "Gender", "ecMaritalStatus", "language"]
        found = "salutation"
        for k in known:
            if k.lower() in text.lower():
                found = k
                break

        opts = self.picks.search(found, query="")
        sample = opts[:8]
        lines = [f"- `{o.get('id')}` : **{o.get('label')}**" for o in sample]
        return {
            "response": f"### 📋 Picklist `{found}` Options (First {len(sample)}):\n" + "\n".join(lines),
            "card": {"type": "picklist_data", "data": opts}
        }

    def _handle_schema_query(self, text: str) -> Dict[str, Any]:
        entities = ["User", "PerPersonal", "PerNationalId", "PerEmail", "EmpEmployment", "EmpJob", "Position"]
        found = "PerPersonal"
        for e in entities:
            if e.lower() in text.lower():
                found = e
                break

        schema = self.meta.get_schema(found)
        if not schema:
            return {"response": f"Schema for `{found}` not found in cached metadata."}

        keys = list(schema.properties.keys())[:15]
        return {
            "response": f"""### 📐 Entity Schema: `{found}`
- **Total Properties:** {len(schema.properties)}
- **Key Fields:** `{', '.join(schema.key_properties)}`
- **Sample Properties:** `{', '.join(keys)}...`
""",
            "card": {"type": "schema", "entity": found, "keys": schema.key_properties}
        }

    def _handle_general_qa(self, text: str) -> Dict[str, Any]:
        return {
            "response": f"""I am the **SFDataBuilder AI Copilot**. I can directly execute SuccessFactors operations:

👉 **Try saying:**
- *"Clone position 3000383 and hire Keanu Reeves"*
- *"Clone position 3000383 to 3000383_COPY"*
- *"Hire Scarlett Johansson to position 3000383_COPY"*
- *"Check position 3000383"*
- *"List top positions"*
- *"Find employee 900170"*
- *"Search picklist salutation"*
"""
        }


copilot = AICopilotEngine()


# ==============================================================================
# HTML FRONTEND (WITH EMBEDDED AI COPILOT CHAT)
# ==============================================================================

HTML_CONTENT = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>SFDataBuilder | AI Studio</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com">
  <link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@300;400;500;600;700;800&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">
  <script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
  <style>
    body {
      font-family: 'Plus Jakarta Sans', sans-serif;
      background: #090d16;
      color: #f1f5f9;
    }
    code, pre, .font-mono {
      font-family: 'JetBrains Mono', monospace;
    }
    .glass-panel {
      background: rgba(15, 23, 42, 0.75);
      backdrop-filter: blur(16px);
      border: 1px solid rgba(255, 255, 255, 0.08);
    }
    .glass-card {
      background: rgba(30, 41, 59, 0.45);
      border: 1px solid rgba(255, 255, 255, 0.06);
    }
    .custom-scrollbar::-webkit-scrollbar {
      width: 6px;
      height: 6px;
    }
    .custom-scrollbar::-webkit-scrollbar-track {
      background: rgba(15, 23, 42, 0.6);
    }
    .custom-scrollbar::-webkit-scrollbar-thumb {
      background: rgba(71, 85, 105, 0.8);
      border-radius: 9999px;
    }
    .markdown-content h3 { font-size: 1.1rem; font-weight: 700; margin-bottom: 0.5rem; color: #fff; }
    .markdown-content ul { list-style-type: disc; margin-left: 1.25rem; margin-bottom: 0.5rem; }
    .markdown-content li { margin-bottom: 0.25rem; }
    .markdown-content code { background: rgba(0,0,0,0.3); padding: 0.15rem 0.35rem; border-radius: 0.25rem; font-size: 0.85em; }
    .markdown-content p { margin-bottom: 0.5rem; }
  </style>
</head>
<body class="min-h-screen flex flex-col antialiased selection:bg-blue-600 selection:text-white">

  <!-- Top Navbar -->
  <header class="sticky top-0 z-50 glass-panel border-b border-slate-800/80 px-6 py-3.5">
    <div class="max-w-7xl mx-auto flex items-center justify-between">
      <div class="flex items-center space-x-3">
        <div class="h-10 w-10 rounded-xl bg-gradient-to-tr from-blue-600 via-indigo-600 to-purple-600 flex items-center justify-center shadow-lg shadow-blue-500/20 ring-1 ring-white/20">
          <i class="fa-solid fa-wand-magic-sparkles text-white text-lg"></i>
        </div>
        <div>
          <div class="flex items-center space-x-2">
            <h1 class="text-lg font-bold tracking-tight text-white">SFDataBuilder Studio</h1>
            <span class="px-2 py-0.5 text-xs font-semibold rounded-full bg-gradient-to-r from-blue-500/20 to-purple-500/20 text-blue-300 border border-blue-500/30">AI Copilot v2.0</span>
          </div>
          <p class="text-xs text-slate-400">Conversational AI & Visual Studio for SAP SuccessFactors Employee Central</p>
        </div>
      </div>

      <div class="flex items-center space-x-4">
        <div id="connStatusBadge" class="flex items-center space-x-2 px-3 py-1.5 rounded-lg bg-emerald-950/40 border border-emerald-500/30 text-emerald-400 text-xs font-medium">
          <span class="h-2 w-2 rounded-full bg-emerald-400 animate-pulse"></span>
          <span id="connStatusText">Connected to SuccessFactors</span>
        </div>
        <button onclick="checkConnection()" title="Refresh connection" class="p-2 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-300 transition">
          <i class="fa-solid fa-rotate text-xs"></i>
        </button>
      </div>
    </div>
  </header>

  <!-- Main Container -->
  <div class="flex-1 max-w-7xl w-full mx-auto p-6 grid grid-cols-12 gap-6">

    <!-- Sidebar Navigation -->
    <aside class="col-span-12 md:col-span-3 space-y-3">
      <div class="glass-panel rounded-2xl p-3 space-y-1.5 shadow-xl">
        <div class="px-3 py-2 text-xs font-bold uppercase tracking-wider text-slate-400">Workspace</div>
        
        <button onclick="switchTab('chat')" id="tab-btn-chat" class="w-full flex items-center space-x-3 px-3.5 py-2.5 rounded-xl text-sm font-medium transition text-white bg-gradient-to-r from-blue-600 to-indigo-600 shadow-md shadow-blue-600/20">
          <i class="fa-solid fa-comments w-5 text-center text-amber-300"></i>
          <span>AI Chat Copilot</span>
        </button>

        <button onclick="switchTab('hire')" id="tab-btn-hire" class="w-full flex items-center space-x-3 px-3.5 py-2.5 rounded-xl text-sm font-medium transition text-slate-400 hover:text-white hover:bg-slate-800/60">
          <i class="fa-solid fa-user-plus w-5 text-center"></i>
          <span>Onboarding Wizard</span>
        </button>

        <button onclick="switchTab('position')" id="tab-btn-position" class="w-full flex items-center space-x-3 px-3.5 py-2.5 rounded-xl text-sm font-medium transition text-slate-400 hover:text-white hover:bg-slate-800/60">
          <i class="fa-solid fa-sitemap w-5 text-center"></i>
          <span>Position Manager</span>
        </button>

        <button onclick="switchTab('query')" id="tab-btn-query" class="w-full flex items-center space-x-3 px-3.5 py-2.5 rounded-xl text-sm font-medium transition text-slate-400 hover:text-white hover:bg-slate-800/60">
          <i class="fa-solid fa-database w-5 text-center"></i>
          <span>OData Explorer</span>
        </button>

        <button onclick="switchTab('picklist')" id="tab-btn-picklist" class="w-full flex items-center space-x-3 px-3.5 py-2.5 rounded-xl text-sm font-medium transition text-slate-400 hover:text-white hover:bg-slate-800/60">
          <i class="fa-solid fa-list-check w-5 text-center"></i>
          <span>Picklist Inspector</span>
        </button>
      </div>

      <!-- Quick Commands Card -->
      <div class="glass-card rounded-2xl p-4 border border-slate-800/80 space-y-3">
        <div class="flex items-center justify-between text-xs text-slate-400 font-semibold uppercase">
          <span>Quick Actions</span>
          <i class="fa-solid fa-bolt text-amber-400"></i>
        </div>
        <div class="space-y-1.5">
          <button onclick="sendQuickPrompt('Clone position 3000383 and hire Keanu Reeves to that position')" class="w-full text-left p-2 rounded-lg bg-slate-900/80 hover:bg-slate-800 text-xs text-slate-300 transition border border-slate-800 flex items-center space-x-2">
            <i class="fa-solid fa-clone text-blue-400 text-[10px]"></i>
            <span class="truncate">Clone 3000383 & Hire Keanu Reeves</span>
          </button>
          <button onclick="sendQuickPrompt('Check position 3000383')" class="w-full text-left p-2 rounded-lg bg-slate-900/80 hover:bg-slate-800 text-xs text-slate-300 transition border border-slate-800 flex items-center space-x-2">
            <i class="fa-solid fa-magnifying-glass text-indigo-400 text-[10px]"></i>
            <span class="truncate">Inspect Position 3000383</span>
          </button>
          <button onclick="sendQuickPrompt('List top positions')" class="w-full text-left p-2 rounded-lg bg-slate-900/80 hover:bg-slate-800 text-xs text-slate-300 transition border border-slate-800 flex items-center space-x-2">
            <i class="fa-solid fa-list text-emerald-400 text-[10px]"></i>
            <span class="truncate">List Top Positions</span>
          </button>
          <button onclick="sendQuickPrompt('Find employee 900170')" class="w-full text-left p-2 rounded-lg bg-slate-900/80 hover:bg-slate-800 text-xs text-slate-300 transition border border-slate-800 flex items-center space-x-2">
            <i class="fa-solid fa-user text-purple-400 text-[10px]"></i>
            <span class="truncate">Find Employee 900170</span>
          </button>
        </div>
      </div>
    </aside>

    <!-- Main Content Workspace -->
    <main class="col-span-12 md:col-span-9 space-y-6">

      <!-- ================= TAB 0: AI CHAT COPILOT (PRIMARY) ================= -->
      <section id="tab-chat" class="space-y-4">
        <div class="glass-panel rounded-2xl flex flex-col h-[700px] shadow-2xl border border-slate-800 relative overflow-hidden">
          
          <!-- Chat Header -->
          <div class="p-4 border-b border-slate-800/90 flex items-center justify-between bg-slate-900/50">
            <div class="flex items-center space-x-3">
              <div class="h-9 w-9 rounded-xl bg-blue-600/20 border border-blue-500/30 flex items-center justify-center">
                <i class="fa-solid fa-robot text-blue-400"></i>
              </div>
              <div>
                <h2 class="text-sm font-bold text-white flex items-center space-x-2">
                  <span>SuccessFactors AI Copilot</span>
                  <span class="px-2 py-0.5 text-[10px] rounded-full bg-emerald-500/10 text-emerald-400 border border-emerald-500/20 font-medium">Live Execution Enabled</span>
                </h2>
                <p class="text-xs text-slate-400">Ask in plain English to clone positions, onboard candidates, inspect schemas, or query data.</p>
              </div>
            </div>
            <button onclick="clearChat()" class="px-3 py-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-300 text-xs transition flex items-center space-x-1.5">
              <i class="fa-solid fa-trash-can"></i>
              <span>Clear</span>
            </button>
          </div>

          <!-- Chat Messages Scroll Area -->
          <div id="chatMessages" class="flex-1 p-5 overflow-y-auto space-y-4 custom-scrollbar">
            
            <!-- Welcome Message -->
            <div class="flex items-start space-x-3">
              <div class="h-8 w-8 rounded-lg bg-gradient-to-tr from-blue-600 to-indigo-600 flex-shrink-0 flex items-center justify-center text-white text-xs shadow-md">
                <i class="fa-solid fa-wand-magic-sparkles"></i>
              </div>
              <div class="max-w-[85%] rounded-2xl rounded-tl-sm bg-slate-900 border border-slate-800 p-4 shadow-lg text-sm text-slate-200">
                <p class="font-semibold text-white mb-1">Hello! I am your SAP SuccessFactors AI Copilot.</p>
                <p class="text-slate-300 mb-3">You can give me natural language instructions to clone positions, create new hires, or query Employee Central. For example:</p>
                <div class="grid grid-cols-1 sm:grid-cols-2 gap-2 text-xs">
                  <div onclick="sendQuickPrompt('Clone position 3000383 and hire Keanu Reeves')" class="p-2 rounded-lg bg-slate-950/80 hover:bg-slate-800/80 cursor-pointer border border-slate-800 transition">
                    <span class="text-blue-400 font-semibold">✨ "Clone position 3000383 and hire Keanu Reeves"</span>
                  </div>
                  <div onclick="sendQuickPrompt('Check position 3000383')" class="p-2 rounded-lg bg-slate-950/80 hover:bg-slate-800/80 cursor-pointer border border-slate-800 transition">
                    <span class="text-indigo-400 font-semibold">🔍 "Check position 3000383"</span>
                  </div>
                  <div onclick="sendQuickPrompt('List top positions')" class="p-2 rounded-lg bg-slate-950/80 hover:bg-slate-800/80 cursor-pointer border border-slate-800 transition">
                    <span class="text-emerald-400 font-semibold">📋 "List top positions"</span>
                  </div>
                  <div onclick="sendQuickPrompt('Find employee 900170')" class="p-2 rounded-lg bg-slate-950/80 hover:bg-slate-800/80 cursor-pointer border border-slate-800 transition">
                    <span class="text-purple-400 font-semibold">👤 "Find employee 900170"</span>
                  </div>
                </div>
              </div>
            </div>

          </div>

          <!-- Chat Input Area -->
          <div class="p-4 border-t border-slate-800/90 bg-slate-900/60">
            <form onsubmit="event.preventDefault(); submitChatMessage();" class="flex items-center space-x-3">
              <div class="flex-1 relative">
                <input type="text" id="chatInput" placeholder="e.g. 'Clone position 3000383 and hire Keanu Reeves' or 'Check position 3000383'..." class="w-full pl-4 pr-12 py-3 rounded-xl bg-slate-950 border border-slate-700 text-white placeholder-slate-500 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent text-sm">
              </div>
              <button type="submit" id="btnChatSend" class="px-5 py-3 rounded-xl bg-gradient-to-r from-blue-600 to-indigo-600 hover:from-blue-500 hover:to-indigo-500 text-white font-semibold text-sm shadow-lg shadow-blue-500/25 transition flex items-center space-x-2">
                <span>Send</span>
                <i class="fa-solid fa-paper-plane text-xs"></i>
              </button>
            </form>
          </div>

        </div>
      </section>

      <!-- ================= TAB 1: ONBOARDING WIZARD ================= -->
      <section id="tab-hire" class="hidden space-y-6">
        <div class="glass-panel rounded-2xl p-6 shadow-xl space-y-6">
          <div class="flex items-center justify-between border-b border-slate-800 pb-4">
            <div>
              <h2 class="text-xl font-bold text-white flex items-center space-x-2">
                <span>Employee Onboarding Wizard</span>
                <span class="text-xs px-2.5 py-0.5 rounded-full bg-indigo-500/10 text-indigo-400 border border-indigo-500/20 font-normal">6-Entity Transactional Flow</span>
              </h2>
              <p class="text-sm text-slate-400 mt-1">Fill out candidate details. Organization and job hierarchy will automatically inherit from the Position code.</p>
            </div>
            <div class="flex space-x-2">
              <button type="button" onclick="fillSampleCandidate('Tom', 'Cruise', '3000383_COPY')" class="px-3 py-1.5 text-xs font-medium rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-300 border border-slate-700 transition">
                <span>Sample: Tom Cruise</span>
              </button>
            </div>
          </div>

          <form id="hireForm" onsubmit="event.preventDefault();" class="space-y-5">
            <div class="grid grid-cols-1 md:grid-cols-3 gap-4">
              <div>
                <label class="block text-xs font-semibold uppercase tracking-wider text-slate-300 mb-1.5">First Name *</label>
                <input type="text" id="hireFirstName" required value="Tom" class="w-full px-3.5 py-2.5 rounded-xl bg-slate-900 border border-slate-700 text-white text-sm">
              </div>
              <div>
                <label class="block text-xs font-semibold uppercase tracking-wider text-slate-300 mb-1.5">Last Name *</label>
                <input type="text" id="hireLastName" required value="Cruise" class="w-full px-3.5 py-2.5 rounded-xl bg-slate-900 border border-slate-700 text-white text-sm">
              </div>
              <div>
                <label class="block text-xs font-semibold uppercase tracking-wider text-slate-300 mb-1.5">Start Date *</label>
                <input type="date" id="hireStartDate" required value="2026-08-20" class="w-full px-3.5 py-2.5 rounded-xl bg-slate-900 border border-slate-700 text-white text-sm">
              </div>
            </div>

            <div class="grid grid-cols-1 md:grid-cols-3 gap-4">
              <div>
                <label class="block text-xs font-semibold uppercase tracking-wider text-slate-300 mb-1.5">Position Code *</label>
                <input type="text" id="hirePosition" required value="3000383_COPY" class="w-full px-3.5 py-2.5 rounded-xl bg-slate-900 border border-slate-700 text-white font-mono text-sm">
              </div>
              <div>
                <label class="block text-xs font-semibold uppercase tracking-wider text-slate-300 mb-1.5">Gender</label>
                <select id="hireGender" class="w-full px-3.5 py-2.5 rounded-xl bg-slate-900 border border-slate-700 text-white text-sm">
                  <option value="M">Male (M)</option>
                  <option value="F">Female (F)</option>
                  <option value="D">Diverse (D)</option>
                </select>
              </div>
              <div>
                <label class="block text-xs font-semibold uppercase tracking-wider text-slate-300 mb-1.5">Salutation</label>
                <select id="hireSalutation" class="w-full px-3.5 py-2.5 rounded-xl bg-slate-900 border border-slate-700 text-white text-sm">
                  <option value="Mr">Mr.</option>
                  <option value="Ms">Ms.</option>
                  <option value="Mrs">Mrs.</option>
                  <option value="Dr">Dr.</option>
                </select>
              </div>
            </div>

            <div class="flex justify-end space-x-3 pt-2">
              <button type="button" onclick="executeHire(true)" id="btnDryRun" class="px-5 py-2.5 rounded-xl bg-slate-800 hover:bg-slate-700 text-slate-200 border border-slate-700 text-sm font-semibold transition">
                <i class="fa-solid fa-vial-circle-check text-indigo-400"></i>
                <span>Dry Run (Validate)</span>
              </button>
              <button type="button" onclick="executeHire(false)" id="btnLiveHire" class="px-6 py-2.5 rounded-xl bg-blue-600 hover:bg-blue-500 text-white text-sm font-semibold shadow-lg shadow-blue-500/25 transition">
                <i class="fa-solid fa-bolt text-amber-300"></i>
                <span>Execute Live Hire</span>
              </button>
            </div>
          </form>
        </div>

        <div id="hireResultContainer" class="hidden glass-panel rounded-2xl p-6 shadow-xl space-y-4 border border-slate-700">
          <div class="flex items-center justify-between">
            <h3 class="text-base font-bold text-white flex items-center space-x-2">
              <span id="hireResultTitle">Execution Results</span>
            </h3>
            <span id="hireResultBadge" class="px-3 py-1 text-xs font-semibold rounded-full bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">SUCCESS</span>
          </div>
          <pre id="hireJsonOutput" class="p-4 rounded-xl bg-slate-950 text-slate-300 font-mono text-xs overflow-x-auto max-h-80 custom-scrollbar border border-slate-800"></pre>
        </div>
      </section>

      <!-- ================= TAB 2: POSITION MANAGER ================= -->
      <section id="tab-position" class="hidden space-y-6">
        <div class="grid grid-cols-1 md:grid-cols-2 gap-6">
          <div class="glass-panel rounded-2xl p-6 shadow-xl space-y-5">
            <h2 class="text-lg font-bold text-white"><i class="fa-solid fa-copy text-blue-400"></i> Clone Position</h2>
            <form onsubmit="event.preventDefault(); clonePosition();" class="space-y-4">
              <div>
                <label class="block text-xs font-semibold text-slate-300 mb-1">Source Code *</label>
                <input type="text" id="cloneSourceCode" required value="3000383" class="w-full px-3.5 py-2 rounded-xl bg-slate-900 border border-slate-700 text-white font-mono text-sm">
              </div>
              <div>
                <label class="block text-xs font-semibold text-slate-300 mb-1">Target Code *</label>
                <input type="text" id="cloneTargetCode" required value="3000383_COPY" class="w-full px-3.5 py-2 rounded-xl bg-slate-900 border border-slate-700 text-white font-mono text-sm">
              </div>
              <div>
                <label class="block text-xs font-semibold text-slate-300 mb-1">Title Suffix</label>
                <input type="text" id="cloneTitleSuffix" value="(Copy)" class="w-full px-3.5 py-2 rounded-xl bg-slate-900 border border-slate-700 text-white text-sm">
              </div>
              <button type="submit" id="btnCloneSubmit" class="w-full py-2.5 rounded-xl bg-blue-600 hover:bg-blue-500 text-white font-semibold text-sm">Clone Position Now</button>
            </form>
            <div id="cloneResult" class="hidden p-3 rounded-xl bg-slate-950 border border-slate-800 text-xs font-mono text-slate-300 overflow-x-auto max-h-48 custom-scrollbar"></div>
          </div>

          <div class="glass-panel rounded-2xl p-6 shadow-xl space-y-5">
            <div class="flex justify-between items-center">
              <h2 class="text-lg font-bold text-white"><i class="fa-solid fa-magnifying-glass text-indigo-400"></i> Position Details</h2>
              <button onclick="fetchPositionList()" class="text-xs text-blue-400 hover:underline">List Top 10</button>
            </div>
            <div id="positionDetailsBox" class="p-4 rounded-xl bg-slate-950/80 border border-slate-800 text-xs text-slate-400 min-h-[220px] flex flex-col justify-center items-center text-center">
              <i class="fa-solid fa-sitemap text-3xl text-slate-700 mb-2"></i>
              <span>Enter a position code above or click "List Top 10" to inspect.</span>
            </div>
          </div>
        </div>
      </section>

      <!-- ================= TAB 3: ODATA EXPLORER ================= -->
      <section id="tab-query" class="hidden space-y-6">
        <div class="glass-panel rounded-2xl p-6 shadow-xl space-y-5">
          <h2 class="text-lg font-bold text-white"><i class="fa-solid fa-terminal text-emerald-400"></i> OData v2 Query Runner</h2>
          <div class="grid grid-cols-1 md:grid-cols-4 gap-3">
            <div>
              <label class="block text-xs text-slate-300 mb-1">Entity</label>
              <select id="queryEntity" class="w-full px-3 py-2 rounded-xl bg-slate-900 border border-slate-700 text-white text-xs font-mono">
                <option value="EmpJob">EmpJob</option>
                <option value="User">User</option>
                <option value="Position">Position</option>
                <option value="PerPersonal">PerPersonal</option>
                <option value="EmpEmployment">EmpEmployment</option>
              </select>
            </div>
            <div class="md:col-span-2">
              <label class="block text-xs text-slate-300 mb-1">$filter</label>
              <input type="text" id="queryFilter" placeholder="e.g. userId eq '900170'" class="w-full px-3 py-2 rounded-xl bg-slate-900 border border-slate-700 text-white font-mono text-xs">
            </div>
            <div>
              <label class="block text-xs text-slate-300 mb-1">$top</label>
              <input type="number" id="queryTop" value="5" class="w-full px-3 py-2 rounded-xl bg-slate-900 border border-slate-700 text-white font-mono text-xs">
            </div>
          </div>
          <button onclick="runODataQuery()" class="px-5 py-2 rounded-xl bg-emerald-600 hover:bg-emerald-500 text-white font-semibold text-xs">Execute Query</button>
          <pre id="queryJsonOutput" class="hidden p-4 rounded-xl bg-slate-950 text-slate-300 font-mono text-xs overflow-x-auto max-h-80 custom-scrollbar border border-slate-800"></pre>
        </div>
      </section>

      <!-- ================= TAB 4: PICKLISTS ================= -->
      <section id="tab-picklist" class="hidden space-y-6">
        <div class="glass-panel rounded-2xl p-6 shadow-xl space-y-5">
          <h2 class="text-lg font-bold text-white"><i class="fa-solid fa-list-ul text-amber-400"></i> Picklist Resolver</h2>
          <div class="grid grid-cols-1 md:grid-cols-3 gap-3">
            <div>
              <label class="block text-xs text-slate-300 mb-1">Picklist ID</label>
              <select id="picklistSelect" class="w-full px-3 py-2 rounded-xl bg-slate-900 border border-slate-700 text-white text-xs font-mono">
                <option value="salutation">salutation</option>
                <option value="SUFFIX">SUFFIX</option>
                <option value="ecEmailType">ecEmailType</option>
                <option value="Gender">Gender</option>
                <option value="ecMaritalStatus">ecMaritalStatus</option>
              </select>
            </div>
            <div>
              <label class="block text-xs text-slate-300 mb-1">Filter Query</label>
              <input type="text" id="picklistQuery" placeholder="Search label or ID" class="w-full px-3 py-2 rounded-xl bg-slate-900 border border-slate-700 text-white text-xs">
            </div>
            <div class="flex items-end">
              <button onclick="searchPicklist()" class="w-full py-2 rounded-xl bg-slate-800 hover:bg-slate-700 text-white font-semibold text-xs">Search Options</button>
            </div>
          </div>
          <div id="picklistResultBox" class="p-3 rounded-xl bg-slate-950 border border-slate-800 text-xs font-mono text-slate-300 overflow-x-auto max-h-80 custom-scrollbar">Click Search Options to view.</div>
        </div>
      </section>

    </main>
  </div>

  <!-- Toast Notification -->
  <div id="toast" class="fixed bottom-6 right-6 transform transition-all duration-300 translate-y-20 opacity-0 pointer-events-none z-50 flex items-center space-x-3 px-4 py-3 rounded-xl text-sm font-medium shadow-2xl"></div>

  <script>
    const chatHistory = [];

    function switchTab(tab) {
      const tabs = ['chat', 'hire', 'position', 'query', 'picklist'];
      tabs.forEach(t => {
        document.getElementById(`tab-${t}`).classList.add('hidden');
        const btn = document.getElementById(`tab-btn-${t}`);
        btn.classList.remove('bg-gradient-to-r', 'from-blue-600', 'to-indigo-600', 'text-white', 'shadow-md', 'shadow-blue-600/20');
        btn.classList.add('text-slate-400');
      });

      document.getElementById(`tab-${tab}`).classList.remove('hidden');
      const activeBtn = document.getElementById(`tab-btn-${tab}`);
      activeBtn.classList.add('bg-gradient-to-r', 'from-blue-600', 'to-indigo-600', 'text-white', 'shadow-md', 'shadow-blue-600/20');
      activeBtn.classList.remove('text-slate-400');
    }

    function showToast(msg, type = 'info') {
      const toast = document.getElementById('toast');
      toast.innerText = msg;
      toast.className = `fixed bottom-6 right-6 transform transition-all duration-300 translate-y-0 opacity-100 z-50 flex items-center space-x-3 px-4 py-3 rounded-xl text-sm font-medium shadow-2xl ${
        type === 'success' ? 'bg-emerald-600 text-white' :
        type === 'error' ? 'bg-rose-600 text-white' : 'bg-slate-800 text-white border border-slate-700'
      }`;
      setTimeout(() => {
        toast.className = 'fixed bottom-6 right-6 transform transition-all duration-300 translate-y-20 opacity-0 pointer-events-none z-50';
      }, 4000);
    }

    function clearChat() {
      const box = document.getElementById('chatMessages');
      box.innerHTML = '';
      chatHistory.length = 0;
      showToast('Chat history cleared', 'info');
    }

    function sendQuickPrompt(promptText) {
      switchTab('chat');
      document.getElementById('chatInput').value = promptText;
      submitChatMessage();
    }

    async function submitChatMessage() {
      const input = document.getElementById('chatInput');
      const text = input.value.trim();
      if (!text) return;

      input.value = '';
      const chatBox = document.getElementById('chatMessages');

      // User Message Bubble
      const userMsgHtml = `
        <div class="flex items-start justify-end space-x-3">
          <div class="max-w-[80%] rounded-2xl rounded-tr-sm bg-gradient-to-r from-blue-600 to-indigo-600 p-4 shadow-lg text-sm text-white font-medium">
            ${escapeHtml(text)}
          </div>
          <div class="h-8 w-8 rounded-lg bg-blue-500 flex-shrink-0 flex items-center justify-center text-white text-xs shadow-md">
            <i class="fa-solid fa-user"></i>
          </div>
        </div>
      `;
      chatBox.insertAdjacentHTML('beforeend', userMsgHtml);
      chatBox.scrollTop = chatBox.scrollHeight;

      // Loading Indicator
      const loadingId = 'loading-' + Date.now();
      const loadingHtml = `
        <div id="${loadingId}" class="flex items-start space-x-3">
          <div class="h-8 w-8 rounded-lg bg-gradient-to-tr from-blue-600 to-indigo-600 flex-shrink-0 flex items-center justify-center text-white text-xs shadow-md">
            <i class="fa-solid fa-wand-magic-sparkles"></i>
          </div>
          <div class="rounded-2xl rounded-tl-sm bg-slate-900 border border-slate-800 p-3.5 text-sm text-slate-400 flex items-center space-x-2">
            <i class="fa-solid fa-spinner fa-spin text-blue-400"></i>
            <span>SuccessFactors AI Copilot is processing your request...</span>
          </div>
        </div>
      `;
      chatBox.insertAdjacentHTML('beforeend', loadingHtml);
      chatBox.scrollTop = chatBox.scrollHeight;

      try {
        const res = await fetch('/api/chat', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({message: text, history: chatHistory})
        });
        const data = await res.json();

        // Remove loading
        const loadEl = document.getElementById(loadingId);
        if (loadEl) loadEl.remove();

        const renderedMarkdown = marked.parse(data.response || 'Done.');

        let actionsHtml = '';
        if (data.card && data.card.type === 'confirmation_proposal') {
          actionsHtml = `
            <div class="pt-2.5 border-t border-slate-800 flex items-center space-x-2.5">
              <button onclick="sendQuickPrompt('Yes')" class="px-4 py-2 rounded-xl bg-gradient-to-r from-emerald-600 to-teal-600 hover:from-emerald-500 hover:to-teal-500 text-white text-xs font-semibold shadow-lg shadow-emerald-600/20 flex items-center space-x-1.5 transition">
                <i class="fa-solid fa-bolt text-amber-300"></i>
                <span>Confirm & Execute Live</span>
              </button>
              <button onclick="document.getElementById('chatInput').value='Use name '; document.getElementById('chatInput').focus();" class="px-3 py-2 rounded-xl bg-slate-800 hover:bg-slate-700 text-slate-300 text-xs font-medium border border-slate-700 transition flex items-center space-x-1">
                <i class="fa-solid fa-pen text-[10px]"></i>
                <span>Change Name</span>
              </button>
            </div>
          `;
        }

        const botMsgHtml = `
          <div class="flex items-start space-x-3">
            <div class="h-8 w-8 rounded-lg bg-gradient-to-tr from-blue-600 to-indigo-600 flex-shrink-0 flex items-center justify-center text-white text-xs shadow-md">
              <i class="fa-solid fa-wand-magic-sparkles"></i>
            </div>
            <div class="max-w-[85%] rounded-2xl rounded-tl-sm bg-slate-900 border border-slate-800 p-4 shadow-lg text-sm text-slate-200 space-y-3">
              <div class="markdown-content">${renderedMarkdown}</div>
              ${actionsHtml}
            </div>
          </div>
        `;
        chatBox.insertAdjacentHTML('beforeend', botMsgHtml);
        chatBox.scrollTop = chatBox.scrollHeight;

        chatHistory.push({role: 'user', content: text});
        chatHistory.push({role: 'assistant', content: data.response});
      } catch (err) {
        const loadEl = document.getElementById(loadingId);
        if (loadEl) loadEl.remove();

        const errHtml = `
          <div class="flex items-start space-x-3">
            <div class="h-8 w-8 rounded-lg bg-rose-600 flex-shrink-0 flex items-center justify-center text-white text-xs">
              <i class="fa-solid fa-triangle-exclamation"></i>
            </div>
            <div class="rounded-2xl rounded-tl-sm bg-rose-950/40 border border-rose-500/30 p-3 text-sm text-rose-300">
              Error connecting to AI Copilot API: ${err.message}
            </div>
          </div>
        `;
        chatBox.insertAdjacentHTML('beforeend', errHtml);
        chatBox.scrollTop = chatBox.scrollHeight;
      }
    }

    function escapeHtml(text) {
      return text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
    }

    async function checkConnection() {
      try {
        const res = await fetch('/api/status');
        const data = await res.json();
        if (data.status === 'OK') {
          document.getElementById('connStatusText').innerText = 'Connected: ' + data.company;
          showToast('SuccessFactors connection active', 'success');
        }
      } catch (e) {
        showToast('Connection check failed', 'error');
      }
    }

    function fillSampleCandidate(first, last, pos) {
      document.getElementById('hireFirstName').value = first;
      document.getElementById('hireLastName').value = last;
      document.getElementById('hirePosition').value = pos;
      showToast(`Loaded candidate ${first} ${last}`, 'info');
    }

    async function previewPosition(code) {
      if (!code) return;
      try {
        const box = document.getElementById('positionDetailsBox');
        box.innerHTML = '<i class="fa-solid fa-spinner fa-spin text-xl text-blue-400"></i>';
        const res = await fetch(`/api/position/${encodeURIComponent(code)}`);
        const data = await res.json();
        box.innerHTML = `<pre class="text-left font-mono text-xs text-slate-300 w-full overflow-x-auto">${JSON.stringify(data, null, 2)}</pre>`;
      } catch (e) {
        showToast('Lookup failed', 'error');
      }
    }

    async function fetchPositionList() {
      try {
        const box = document.getElementById('positionDetailsBox');
        box.innerHTML = '<i class="fa-solid fa-spinner fa-spin text-xl text-blue-400"></i>';
        const res = await fetch('/api/positions?limit=10');
        const list = await res.json();
        box.innerHTML = `<pre class="text-left font-mono text-xs text-slate-300 w-full overflow-x-auto">${JSON.stringify(list, null, 2)}</pre>`;
      } catch (e) {
        showToast('List failed', 'error');
      }
    }

    async function clonePosition() {
      const source = document.getElementById('cloneSourceCode').value;
      const target = document.getElementById('cloneTargetCode').value;
      const suffix = document.getElementById('cloneTitleSuffix').value;
      const resBox = document.getElementById('cloneResult');
      resBox.classList.remove('hidden');
      resBox.innerText = 'Cloning position in SF...';

      try {
        const res = await fetch('/api/position/clone', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({source_code: source, target_code: target, title_suffix: suffix})
        });
        const data = await res.json();
        resBox.innerText = JSON.stringify(data, null, 2);
        showToast('Position cloned!', 'success');
      } catch (e) {
        resBox.innerText = 'Error cloning position.';
      }
    }

    async function executeHire(dryRun) {
      const inputs = {
        first_name: document.getElementById('hireFirstName').value,
        last_name: document.getElementById('hireLastName').value,
        start_date: document.getElementById('hireStartDate').value,
        position: document.getElementById('hirePosition').value,
        gender: document.getElementById('hireGender').value,
        salutation: document.getElementById('hireSalutation').value,
      };
      const endpoint = dryRun ? '/api/hire/dry-run' : '/api/hire/execute';
      const container = document.getElementById('hireResultContainer');
      container.classList.remove('hidden');
      document.getElementById('hireJsonOutput').innerText = 'Processing...';

      try {
        const res = await fetch(endpoint, {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify(inputs)
        });
        const data = await res.json();
        document.getElementById('hireJsonOutput').innerText = JSON.stringify(data, null, 2);
        showToast(dryRun ? 'Dry run completed' : 'Hire executed live!', 'success');
      } catch (e) {
        document.getElementById('hireJsonOutput').innerText = 'Error executing hire.';
      }
    }

    async function runODataQuery() {
      const entity = document.getElementById('queryEntity').value;
      const filter = document.getElementById('queryFilter').value;
      const top = document.getElementById('queryTop').value;
      const pre = document.getElementById('queryJsonOutput');
      pre.classList.remove('hidden');
      pre.innerText = 'Executing query...';

      try {
        const res = await fetch('/api/query', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({entity, filter, top: parseInt(top)})
        });
        const data = await res.json();
        pre.innerText = JSON.stringify(data, null, 2);
      } catch (e) {
        pre.innerText = 'Query error.';
      }
    }

    async function searchPicklist() {
      const id = document.getElementById('picklistSelect').value;
      const query = document.getElementById('picklistQuery').value;
      const box = document.getElementById('picklistResultBox');
      box.innerText = 'Searching...';
      try {
        const res = await fetch(`/api/picklists/search?id=${encodeURIComponent(id)}&query=${encodeURIComponent(query)}`);
        const data = await res.json();
        box.innerText = JSON.stringify(data, null, 2);
      } catch (e) {
        box.innerText = 'Error searching.';
      }
    }
  </script>
</body>
</html>
"""


# ==============================================================================
# ROUTE HANDLERS
# ==============================================================================

async def index(request: Request):
    return HTMLResponse(HTML_CONTENT)


async def api_chat(request: Request):
    """Conversational AI Copilot endpoint."""
    try:
        body = await request.json()
        user_message = body.get("message", "")
        history = body.get("history", [])

        result = copilot.process_message(user_message, history)
        return JSONResponse(result)
    except Exception as e:
        traceback.print_exc()
        return JSONResponse({
            "response": f"❌ **Error executing AI Copilot request:** {str(e)}",
            "card": {"type": "error", "message": str(e)}
        }, status_code=500)


async def api_status(request: Request):
    try:
        res = client.get("FOCompany", params={"$top": "1", "$select": "externalCode,name"})
        results = res.get("d", {}).get("results", [])
        company_name = results[0].get("name", "7000") if results else "SF EC"
        return JSONResponse({"status": "OK", "company": company_name})
    except Exception as e:
        return JSONResponse({"status": "ERROR", "message": str(e)}, status_code=500)


async def api_get_position(request: Request):
    code = request.path_params["code"]
    try:
        pos = pos_service.get_position(code)
        if not pos:
            return JSONResponse({"status": "NOT_FOUND", "message": f"Position {code} not found"}, status_code=404)
        clean = {k: v for k, v in pos.items() if not isinstance(v, dict)}
        return JSONResponse(clean)
    except Exception as e:
        return JSONResponse({"status": "ERROR", "message": str(e)}, status_code=500)


async def api_list_positions(request: Request):
    limit = int(request.query_params.get("limit", 10))
    try:
        positions = pos_service.list_positions(top=limit)
        clean = [{k: v for k, v in p.items() if not isinstance(v, dict)} for p in positions]
        return JSONResponse(clean)
    except Exception as e:
        return JSONResponse({"status": "ERROR", "message": str(e)}, status_code=500)


async def api_clone_position(request: Request):
    try:
        data = await request.json()
        source = data.get("source_code")
        target = data.get("target_code")
        suffix = data.get("title_suffix", "(Copy)")
        overrides = data.get("overrides")

        if not source or not target:
            return JSONResponse({"status": "ERROR", "message": "source_code and target_code required"}, status_code=400)

        res = pos_service.clone_position(source_code=source, target_code=target, title_suffix=suffix, overrides=overrides)
        return JSONResponse(res)
    except Exception as e:
        return JSONResponse({"status": "ERROR", "message": str(e)}, status_code=500)


async def api_hire_dry_run(request: Request):
    try:
        data = await request.json()
        res = hire_service.hire_employee(data, dry_run=True)
        return JSONResponse(res)
    except Exception as e:
        return JSONResponse({"status": "ERROR", "message": str(e)}, status_code=500)


async def api_hire_execute(request: Request):
    try:
        data = await request.json()
        res = hire_service.hire_employee(data, dry_run=False)
        return JSONResponse(res)
    except Exception as e:
        return JSONResponse({"status": "ERROR", "message": str(e)}, status_code=500)


async def api_query(request: Request):
    try:
        data = await request.json()
        entity = data.get("entity")
        flt = data.get("filter")
        top = data.get("top", 10)

        params = {"$top": str(top)}
        if flt:
            params["$filter"] = flt

        res = client.get(entity, params=params)
        records = res.get("d", {}).get("results", [])
        if not records and "d" in res and not isinstance(res["d"], list):
            records = [res["d"]]
        clean = [{k: v for k, v in r.items() if not isinstance(v, dict)} for r in records]
        return JSONResponse(clean)
    except Exception as e:
        return JSONResponse({"status": "ERROR", "message": str(e)}, status_code=500)


async def api_picklist_search(request: Request):
    try:
        pick_id = request.query_params.get("id", "salutation")
        q = request.query_params.get("query", "")
        opts = picklists.search(pick_id, q)
        return JSONResponse(opts)
    except Exception as e:
        return JSONResponse({"status": "ERROR", "message": str(e)}, status_code=500)


routes = [
    Route("/", endpoint=index, methods=["GET"]),
    Route("/api/chat", endpoint=api_chat, methods=["POST"]),
    Route("/api/status", endpoint=api_status, methods=["GET"]),
    Route("/api/position/{code}", endpoint=api_get_position, methods=["GET"]),
    Route("/api/positions", endpoint=api_list_positions, methods=["GET"]),
    Route("/api/position/clone", endpoint=api_clone_position, methods=["POST"]),
    Route("/api/hire/dry-run", endpoint=api_hire_dry_run, methods=["POST"]),
    Route("/api/hire/execute", endpoint=api_hire_execute, methods=["POST"]),
    Route("/api/query", endpoint=api_query, methods=["POST"]),
    Route("/api/picklists/search", endpoint=api_picklist_search, methods=["GET"]),
]

middleware = [
    Middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
]

app = Starlette(debug=True, routes=routes, middleware=middleware)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    print(f"Starting SFDataBuilder Studio Web App with AI Copilot at http://localhost:{port}")
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")
