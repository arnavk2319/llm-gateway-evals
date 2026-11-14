from app.profile import build_profile
from app.schemas import ChatRequest


def req(content="hello", **kw):
    return ChatRequest(messages=[{"role": "user", "content": content}], **kw)


def test_explicit_hint_wins_with_full_confidence():
    p = build_profile(req("anything"), task_hint="code")
    assert (p.task_type, p.confidence) == ("code", 1.0)


def test_unknown_hint_is_ignored():
    p = build_profile(req("hi"), task_hint="nonsense")
    assert p.task_type == "chat"


def test_tools_mean_agent_tool():
    p = build_profile(req("hi", tools=[{"type": "function", "function": {"name": "f"}}]))
    assert p.task_type == "agent_tool" and p.needs_tools


def test_json_response_format_means_extraction():
    p = build_profile(req("pull the fields", response_format={"type": "json_object"}))
    assert p.task_type == "extraction" and p.needs_json


def test_code_markers():
    p = build_profile(req("please fix this bug in my function"))
    assert p.task_type == "code"
    assert p.confidence < 0.7  # deliberately below the default LLM-classify threshold


def test_short_max_tokens_means_classification():
    assert build_profile(req("is this spam?", max_tokens=8)).task_type == "classification"


def test_reviewer_role_defaults_to_review():
    p = build_profile(req("check this diff"), node_role="reviewer")
    assert p.task_type == "review" and p.risk == "medium"


def test_plain_chat_is_low_confidence():
    p = build_profile(req("what's up"))
    assert p.task_type == "chat" and p.confidence == 0.5


def test_quality_hint_overrides_complexity():
    assert build_profile(req("hi"), quality_hint="high").complexity == "high"
    assert build_profile(req("hi"), quality_hint="low").complexity == "low"
