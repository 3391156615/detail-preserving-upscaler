"""算法引擎包。"""

from __future__ import annotations

from .base import BaseEngine, EngineUnavailable, ProcessResult, ScalePlan
from .classic import AreaEngine, BicubicEngine, BilinearEngine, LanczosEngine, NearestEngine
from .edge_directed import GuidedFilterEngine, NediEngine
from .onnx_inference import OnnxSuperResolutionEngine
from .registry import EngineRegistry

__all__ = [
    "BaseEngine",
    "EngineUnavailable",
    "ProcessResult",
    "ScalePlan",
    "NearestEngine",
    "BilinearEngine",
    "BicubicEngine",
    "LanczosEngine",
    "AreaEngine",
    "NediEngine",
    "GuidedFilterEngine",
    "OnnxSuperResolutionEngine",
    "EngineRegistry",
]
