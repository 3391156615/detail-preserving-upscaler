"""算法引擎注册表：GUI 通过它枚举全部可用算法。"""

from __future__ import annotations

from typing import Dict, List, Optional

from ..utils import config as config_module
from .base import BaseEngine, EngineUnavailable, ProcessResult, ScalePlan
from .classic import AreaEngine, BicubicEngine, BilinearEngine, LanczosEngine, NearestEngine
from .edge_directed import GuidedFilterEngine, NediEngine
from .onnx_inference import OnnxSuperResolutionEngine

ENGINE_CLASSES = (
    NearestEngine,
    BilinearEngine,
    BicubicEngine,
    LanczosEngine,
    AreaEngine,
    NediEngine,
    GuidedFilterEngine,
    OnnxSuperResolutionEngine,
)


class EngineRegistry:
    """引擎工厂 + 缓存。"""

    def __init__(self, config=None) -> None:
        self.config = config
        self._instances: Dict[str, BaseEngine] = {}

    def keys(self) -> List[str]:
        return [cls.key for cls in ENGINE_CLASSES]

    def create(self, key: str) -> BaseEngine:
        if key not in self._instances:
            for cls in ENGINE_CLASSES:
                if cls.key == key:
                    self._instances[key] = cls(self.config)
                    break
            else:
                raise KeyError(f"未知算法：{key}")
        return self._instances[key]

    def get(self, key: str) -> BaseEngine:
        return self.create(key)

    def all_engines(self) -> List[BaseEngine]:
        return [self.create(key) for key in self.keys()]

    def catalog(self) -> List[Dict]:
        result = []
        for engine in self.all_engines():
            entry = dict(config_module.catalog_entry(engine.key) or {})
            entry.update(engine.info())
            result.append(entry)
        return result

    def run_with_fallback(
        self,
        key: str,
        image,
        dst_size=None,
        scale=None,
        params: Optional[Dict] = None,
        progress=None,
        fallback: str = "bicubic",
    ) -> ProcessResult:
        """按 key 执行；不可用时自动回退到 fallback 并在结果中标注。"""
        engine = self.create(key)
        plan = ScalePlan.create((image.shape[1], image.shape[0]), dst_size, scale)
        if not engine.is_available(plan):
            reason = engine.unavailable_reason(plan)
            engine = self.create(fallback)
            result = engine.process(image, dst_size=dst_size, scale=scale, params=params, progress=progress)
            result.notes.append(f"{reason}，已回退到 {engine.display_name}")
            result.fallback_from = key
            return result
        return engine.process(image, dst_size=dst_size, scale=scale, params=params, progress=progress)


__all__ = [
    "EngineRegistry",
    "ENGINE_CLASSES",
    "BaseEngine",
    "EngineUnavailable",
    "ProcessResult",
    "ScalePlan",
]
