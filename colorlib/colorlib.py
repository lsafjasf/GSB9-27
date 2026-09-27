"""colorlib — 颜色空间转换库（仅标准库）。

实现的相互转换：
  1. sRGB (gamma 编码, 8bit)  <->  线性 RGB (float, [0,1])   —— 含 gamma 校正
  2. sRGB (8bit)              <->  HSV (h:[0,360), s/v:[0,1])
  3. sRGB (8bit)              <->  CIE XYZ (D65, 2°)

常数来源（gamma 校正环节）：
  遵循 IEC 61966-2-1 (sRGB 标准) 分段传递函数：
    线性段斜率        12.92
    线性段阈值(线性侧) 0.0031308
    线性段阈值(sRGB侧) 0.04045
    幂指数            1/2.4 与 2.4
    增益/偏移         a=1.055, b=0.055
  XYZ 矩阵为 IEC 61966-2-1 给出的 D65/2° 标准矩阵。

取整规则：
  float -> 8bit 一律使用 round-half-up：floor(x * 255 + 0.5)，
  即 0.5 向上进位（不使用 Python 内置 round 的银行家舍入，
  保证跨平台、跨解释器结果一致且可复现）。

越界策略（mode 参数）：
  mode="clamp"  (默认) 负值裁到 0，超过上限裁到上限；
  mode="strict" 任何越界输入抛出 ValueError。
  NaN / 非数值输入在两种模式下都抛出 ValueError（NaN 无法被合理裁剪）。
"""

import math

__all__ = [
    "srgb8_to_linear", "linear_to_srgb8",
    "srgb8_to_hsv", "hsv_to_srgb8",
    "srgb8_to_xyz", "xyz_to_srgb8",
    "build_gamma_lut", "build_inverse_gamma_lut",
]

# ---------------------------------------------------------------- 常数
_SRGB_SLOPE = 12.92
_SRGB_THRESH_LINEAR = 0.0031308   # 线性侧分段点
_SRGB_THRESH_SRGB = 0.04045       # sRGB 侧分段点
_SRGB_A = 1.055
_SRGB_GAMMA = 2.4

# IEC 61966-2-1: sRGB(D65) <-> XYZ 矩阵
_M_RGB_TO_XYZ = (
    (0.4124, 0.3576, 0.1805),
    (0.2126, 0.7152, 0.0722),
    (0.0193, 0.1192, 0.9505),
)
_M_XYZ_TO_RGB = (
    (3.2406, -1.5372, -0.4986),
    (-0.9689, 1.8758, 0.0415),
    (0.0557, -0.2040, 1.0570),
)


# ---------------------------------------------------------------- 工具
def _iround(x):
    """round-half-up：floor(x + 0.5)。x >= 0。"""
    return math.floor(x + 0.5)


def _check(value, lo, hi, name, mode):
    """按 mode 校验并规整单个数值输入。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("%s: 非数值输入 %r" % (name, value))
    if isinstance(value, float) and math.isnan(value):
        raise ValueError("%s: NaN 输入被拒绝" % name)
    if mode == "strict":
        if value < lo or value > hi:
            raise ValueError("%s: 值 %r 超出 [%s, %s]" % (name, value, lo, hi))
        return float(value)
    # clamp
    if value < lo:
        return float(lo)
    if value > hi:
        return float(hi)
    return float(value)


def _check_rgb8(rgb, mode):
    if len(rgb) != 3:
        raise ValueError("RGB 需要 3 个通道，得到 %d 个" % len(rgb))
    return tuple(_check(c, 0, 255, "RGB通道", mode) for c in rgb)


def _check_linear(rgb, mode):
    if len(rgb) != 3:
        raise ValueError("线性 RGB 需要 3 个通道，得到 %d 个" % len(rgb))
    return tuple(_check(c, 0.0, 1.0, "线性通道", mode) for c in rgb)


# ---------------------------------------------------------------- gamma
def _srgb_encode(c):
    """线性 [0,1] -> sRGB [0,1]（gamma 编码）。"""
    if c <= _SRGB_THRESH_LINEAR:
        return _SRGB_SLOPE * c
    return _SRGB_A * (c ** (1.0 / _SRGB_GAMMA)) - (_SRGB_A - 1.0)


def _srgb_decode(c):
    """sRGB [0,1] -> 线性 [0,1]（gamma 解码）。"""
    if c <= _SRGB_THRESH_SRGB:
        return c / _SRGB_SLOPE
    return ((c + (_SRGB_A - 1.0)) / _SRGB_A) ** _SRGB_GAMMA


def build_gamma_lut():
    """8bit sRGB -> 线性 float 查找表（256 项）。"""
    return tuple(_srgb_decode(i / 255.0) for i in range(256))


def build_inverse_gamma_lut():
    """线性 float 量化 -> 8bit sRGB 查找表（4096 项，步长 1/4095）。"""
    n = 4096
    return tuple(_iround(_srgb_encode(i / (n - 1)) * 255.0) for i in range(n))


_GAMMA_LUT = build_gamma_lut()
_INV_GAMMA_LUT = build_inverse_gamma_lut()


# ---------------------------------------------------------------- sRGB8 <-> 线性
def srgb8_to_linear(rgb, mode="clamp"):
    """(r,g,b) 8bit -> (r,g,b) 线性 float [0,1]。"""
    r, g, b = _check_rgb8(rgb, mode)
    return (_GAMMA_LUT[_iround(r)], _GAMMA_LUT[_iround(g)], _GAMMA_LUT[_iround(b)])


def linear_to_srgb8(rgb, mode="clamp"):
    """(r,g,b) 线性 float [0,1] -> (r,g,b) 8bit。"""
    r, g, b = _check_linear(rgb, mode)
    return (
        _iround(_srgb_encode(r) * 255.0),
        _iround(_srgb_encode(g) * 255.0),
        _iround(_srgb_encode(b) * 255.0),
    )


# ---------------------------------------------------------------- sRGB8 <-> HSV
def srgb8_to_hsv(rgb, mode="clamp"):
    """(r,g,b) 8bit -> (h:[0,360), s:[0,1], v:[0,1])。

    注意：HSV 定义在 gamma 编码后的 sRGB 值上（与常见图形软件一致）。
    """
    r, g, b = _check_rgb8(rgb, mode)
    r, g, b = r / 255.0, g / 255.0, b / 255.0
    mx = max(r, g, b)
    mn = min(r, g, b)
    delta = mx - mn
    v = mx
    s = 0.0 if mx == 0.0 else delta / mx
    if delta == 0.0:
        h = 0.0
    elif mx == r:
        h = 60.0 * (((g - b) / delta) % 6.0)
    elif mx == g:
        h = 60.0 * ((b - r) / delta + 2.0)
    else:
        h = 60.0 * ((r - g) / delta + 4.0)
    return (h, s, v)


def hsv_to_srgb8(hsv, mode="clamp"):
    """(h, s, v) -> (r,g,b) 8bit。h 取模 360，s/v 按 mode 处理。"""
    if len(hsv) != 3:
        raise ValueError("HSV 需要 3 个分量，得到 %d 个" % len(hsv))
    h, s, v = hsv
    if isinstance(h, float) and math.isnan(h):
        raise ValueError("H: NaN 输入被拒绝")
    h = float(h) % 360.0
    s = _check(s, 0.0, 1.0, "S", mode)
    v = _check(v, 0.0, 1.0, "V", mode)
    c = v * s
    x = c * (1.0 - abs((h / 60.0) % 2.0 - 1.0))
    m = v - c
    if h < 60.0:
        rp, gp, bp = c, x, 0.0
    elif h < 120.0:
        rp, gp, bp = x, c, 0.0
    elif h < 180.0:
        rp, gp, bp = 0.0, c, x
    elif h < 240.0:
        rp, gp, bp = 0.0, x, c
    elif h < 300.0:
        rp, gp, bp = x, 0.0, c
    else:
        rp, gp, bp = c, 0.0, x
    return (
        _iround((rp + m) * 255.0),
        _iround((gp + m) * 255.0),
        _iround((bp + m) * 255.0),
    )


# ---------------------------------------------------------------- sRGB8 <-> XYZ
def srgb8_to_xyz(rgb, mode="clamp"):
    """(r,g,b) 8bit -> (x,y,z)，Y 归一化到 [0,1]（白点 Y=1）。"""
    r, g, b = srgb8_to_linear(rgb, mode)
    x = _M_RGB_TO_XYZ[0][0] * r + _M_RGB_TO_XYZ[0][1] * g + _M_RGB_TO_XYZ[0][2] * b
    y = _M_RGB_TO_XYZ[1][0] * r + _M_RGB_TO_XYZ[1][1] * g + _M_RGB_TO_XYZ[1][2] * b
    z = _M_RGB_TO_XYZ[2][0] * r + _M_RGB_TO_XYZ[2][1] * g + _M_RGB_TO_XYZ[2][2] * b
    return (x, y, z)


def xyz_to_srgb8(xyz, mode="clamp"):
    """(x,y,z) -> (r,g,b) 8bit。矩阵逆变换产生的负值/超界值按 mode 处理。"""
    if len(xyz) != 3:
        raise ValueError("XYZ 需要 3 个分量，得到 %d 个" % len(xyz))
    # D65 白点 Z≈1.089，故 XYZ 合法上限取 1.5（而非 1.0）
    x, y, z = (_check(v, 0.0, 1.5, "XYZ分量", mode) for v in xyz)
    r = _M_XYZ_TO_RGB[0][0] * x + _M_XYZ_TO_RGB[0][1] * y + _M_XYZ_TO_RGB[0][2] * z
    g = _M_XYZ_TO_RGB[1][0] * x + _M_XYZ_TO_RGB[1][1] * y + _M_XYZ_TO_RGB[1][2] * z
    b = _M_XYZ_TO_RGB[2][0] * x + _M_XYZ_TO_RGB[2][1] * y + _M_XYZ_TO_RGB[2][2] * z
    # 逆矩阵可能产生轻微越界，此处统一按 mode 收敛到 [0,1]
    return linear_to_srgb8((r, g, b), mode)
