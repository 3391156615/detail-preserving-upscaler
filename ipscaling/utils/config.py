"""配置管理：路径常量、默认配置、算法元数据与参数 Schema、持久化读写。

约定：
- 所有"可调参数"集中在本模块声明，GUI 依据 PARAM_SCHEMA 动态生成控件，
  引擎依据同一份默认值兜底，二者不会漂移；
- 用户配置写入 configs/user_settings.json，系统默认写在 configs/default_config.json，
  读取时以默认配置为底做深合并，保证旧版本配置缺字段也能启动。
"""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------- 路径常量

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = PROJECT_ROOT / "configs"
WEIGHTS_DIR = PROJECT_ROOT / "weights"
DATA_DIR = PROJECT_ROOT / "data"
RESULTS_DIR = PROJECT_ROOT / "results"
LOGS_DIR = PROJECT_ROOT / "logs"
DIST_DIR = PROJECT_ROOT / "dist"
CACHE_DIR = DATA_DIR / "cache"

DEFAULT_CONFIG_FILE = CONFIG_DIR / "default_config.json"
USER_CONFIG_FILE = CONFIG_DIR / "user_settings.json"

MANAGED_DIRS = (CONFIG_DIR, WEIGHTS_DIR, DATA_DIR, RESULTS_DIR, LOGS_DIR, DIST_DIR, CACHE_DIR)


def ensure_directories() -> None:
    """创建运行时所需的全部目录（幂等）。"""
    for directory in MANAGED_DIRS:
        try:
            directory.mkdir(parents=True, exist_ok=True)
        except OSError:  # 只读介质等极端情况：交由上层日志记录
            continue


# ---------------------------------------------------------------- 默认配置

DEFAULT_SETTINGS: Dict[str, Any] = {
    "version": 1,
    "gui": {
        "theme": "light",
        "accent": "#1a6fd4",
        "font_scale": 1.0,
        "window": {"width": 1480, "height": 920},
        "show_log_panel": True,
        "show_left_panel": True,
        "bottom_height": 330,
        "auto_preview": True,
    },
    "output": {
        "root": "results",
        "group_by_date": True,
        "group_by_algorithm": True,
        "format": "png",
        "jpeg_quality": 95,
        "save_comparison": True,
    },
    "logging": {"level": "INFO", "max_mb": 2, "backup_count": 5},
    "performance": {
        "auto_tiling": True,
        "tile_threshold_mp": 12.0,
        "tile_size": 1024,
        "tile_overlap": 32,
    },
    "evaluation": {
        "metric_reference": "auto",
        "auto_reference_scale": 2,
        "psnr_peak": 255.0,
    },
    "algorithms": {
        "nearest": {},
        "bilinear": {},
        "bicubic": {"a": -0.75},
        "lanczos": {"a": 3},
        "area": {},
        "nedi": {"window": 8, "strength": 0.35, "sharpen": 0.0},
        "guided": {"radius": 8, "eps": 1e-4, "detail_gain": 0.85, "prefilter": "cubic"},
        "onnx": {"model": "auto", "tile": 256, "overlap": 16, "fallback": "bicubic"},
    },
}


# ---------------------------------------------------------------- 算法目录
# category: baseline / edge / deep —— 用于 GUI 分组
# scales  : 支持的缩放区间描述（任意倍数 / 整数倍）

ALGORITHM_CATALOG: List[Dict[str, Any]] = [
    {
        "key": "nearest",
        "name": "最近邻 (Nearest)",
        "category": "baseline",
        "blurb": "速度最快，块状伪影明显，仅作对照基线。",
        "arbitrary_scale": True,
        "supports_upscale": True,
        "supports_downscale": False,
    },
    {
        "key": "bilinear",
        "name": "双线性 (Bilinear)",
        "category": "baseline",
        "blurb": "线性加权插值，边缘易发虚。",
        "arbitrary_scale": True,
        "supports_upscale": True,
        "supports_downscale": True,
    },
    {
        "key": "bicubic",
        "name": "双三次 (Bicubic)",
        "category": "baseline",
        "blurb": "经典三次卷积基线，性价比高，但边缘会过冲。",
        "arbitrary_scale": True,
        "supports_upscale": True,
        "supports_downscale": True,
    },
    {
        "key": "lanczos",
        "name": "Lanczos",
        "category": "baseline",
        "blurb": "窗化 sinc 插值，锐度高，细节丰富但可能振铃。",
        "arbitrary_scale": True,
        "supports_upscale": True,
        "supports_downscale": True,
    },
    {
        "key": "area",
        "name": "区域平均 (Area)",
        "category": "baseline",
        "blurb": "专用于缩小，按覆盖面积加权，天然抗走样。",
        "arbitrary_scale": True,
        "supports_upscale": False,
        "supports_downscale": True,
    },
    {
        "key": "nedi",
        "name": "NEDI 边缘导向",
        "category": "edge",
        "blurb": "用结构张量估计边缘方向，沿切线方向重采样并与 Lanczos 基线融合，抑制锯齿。",
        "arbitrary_scale": False,
        "supports_upscale": True,
        "supports_downscale": False,
    },
    {
        "key": "guided",
        "name": "引导滤波上采样",
        "category": "edge",
        "blurb": "以边缘为引导做保边联合上采样，再回注细节层。",
        "arbitrary_scale": True,
        "supports_upscale": True,
        "supports_downscale": True,
    },
    {
        "key": "onnx",
        "name": "深度学习超分 (ONNX)",
        "category": "deep",
        "blurb": "加载 weights/ 下的亚像素卷积模型，CPU 推理。",
        "arbitrary_scale": False,
        "supports_upscale": True,
        "supports_downscale": False,
    },
]


# ---------------------------------------------------------------- 参数 Schema
# kind: int / float / choice / bool
# 每个控件都会自动刷新预览，并在配置中持久化

PARAM_SCHEMA: Dict[str, List[Dict[str, Any]]] = {
    "nearest": [],
    "bilinear": [],
    "bicubic": [
        {"key": "a", "label": "三次核系数 a", "kind": "float", "minimum": -1.0, "maximum": 0.0,
         "default": -0.75, "step": 0.05, "decimals": 2,
         "tip": "a = -0.75 为 OpenCV 经典值；越接近 0 越锐、过冲越明显。"},
    ],
    "lanczos": [
        {"key": "a", "label": "窗半径 a", "kind": "choice", "choices": [2, 3, 4, 5],
         "default": 3, "tip": "a 越大越锐，振铃风险也越高。"},
    ],
    "area": [],
    "nedi": [
        {"key": "window", "label": "方向统计窗口", "kind": "choice", "choices": [4, 6, 8, 12],
         "default": 8, "tip": "结构张量的平滑半径来源，窗口越大方向估计越稳，边缘定位分辨率下降。"},
        {"key": "strength", "label": "边缘导向强度", "kind": "float", "minimum": 0.0, "maximum": 1.0,
         "default": 0.35, "step": 0.05, "decimals": 2,
         "tip": "沿边缘切线方向重采样的融合比例：越大越抑制锯齿，过大则偏软。"},
        {"key": "sharpen", "label": "细节增强", "kind": "float", "minimum": 0.0, "maximum": 1.0,
         "default": 0.0, "step": 0.05, "decimals": 2,
         "tip": "对高频层做额外的非锐化掩蔽强度。"},
    ],
    "guided": [
        {"key": "radius", "label": "引导半径 r", "kind": "int", "minimum": 1, "maximum": 64,
         "default": 8, "step": 1, "tip": "决定保边的空间尺度。"},
        {"key": "eps", "label": "正则 ε", "kind": "float", "minimum": 1e-6, "maximum": 1e-1,
         "default": 1e-4, "step": 1e-5, "decimals": 6, "log_scale": True,
         "tip": "ε 越大越平滑，越小越贴近引导图细节。"},
        {"key": "detail_gain", "label": "细节回注增益", "kind": "float", "minimum": 0.0,
         "maximum": 1.5, "default": 0.85, "step": 0.05, "decimals": 2,
         "tip": "把双三次高频层按此增益叠加回引导滤波结果。"},
        {"key": "prefilter", "label": "预滤波", "kind": "choice",
         "choices": ["cubic", "lanczos", "none"], "default": "cubic",
         "tip": "上采样前的抗混叠预滤波选择。"},
    ],
    "onnx": [
        {"key": "model", "label": "模型文件", "kind": "choice", "choices": ["auto"],
         "default": "auto", "dynamic": True,
         "tip": "auto 表示按缩放倍率自动匹配 weights/ 下的模型。"},
        {"key": "tile", "label": "推理分块尺寸", "kind": "choice", "choices": [128, 192, 256, 384, 512],
         "default": 256, "tip": "大图必须分块推理，否则内存占用会线性膨胀。"},
        {"key": "overlap", "label": "分块重叠像素", "kind": "choice", "choices": [8, 16, 24, 32],
         "default": 16, "tip": "消除分块接缝，重叠越大越慢。"},
        {"key": "fallback", "label": "不可用时的回退", "kind": "choice",
         "choices": ["bicubic", "lanczos"], "default": "bicubic",
         "tip": "当倍率不匹配或缺模型时使用的传统算法。"},
    ],
}


def catalog_entry(key: str) -> Optional[Dict[str, Any]]:
    for entry in ALGORITHM_CATALOG:
        if entry["key"] == key:
            return entry
    return None


def display_name(key: str) -> str:
    entry = catalog_entry(key)
    return entry["name"] if entry else key


# ---------------------------------------------------------------- 配置读写


def _deep_merge(base: Dict[str, Any], patch: Dict[str, Any]) -> Dict[str, Any]:
    result = copy.deepcopy(base)
    for key, value in (patch or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def read_json(path: Path | str, default: Any = None) -> Any:
    try:
        with open(path, "r", encoding="utf-8-sig") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError):
        return default


def write_json(path: Path | str, payload: Any) -> bool:
    target = Path(path)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(target.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        os.replace(tmp, target)
        return True
    except OSError:
        return False


class Config:
    """点分路径访问的配置对象，线程内只读为主，写操作走 save()。"""

    def __init__(self, data: Optional[Dict[str, Any]] = None) -> None:
        self._data = _deep_merge(DEFAULT_SETTINGS, data or {})

    # -- 字典协议
    def __getitem__(self, key: str) -> Any:
        return self.get(key)

    def __contains__(self, key: str) -> bool:
        return self.get(key, KeyError) is not KeyError

    # -- 读取
    def get(self, path: str, default: Any = None) -> Any:
        node: Any = self._data
        for part in path.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    # -- 写入
    def set(self, path: str, value: Any) -> None:
        parts = path.split(".")
        node = self._data
        for part in parts[:-1]:
            node = node.setdefault(part, {})
            if not isinstance(node, dict):
                raise TypeError(f"配置路径 {path} 的类型冲突")
        node[parts[-1]] = value

    def update(self, patch: Dict[str, Any]) -> None:
        self._data = _deep_merge(self._data, patch)

    def algorithm_params(self, key: str) -> Dict[str, Any]:
        params = copy.deepcopy(self.get(f"algorithms.{key}", {}) or {})
        for spec in PARAM_SCHEMA.get(key, []):
            if spec.get("dynamic"):
                continue
            params.setdefault(spec["key"], spec.get("default"))
        return params

    # -- 持久化
    @classmethod
    def load(cls, path: Path | str = USER_CONFIG_FILE) -> "Config":
        return cls(read_json(path, {}) or {})

    def save(self, path: Path | str = USER_CONFIG_FILE) -> bool:
        return write_json(path, self._data)


def default_config() -> Config:
    return Config()

