# tests/test_model_resolution.py
from webapp.model_resolution import resolve_model, score_candidate, catalog_fingerprint


def test_exact_match_not_remapped():
    catalog = ["meta-llama/llama-3.3-70b-instruct:free", "qwen/qwen3-coder:free"]
    result = resolve_model("meta-llama/llama-3.3-70b-instruct:free", catalog)
    assert result.resolved == "meta-llama/llama-3.3-70b-instruct:free"
    assert result.remapped is False


def test_missing_prefers_same_family_and_free_tier():
    catalog = [
        "google/gemma-4-26b-a4b-it:free",
        "meta-llama/llama-3.3-70b-instruct:free",
        "qwen/qwen3-coder:free",
    ]
    result = resolve_model("meta-llama/llama-4-missing:free", catalog, provider="openrouter")
    assert result.resolved == "meta-llama/llama-3.3-70b-instruct:free"
    assert result.remapped is True
    assert "family" in result.reason or "closest" in result.reason


def test_exclude_skips_failed_id():
    catalog = ["a/model-a:free", "a/model-b:free"]
    result = resolve_model("a/gone:free", catalog, exclude={"a/model-a:free"})
    assert result.resolved == "a/model-b:free"


def test_empty_catalog_fails_soft():
    result = resolve_model("x", [])
    assert result.resolved is None
    assert result.remapped is False
    assert "empty" in result.reason.lower() or "no" in result.reason.lower()


def test_fingerprint_stable():
    assert catalog_fingerprint(["b", "a"]) == catalog_fingerprint(["a", "b"])
