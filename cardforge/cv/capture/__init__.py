"""Pacote cardforge.cv.capture — aquisição de imagem (CV-CL-01)."""

from .camera import CameraService, FileCameraService, Frame, sharpness

__all__ = ["CameraService", "FileCameraService", "Frame", "sharpness"]
