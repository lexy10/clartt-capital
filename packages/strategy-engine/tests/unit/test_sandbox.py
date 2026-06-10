"""Unit tests for CodeSandbox."""

import os
import tempfile
import textwrap

import pytest

from src.agents.sandbox import CodeSandbox, SecurityError


class TestCheckPathAllowed:
    """Tests for _check_path_allowed path restriction."""

    def test_allowed_path_passes(self, tmp_path):
        sandbox = CodeSandbox(allowed_paths=[str(tmp_path)])
        # Should not raise
        sandbox._check_path_allowed(str(tmp_path / "test.py"))

    def test_disallowed_path_raises_security_error(self, tmp_path):
        sandbox = CodeSandbox(allowed_paths=[str(tmp_path / "allowed")])
        with pytest.raises(SecurityError, match="outside allowed directories"):
            sandbox._check_path_allowed(str(tmp_path / "forbidden" / "evil.py"))

    def test_default_allowed_paths(self):
        sandbox = CodeSandbox()
        assert sandbox.allowed_paths == ["src/strategy/algorithms/"]

    def test_traversal_attack_blocked(self, tmp_path):
        allowed = tmp_path / "allowed"
        allowed.mkdir()
        sandbox = CodeSandbox(allowed_paths=[str(allowed)])
        with pytest.raises(SecurityError):
            sandbox._check_path_allowed(str(allowed / ".." / "secret.py"))


class TestExecute:
    """Tests for execute() subprocess execution."""

    @pytest.mark.asyncio
    async def test_simple_code_execution(self):
        sandbox = CodeSandbox(timeout_seconds=10)
        result = await sandbox.execute("print('hello world')")
        assert result.success is True
        assert "hello world" in result.stdout
        assert result.execution_time_seconds > 0

    @pytest.mark.asyncio
    async def test_code_with_error(self):
        sandbox = CodeSandbox(timeout_seconds=10)
        result = await sandbox.execute("raise ValueError('test error')")
        assert result.success is False
        assert "ValueError" in result.stderr
        assert result.error is not None

    @pytest.mark.asyncio
    async def test_timeout_kills_process(self):
        sandbox = CodeSandbox(timeout_seconds=1)
        result = await sandbox.execute("import time; time.sleep(30)")
        assert result.success is False
        assert result.error == "timeout"

    @pytest.mark.asyncio
    async def test_entry_point(self):
        sandbox = CodeSandbox(timeout_seconds=10)
        code = "def greet():\n    print('from entry')"
        result = await sandbox.execute(code, entry_point="greet()")
        assert result.success is True
        assert "from entry" in result.stdout


class TestValidateFile:
    """Tests for validate_file() syntax/import/interface checks."""

    @pytest.mark.asyncio
    async def test_syntax_error_detected(self, tmp_path):
        bad_file = tmp_path / "bad.py"
        bad_file.write_text("def foo(\n")  # syntax error
        sandbox = CodeSandbox(allowed_paths=[str(tmp_path)])
        result = await sandbox.validate_file(str(bad_file))
        assert result.success is False
        assert result.error == "syntax_error"

    @pytest.mark.asyncio
    async def test_file_not_found(self, tmp_path):
        sandbox = CodeSandbox(allowed_paths=[str(tmp_path)])
        result = await sandbox.validate_file(str(tmp_path / "nonexistent.py"))
        assert result.success is False
        assert result.error == "file_not_found"

    @pytest.mark.asyncio
    async def test_path_violation_raises(self, tmp_path):
        sandbox = CodeSandbox(allowed_paths=[str(tmp_path / "allowed")])
        with pytest.raises(SecurityError):
            await sandbox.validate_file(str(tmp_path / "forbidden" / "file.py"))

    @pytest.mark.asyncio
    async def test_valid_algorithm_file(self, tmp_path):
        algo_file = tmp_path / "test_algo.py"
        algo_file.write_text(textwrap.dedent("""\
            class TestAlgo:
                @staticmethod
                def name():
                    return "test_algo"

                @staticmethod
                def description():
                    return "A test algorithm"

                @staticmethod
                def default_params():
                    return {}

                @staticmethod
                def param_schema():
                    return {}

                def analyze(self, entry_candles, structure_candles, trend_candles, config):
                    return []
        """))
        sandbox = CodeSandbox(allowed_paths=[str(tmp_path)], timeout_seconds=15)
        result = await sandbox.validate_file(str(algo_file))
        assert result.success is True
        assert result.return_value is not None
        assert result.return_value["algorithm_class"] == "TestAlgo"

    @pytest.mark.asyncio
    async def test_missing_interface_methods(self, tmp_path):
        algo_file = tmp_path / "incomplete.py"
        algo_file.write_text(textwrap.dedent("""\
            class IncompleteAlgo:
                def name(self):
                    return "incomplete"
                # Missing other required methods
        """))
        sandbox = CodeSandbox(allowed_paths=[str(tmp_path)], timeout_seconds=15)
        result = await sandbox.validate_file(str(algo_file))
        assert result.success is False


class TestSmokeTest:
    """Tests for smoke_test() algorithm instantiation and analyze() call."""

    @pytest.mark.asyncio
    async def test_path_violation_raises(self, tmp_path):
        sandbox = CodeSandbox(allowed_paths=[str(tmp_path / "allowed")])
        with pytest.raises(SecurityError):
            await sandbox.smoke_test(str(tmp_path / "evil.py"), "EvilAlgo")

    @pytest.mark.asyncio
    async def test_missing_class_fails(self, tmp_path):
        algo_file = tmp_path / "empty_mod.py"
        algo_file.write_text("x = 1\n")
        sandbox = CodeSandbox(allowed_paths=[str(tmp_path)], timeout_seconds=15)
        result = await sandbox.smoke_test(str(algo_file), "NonExistent")
        assert result.success is False


class TestConstructor:
    """Tests for CodeSandbox constructor defaults."""

    def test_default_values(self):
        sandbox = CodeSandbox()
        assert sandbox.timeout_seconds == 30
        assert sandbox.memory_mb == 512
        assert sandbox.allowed_paths == ["src/strategy/algorithms/"]

    def test_custom_values(self):
        sandbox = CodeSandbox(
            timeout_seconds=60,
            memory_mb=1024,
            allowed_paths=["/custom/path/"],
        )
        assert sandbox.timeout_seconds == 60
        assert sandbox.memory_mb == 1024
        assert sandbox.allowed_paths == ["/custom/path/"]
