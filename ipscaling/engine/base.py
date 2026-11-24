"""插值缩放算法引擎的公共基础。

统一接口约定（所有子类只需实现 `_process_block`）：
    engine.process(image, dst_size=(w, h), params={...}) -> ProcessResult

特性：
1. 目标尺寸输入 —— 支持显式目标尺寸或任意倍率（含非整数倍）；
2. 方向判定 —— 自动识别 upscale / downscale / mixed，缩小路径强制走抗混叠预滤波；
3. 参数自适应 —— `adaptive_params` 依据图像尺寸、倍率、边缘强度调整超参；
4. 自动分块（Tiling）—— 超大图或超大输出时按目标域切块，源域外扩后贴回，
   防止一次性分配造成的内存峰值；分块对 GUI 提供进度回调；
5. 性能埋点 —— 逐块计时、分块数、有效吞吐（MPix/s）。
"""

from __future__ import annotations

import math
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

import cv2
import numpy as np

from ..utils.evaluator import to_float, to_uint8

ProgressCallback = Optional[Callable[[int, int], None]]


class EngineUnavailable(Exception):
    """当前算法无法处理该请求（倍率不支持、缺模型等）。"""

    def __init__(self, message: str, fallback_hint: str = "bicubic") -> None:
        super().__init__(message)
        self.fallback_hint = fallback_hint


@dataclass(frozen=True)
class ScalePlan:
    """一次缩放请求的完整描述。"""

    src_size: Tuple[int, int]
    dst_size: Tuple[int, int]
    scale_x: float
    scale_y: float
    direction: str

    @classmethod
    def create(
        cls,
        src_size: Tuple[int, int],
        dst_size: Optional[Tuple[int, int]] = None,
        scale: Optional[float | Tuple[float, float]] = None,
        min_side: int = 1,
    ) -> "ScalePlan":
        src_w, src_h = int(src_size[0]), int(src_size[1])
        if dst_size is not None:
            dst_w = max(min_side, int(round(dst_size[0])))
            dst_h = max(min_side, int(round(dst_size[1])))
        else:
            if isinstance(scale, (tuple, list)):
                scale_x, scale_y = float(scale[0]), float(scale[1])
            else:
                scale_x = scale_y = float(scale if scale is not None else 1.0)
            dst_w = max(min_side, int(round(src_w * scale_x)))
            dst_h = max(min_side, int(round(src_h * scale_y)))
        scale_x = dst_w / max(1, src_w)
        scale_y = dst_h / max(1, src_h)
        return cls((src_w, src_h), (dst_w, dst_h), scale_x, scale_y, cls._direction(scale_x, scale_y))

    @staticmethod
    def _direction(scale_x: float, scale_y: float) -> str:
        up = scale_x > 1.0 + 1e-9 or scale_y > 1.0 + 1e-9
        down = scale_x < 1.0 - 1e-9 or scale_y < 1.0 - 1e-9
        if up and down:
            return "mixed"
        if down:
            return "downscale"
        if up:
            return "upscale"
        return "identity"

    @property
    def scale_label(self) -> str:
        def _fmt(value: float) -> str:
            return f"{value:.2f}".rstrip("0").rstrip(".") if abs(value - round(value)) > 1e-6 else str(int(round(value)))

        if abs(self.scale_x - self.scale_y) < 1e-6:
            return f"{_fmt(self.scale_x)}x"
        label_x, label_y = _fmt(self.scale_x), _fmt(self.scale_y)
        if label_x == label_y:
            # 两轴四舍五入到两位后相同（如 113/451 与 75/300），按单倍率显示，避免“0.25x / 0.25x”
            return f"{label_x}x"
        return f"{label_x}x / {label_y}x"

    @property
    def is_integer_scale(self) -> bool:
        return (abs(self.scale_x - round(self.scale_x)) < 1e-6
                and abs(self.scale_y - round(self.scale_y)) < 1e-6)

    @property
    def megapixels(self) -> float:
        """输出图像的总像素数（百万像素），用于判断是否需要分块处理。"""
        return (self.dst_size[0] * self.dst_size[1]) / 1_000_000.0


@dataclass
class ProcessResult:
    """算法执行结果。"""

    image: np.ndarray
    algorithm: str
    display_name: str
    plan: ScalePlan
    elapsed_ms: float
    params: Dict[str, Any] = field(default_factory=dict)
    tiles: int = 1
    notes: List[str] = field(default_factory=list)
    fallback_from: Optional[str] = None

    @property
    def megapixels(self) -> float:
        w, h = self.plan.dst_size
        return (w * h) / 1e6


class BaseEngine(ABC):
    """所有插值引擎的基类。"""

    key: str = "base"
    display_name: str = "基础引擎"
    category: str = "baseline"
    supports_upscale: bool = True
    supports_downscale: bool = True
    arbitrary_scale: bool = True
    integer_scales: Tuple[int, ...] = ()
    # 引擎自带内部分块（如 ONNX 推理），外层不必再切
    internal_tiling: bool = False

    def __init__(self, config: Any = None) -> None:
        self.config = config

    # ------------------------------------------------------------ 能力查询

    def check_plan(self, plan: ScalePlan) -> None:
        """不满足条件时抛出 EngineUnavailable，附带回退建议。"""
        reason = self.unavailable_reason(plan)
        if reason:
            raise EngineUnavailable(reason, self.key)

    def unavailable_reason(self, plan: ScalePlan) -> str:
        if plan.direction == "upscale" and not self.supports_upscale:
            return f"{self.display_name} 不支持放大"
        if plan.direction == "downscale" and not self.supports_downscale:
            return f"{self.display_name} 不支持缩小"
        if plan.direction == "mixed" and not (self.supports_upscale and self.supports_downscale):
            return f"{self.display_name} 不支持各向异性缩放"
        if not self.arbitrary_scale and not self._scale_supported(plan):
            allowed = "、".join(f"{s}x" for s in self.integer_scales) or "整数倍"
            return f"{self.display_name} 仅支持 {allowed}，当前请求为 {plan.scale_label}"
        if plan.direction == "identity":
            return ""
        return ""

    def _scale_supported(self, plan: ScalePlan) -> bool:
        if not plan.is_integer_scale:
            return False
        if not self.integer_scales:
            return True
        sx, sy = int(round(plan.scale_x)), int(round(plan.scale_y))
        return sx in self.integer_scales and sy in self.integer_scales

    def is_available(self, plan: ScalePlan) -> bool:
        return not self.unavailable_reason(plan)

    # ------------------------------------------------------------ 参数自适应

    def adaptive_params(self, image: np.ndarray, plan: ScalePlan, params: Dict[str, Any]) -> Dict[str, Any]:
        """依据输入图像与倍率微调参数（子类覆盖）。"""
        merged = dict(params or {})
        merged.setdefault("_plan", plan)
        return merged

    # ------------------------------------------------------------ 主流程

    def process(
        self,
        image: np.ndarray,
        dst_size: Optional[Tuple[int, int]] = None,
        scale: Optional[float | Tuple[float, float]] = None,
        params: Optional[Dict[str, Any]] = None,
        progress: ProgressCallback = None,
    ) -> ProcessResult:
        if image is None or image.size == 0:
            raise ValueError("输入图像为空")

        src = image if image.dtype in (np.uint8, np.uint16, np.float32, np.float64) else image.astype(np.float32)
        plan = ScalePlan.create((src.shape[1], src.shape[0]), dst_size, scale)
        self.check_plan(plan)

        raw_params = dict(params or {})
        work_params = self.adaptive_params(src, plan, raw_params)
        work = to_float(src)
        notes: List[str] = []

        started = time.perf_counter()
        if plan.direction == "identity":
            out = work.copy()
            tiles = 1
            notes.append("倍率为 1，直接返回原图")
        elif self._needs_tiling(plan):
            out, tiles = self._process_tiled(work, plan, work_params, progress)
        else:
            out, tiles = self._process_block(work, plan.dst_size, work_params), 1
        elapsed_ms = (time.perf_counter() - started) * 1000.0

        out = np.asarray(out)
        if out.shape[1] != plan.dst_size[0] or out.shape[0] != plan.dst_size[1]:
            # 兜底校正：引擎内部四舍五入导致 ±1 像素偏差时强制对齐
            out = self._force_size(out, plan.dst_size)

        out = np.clip(out, 0.0, 1.0)
        if src.dtype == np.uint8:
            out = to_uint8(out)
        elif src.dtype == np.uint16:
            out = np.clip(np.rint(out * 65535.0), 0, 65535).astype(np.uint16)
        else:
            out = out.astype(np.float32)

        return ProcessResult(
            image=out,
            algorithm=self.key,
            display_name=self.display_name,
            plan=plan,
            elapsed_ms=elapsed_ms,
            params=raw_params,
            tiles=tiles,
            notes=notes,
        )

    # ------------------------------------------------------------ 分块处理

    def _needs_tiling(self, plan: ScalePlan) -> bool:
        if self.internal_tiling:
            return False
        if self.config is None:
            threshold_mp = 8.0
            auto = True
        else:
            auto = bool(self.config.get("performance.auto_tiling", True))
            threshold_mp = float(self.config.get("performance.tile_threshold_mp", 8.0) or 8.0)
        if not auto:
            return False
        return plan.megapixels > threshold_mp

    def _tile_spec(self, plan: ScalePlan) -> Tuple[int, int]:
        """返回 (目标域块尺寸, 目标域重叠像素)。"""
        tile_size = 1024
        overlap = 32
        if self.config is not None:
            tile_size = int(self.config.get("performance.tile_size", 1024) or 1024)
            overlap = int(self.config.get("performance.tile_overlap", 32) or 32)
        tile_size = max(64, tile_size)
        overlap = max(0, min(overlap, tile_size // 4))
        return tile_size, overlap

    def _process_tiled(
        self,
        work: np.ndarray,
        plan: ScalePlan,
        params: Dict[str, Any],
        progress: ProgressCallback = None,
    ) -> Tuple[np.ndarray, int]:
        tile_size, overlap = self._tile_spec(plan)
        dst_w, dst_h = plan.dst_size
        src_h, src_w = work.shape[:2]
        channels = work.shape[2] if work.ndim == 3 else 0

        out_shape = (dst_h, dst_w, channels) if channels else (dst_h, dst_w)
        out = np.empty(out_shape, dtype=np.float32)

        xs = list(range(0, dst_w, tile_size))
        ys = list(range(0, dst_h, tile_size))
        total = len(xs) * len(ys)
        done = 0

        for y0 in ys:
            y1 = min(dst_h, y0 + tile_size)
            for x0 in xs:
                x1 = min(dst_w, x0 + tile_size)

                ex0, ex1 = max(0, x0 - overlap), min(dst_w, x1 + overlap)
                ey0, ey1 = max(0, y0 - overlap), min(dst_h, y1 + overlap)

                # 目标域 → 源域（外扩映射，保证边界像素参与计算）
                sx0 = int(math.floor(ex0 / plan.scale_x))
                sx1 = int(math.ceil(ex1 / plan.scale_x))
                sy0 = int(math.floor(ey0 / plan.scale_y))
                sy1 = int(math.ceil(ey1 / plan.scale_y))
                sx0 = max(0, min(sx0, src_w - 1))
                sy0 = max(0, min(sy0, src_h - 1))
                sx1 = max(sx0 + 1, min(sx1, src_w))
                sy1 = max(sy0 + 1, min(sy1, src_h))

                src_roi = work[sy0:sy1, sx0:sx1]
                block_dst = (ex1 - ex0, ey1 - ey0)
                block = self._process_block(src_roi, block_dst, params)
                block = np.asarray(block)

                ox, oy = x0 - ex0, y0 - ey0
                piece = block[oy:oy + (y1 - y0), ox:ox + (x1 - x0)]
                if piece.shape[:2] != (y1 - y0, x1 - x0):
                    piece = self._force_size(piece, (x1 - x0, y1 - y0))
                out[y0:y1, x0:x1] = piece

                done += 1
                if progress is not None:
                    try:
                        progress(done, total)
                    except Exception:  # noqa: BLE001 - 进度回调异常不应中断计算
                        pass
        return out, total

    @staticmethod
    def _force_size(image: np.ndarray, dst_size: Tuple[int, int]) -> np.ndarray:
        dst_w, dst_h = int(dst_size[0]), int(dst_size[1])
        if image.shape[1] == dst_w and image.shape[0] == dst_h:
            return image
        interp = cv2.INTER_AREA if (image.shape[1] > dst_w or image.shape[0] > dst_h) else cv2.INTER_LINEAR
        return cv2.resize(image, (dst_w, dst_h), interpolation=interp)

    # ------------------------------------------------------------ 子类实现

    @abstractmethod
    def _process_block(self, src: np.ndarray, dst_size: Tuple[int, int], params: Dict[str, Any]) -> np.ndarray:
        """处理单个块：输入输出均为 float32 且值域 [0, 1]。"""

    # ------------------------------------------------------------ 工具方法

    def info(self) -> Dict[str, Any]:
        return {
            "key": self.key,
            "name": self.display_name,
            "category": self.category,
            "supports_upscale": self.supports_upscale,
            "supports_downscale": self.supports_downscale,
            "arbitrary_scale": self.arbitrary_scale,
            "integer_scales": list(self.integer_scales),
        }


def prefilter_antialias(image: np.ndarray, scale_x: float, scale_y: float) -> np.ndarray:
    """缩小前的抗混叠低通：截止频率与目标采样率匹配（σ = 0.5/scale）。"""
    factor = min(scale_x, scale_y)
    if factor >= 1.0 - 1e-9:
        return image
    sigma = float(np.clip(0.5 / max(factor, 1e-3), 0.3, 8.0))
    return cv2.GaussianBlur(image, (0, 0), sigmaX=sigma, sigmaY=sigma, borderType=cv2.BORDER_REFLECT)
