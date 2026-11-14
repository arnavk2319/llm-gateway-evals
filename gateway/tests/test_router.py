from app.config import DATA_DIR
from app.policy import load_policy_file
from app.profile import TaskProfile
from app.router import ParentRef, select_route

POLICY = load_policy_file(DATA_DIR / "seed_policy.yaml")
ENV: dict[str, str] = {}  # only the mock provider is available


def route(task="chat", **kw):
    prof_kw = {"context_tokens": 100, "expected_output_tokens": 100}
    prof_kw.update({k: kw.pop(k) for k in list(kw) if k in TaskProfile.__dataclass_fields__})
    prof = TaskProfile(task_type=task, **prof_kw)
    return select_route(POLICY, prof, env=ENV, **kw)


def test_chat_prefers_cheapest_adequate_model():
    assert route("chat").primary.id == "mock/small"


def test_code_requires_tier_two():
    assert route("code").primary.id == "mock/medium"


def test_high_complexity_bumps_the_tier():
    assert route("chat", complexity="high").primary.tier >= 2


def test_escalation_raises_tier():
    assert route("code", escalate_tiers=1).primary.id == "mock/large"


def test_context_too_large_for_small_model():
    d = route("chat", context_tokens=20_000)
    assert d.primary.id != "mock/small"


def test_tier_relaxed_when_escalation_exceeds_available():
    d = route("code", escalate_tiers=5)
    assert d.primary.id == "mock/large" and "tier" in d.relaxed


def test_pinned_model_bypasses_ranking():
    assert route("chat", pinned="mock/large").primary.id == "mock/large"
    assert route("chat", pinned="does/not-exist") is None


def test_excluded_models_are_skipped_and_exhaustion_returns_none():
    assert route("chat", excluded={"mock/small"}).primary.id == "mock/medium"
    assert route("chat", excluded={"mock/small", "mock/medium", "mock/large"}) is None


def test_reviewer_must_differ_from_parent_family():
    d = route("review", node_role="reviewer", parent=ParentRef("mock/medium", "mock-b"))
    assert d.primary.family != "mock-b"
    d = route("review", node_role="reviewer", parent=ParentRef("mock/large", "mock-c"))
    assert d.primary.id == "mock/medium"


def test_independence_relaxes_tier_before_giving_up():
    d = route(
        "review", node_role="reviewer", parent=ParentRef("mock/large", "mock-c"),
        excluded={"mock/medium"},
    )
    assert d.primary.id == "mock/small"
    assert "tier-for-independence" in d.relaxed


def test_non_reviewer_roles_ignore_lineage():
    d = route("code", node_role="implementer", parent=ParentRef("mock/medium", "mock-b"))
    assert d.primary.id == "mock/medium"


def test_reason_is_explainable():
    d = route("code")
    assert "task=code" in d.reason and d.primary.id in d.reason


def test_real_providers_only_appear_when_configured():
    env = {"GROQ_API_KEY": "x", "GATEWAY_DISABLE_MOCK": "1"}
    prof = TaskProfile(task_type="chat", context_tokens=100, expected_output_tokens=100)
    d = select_route(POLICY, prof, env=env)
    assert d.primary.provider == "groq"
