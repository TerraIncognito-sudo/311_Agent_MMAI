"""W4 orchestrator + interaction tree, with a scripted model (no LLM needed)."""
from conftest import tool_call
from src.agent.orchestrator import handle_turn

BIN_FIELDS = {"location": "12 Oak Street, G1A 1A1", "bin_type": "recycling", "damage_type": "cracked lid"}


def test_routine_bin_case_end_to_end(llm, sel, fresh_store):
    fake = llm(
        [tool_call("classifier", {"text": "cracked recycling bin lid"})],
        [tool_call("policy_lookup", {"code": "SW-BIN"})],
        [tool_call("search_tickets", {"location": "12 Oak Street", "code": "SW-BIN"})],
        [tool_call("create_ticket", {"code": "SW-BIN", "fields": BIN_FIELDS})],
        "I've created request 311-2026-000001 for Damaged or missing bin at 12 Oak Street.",
    )
    out = handle_turn(None, "My recycling bin lid at 12 Oak Street G1A 1A1 is cracked", sel)
    ticket_id = fresh_store.tickets[0]["ticket_id"]
    assert ticket_id in out["reply"] or out["rule_id"] == "M3"
    assert len(fresh_store.tickets) == 1
    steps = [e["step"] for e in fresh_store.events]
    assert "model" in steps and "tool:create_ticket" in steps
    assert len(fake.calls) == 5


def test_e1_emergency_skips_model(llm, sel, fresh_store):
    fake = llm()
    out = handle_turn(None, "There's a strong smell of gas in my building", sel)
    assert "911" in out["reply"] and out["rule_id"] == "E1"
    assert fake.calls == [] and fresh_store.tickets == []


def test_a2_create_blocked_before_policy_lookup(llm, sel, fresh_store):
    llm([tool_call("create_ticket", {"code": "SW-BIN", "fields": BIN_FIELDS})], "ok")
    handle_turn(None, "bin broken", sel)
    assert fresh_store.tickets == []
    assert any(e["outcome"] == "blocked" and e["rule_id"] == "A2" for e in fresh_store.events)


def test_a8_only_one_ticket(llm, sel, fresh_store):
    llm(
        [tool_call("policy_lookup", {"code": "SW-BIN"})],
        [tool_call("create_ticket", {"code": "SW-BIN", "fields": BIN_FIELDS})],
        [tool_call("create_ticket", {"code": "SW-BIN", "fields": BIN_FIELDS}, "c2")],
        "done",
    )
    handle_turn(None, "bin broken", sel)
    assert len(fresh_store.tickets) == 1


def test_m3_invented_ticket_id_becomes_handoff(llm, sel, fresh_store):
    llm("Your request 311-2026-123456 has been created.")
    out = handle_turn(None, "bin broken", sel)
    assert out["rule_id"] == "M3" and "311-2026-123456" not in out["reply"]
    assert fresh_store.handoffs


def test_model_handoff_tool_returns_reference(llm, sel, fresh_store):
    llm([tool_call("handoff", {"rule_id": "E2", "reason": "resident injured by bin"})])
    out = handle_turn(None, "The bin fell on me and hurt my foot", sel)
    assert out["rule_id"] == "E2" and fresh_store.handoffs[0]["ref_id"] in out["reply"]


def test_conversation_continues_with_same_interaction(llm, sel):
    llm("Which bin type is it?", "Thanks.")
    first = handle_turn(None, "my bin is broken", sel)
    second = handle_turn(first["interaction_id"], "recycling", sel)
    assert first["interaction_id"] == second["interaction_id"]


def test_m3_resident_can_have_their_own_ticket_number_repeated(llm, sel, fresh_store):
    # Repeating the number the resident typed is not inventing one (status check, A5).
    llm("Could you give me the postal code on ticket 311-2026-000001?")
    out = handle_turn(None, "what is the status of ticket 311-2026-000001", sel)
    assert out["rule_id"] is None and "311-2026-000001" in out["reply"] and not fresh_store.handoffs


def test_m3_still_blocks_ids_nobody_gave(llm, sel, fresh_store):
    llm("Ticket 311-2026-000777 is open.")
    out = handle_turn(None, "what is the status of ticket 311-2026-000001", sel)
    assert out["rule_id"] == "M3" and fresh_store.handoffs


# --- Policy v1.0: C1 to C4 and C7 replace the reply (not only logged) ---------
import pytest  # noqa: E402


@pytest.mark.parametrize("model_reply, rule, wording_start", [
    ("Thanks! A crew will come by tomorrow to replace it.", "C7", "I can't say whether someone will visit."),
    ("It will be replaced within 3 days.", "C1", "I can't give a date."),
    ("I've marked this as urgent for you.", "C2", "I can't give a date."),
    ("Your neighbour is violating the by-law.", "C3", "I can't give a date."),
    ("The city will pay for a new bin.", "C4", "I can't give a date."),
])
def test_commitment_reply_is_replaced_with_approved_wording(llm, sel, fresh_store, model_reply, rule, wording_start):
    llm([tool_call("policy_lookup", {"code": "SW-BIN"})], model_reply)
    out = handle_turn(None, "My recycling bin lid at 12 Oak Street is cracked", sel)
    assert out["reply"].startswith(wording_start) and out["rule_id"] == rule
    assert "Solid Waste" in out["reply"] and "  " not in out["reply"]      # {unit} filled
    assert model_reply not in out["reply"]


def test_commitment_without_a_code_still_reads_well(llm, sel, fresh_store):
    llm("We'll refund your fee.")
    out = handle_turn(None, "hello", sel)
    assert out["reply"] == "I can't give a date. The City team schedules the work after reviewing it."


def test_reply_without_commitment_is_unchanged(llm, sel, fresh_store):
    llm([tool_call("policy_lookup", {"code": "SW-BIN"})], "Which bin is it: garbage, recycling or organics?")
    out = handle_turn(None, "My bin at 12 Oak Street is broken", sel)
    assert out["reply"] == "Which bin is it: garbage, recycling or organics?"
