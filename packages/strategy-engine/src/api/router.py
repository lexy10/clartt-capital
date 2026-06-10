"""FastAPI router for algorithm management endpoints."""

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from pydantic import BaseModel

from src.api.algorithm_manager import AlgorithmManager

logger = logging.getLogger("strategy_engine.api")

router = APIRouter(prefix="/algorithms", tags=["algorithms"])

# Will be set by main.py after creating the manager
_manager: Optional[AlgorithmManager] = None


def set_manager(manager: AlgorithmManager) -> None:
    global _manager
    _manager = manager


def get_manager() -> AlgorithmManager:
    if _manager is None:
        raise HTTPException(status_code=503, detail="Algorithm manager not initialized")
    return _manager


class AlgorithmResponse(BaseModel):
    name: str
    description: str
    default_params: dict
    param_schema: dict


class UploadResponse(BaseModel):
    name: str
    message: str


class SourceResponse(BaseModel):
    name: str
    source: str
    filename: str


@router.get("", response_model=list[AlgorithmResponse])
def list_algorithms():
    """List all registered algorithms with their metadata."""
    mgr = get_manager()
    return mgr.registry.list_algorithms()


@router.get("/{name}", response_model=AlgorithmResponse)
def get_algorithm(name: str):
    """Get a single algorithm's metadata."""
    mgr = get_manager()
    if not mgr.registry.has(name):
        raise HTTPException(status_code=404, detail=f"Algorithm '{name}' not found")
    alg = mgr.registry.get(name)
    return {
        "name": alg.name(),
        "description": alg.description(),
        "default_params": alg.default_params(),
        "param_schema": alg.param_schema(),
    }


@router.get("/{name}/source", response_model=SourceResponse)
def get_algorithm_source(name: str):
    """Get the source code of an algorithm."""
    mgr = get_manager()
    if not mgr.registry.has(name):
        raise HTTPException(status_code=404, detail=f"Algorithm '{name}' not found")
    source = mgr.get_algorithm_source(name)
    if source is None:
        raise HTTPException(status_code=404, detail="Source file not found")
    filepath = mgr._find_file_for_algorithm(name)
    return {
        "name": name,
        "source": source,
        "filename": filepath.name if filepath else "unknown.py",
    }


@router.post("", response_model=UploadResponse, status_code=201)
async def upload_algorithm(
    file: UploadFile = File(...),
    filename: str = Form(None),
):
    """Upload a new algorithm Python file.

    The file must contain exactly one class that subclasses StrategyAlgorithm.
    """
    mgr = get_manager()

    content = await file.read()
    try:
        source = content.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(status_code=400, detail="File must be valid UTF-8 Python source")

    # Basic validation
    if "StrategyAlgorithm" not in source:
        raise HTTPException(
            status_code=400,
            detail="File must contain a class that subclasses StrategyAlgorithm",
        )

    fname = filename or file.filename or "algorithm.py"
    name = mgr.save_algorithm_file(fname, source)
    if name is None:
        raise HTTPException(
            status_code=400,
            detail="Failed to load algorithm. Ensure the file contains a valid "
            "StrategyAlgorithm subclass with name(), description(), "
            "default_params(), param_schema(), and analyze() methods.",
        )

    return {"name": name, "message": f"Algorithm '{name}' loaded successfully"}


@router.put("/{name}", response_model=UploadResponse)
async def update_algorithm(
    name: str,
    file: UploadFile = File(...),
):
    """Update an existing algorithm by uploading a new version."""
    mgr = get_manager()

    if not mgr.registry.has(name):
        raise HTTPException(status_code=404, detail=f"Algorithm '{name}' not found")

    content = await file.read()
    try:
        source = content.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(status_code=400, detail="File must be valid UTF-8 Python source")

    if "StrategyAlgorithm" not in source:
        raise HTTPException(
            status_code=400,
            detail="File must contain a class that subclasses StrategyAlgorithm",
        )

    # Remove old, save new
    filepath = mgr._find_file_for_algorithm(name)
    fname = filepath.name if filepath else f"{name}.py"

    # Remove old registration
    mgr.remove_algorithm(name)

    # Save and load new version
    loaded_name = mgr.save_algorithm_file(fname, source)
    if loaded_name is None:
        raise HTTPException(
            status_code=400,
            detail="Failed to load updated algorithm. The previous version has been removed.",
        )

    return {"name": loaded_name, "message": f"Algorithm '{loaded_name}' updated successfully"}


class UpdateSourceRequest(BaseModel):
    source: str


@router.patch("/{name}/source", response_model=UploadResponse)
def update_algorithm_source(name: str, body: UpdateSourceRequest):
    """Update an algorithm's source code from inline editor."""
    mgr = get_manager()

    if not mgr.registry.has(name):
        raise HTTPException(status_code=404, detail=f"Algorithm '{name}' not found")

    if "StrategyAlgorithm" not in body.source:
        raise HTTPException(
            status_code=400,
            detail="Source must contain a class that subclasses StrategyAlgorithm",
        )

    filepath = mgr._find_file_for_algorithm(name)
    fname = filepath.name if filepath else f"{name}.py"

    # Remove old registration
    mgr.remove_algorithm(name)

    # Save and load new version
    loaded_name = mgr.save_algorithm_file(fname, body.source)
    if loaded_name is None:
        raise HTTPException(
            status_code=400,
            detail="Failed to load updated algorithm. Check that the source is valid Python "
            "with a StrategyAlgorithm subclass implementing name(), description(), "
            "default_params(), param_schema(), and analyze().",
        )

    return {"name": loaded_name, "message": f"Algorithm '{loaded_name}' updated successfully"}


@router.delete("/{name}", status_code=204)
def delete_algorithm(name: str):
    """Delete an algorithm and its file."""
    mgr = get_manager()

    if not mgr.registry.has(name):
        raise HTTPException(status_code=404, detail=f"Algorithm '{name}' not found")

    if not mgr.remove_algorithm(name):
        raise HTTPException(status_code=500, detail="Failed to remove algorithm")
