"""Dynamic algorithm manager with file watching and hot-reload.

Scans the algorithms directory, dynamically imports algorithm classes,
validates they conform to the StrategyAlgorithm interface, and watches
for file changes to auto-reload.
"""

import importlib
import importlib.util
import inspect
import logging
import os
import threading
import time
from pathlib import Path
from typing import Optional

from src.strategy.base import StrategyAlgorithm
from src.strategy.registry import StrategyRegistry

logger = logging.getLogger("strategy_engine.algorithm_manager")

ALGORITHMS_DIR = Path(__file__).resolve().parent.parent / "strategy" / "algorithms"


class AlgorithmManager:
    """Manages dynamic algorithm loading, validation, and file watching."""

    def __init__(self, registry: StrategyRegistry) -> None:
        self._registry = registry
        self._lock = threading.Lock()
        self._watcher_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._file_mtimes: dict[str, float] = {}

    @property
    def registry(self) -> StrategyRegistry:
        return self._registry

    def scan_and_load(self) -> list[str]:
        """Scan algorithms directory and load all valid algorithm classes.

        Returns list of newly loaded algorithm names.
        """
        loaded: list[str] = []
        if not ALGORITHMS_DIR.exists():
            logger.warning("Algorithms directory not found: %s", ALGORITHMS_DIR)
            return loaded

        for py_file in sorted(ALGORITHMS_DIR.glob("*.py")):
            if py_file.name.startswith("_"):
                continue
            name = self._load_algorithm_from_file(py_file)
            if name:
                loaded.append(name)
                self._file_mtimes[str(py_file)] = py_file.stat().st_mtime

        return loaded

    def _load_algorithm_from_file(self, filepath: Path) -> Optional[str]:
        """Load a single algorithm file and register it.

        Returns the algorithm name if successful, None otherwise.
        """
        module_name = f"algorithms_dynamic.{filepath.stem}"
        try:
            spec = importlib.util.spec_from_file_location(module_name, str(filepath))
            if spec is None or spec.loader is None:
                logger.warning("Cannot create module spec for %s", filepath)
                return None

            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)

            # Find the StrategyAlgorithm subclass
            alg_class = None
            for _, obj in inspect.getmembers(module, inspect.isclass):
                if (
                    issubclass(obj, StrategyAlgorithm)
                    and obj is not StrategyAlgorithm
                ):
                    alg_class = obj
                    break

            if alg_class is None:
                logger.warning(
                    "No StrategyAlgorithm subclass found in %s", filepath.name
                )
                return None

            instance = alg_class()
            alg_name = instance.name()

            # Validate required methods return correct types
            if not isinstance(alg_name, str) or not alg_name:
                logger.warning("Invalid name() in %s", filepath.name)
                return None
            if not isinstance(instance.description(), str):
                logger.warning("Invalid description() in %s", filepath.name)
                return None
            if not isinstance(instance.default_params(), dict):
                logger.warning("Invalid default_params() in %s", filepath.name)
                return None
            if not isinstance(instance.param_schema(), dict):
                logger.warning("Invalid param_schema() in %s", filepath.name)
                return None

            with self._lock:
                if not self._registry.has(alg_name):
                    self._registry.register(instance)
                    logger.info("Loaded algorithm '%s' from %s", alg_name, filepath.name)
                    # Record mtime so watcher doesn't re-detect this file
                    self._file_mtimes[str(filepath)] = filepath.stat().st_mtime
                    return alg_name
                else:
                    logger.debug("Algorithm '%s' already registered, skipping", alg_name)
                    # Still record mtime so the watcher doesn't keep flagging it as new
                    self._file_mtimes[str(filepath)] = filepath.stat().st_mtime
                    return None

        except Exception:
            logger.exception("Failed to load algorithm from %s", filepath.name)
            return None

    def reload_file(self, filepath: Path) -> Optional[str]:
        """Reload a single algorithm file (for hot-reload on change).

        Unregisters the old version and loads the new one.
        """
        module_name = f"algorithms_dynamic.{filepath.stem}"
        try:
            spec = importlib.util.spec_from_file_location(module_name, str(filepath))
            if spec is None or spec.loader is None:
                return None

            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)

            alg_class = None
            for _, obj in inspect.getmembers(module, inspect.isclass):
                if (
                    issubclass(obj, StrategyAlgorithm)
                    and obj is not StrategyAlgorithm
                ):
                    alg_class = obj
                    break

            if alg_class is None:
                return None

            instance = alg_class()
            alg_name = instance.name()

            with self._lock:
                # Replace in registry
                self._registry._algorithms[alg_name] = instance
                logger.info("Reloaded algorithm '%s' from %s", alg_name, filepath.name)
                return alg_name

        except Exception:
            logger.exception("Failed to reload algorithm from %s", filepath.name)
            return None

    def remove_algorithm(self, name: str) -> bool:
        """Remove an algorithm from the registry and delete its file.

        Returns True if removed successfully.
        """
        with self._lock:
            if not self._registry.has(name):
                return False

            # Find the file for this algorithm
            filepath = self._find_file_for_algorithm(name)
            if filepath and filepath.exists():
                filepath.unlink()
                self._file_mtimes.pop(str(filepath), None)
                logger.info("Deleted algorithm file: %s", filepath.name)

            del self._registry._algorithms[name]
            logger.info("Removed algorithm '%s' from registry", name)
            return True

    def save_algorithm_file(self, filename: str, content: str) -> Optional[str]:
        """Save a new algorithm file and load it.

        Returns the algorithm name if successful, None otherwise.
        """
        if not filename.endswith(".py"):
            filename += ".py"

        # Sanitize filename
        safe_name = "".join(
            c for c in filename if c.isalnum() or c in ("_", "-", ".")
        )
        filepath = ALGORITHMS_DIR / safe_name

        # Write the file
        filepath.write_text(content, encoding="utf-8")
        logger.info("Saved algorithm file: %s", filepath.name)

        # Try to load it
        name = self._load_algorithm_from_file(filepath)
        if name:
            self._file_mtimes[str(filepath)] = filepath.stat().st_mtime
            return name
        else:
            # Invalid file — remove it
            filepath.unlink()
            logger.warning("Removed invalid algorithm file: %s", filepath.name)
            return None

    def get_algorithm_source(self, name: str) -> Optional[str]:
        """Get the source code of an algorithm by name."""
        filepath = self._find_file_for_algorithm(name)
        if filepath and filepath.exists():
            return filepath.read_text(encoding="utf-8")
        return None

    def _find_file_for_algorithm(self, name: str) -> Optional[Path]:
        """Find the .py file that contains the algorithm with the given name."""
        for py_file in ALGORITHMS_DIR.glob("*.py"):
            if py_file.name.startswith("_"):
                continue
            try:
                content = py_file.read_text(encoding="utf-8")
                # Quick check before doing full import
                if f'"{name}"' in content or f"'{name}'" in content:
                    return py_file
            except Exception:
                continue
        return None

    # --- File Watcher ---

    def start_watcher(self, poll_interval: float = 3.0) -> None:
        """Start background file watcher thread."""
        if self._watcher_thread and self._watcher_thread.is_alive():
            return

        self._stop_event.clear()
        self._watcher_thread = threading.Thread(
            target=self._watch_loop,
            args=(poll_interval,),
            daemon=True,
            name="algorithm-watcher",
        )
        self._watcher_thread.start()
        logger.info("Algorithm file watcher started (poll every %.1fs)", poll_interval)

    def stop_watcher(self) -> None:
        """Stop the file watcher thread."""
        self._stop_event.set()
        if self._watcher_thread:
            self._watcher_thread.join(timeout=5.0)
            logger.info("Algorithm file watcher stopped")

    def _watch_loop(self, poll_interval: float) -> None:
        """Poll the algorithms directory for changes."""
        while not self._stop_event.is_set():
            try:
                self._check_for_changes()
            except Exception:
                logger.exception("Error in algorithm watcher")
            self._stop_event.wait(poll_interval)

    def _check_for_changes(self) -> None:
        """Check for new, modified, or deleted algorithm files."""
        if not ALGORITHMS_DIR.exists():
            return

        current_files: set[str] = set()

        for py_file in ALGORITHMS_DIR.glob("*.py"):
            if py_file.name.startswith("_"):
                continue
            fpath = str(py_file)
            current_files.add(fpath)
            mtime = py_file.stat().st_mtime

            if fpath not in self._file_mtimes:
                # New file
                logger.info("New algorithm file detected: %s", py_file.name)
                name = self._load_algorithm_from_file(py_file)
                if name:
                    self._file_mtimes[fpath] = mtime
            elif mtime > self._file_mtimes[fpath]:
                # Modified file
                logger.info("Algorithm file modified: %s", py_file.name)
                self.reload_file(py_file)
                self._file_mtimes[fpath] = mtime

        # Check for deleted files
        deleted = set(self._file_mtimes.keys()) - current_files
        for fpath in deleted:
            del self._file_mtimes[fpath]
            logger.info("Algorithm file removed: %s", Path(fpath).name)
