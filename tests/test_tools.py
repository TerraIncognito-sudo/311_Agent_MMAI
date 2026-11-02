"""W4 tool contracts (docs/interfaces.md): every tool returns a dict with ok and error."""
import time

from src.models.classifier import classify
from src.tools.handoff import handoff
from src.tools.knowledge import kb_search
from src.tools.policy import policy_lookup
from src.tools.tickets import create_ticket, get_status, search_tickets

BIN = {"address": "12 Oak Street, G1A 1A1", "bin_type": "recycling", "damage_type": "cracked lid"}


def test_policy_lookup_known_and_unknown():
    r = policy_lookup("SW-BIN")
    assert r["ok"] and r["unit"] == "Solid Waste" and r["required_fields"] == ["address", "bin_type", "damage_type"]
    assert not policy_lookup("XX-NOPE")["ok"]          # A1


def test_a4_create_ticket_requires_all_fields():
    r = create_ticket("SW-BIN", {"address": "12 Oak Street"})
    assert not r["ok"] and set(r["missing"]) == {"bin_type", "damage_type"}


def test_create_then_status_needs_postal_code():
    r = create_ticket("SW-BIN", BIN)
    assert r["ok"] and r["ticket_id"].startswith("311-")
    assert not get_status(r["ticket_id"], "")["ok"]           # D5
    assert not get_status(r["ticket_id"], "Z9Z 9Z9")["ok"]    # D5: wrong postal code
    s = get_status(r["ticket_id"], "g1a1a1")
    assert s["ok"] and s["status"] == "open"


def test_d3_search_returns_no_personal_data():
    create_ticket("SW-BIN", {**BIN, "name": "Jane Doe", "phone": "416-555-0199"})
    r = search_tickets("12 Oak Street", "SW-BIN")
    assert r["ok"] and len(r["tickets"]) == 1
    assert set(r["tickets"][0]) == {"ticket_id", "code", "status", "date_opened"}


def test_d2_redacted_before_storage(fresh_store):
    create_ticket("SW-BIN", {**BIN, "damage_type": "cracked, my SIN is 123 456 789"})
    assert "123 456 789" not in str(fresh_store.tickets)


def test_handoff_masks_transcript(fresh_store):
    r = handoff("E7", {"transcript": [{"role": "resident", "text": "call me at 416-555-0199"}]})
    assert r["ok"] and r["ref_id"].startswith("HO-")
    assert "416-555-0199" not in str(fresh_store.handoffs)


def test_classifier_interface():
    r = classify("my recycling bin lid is cracked")
    assert set(r) == {"code", "confidence", "top2"} and r["code"] == "SW-BIN"


KB_DOC = {"source": "waste_collection", "title": "Waste collection",
          "content": "# Waste collection\n\nCarts must be at the curb by 7:00 a.m. on collection day."}


def test_kb_search_returns_synced_passages(fresh_store):
    from src.rag.ingest import chunk_stale
    fresh_store.upsert_document(KB_DOC)                      # a row added in the dashboard
    assert chunk_stale(fresh_store) == 1
    r = kb_search("what time do carts need to be at the curb")
    assert r["ok"] and any("7:00" in p["text"] for p in r["passages"])


def test_kb_dashboard_edit_goes_live_without_redeploy(fresh_store):
    from src.rag.ingest import start_sync
    fresh_store.upsert_document(KB_DOC)
    stop = start_sync(0.05, store=fresh_store)
    try:
        fresh_store.upsert_document({**KB_DOC, "content": "Carts must be at the curb by 6:30 a.m."})
        deadline = time.time() + 5
        while time.time() < deadline and not any("6:30" in c["content"] for c in fresh_store.chunks):
            time.sleep(0.05)
    finally:
        stop.set()
    texts = [p["text"] for p in kb_search("curb carts time")["passages"]]
    assert any("6:30" in t for t in texts) and not any("7:00" in t for t in texts)


def test_kb_bulk_import(fresh_store, tmp_path):
    from src.rag.ingest import chunk_stale, import_files
    (tmp_path / "parks.md").write_text("# Parks\n\nPark washrooms open May to October.", encoding="utf-8")
    (tmp_path / "README.md").write_text("not a KB article", encoding="utf-8")
    assert import_files(fresh_store, tmp_path) == 1
    chunk_stale(fresh_store)
    assert kb_search("park washrooms")["passages"][0]["title"] == "Parks"
