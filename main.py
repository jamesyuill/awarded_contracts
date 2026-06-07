import os
import requests
from datetime import date, timedelta
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

# ----------------------------
# SUPABASE CLIENT
# ----------------------------
supabase = create_client(
    os.getenv("SUPABASE_URL"),
    os.getenv("SUPABASE_KEY")
)

# ----------------------------
# INGESTION LOG START
# ----------------------------
run = supabase.table("ingestion_runs").insert({
    "status": "running"
}).execute()

run_id = run.data[0]["id"]

# ----------------------------
# DATE RANGE
# ----------------------------
today = date.today()
published_from = today - timedelta(days=3)

# ----------------------------
# API REQUEST
# ----------------------------
url = "https://www.contractsfinder.service.gov.uk/Published/Notices/OCDS/Search"

params = {
    "publishedFrom": published_from.isoformat(),
    "publishedTo": today.isoformat(),
    "stages": "award",
    "limit": 100
}

headers = {
    "Accept": "application/json"
}

try:

    response = requests.get(url, params=params, headers=headers)
    response.raise_for_status()
    data = response.json()

    # ----------------------------
    # STORAGE CONTAINERS
    # ----------------------------
    award_map = {}
    supplier_map = {}
    award_supplier_rows = []

    # ----------------------------
    # PARSE RESPONSE
    # ----------------------------
    for release in data.get("releases", []):

        buyer = release.get("buyer", {}).get("name")
        tender = release.get("tender", {})
        title = tender.get("title")
        ocid = release.get("ocid")

        published_date = release.get("date")

        for award in release.get("awards", []):

            suppliers = [
                s.get("name")
                for s in award.get("suppliers", [])
                if s.get("name")
            ]

            award_value = award.get("value", {}).get("amount") or 0
            currency = award.get("value", {}).get("currency")

            # ----------------------------
            # AWARDS TABLE
            # ----------------------------
        if ocid not in award_map:
            award_map[ocid] = {
                "ocid": ocid,
                "source": "ContractsFinder",
                "title": title,
                "buyer": buyer,
                "award_date": award.get("date"),
                "published_date": published_date,
                "award_value": award_value,
                "currency": currency,
                "notice_url": f"https://www.contractsfinder.service.gov.uk/Notice/{ocid}"
            }

            # ----------------------------
            # SUPPLIERS + JOIN TABLE
            # ----------------------------
            for name in suppliers:

                if not name:
                    continue

                # suppliers table (deduped)
                if name not in supplier_map:
                    supplier_map[name] = {
                        "supplier_name": name,
                        
                    }

                # JOIN TABLE (award ↔ supplier link)
                award_supplier_rows.append({
                    "ocid": ocid,
                    "supplier_name": name,
                   
                })

    supplier_rows = list(supplier_map.values())
    award_rows = list(award_map.values())

    # ----------------------------
    # UPSERT: AWARDS
    # ----------------------------
    if award_rows:
        supabase.table("awards").upsert(
            award_rows,
            on_conflict="ocid"
        ).execute()

    # ----------------------------
    # UPSERT: SUPPLIERS
    # ----------------------------
    if supplier_rows:
        supabase.table("suppliers").upsert(
            supplier_rows,
            on_conflict="supplier_name"
        ).execute()

    # ----------------------------
    # INSERT: JOIN TABLE
    # ----------------------------
    if award_supplier_rows:
        supabase.table("award_suppliers").insert(
            award_supplier_rows
        ).execute()

    # ----------------------------
    # SUCCESS LOG
    # ----------------------------
    supabase.table("ingestion_runs").update({
        "completed_at": date.today().isoformat(),
        "records_found": len(award_rows),
        "records_inserted": len(award_rows),
        "status": "success"
    }).eq("id", run_id).execute()

    print(f"Inserted {len(award_rows)} awards, {len(supplier_rows)} suppliers, {len(award_supplier_rows)} links")

except Exception as e:

    # ----------------------------
    # FAILURE LOG
    # ----------------------------
    supabase.table("ingestion_runs").update({
        "completed_at": date.today().isoformat(),
        "status": "failed",
        "error_message": str(e)
    }).eq("id", run_id).execute()

    raise