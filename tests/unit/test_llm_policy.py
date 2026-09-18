from invoiceops.llm.policy import CircuitBreaker, LLMPolicy
from invoiceops.llm.provider import FakeLLMProvider
from invoiceops.adapters.in_memory import InMemoryRepository
from invoiceops.application.service import TriageService


LABELS = ("OTHER_REVIEW", "PAYMENT_STATUS")


class SlowProvider(FakeLLMProvider):
    def suggest(self, **kwargs):
        import time
        time.sleep(0.01)
        return super().suggest(**kwargs)


def test_fake_provider_rejects_unknown_labels() -> None:
    policy = LLMPolicy(enabled=True, budget_usd=1.0)
    assert policy.request(FakeLLMProvider({"labels": ["UNKNOWN"], "confidence": 0.5}), sanitized=True, text="redacted", allowed_labels=LABELS, prompt_version="prompt-v1") is None


def test_policy_skips_unsanitized_and_opens_after_failures() -> None:
    policy = LLMPolicy(enabled=True, budget_usd=1.0, breaker=CircuitBreaker(failure_limit=2))
    provider = FakeLLMProvider({"labels": ["UNKNOWN"], "confidence": 0.5})
    assert policy.request(provider, sanitized=False, text="raw", allowed_labels=LABELS, prompt_version="prompt-v1") is None
    assert policy.request(provider, sanitized=True, text="redacted", allowed_labels=LABELS, prompt_version="prompt-v1") is None
    assert policy.request(provider, sanitized=True, text="redacted", allowed_labels=LABELS, prompt_version="prompt-v1") is None
    assert policy.breaker.opened is True


def test_enabled_fake_provider_returns_versioned_suggestion() -> None:
    policy = LLMPolicy(enabled=True, budget_usd=1.0)
    suggestion = policy.request(FakeLLMProvider(), sanitized=True, text="redacted", allowed_labels=LABELS, prompt_version="prompt-v1")
    assert suggestion is not None
    assert suggestion.provider == "fake"
    assert suggestion.prompt_version == "prompt-v1"


def test_timeout_is_retried_once_then_degrades() -> None:
    policy = LLMPolicy(enabled=True, budget_usd=1.0, timeout_seconds=0.001, breaker=CircuitBreaker(failure_limit=3))
    assert policy.request(SlowProvider(), sanitized=True, text="redacted", allowed_labels=LABELS, prompt_version="prompt-v1") is None
    assert policy.breaker.failures == 2


def test_service_persists_llm_suggestion_without_changing_final_decision() -> None:
    repository = InMemoryRepository()
    service = TriageService(repository=repository, llm_policy=LLMPolicy(enabled=True, budget_usd=1.0), llm_provider=FakeLLMProvider())
    response = service.classify(request_id="llm-1", source="email", text="unclear invoice", taxonomy_version="invoiceops-v1", metadata={}, idempotency_key="llm-key", trace_id="trace")
    assert response["decision"] == "needs_review"
    assert repository.llm_suggestions[0]["prompt_version"] == "invoiceops-prompt-v1"
