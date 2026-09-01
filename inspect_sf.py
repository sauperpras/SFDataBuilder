from sf_client import SFClient
from profile_builder import _format_date

c = SFClient()

base_payload = {
    "__metadata": {"uri": "PerPersonal"},
    "personIdExternal": "900163",
    "startDate": _format_date("2026-08-01"),
    "firstName": "Alex",
    "lastName": "Taylor",
    "gender": "M",
    "salutation": "10808",
    "maritalStatus": "Single",
    "nationality": "USA",
    "secondNationality": "CAN",
    "nativePreferredLang": "10223",
    "suffix": "5457",
}

# Query all PicklistOption
print("Querying PicklistOption...")
res = c.get("PicklistOption", params={"$top": "1000", "$select": "id,externalCode,status,picklist/picklistId", "$expand": "picklist"})
options = res.get("d", {}).get("results", [])
print(f"Total options fetched: {len(options)}")

for o in options:
    opt_id = str(o.get("id"))
    code = str(o.get("externalCode"))
    pl_id = o.get("picklist", {}).get("picklistId") if isinstance(o.get("picklist"), dict) else ""
    
    p = dict(base_payload)
    p["customString1"] = opt_id
    try:
        res_up = c.deep_upsert(p)
        print(f"\n=======================================================")
        print(f"!!! SUCCESS !!! Picklist: {pl_id}, Option ID: {opt_id}, Code: {code}")
        print(f"=======================================================\n")
        break
    except Exception as e:
        err = str(e)
        if "The given value" in err or "is an invalid picklist value" in err:
            continue
        else:
            print(f"Other error on {opt_id} ({code}): {err}")
