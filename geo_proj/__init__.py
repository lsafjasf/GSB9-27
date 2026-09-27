"""投影与球面度量库（仅标准库）。

角度对外一律使用十进制度（D.DD），内部使用弧度。
坐标约定：纬度 lat ∈ [-90, 90]，经度 lon ∈ (-180, 180]。
"""

from .core import (
    EARTH_MEAN_RADIUS,
    WGS84_A,
    Equirectangular,
    LambertCylindricalEqualArea,
    clamp_lon,
    wrap_delta_deg,
    great_circle_distance,
    haversine,
    path_length,
    spherical_polygon_area,
    planar_polygon_area,
    roundtrip_errors,
)

__all__ = [
    "EARTH_MEAN_RADIUS",
    "WGS84_A",
    "Equirectangular",
    "LambertCylindricalEqualArea",
    "clamp_lon",
    "wrap_delta_deg",
    "great_circle_distance",
    "haversine",
    "path_length",
    "spherical_polygon_area",
    "planar_polygon_area",
    "roundtrip_errors",
]
