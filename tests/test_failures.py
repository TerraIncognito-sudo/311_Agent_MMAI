"""Failure handling F1-F4 / E9: every failure ends in a clean handoff, never a claimed ticket."""
from conftest import tool_call
from src.agent.orchestrator import SetupError, handle_turn
from src.models.llm import LLMError

BIN_FIELDS = {"address": "12 Oak Street", "bin_type": "recycling", "damage_type": "cracked lid"}


def test_f1_model_timeout_retried_once_then_e9(llm, sel, fresh_store):
    fake = llm(LLMError("timed out"), LLMError("timed out"))
    out = handle_turn(None, "bin broken", sel)
    assert len(fake.calls) == 2 and out["rule_id"] == "E9"
    assert fresh_store.handoffs and not fresh_store.tickets


def test_f1_model_recovers_on_retry(llm, sel):
    llm(LLMError("timed out"), "Which bin type is it?")
    assert handle_turn(None, "bin broken", sel)["reply"] == "Which bin type is it?"


def test_f3_create_ticket_failure_hands_off(llm, sel, fresh_store):
    fresh_store.fail.add("create_ticket")
    llm(
        [tool_call("policy_lookup", {"code": "SW-BIN"})],
        [tool_call("create_ticket", {"code": "SW-BIN", "fields": BIN_FIELDS})],
    )
    out = handle_turn(None, "bin broken", sel)
    assert out["rule_id"] == "E9" and "311-" not in out["reply"]
    assert fresh_store.handoffs[0]["fields"]["bin_type"] == "recycling"   # collected fields go with it


def test_e6_missing_field_after_two_attempts(llm, sel, fresh_store):
    partial = {"address": "12 Oak Street", "bin_type": "recycling"}
    llm(
        [tool_call("policy_lookup", {"code": "SW-BIN"})],
        [tool_call("create_ticket", {"code": "SW-BIN", "fields": partial}, "a")],
        [tool_call("create_ticket", {"code": "SW-BIN", "fields": partial}, "b")],
        [tool_call("create_ticket", {"code": "SW-BIN", "fields": partial}, "c")],
    )
    out = handle_turn(None, "bin broken", sel)
    assert out["rule_id"] == "E6" and fresh_store.handoffs


def test_setup_error_is_not_a_handoff(llm, sel, fresh_store):
    llm(LLMError("401 invalid key", retryable=False))
    try:
        handle_turn(None, "bin broken", sel)
        raise AssertionError("expected SetupError")
    except SetupError:
        pass
    assert not fresh_store.handoffs
