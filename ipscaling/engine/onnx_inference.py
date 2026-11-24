"""ONNX 深度学习超分推理（CPU / onnxruntime）。

设计：
- 自动扫描 weights/*.onnx，从文件名（_x2/_x3/_x4）或 ONNX 元数据读取缩放倍率；
- 按倍率自动匹配模型（auto），也可在参数中显式指定；
- 大图按 tile 分块推理 + overlap 拼接，避免一次性内存爆炸；
- 模型缺失 / onnxruntime 缺失 / 倍率不匹配时优雅降级，由上层回退到传统算法；
- 提供模型清单（manifest）用于交付清单校验。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np

from ..utils import config as config_module
from ..utils.logger import get_logger
from .base import BaseEngine, prefilter_antialias
from .classic import cubic_kernel, lanczos_kernel, resample_separable

_LOG = get_logger("ipscaling.engine.onnx")

try:  # onnxruntime 属于生产依赖，但缺失时不应导致整个程序无法启动
    import onnxruntime as ort

    _ORT_AVAILABLE = True
except Exception:  # noqa: BLE001
    ort = None  # type: ignore[assignment]
    _ORT_AVAILABLE = False

_SCALE_PATTERN = re.compile(r"[_.-]x(\d+)(?:\D|$)", re.IGNORECASE)


def discover_models(weights_dir: Path | str | None = None) -> List[Dict[str, Any]]:
    """扫描模型目录，返回 [{name, path, scale, size_mb, description}]。"""
    directory = Path(weights_dir) if weights_dir else config_module.WEIGHTS_DIR
    models: List[Dict[str, Any]] = []
    if not directory.exists():
        return models
    for path in sorted(directory.glob("*.onnx")):
        scale = None
        match = _SCALE_PATTERN.search(path.stem)
        if match:
            scale = int(match.group(1))
        description = ""
        sidecar = path.with_suffix(".json")
        if sidecar.exists():
            try:
                meta = json.loads(sidecar.read_text(encoding="utf-8"))
                scale = int(meta.get("scale", scale)) if meta.get("scale") else scale
                description = str(meta.get("description", ""))
            except (OSError, ValueError, TypeError):
                pass
        models.append({
            "name": path.name,
            "path": str(path),
            "scale": scale,
            "size_mb": round(path.stat().st_size / (1024 * 1024), 3),
            "description": description,
        })
    return models


def pick_model(models: List[Dict[str, Any]], scale: int) -> Optional[Dict[str, Any]]:
    for model in models:
        if model.get("scale") == scale:
            return model
    return None


class OnnxSuperResolutionEngine(BaseEngine):
    key = "onnx"
    display_name = "深度学习超分 (ONNX)"
    category = "deep"
    supports_upscale = True
    supports_downscale = False
    arbitrary_scale = False
    integer_scales = (2, 3, 4)
    internal_tiling = True

    def __init__(self, config: Any = None) -> None:
        super().__init__(config)
        self._sessions: Dict[str, Any] = {}
        self._models: Optional[List[Dict[str, Any]]] = None

    # ------------------------------------------------------------ 模型管理

    @property
    def models(self) -> List[Dict[str, Any]]:
        if self._models is None:
            self._models = discover_models()
        return self._models

    def _load_session(self, model_path: str):
        if model_path in self._sessions:
            return self._sessions[model_path]
        if not _ORT_AVAILABLE:
            raise RuntimeError("缺少 onnxruntime，无法执行深度学习超分")
        options = ort.SessionOptions()
        options.intra_op_num_threads = 0
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        session = ort.InferenceSession(model_path, sess_options=options, providers=["CPUExecutionProvider"])
        self._sessions[model_path] = session
        return session

    def resolve_model(self, params: Dict[str, Any], target_scale: int) -> Optional[Dict[str, Any]]:
        choice = str((params or {}).get("model", "auto"))
        if choice and choice != "auto":
            for model in self.models:
                if model["name"] == choice:
                    return model
            return None
        return pick_model(self.models, target_scale)

    # ------------------------------------------------------------ 能力检查

    def unavailable_reason(self, plan) -> str:
        reason = super().unavailable_reason(plan)
        if reason:
            return reason
        if not _ORT_AVAILABLE:
            return "未检测到 onnxruntime，无法执行深度学习超分"
        if not self.models:
            return "weights/ 目录下没有可用的 .onnx 模型"
        if plan.direction == "identity":
            return ""
        if not plan.is_integer_scale:
            return f"超分模型仅支持整数倍（当前 {plan.scale_label}）"
        scale = int(round(plan.scale_x))
        if self.resolve_model({}, scale) is None:
            available = "、".join(f"{m['scale']}x" for m in self.models if m.get("scale"))
            return f"没有匹配 {scale}x 的模型（现有：{available or '无'}）"
        return ""

    def adaptive_params(self, image: np.ndarray, plan, params: Dict[str, Any]) -> Dict[str, Any]:
        merged = super().adaptive_params(image, plan, params)
        merged["tile"] = int(np.clip(int(merged.get("tile", 256)), 64, 1024))
        merged["overlap"] = int(np.clip(int(merged.get("overlap", 16)), 0, 128))
        merged["fallback"] = str(merged.get("fallback", "bicubic"))
        return merged

    # ------------------------------------------------------------ 推理

    def _process_block(self, src: np.ndarray, dst_size: Tuple[int, int], params: Dict[str, Any]) -> np.ndarray:
        dst_w, dst_h = int(dst_size[0]), int(dst_size[1])
        scale = max(1, int(round(dst_w / max(1, src.shape[1]))))
        model = self.resolve_model(params, scale)
        if model is None or model.get("scale") is None:
            return self._fallback(src, (dst_w, dst_h), params)

        session = self._load_session(model["path"])
        model_scale = int(model["scale"])
        channels = 1 if src.ndim == 2 else min(3, src.shape[2])

        tile = int(params.get("tile", 256))
        overlap = int(params.get("overlap", 16))

        # 分块推理（模型自带 scale 倍的输出放缩）
        src_h, src_w = src.shape[:2]
        ys = list(range(0, src_h, tile))
        xs = list(range(0, src_w, tile))
        out_h, out_w = src_h * model_scale, src_w * model_scale
        out_shape = (out_h, out_w) if channels == 1 else (out_h, out_w, channels)
        canvas = np.zeros(out_shape, dtype=np.float32)

        for y0 in ys:
            y1 = min(src_h, y0 + tile)
            for x0 in xs:
                x1 = min(src_w, x0 + tile)
                ey0, ey1 = max(0, y0 - overlap), min(src_h, y1 + overlap)
                ex0, ex1 = max(0, x0 - overlap), min(src_w, x1 + overlap)

                crop = src[ey0:ey1, ex0:ex1]
                patch = self._infer_patch(session, crop, channels)
                if patch is None:
                    continue

                # 裁剪重叠区域后贴回
                top = (y0 - ey0) * model_scale
                left = (x0 - ex0) * model_scale
                height = (y1 - y0) * model_scale
                width = (x1 - x0) * model_scale
                piece = patch[top:top + height, left:left + width]
                target = canvas[y0 * model_scale:y1 * model_scale, x0 * model_scale:x1 * model_scale]
                if piece.shape[:2] != target.shape[:2]:
                    piece = cv2.resize(piece, (target.shape[1], target.shape[0]), interpolation=cv2.INTER_LINEAR)
                canvas[y0 * model_scale:y1 * model_scale, x0 * model_scale:x1 * model_scale] = piece

        # 模型倍率与请求倍率不一致时做最后一步重采样
        if (out_w, out_h) != (dst_w, dst_h):
            canvas = self._fallback_resample(canvas, (dst_w, dst_h))
        return np.clip(canvas, 0.0, 1.0)

    def _infer_patch(self, session, crop: np.ndarray, channels: int) -> Optional[np.ndarray]:
        """单块推理：crop 为 float32 [0,1]，返回 float32 [0,1]。"""
        # 模型固定 3 通道输入（灰度/彩色统一转 BGR）
        if channels == 1:
            prepared = np.repeat(crop[:, :, None], 3, axis=2)
        elif crop.shape[2] == 4:
            prepared = crop[:, :, :3]
        else:
            prepared = crop

        model_scale = int(self._model_scale(session))
        # 输入尺寸补齐到 2 的倍数，避免像素重排层整除问题
        pad_multiple = 2
        h, w = prepared.shape[:2]
        pad_h = (-h) % pad_multiple
        pad_w = (-w) % pad_multiple
        if pad_h or pad_w:
            prepared = cv2.copyMakeBorder(prepared, 0, pad_h, 0, pad_w, cv2.BORDER_REFLECT)

        tensor = np.ascontiguousarray(prepared.transpose(2, 0, 1)[None, ...], dtype=np.float32)
        try:
            input_name = session.get_inputs()[0].name
            output = session.run(None, {input_name: tensor})[0]
        except Exception as exc:  # noqa: BLE001 - 推理失败回退
            _LOG.warning("ONNX 推理失败：%s", exc)
            return None

        result = np.clip(output[0], 0.0, 1.0).transpose(1, 2, 0)
        valid_h, valid_w = h * model_scale, w * model_scale
        result = result[:valid_h, :valid_w]
        if channels == 1:
            return cv2.cvtColor(result, cv2.COLOR_BGR2GRAY)[:, :, None]
        return result

    @staticmethod
    def _model_scale(session) -> float:
        meta = session.get_modelmeta().custom_metadata_map or {}
        try:
            return float(meta.get("scale", 0) or 0) or 2.0
        except (TypeError, ValueError):
            return 2.0

    # ------------------------------------------------------------ 降级路径

    def _fallback(self, src: np.ndarray, dst_size: Tuple[int, int], params: Dict[str, Any]) -> np.ndarray:
        mode = str(params.get("fallback", "bicubic"))
        _LOG.warning("ONNX 模型不可用，回退到 %s", mode)
        return self._fallback_resample(src, dst_size, mode)

    @staticmethod
    def _fallback_resample(src: np.ndarray, dst_size: Tuple[int, int], mode: str = "bicubic") -> np.ndarray:
        dst_w, dst_h = int(dst_size[0]), int(dst_size[1])
        if dst_w < src.shape[1] or dst_h < src.shape[0]:
            src = prefilter_antialias(src, dst_w / src.shape[1], dst_h / src.shape[0])
        if mode == "lanczos":
            return resample_separable(src, (dst_w, dst_h), lanczos_kernel(3.0), 3.0, cache_key="lanczos3")
        return resample_separable(src, (dst_w, dst_h), cubic_kernel(-0.75), 2.0, cache_key="cubic-0.750")

    # ------------------------------------------------------------ 元信息

    def info(self) -> Dict[str, Any]:
        data = super().info()
        data["runtime_available"] = _ORT_AVAILABLE
        data["models"] = [m["name"] for m in self.models]
        return data
