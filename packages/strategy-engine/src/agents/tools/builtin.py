"""Built-in tool definitions and execute functions for the agent tool system.

Wraps existing platform APIs (BacktestEngine, StrategyRegistry, AlgorithmManager)
and backend REST endpoints as typed tools with JSON schema validation.

Requirements: 4.5
"""

import logging
import os
from pathlib import Path
from typing import Any, Callable, Optional

import requests

from src.agents.tools.registry import ToolDefinition, ToolRegistry

logger = logging.getLogger(__name__)

# Allowed directory for algorithm file writes (relative to strategy-engine root)
ALGORITHMS_DIR = Path("src/strategy/algorithms")


# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

RUN_BACKTEST_TOOL = ToolDefinition(
    name="run_backtest",
    description="Run a backtest for a strategy using the BacktestEngine.",
    input_schema={
        "type": "object",
        "properties": {
            "strategy_id": {"type": "string", "description": "Strategy ID to backtest"},
            "instruments": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Instruments to include",
            },
            "start_date": {"type": "string", "description": "ISO start date"},
            "end_date": {"type": "string", "description": "ISO end date"},
            "initial_capital": {"type": "number", "default": 10000},
        },
        "required": ["strategy_id"],
    },
    output_schema={
        "type": "object",
        "properties": {
            "strategy_id": {"type": "string"},
            "stats": {"type": "object"},
            "trade_count": {"type": "integer"},
            "equity_curve": {"type": "array", "items": {"type": "number"}},
        },
        "required": ["strategy_id", "stats"],
    },
    timeout_seconds=120,
)

OPTIMIZE_PARAMETERS_TOOL = ToolDefinition(
    name="optimize_parameters",
    description="Optimize strategy parameters via grid search using the BacktestEngine.",
    input_schema={
        "type": "object",
        "properties": {
            "strategy_id": {"type": "string", "description": "Strategy ID"},
            "param_ranges": {
                "type": "object",
                "description": "Parameter name → list of values to search",
            },
            "metric": {"type": "string", "default": "net_profit"},
        },
        "required": ["strategy_id", "param_ranges"],
    },
    output_schema={
        "type": "object",
        "properties": {
            "best_params": {"type": "object"},
            "best_score": {"type": "number"},
            "all_results": {"type": "array", "items": {"type": "object"}},
            "metric": {"type": "string"},
        },
        "required": ["best_params", "best_score"],
    },
    timeout_seconds=300,
)

WALK_FORWARD_ANALYSIS_TOOL = ToolDefinition(
    name="walk_forward_analysis",
    description="Run walk-forward analysis with in-sample/out-of-sample windows.",
    input_schema={
        "type": "object",
        "properties": {
            "strategy_id": {"type": "string", "description": "Strategy ID"},
            "windows": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "start": {"type": "string"},
                        "end": {"type": "string"},
                    },
                    "required": ["start", "end"],
                },
                "description": "Alternating in-sample / out-of-sample windows",
            },
        },
        "required": ["strategy_id", "windows"],
    },
    output_schema={
        "type": "object",
        "properties": {
            "windows": {"type": "array", "items": {"type": "object"}},
            "combined_stats": {"type": "object"},
        },
        "required": ["combined_stats"],
    },
    timeout_seconds=300,
)

LIST_ALGORITHMS_TOOL = ToolDefinition(
    name="list_algorithms",
    description="List all registered strategy algorithms with their metadata.",
    input_schema={
        "type": "object",
        "properties": {},
    },
    output_schema={
        "type": "object",
        "properties": {
            "algorithms": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "description": {"type": "string"},
                    },
                },
            },
        },
        "required": ["algorithms"],
    },
    timeout_seconds=10,
    retryable=False,
)

GET_STRATEGY_CONFIG_TOOL = ToolDefinition(
    name="get_strategy_config",
    description="Fetch a strategy configuration from the backend API.",
    input_schema={
        "type": "object",
        "properties": {
            "strategy_id": {"type": "string", "description": "Strategy ID to fetch"},
        },
        "required": ["strategy_id"],
    },
    output_schema={
        "type": "object",
        "properties": {
            "id": {"type": "string"},
            "name": {"type": "string"},
            "algorithm": {"type": "string"},
            "config": {"type": "object"},
        },
        "required": ["id"],
    },
    timeout_seconds=15,
)

CREATE_STRATEGY_TOOL = ToolDefinition(
    name="create_strategy",
    description="Create a new strategy via the backend API.",
    input_schema={
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Strategy name"},
            "algorithm": {"type": "string", "description": "Algorithm name"},
            "config": {"type": "object", "description": "Strategy configuration"},
        },
        "required": ["name", "algorithm"],
    },
    output_schema={
        "type": "object",
        "properties": {
            "id": {"type": "string"},
            "name": {"type": "string"},
            "algorithm": {"type": "string"},
        },
        "required": ["id"],
    },
    timeout_seconds=15,
)

QUERY_EVENTS_TOOL = ToolDefinition(
    name="query_events",
    description="Query trading events from the backend event store.",
    input_schema={
        "type": "object",
        "properties": {
            "event_type": {"type": "string", "description": "Filter by event type"},
            "aggregate_id": {"type": "string", "description": "Filter by aggregate ID"},
            "start_date": {"type": "string", "description": "ISO start date"},
            "end_date": {"type": "string", "description": "ISO end date"},
            "limit": {"type": "integer", "default": 50},
        },
    },
    output_schema={
        "type": "object",
        "properties": {
            "events": {"type": "array", "items": {"type": "object"}},
            "total": {"type": "integer"},
        },
        "required": ["events"],
    },
    timeout_seconds=15,
)

QUERY_SIGNALS_TOOL = ToolDefinition(
    name="query_signals",
    description="Query trading signals from the backend API.",
    input_schema={
        "type": "object",
        "properties": {
            "instrument": {"type": "string", "description": "Filter by instrument"},
            "strategy_id": {"type": "string", "description": "Filter by strategy ID"},
            "start_date": {"type": "string", "description": "ISO start date"},
            "end_date": {"type": "string", "description": "ISO end date"},
            "limit": {"type": "integer", "default": 50},
        },
    },
    output_schema={
        "type": "object",
        "properties": {
            "signals": {"type": "array", "items": {"type": "object"}},
            "total": {"type": "integer"},
        },
        "required": ["signals"],
    },
    timeout_seconds=15,
)

WRITE_ALGORITHM_FILE_TOOL = ToolDefinition(
    name="write_algorithm_file",
    description="Write a Python algorithm file to src/strategy/algorithms/.",
    input_schema={
        "type": "object",
        "properties": {
            "filename": {
                "type": "string",
                "description": "Filename (e.g. my_strategy.py). Must end with .py.",
            },
            "code": {"type": "string", "description": "Python source code"},
        },
        "required": ["filename", "code"],
    },
    output_schema={
        "type": "object",
        "properties": {
            "file_path": {"type": "string"},
            "success": {"type": "boolean"},
        },
        "required": ["file_path", "success"],
    },
    timeout_seconds=10,
    retryable=False,
)

VALIDATE_ALGORITHM_TOOL = ToolDefinition(
    name="validate_algorithm",
    description="Validate an algorithm file: syntax check, import test, and interface check.",
    input_schema={
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Path to the algorithm file relative to strategy-engine root",
            },
        },
        "required": ["file_path"],
    },
    output_schema={
        "type": "object",
        "properties": {
            "valid": {"type": "boolean"},
            "algorithm_name": {"type": ["string", "null"]},
            "errors": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["valid"],
    },
    timeout_seconds=30,
    retryable=False,
)

READ_ALGORITHM_SOURCE_TOOL = ToolDefinition(
    name="read_algorithm_source",
    description="Read the Python source code of a named strategy algorithm.",
    input_schema={
        "type": "object",
        "properties": {
            "algorithm_name": {
                "type": "string",
                "description": "Name of the algorithm to read (e.g. 'ict_order_block')",
            },
        },
        "required": ["algorithm_name"],
    },
    output_schema={
        "type": "object",
        "properties": {
            "algorithm_name": {"type": "string"},
            "source_code": {"type": "string"},
            "file_path": {"type": "string"},
        },
        "required": ["algorithm_name", "source_code", "file_path"],
    },
    timeout_seconds=15,
    retryable=True,
    max_retries=2,
)

READ_STRATEGY_CONFIG_TOOL = ToolDefinition(
    name="read_strategy_config",
    description="Read the full configuration of a strategy by ID, including algorithm_params, instruments, timeframes, and risk_settings.",
    input_schema={
        "type": "object",
        "properties": {
            "strategy_id": {
                "type": "string",
                "description": "Strategy ID to read config for",
            },
        },
        "required": ["strategy_id"],
    },
    output_schema={
        "type": "object",
        "properties": {
            "id": {"type": "string"},
            "name": {"type": "string"},
            "algorithm": {"type": "string"},
            "config": {"type": "object"},
            "enabled": {"type": "boolean"},
        },
        "required": ["id", "name", "algorithm", "config", "enabled"],
    },
    timeout_seconds=15,
    retryable=True,
    max_retries=2,
)

UPDATE_STRATEGY_CONFIG_TOOL = ToolDefinition(
    name="update_strategy_config",
    description="Update a strategy's config JSONB via PATCH, supporting partial config updates.",
    input_schema={
        "type": "object",
        "properties": {
            "strategy_id": {
                "type": "string",
                "description": "Strategy ID to update",
            },
            "config": {
                "type": "object",
                "description": "Config fields to update (partial merge into existing config)",
            },
        },
        "required": ["strategy_id", "config"],
    },
    output_schema={
        "type": "object",
        "properties": {
            "id": {"type": "string"},
            "name": {"type": "string"},
            "algorithm": {"type": "string"},
            "config": {"type": "object"},
            "enabled": {"type": "boolean"},
        },
        "required": ["id", "name", "algorithm", "config", "enabled"],
    },
    timeout_seconds=15,
    retryable=False,
)


# ---------------------------------------------------------------------------
# Execute function factories (closures capturing dependencies)
# ---------------------------------------------------------------------------


def _make_run_backtest(backend_url: str, backtest_engine: Any = None) -> Callable:
    """Create execute function for run_backtest tool."""

    async def execute(input_data: dict) -> dict:
        strategy_id = input_data["strategy_id"]

        # Fetch strategy config from backend
        resp = requests.get(
            f"{backend_url}/api/strategies/{strategy_id}", timeout=10
        )
        resp.raise_for_status()
        strategy_data = resp.json()

        if backtest_engine is None:
            # Fallback: delegate to backend backtest endpoint if no local engine
            resp = requests.post(
                f"{backend_url}/api/strategies/{strategy_id}/backtest",
                json={
                    "instruments": input_data.get("instruments", []),
                    "start_date": input_data.get("start_date"),
                    "end_date": input_data.get("end_date"),
                    "initial_capital": input_data.get("initial_capital", 10000),
                },
                timeout=120,
            )
            resp.raise_for_status()
            return resp.json()

        # Local engine execution
        from src.backtesting.backtest_engine import BacktestEngine
        from src.models.backtest import BacktestParams
        from src.models.strategy_config import StrategyConfig

        strategy = StrategyConfig(**strategy_data)
        params = BacktestParams(
            initial_capital=input_data.get("initial_capital", 10000),
        )

        # Fetch candle data from backend
        candle_resp = requests.get(
            f"{backend_url}/api/candles",
            params={
                "instrument": input_data.get("instruments", ["US30"])[0]
                if input_data.get("instruments")
                else "US30",
                "start_date": input_data.get("start_date"),
                "end_date": input_data.get("end_date"),
            },
            timeout=30,
        )
        candle_resp.raise_for_status()
        candle_data = candle_resp.json()

        from src.models.candle import Candle

        candles = [Candle(**c) for c in candle_data]
        result = backtest_engine.run(strategy, candles, params)

        return {
            "strategy_id": strategy_id,
            "stats": result.stats.model_dump() if hasattr(result.stats, "model_dump") else {},
            "trade_count": len(result.trades),
            "equity_curve": result.equity_curve,
        }

    return execute


def _make_optimize_parameters(backend_url: str, backtest_engine: Any = None) -> Callable:
    """Create execute function for optimize_parameters tool."""

    async def execute(input_data: dict) -> dict:
        strategy_id = input_data["strategy_id"]
        param_ranges = input_data["param_ranges"]

        resp = requests.get(
            f"{backend_url}/api/strategies/{strategy_id}", timeout=10
        )
        resp.raise_for_status()
        strategy_data = resp.json()

        if backtest_engine is None:
            return {
                "best_params": {},
                "best_score": 0.0,
                "all_results": [],
                "metric": input_data.get("metric", "net_profit"),
            }

        from src.models.strategy_config import StrategyConfig

        strategy = StrategyConfig(**strategy_data)
        result = backtest_engine.optimize_parameters(
            strategy, [], param_ranges
        )

        return {
            "best_params": result.best_params,
            "best_score": result.best_score,
            "all_results": result.all_results,
            "metric": result.metric,
        }

    return execute


def _make_walk_forward_analysis(backend_url: str, backtest_engine: Any = None) -> Callable:
    """Create execute function for walk_forward_analysis tool."""

    async def execute(input_data: dict) -> dict:
        strategy_id = input_data["strategy_id"]
        windows_raw = input_data.get("windows", [])

        resp = requests.get(
            f"{backend_url}/api/strategies/{strategy_id}", timeout=10
        )
        resp.raise_for_status()
        strategy_data = resp.json()

        if backtest_engine is None:
            return {"windows": [], "combined_stats": {}}

        from src.models.backtest import TimeWindow
        from src.models.strategy_config import StrategyConfig

        strategy = StrategyConfig(**strategy_data)
        windows = [TimeWindow(start=w["start"], end=w["end"]) for w in windows_raw]
        result = backtest_engine.walk_forward(strategy, [], windows)

        return {
            "windows": [
                {
                    "in_sample_stats": w.in_sample_stats.model_dump()
                    if hasattr(w.in_sample_stats, "model_dump")
                    else {},
                    "out_of_sample_stats": w.out_of_sample_stats.model_dump()
                    if hasattr(w.out_of_sample_stats, "model_dump")
                    else {},
                }
                for w in result.windows
            ],
            "combined_stats": result.combined_stats.model_dump()
            if hasattr(result.combined_stats, "model_dump")
            else {},
        }

    return execute


def _make_list_algorithms(strategy_registry: Any = None) -> Callable:
    """Create execute function for list_algorithms tool."""

    async def execute(input_data: dict) -> dict:
        if strategy_registry is None:
            return {"algorithms": []}

        algorithms = strategy_registry.list_algorithms()
        return {"algorithms": algorithms}

    return execute


def _make_get_strategy_config(backend_url: str) -> Callable:
    """Create execute function for get_strategy_config tool."""

    async def execute(input_data: dict) -> dict:
        strategy_id = input_data["strategy_id"]
        resp = requests.get(
            f"{backend_url}/api/strategies/{strategy_id}", timeout=10
        )
        resp.raise_for_status()
        return resp.json()

    return execute


def _make_create_strategy(backend_url: str) -> Callable:
    """Create execute function for create_strategy tool."""

    async def execute(input_data: dict) -> dict:
        payload = {
            "name": input_data["name"],
            "algorithm": input_data["algorithm"],
        }
        if "config" in input_data:
            payload["config"] = input_data["config"]

        resp = requests.post(
            f"{backend_url}/api/strategies",
            json=payload,
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()

    return execute


def _make_query_events(backend_url: str) -> Callable:
    """Create execute function for query_events tool."""

    async def execute(input_data: dict) -> dict:
        params: dict[str, Any] = {}
        if "event_type" in input_data:
            params["event_type"] = input_data["event_type"]
        if "aggregate_id" in input_data:
            params["aggregate_id"] = input_data["aggregate_id"]
        if "start_date" in input_data:
            params["start_date"] = input_data["start_date"]
        if "end_date" in input_data:
            params["end_date"] = input_data["end_date"]
        params["limit"] = input_data.get("limit", 50)

        resp = requests.get(
            f"{backend_url}/api/events",
            params=params,
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()

        # Normalize response — backend may return a list or an object
        if isinstance(data, list):
            return {"events": data, "total": len(data)}
        return {
            "events": data.get("events", data.get("data", [])),
            "total": data.get("total", len(data.get("events", []))),
        }

    return execute


def _make_query_signals(backend_url: str) -> Callable:
    """Create execute function for query_signals tool."""

    async def execute(input_data: dict) -> dict:
        params: dict[str, Any] = {}
        if "instrument" in input_data:
            params["instrument"] = input_data["instrument"]
        if "strategy_id" in input_data:
            params["strategy_id"] = input_data["strategy_id"]
        if "start_date" in input_data:
            params["start_date"] = input_data["start_date"]
        if "end_date" in input_data:
            params["end_date"] = input_data["end_date"]
        params["limit"] = input_data.get("limit", 50)

        resp = requests.get(
            f"{backend_url}/api/signals",
            params=params,
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()

        if isinstance(data, list):
            return {"signals": data, "total": len(data)}
        return {
            "signals": data.get("signals", data.get("data", [])),
            "total": data.get("total", len(data.get("signals", []))),
        }

    return execute


def _make_write_algorithm_file() -> Callable:
    """Create execute function for write_algorithm_file tool."""

    async def execute(input_data: dict) -> dict:
        filename = input_data["filename"]
        code = input_data["code"]

        # Security: ensure filename is safe
        if not filename.endswith(".py"):
            return {"file_path": filename, "success": False}

        # Prevent path traversal
        safe_name = Path(filename).name
        file_path = ALGORITHMS_DIR / safe_name

        # Ensure the algorithms directory exists
        file_path.parent.mkdir(parents=True, exist_ok=True)

        file_path.write_text(code, encoding="utf-8")
        logger.info("Wrote algorithm file: %s", file_path)

        return {"file_path": str(file_path), "success": True}

    return execute


def _make_validate_algorithm(algorithm_manager: Any = None) -> Callable:
    """Create execute function for validate_algorithm tool."""

    async def execute(input_data: dict) -> dict:
        file_path_str = input_data["file_path"]
        file_path = Path(file_path_str)
        errors: list[str] = []

        # 1. Check file exists
        if not file_path.exists():
            return {"valid": False, "algorithm_name": None, "errors": [f"File not found: {file_path_str}"]}

        # 2. Syntax check via compile
        code = file_path.read_text(encoding="utf-8")
        try:
            compile(code, str(file_path), "exec")
        except SyntaxError as exc:
            return {
                "valid": False,
                "algorithm_name": None,
                "errors": [f"Syntax error: {exc.msg} (line {exc.lineno})"],
            }

        # 3. Use AlgorithmManager for full load + interface check if available
        if algorithm_manager is not None:
            try:
                alg_name = algorithm_manager._load_algorithm_from_file(file_path)
                if alg_name is None:
                    errors.append(
                        "AlgorithmManager could not load the file "
                        "(no valid StrategyAlgorithm subclass found or already registered)"
                    )
                    return {"valid": False, "algorithm_name": None, "errors": errors}
                return {"valid": True, "algorithm_name": alg_name, "errors": []}
            except Exception as exc:
                return {
                    "valid": False,
                    "algorithm_name": None,
                    "errors": [f"Load failed: {exc}"],
                }

        # Fallback: basic import check without AlgorithmManager
        import importlib.util

        module_name = f"_validate_{file_path.stem}"
        try:
            spec = importlib.util.spec_from_file_location(module_name, str(file_path))
            if spec is None or spec.loader is None:
                return {"valid": False, "algorithm_name": None, "errors": ["Cannot create module spec"]}
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        except Exception as exc:
            return {"valid": False, "algorithm_name": None, "errors": [f"Import error: {exc}"]}

        # Check for StrategyAlgorithm subclass
        import inspect

        from src.strategy.base import StrategyAlgorithm

        alg_class = None
        for _, obj in inspect.getmembers(module, inspect.isclass):
            if issubclass(obj, StrategyAlgorithm) and obj is not StrategyAlgorithm:
                alg_class = obj
                break

        if alg_class is None:
            return {
                "valid": False,
                "algorithm_name": None,
                "errors": ["No StrategyAlgorithm subclass found"],
            }

        try:
            instance = alg_class()
            name = instance.name()
        except Exception as exc:
            return {"valid": False, "algorithm_name": None, "errors": [f"Instantiation failed: {exc}"]}

        return {"valid": True, "algorithm_name": name, "errors": []}

    return execute


def _make_read_algorithm_source(backend_url: str) -> Callable:
    """Create execute function for read_algorithm_source tool."""

    async def execute(input_data: dict) -> dict:
        name = input_data["algorithm_name"]
        resp = requests.get(
            f"{backend_url}/api/algorithms/{name}/source", timeout=10
        )
        resp.raise_for_status()
        data = resp.json()
        return {
            "algorithm_name": name,
            "source_code": data.get("source", data.get("code", "")),
            "file_path": data.get("file_path", f"src/strategy/algorithms/{name}.py"),
        }

    return execute


def _make_read_strategy_config(backend_url: str) -> Callable:
    """Create execute function for read_strategy_config tool."""

    async def execute(input_data: dict) -> dict:
        strategy_id = input_data["strategy_id"]
        resp = requests.get(
            f"{backend_url}/api/strategies/{strategy_id}", timeout=10
        )
        resp.raise_for_status()
        data = resp.json()
        return {
            "id": data.get("id", strategy_id),
            "name": data.get("name", ""),
            "algorithm": data.get("algorithm", ""),
            "config": data.get("config", {}),
            "enabled": data.get("enabled", False),
        }

    return execute


def _make_update_strategy_config(backend_url: str) -> Callable:
    """Create execute function for update_strategy_config tool."""

    async def execute(input_data: dict) -> dict:
        strategy_id = input_data["strategy_id"]
        config_update = input_data["config"]
        resp = requests.patch(
            f"{backend_url}/api/strategies/{strategy_id}",
            json={"config": config_update},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        return {
            "id": data.get("id", strategy_id),
            "name": data.get("name", ""),
            "algorithm": data.get("algorithm", ""),
            "config": data.get("config", {}),
            "enabled": data.get("enabled", False),
        }

    return execute


# ---------------------------------------------------------------------------
# Registration helper
# ---------------------------------------------------------------------------


def register_builtin_tools(
    registry: ToolRegistry,
    backend_url: str,
    backtest_engine: Any = None,
    strategy_registry: Any = None,
    algorithm_manager: Any = None,
) -> None:
    """Register all built-in tools with the ToolRegistry.

    Args:
        registry: The ToolRegistry to register tools into.
        backend_url: Base URL for the backend API (e.g. "http://backend:3000").
        backtest_engine: Optional BacktestEngine instance for local backtest tools.
        strategy_registry: Optional StrategyRegistry instance for algorithm listing.
        algorithm_manager: Optional AlgorithmManager instance for algorithm validation.
    """
    registry.register(RUN_BACKTEST_TOOL, _make_run_backtest(backend_url, backtest_engine))
    registry.register(OPTIMIZE_PARAMETERS_TOOL, _make_optimize_parameters(backend_url, backtest_engine))
    registry.register(WALK_FORWARD_ANALYSIS_TOOL, _make_walk_forward_analysis(backend_url, backtest_engine))
    registry.register(LIST_ALGORITHMS_TOOL, _make_list_algorithms(strategy_registry))
    registry.register(GET_STRATEGY_CONFIG_TOOL, _make_get_strategy_config(backend_url))
    registry.register(CREATE_STRATEGY_TOOL, _make_create_strategy(backend_url))
    registry.register(QUERY_EVENTS_TOOL, _make_query_events(backend_url))
    registry.register(QUERY_SIGNALS_TOOL, _make_query_signals(backend_url))
    registry.register(WRITE_ALGORITHM_FILE_TOOL, _make_write_algorithm_file())
    registry.register(VALIDATE_ALGORITHM_TOOL, _make_validate_algorithm(algorithm_manager))
    registry.register(READ_ALGORITHM_SOURCE_TOOL, _make_read_algorithm_source(backend_url))
    registry.register(READ_STRATEGY_CONFIG_TOOL, _make_read_strategy_config(backend_url))
    registry.register(UPDATE_STRATEGY_CONFIG_TOOL, _make_update_strategy_config(backend_url))

    logger.info("Registered %d built-in tools", 13)
