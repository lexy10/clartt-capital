"""Unit tests for StrategyAlgorithm base class and StrategyRegistry."""

import pytest

from src.models import Candle, Signal, StrategyConfig
from src.strategy.base import StrategyAlgorithm
from src.strategy.registry import StrategyRegistry


# ---------------------------------------------------------------------------
# Concrete test algorithm for exercising the abstract base class
# ---------------------------------------------------------------------------

class DummyAlgorithm(StrategyAlgorithm):
    """Minimal concrete implementation for testing."""

    @staticmethod
    def name() -> str:
        return "dummy"

    @staticmethod
    def description() -> str:
        return "A dummy algorithm for testing"

    @staticmethod
    def default_params() -> dict:
        return {"param_a": 1}

    @staticmethod
    def param_schema() -> dict:
        return {
            "type": "object",
            "properties": {"param_a": {"type": "integer"}},
        }

    def analyze(self, entry_candles, higher_tf_candles, config):
        return []


class AnotherAlgorithm(StrategyAlgorithm):
    """Second concrete implementation for multi-registration tests."""

    @staticmethod
    def name() -> str:
        return "another"

    @staticmethod
    def description() -> str:
        return "Another algorithm"

    @staticmethod
    def default_params() -> dict:
        return {}

    @staticmethod
    def param_schema() -> dict:
        return {"type": "object", "properties": {}}

    def analyze(self, entry_candles, higher_tf_candles, config):
        return []


# ---------------------------------------------------------------------------
# StrategyAlgorithm tests
# ---------------------------------------------------------------------------

class TestStrategyAlgorithm:
    def test_cannot_instantiate_abstract_class(self):
        with pytest.raises(TypeError):
            StrategyAlgorithm()

    def test_concrete_subclass_instantiates(self):
        alg = DummyAlgorithm()
        assert alg.name() == "dummy"
        assert alg.description() == "A dummy algorithm for testing"
        assert alg.default_params() == {"param_a": 1}
        assert "properties" in alg.param_schema()

    def test_analyze_returns_list(self):
        alg = DummyAlgorithm()
        result = alg.analyze([], [], None)
        assert result == []


# ---------------------------------------------------------------------------
# StrategyRegistry tests
# ---------------------------------------------------------------------------

class TestStrategyRegistry:
    def test_register_and_get(self):
        registry = StrategyRegistry()
        alg = DummyAlgorithm()
        registry.register(alg)
        assert registry.get("dummy") is alg

    def test_register_duplicate_raises_value_error(self):
        registry = StrategyRegistry()
        registry.register(DummyAlgorithm())
        with pytest.raises(ValueError, match="already registered"):
            registry.register(DummyAlgorithm())

    def test_get_unknown_raises_key_error(self):
        registry = StrategyRegistry()
        registry.register(DummyAlgorithm())
        with pytest.raises(KeyError, match="Unknown algorithm 'nonexistent'"):
            registry.get("nonexistent")

    def test_key_error_lists_available(self):
        registry = StrategyRegistry()
        registry.register(DummyAlgorithm())
        registry.register(AnotherAlgorithm())
        with pytest.raises(KeyError) as exc_info:
            registry.get("missing")
        msg = str(exc_info.value)
        assert "dummy" in msg
        assert "another" in msg

    def test_has_registered(self):
        registry = StrategyRegistry()
        registry.register(DummyAlgorithm())
        assert registry.has("dummy") is True
        assert registry.has("nonexistent") is False

    def test_list_algorithms_empty(self):
        registry = StrategyRegistry()
        assert registry.list_algorithms() == []

    def test_list_algorithms_returns_metadata(self):
        registry = StrategyRegistry()
        registry.register(DummyAlgorithm())
        registry.register(AnotherAlgorithm())

        result = registry.list_algorithms()
        assert len(result) == 2

        names = {entry["name"] for entry in result}
        assert names == {"dummy", "another"}

        for entry in result:
            assert "name" in entry
            assert "description" in entry
            assert "default_params" in entry
            assert "param_schema" in entry

    def test_list_algorithms_metadata_values(self):
        registry = StrategyRegistry()
        registry.register(DummyAlgorithm())

        result = registry.list_algorithms()
        assert len(result) == 1
        entry = result[0]
        assert entry["name"] == "dummy"
        assert entry["description"] == "A dummy algorithm for testing"
        assert entry["default_params"] == {"param_a": 1}
        assert entry["param_schema"]["type"] == "object"

    def test_duplicate_does_not_change_registry(self):
        registry = StrategyRegistry()
        registry.register(DummyAlgorithm())
        initial_count = len(registry.list_algorithms())
        with pytest.raises(ValueError):
            registry.register(DummyAlgorithm())
        assert len(registry.list_algorithms()) == initial_count

    def test_get_returns_exact_instance(self):
        registry = StrategyRegistry()
        alg1 = DummyAlgorithm()
        alg2 = AnotherAlgorithm()
        registry.register(alg1)
        registry.register(alg2)
        assert registry.get("dummy") is alg1
        assert registry.get("another") is alg2
