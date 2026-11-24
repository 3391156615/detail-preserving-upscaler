"""ipscaling —— 细节保持的图像插值/缩放系统。

顶层包只做轻量导出，避免在导入时拉起重型依赖（PyQt5 / onnxruntime），
方便单元测试与命令行场景按需加载子模块。
"""

from __future__ import annotations

__version__ = "1.0.0"
__app_name__ = "细节保持的图像插值/缩放系统"
__app_name_en__ = "Detail-Preserving Image Upscaling System"

__all__ = ["__version__", "__app_name__", "__app_name_en__"]
