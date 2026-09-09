"""Offline regression for optional versus partial MCP insight deployments."""
import copy
import importlib.util
from pathlib import Path
import pytest

spec = importlib.util.spec_from_file_location("insight_stdio_contract", Path(__file__).with_name("real_sdk_stdio_smoke.py"))
smoke = importlib.util.module_from_spec(spec)
spec.loader.exec_module(smoke)


def advertised():
    tools = [{"name": "kumiho_memory_engage", "inputSchema": {"properties": {
        "include_insights": {"type": "boolean"}, "include_learned_sources": {"type": "boolean"},
        "current_context": {"type": "string"}, "goals": {"type": "array"}}}}]
    for name in ("record_experience", "record_outcome", "prepare_patterns", "store_pattern", "check_pattern", "validate_insight_response"):
        tools.append({"name": "kumiho_memory_" + name, "annotations": {
            "readOnlyHint": name in ("prepare_patterns", "check_pattern", "validate_insight_response")}})
    return tools


def test_old_server_is_supported_but_cannot_satisfy_release_gate():
    assert smoke.validate_insight_contract([]) is False
    with pytest.raises(RuntimeError, match="does not advertise"):
        smoke.validate_insight_contract([], required=True)


def test_complete_contract():
    assert smoke.validate_insight_contract(advertised(), required=True)


@pytest.mark.parametrize("index", range(1, 7))
def test_partial_rollout_is_not_reported_as_complete(index):
    tools = advertised()
    tools.pop(index)
    with pytest.raises(RuntimeError, match="omitted"):
        smoke.validate_insight_contract(tools)


def test_write_tool_cannot_masquerade_as_read_only():
    tools = copy.deepcopy(advertised())
    tools[1]["annotations"]["readOnlyHint"] = True
    with pytest.raises(RuntimeError, match="readOnlyHint"):
        smoke.validate_insight_contract(tools)
