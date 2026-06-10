"""Code sandbox for isolated execution of agent-generated Python code.

Executes Python code in subprocesses with timeout and memory limits,
restricted to the src/strategy/algorithms/ directory.
"""

import ast
import asyncio
import logging
import os
import platform
import sys
import tempfile
import textwrap
import time
from pathlib import Path
from typing import Optional

from src.agents.models import SandboxResult

logger = logging.getLogger(__name__)


class SecurityError(Exception):
    """Raised when a path violation or security constraint is breached."""


class CodeSandbox:
    """Executes Python code in an isolated subprocess with resource limits."""

    def __init__(
        self,
        timeout_seconds: int = 30,
        memory_mb: int = 512,
        allowed_paths: Optional[list[str]] = None,
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self.memory_mb = memory_mb
        self.allowed_paths = allowed_paths or ["src/strategy/algorithms/"]

    def _check_path_allowed(self, path: str) -> None:
        """Raise SecurityError if path is outside allowed directories."""
        resolved = Path(path).resolve()
        for allowed in self.allowed_paths:
            allowed_resolved = Path(allowed).resolve()
            try:
                resolved.relative_to(allowed_resolved)
                return
            except ValueError:
                continue
        raise SecurityError(
            f"Path '{path}' is outside allowed directories: {self.allowed_paths}"
        )

    def _build_resource_preexec(self) -> Optional[callable]:
        """Build a preexec_fn that sets memory limits via resource module (Linux/macOS).

        On macOS, RLIMIT_AS may not be supported; falls back to RLIMIT_RSS
        or skips memory limits if neither is available.
        """
        if platform.system() == "Windows":
            return None

        memory_bytes = self.memory_mb * 1024 * 1024

        def _set_limits():
            import resource
            # Try RLIMIT_AS first (Linux), fall back to RLIMIT_RSS (macOS)
            for limit_type in ("RLIMIT_AS", "RLIMIT_RSS"):
                attr = getattr(resource, limit_type, None)
                if attr is not None:
                    try:
                        resource.setrlimit(attr, (memory_bytes, memory_bytes))
                        return
                    except (ValueError, OSError):
                        continue

        return _set_limits

    async def execute(self, code: str, entry_point: Optional[str] = None) -> SandboxResult:
        """Execute Python code in a subprocess with timeout and memory limits.

        1. Write code to a temporary file.
        2. Spawn subprocess with resource limits.
        3. Capture stdout, stderr, and return value.
        4. Kill subprocess if timeout or memory limit exceeded.
        5. Return SandboxResult.
        """
        start_time = time.monotonic()

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", delete=False
        ) as tmp:
            tmp.write(code)
            if entry_point:
                tmp.write(f"\n\nif __name__ == '__main__':\n    {entry_point}\n")
            tmp_path = tmp.name

        try:
            process = await asyncio.create_subprocess_exec(
                sys.executable, tmp_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                preexec_fn=self._build_resource_preexec(),
            )

            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    process.communicate(),
                    timeout=self.timeout_seconds,
                )
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
                elapsed = time.monotonic() - start_time
                return SandboxResult(
                    success=False,
                    stderr=f"Process killed: exceeded timeout of {self.timeout_seconds}s",
                    execution_time_seconds=round(elapsed, 3),
                    error="timeout",
                )

            elapsed = time.monotonic() - start_time
            stdout = stdout_bytes.decode("utf-8", errors="replace")
            stderr = stderr_bytes.decode("utf-8", errors="replace")

            if process.returncode != 0:
                return SandboxResult(
                    success=False,
                    stdout=stdout,
                    stderr=stderr,
                    execution_time_seconds=round(elapsed, 3),
                    error=f"Process exited with code {process.returncode}",
                )

            return SandboxResult(
                success=True,
                stdout=stdout,
                stderr=stderr,
                execution_time_seconds=round(elapsed, 3),
            )
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    async def validate_file(self, file_path: str) -> SandboxResult:
        """Validate a Python file: syntax check (compile), import test, interface check.

        Steps:
        1. Syntax check — compile the source with ast.parse.
        2. Import test — dynamically import the module in a subprocess.
        3. Interface check — verify the module contains a class implementing
           StrategyAlgorithm abstract methods.
        """
        self._check_path_allowed(file_path)
        start_time = time.monotonic()

        # Step 1: Syntax check
        try:
            with open(file_path, "r") as f:
                source = f.read()
            ast.parse(source, filename=file_path)
        except SyntaxError as e:
            elapsed = time.monotonic() - start_time
            return SandboxResult(
                success=False,
                stderr=f"Syntax error: {e}",
                execution_time_seconds=round(elapsed, 3),
                error="syntax_error",
            )
        except FileNotFoundError:
            elapsed = time.monotonic() - start_time
            return SandboxResult(
                success=False,
                stderr=f"File not found: {file_path}",
                execution_time_seconds=round(elapsed, 3),
                error="file_not_found",
            )

        # Step 2 & 3: Import test + interface check in subprocess
        validation_code = textwrap.dedent(f"""\
            import sys
            import importlib.util
            import os

            file_path = {file_path!r}
            module_name = os.path.splitext(os.path.basename(file_path))[0]

            spec = importlib.util.spec_from_file_location(module_name, file_path)
            if spec is None:
                print("FAIL:import:Could not create module spec", file=sys.stderr)
                sys.exit(1)

            module = importlib.util.module_from_spec(spec)
            try:
                spec.loader.exec_module(module)
            except Exception as e:
                print(f"FAIL:import:{{type(e).__name__}}: {{e}}", file=sys.stderr)
                sys.exit(1)

            # Find classes that look like StrategyAlgorithm implementations
            required_methods = ["name", "description", "default_params", "param_schema", "analyze"]
            found_class = False

            for attr_name in dir(module):
                obj = getattr(module, attr_name)
                if isinstance(obj, type) and attr_name != "StrategyAlgorithm":
                    methods = [m for m in required_methods if hasattr(obj, m)]
                    if len(methods) == len(required_methods):
                        found_class = True
                        print(f"OK:{{attr_name}}")
                        break

            if not found_class:
                print("FAIL:interface:No class implementing StrategyAlgorithm interface found", file=sys.stderr)
                sys.exit(1)
        """)

        result = await self.execute(validation_code)
        elapsed = time.monotonic() - start_time
        result.execution_time_seconds = round(elapsed, 3)

        if result.success and result.stdout.startswith("OK:"):
            result.return_value = {
                "algorithm_class": result.stdout.strip().split(":", 1)[1]
            }

        return result

    async def smoke_test(self, file_path: str, algorithm_class: str) -> SandboxResult:
        """Instantiate algorithm, call analyze() with synthetic candles, verify no exceptions.

        Creates minimal synthetic candle data and a basic StrategyConfig,
        instantiates the algorithm class, and calls analyze() to verify
        it returns a list without raising.
        """
        self._check_path_allowed(file_path)

        smoke_code = textwrap.dedent(f"""\
            import sys
            import importlib.util
            import os
            import json

            file_path = {file_path!r}
            class_name = {algorithm_class!r}

            # Add project root to path so src.* imports work
            project_root = os.path.abspath(os.path.join(os.path.dirname(file_path), "..", "..", ".."))
            if project_root not in sys.path:
                sys.path.insert(0, project_root)

            module_name = os.path.splitext(os.path.basename(file_path))[0]
            spec = importlib.util.spec_from_file_location(module_name, file_path)
            if spec is None:
                print("FAIL:Could not create module spec", file=sys.stderr)
                sys.exit(1)

            module = importlib.util.module_from_spec(spec)
            try:
                spec.loader.exec_module(module)
            except Exception as e:
                print(f"FAIL:Import error: {{type(e).__name__}}: {{e}}", file=sys.stderr)
                sys.exit(1)

            algo_cls = getattr(module, class_name, None)
            if algo_cls is None:
                print(f"FAIL:Class '{{class_name}}' not found in module", file=sys.stderr)
                sys.exit(1)

            # Instantiate
            try:
                algo = algo_cls()
            except Exception as e:
                print(f"FAIL:Instantiation error: {{type(e).__name__}}: {{e}}", file=sys.stderr)
                sys.exit(1)

            # Build synthetic candles
            from src.models import Candle, StrategyConfig, Timeframe, RiskSettings

            base_price = 100.0
            candles = []
            for i in range(50):
                candles.append(Candle(
                    instrument="TEST",
                    timeframe=Timeframe.FIFTEEN_MINUTES,
                    open=base_price + i * 0.1,
                    high=base_price + i * 0.1 + 0.5,
                    low=base_price + i * 0.1 - 0.3,
                    close=base_price + i * 0.1 + 0.2,
                    volume=1000.0 + i * 10,
                    timestamp=f"2024-01-01T{{i:02d}}:00:00Z",
                ))

            config = StrategyConfig(
                id="smoke-test",
                name="Smoke Test Strategy",
                algorithm=module_name,
                instruments=["TEST"],
                timeframes=[Timeframe.FIFTEEN_MINUTES, Timeframe.ONE_HOUR, Timeframe.FOUR_HOURS],
                higher_timeframe=Timeframe.ONE_HOUR,
                entry_timeframe=Timeframe.FIFTEEN_MINUTES,
                risk_settings=RiskSettings(
                    max_risk_per_trade_pct=2.0,
                    max_daily_loss_pct=5.0,
                    max_spread=5.0,
                    max_slippage=3.0,
                    volatility_multiplier=2.0,
                ),
                mode="backtest",
            )

            # Call analyze
            try:
                result = algo.analyze(
                    entry_candles=candles,
                    structure_candles=candles,
                    trend_candles=candles,
                    config=config,
                )
            except Exception as e:
                print(f"FAIL:analyze() raised: {{type(e).__name__}}: {{e}}", file=sys.stderr)
                sys.exit(1)

            if not isinstance(result, list):
                print(f"FAIL:analyze() returned {{type(result).__name__}}, expected list", file=sys.stderr)
                sys.exit(1)

            print(f"OK:signals={{len(result)}}")
        """)

        result = await self.execute(smoke_code)

        if result.success and result.stdout.startswith("OK:"):
            parts = result.stdout.strip().split("=")
            signal_count = int(parts[1]) if len(parts) == 2 else 0
            result.return_value = {"signal_count": signal_count}

        return result
