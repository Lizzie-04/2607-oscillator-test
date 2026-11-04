"""474-bit chip control-word model compatible with Matlab_Scripts/drive.m."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Iterable


Q16_FRACTION_BITS = 16
Q16_SCALE = 1 << Q16_FRACTION_BITS
ALPHA_Q16_WIDTH = 18
COEFFICIENT_Q16_WIDTH = 29


def _parse_decimal(text: str, name: str) -> Decimal:
    value_text = text.strip()
    if not value_text:
        raise ValueError(f"{name} 不能为空")
    try:
        value = Decimal(value_text)
    except InvalidOperation as exc:
        raise ValueError(f"{name} 必须为十进制数值") from exc
    if not value.is_finite():
        raise ValueError(f"{name} 必须为有限十进制数值")
    return value


def _decimal_to_scaled_q16(
    text: str,
    name: str,
    *,
    minimum: Decimal,
    maximum: Decimal,
) -> int:
    value = _parse_decimal(text, name)
    if value < minimum or value > maximum:
        raise ValueError(
            f"{name} 超出Q16范围：允许 {minimum} 到 {maximum}"
        )
    return int((value * Q16_SCALE).to_integral_value(rounding=ROUND_HALF_UP))


def parse_alpha_decimal(text: str, name: str = "ALPHA") -> int:
    """Parse unsigned Q2.16 decimal text into the 18-bit raw ALPHA word."""
    maximum = Decimal((1 << ALPHA_Q16_WIDTH) - 1) / Q16_SCALE
    value = _decimal_to_scaled_q16(
        text,
        name,
        minimum=Decimal(0),
        maximum=maximum,
    )
    if not 0 <= value < (1 << ALPHA_Q16_WIDTH):
        raise ValueError(f"{name} 超出18-bit unsigned Q2.16范围")
    return value


def parse_coefficient_decimal(text: str, name: str) -> int:
    """Parse signed Q12.16 decimal text into a signed 29-bit integer."""
    minimum_scaled = -(1 << (COEFFICIENT_Q16_WIDTH - 1))
    maximum_scaled = (1 << (COEFFICIENT_Q16_WIDTH - 1)) - 1
    value = _decimal_to_scaled_q16(
        text,
        name,
        minimum=Decimal(minimum_scaled) / Q16_SCALE,
        maximum=Decimal(maximum_scaled) / Q16_SCALE,
    )
    if not minimum_scaled <= value <= maximum_scaled:
        raise ValueError(f"{name} 超出29-bit signed Q12.16范围")
    return value


def signed_q16_to_decimal_text(value: int, width: int = COEFFICIENT_Q16_WIDTH) -> str:
    raw = _checked(value, width, "signed_q16")
    sign_bit = 1 << (width - 1)
    if raw & sign_bit:
        raw -= 1 << width
    return scaled_q16_to_decimal_text(raw)


def unsigned_q16_to_decimal_text(value: int, width: int = ALPHA_Q16_WIDTH) -> str:
    return scaled_q16_to_decimal_text(_checked(value, width, "unsigned_q16"))


def scaled_q16_to_decimal_text(value: int) -> str:
    decimal_value = Decimal(value) / Q16_SCALE
    text = format(decimal_value.normalize(), "f")
    if "." not in text:
        text += ".0"
    return text


def _checked(value: int, width: int, name: str) -> int:
    if not isinstance(value, int):
        raise TypeError(f"{name} 必须为整数")
    if value < 0:
        value += 1 << width
    if not 0 <= value < (1 << width):
        raise ValueError(f"{name} 超出 {width}-bit 可表示范围")
    return value


def _msb_bits(value: int, width: int, name: str) -> list[int]:
    value = _checked(value, width, name)
    return [(value >> bit) & 1 for bit in range(width - 1, -1, -1)]


@dataclass(slots=True)
class ControlWord:
    """Structured form of the MATLAB logical control word.

    Coefficients may be supplied either as raw unsigned bit patterns or as
    signed integers; negative values are converted to width-limited two's
    complement.
    """

    bypass: int = 0
    cs: int = 0
    en_bias: int = 1
    en_dllbuf: int = 1
    en_intbuf: int = 1
    en_sensor: int = 1
    en_xo: int = 1
    en_xobufa: int = 1
    su: int = 0
    trim_c_coarse: int = 0
    trim_c_fine: int = 0
    trim_r_xo: int = 0
    alpha: int = 0x10000
    batch: list[int] = field(default_factory=lambda: [0] * 7)
    polarity: int = 0
    poly: list[int] = field(default_factory=lambda: [0] * 8)

    def validate(self) -> None:
        for name in (
            "bypass",
            "en_bias",
            "en_dllbuf",
            "en_intbuf",
            "en_sensor",
            "en_xo",
            "en_xobufa",
            "su",
            "polarity",
        ):
            _checked(getattr(self, name), 1, name)
        _checked(self.cs, 2, "cs")
        _checked(self.trim_c_coarse, 3, "trim_c_coarse")
        _checked(self.trim_c_fine, 3, "trim_c_fine")
        _checked(self.trim_r_xo, 4, "trim_r_xo")
        _checked(self.alpha, 18, "alpha")
        if len(self.batch) != 7:
            raise ValueError("Batch 必须包含7个29-bit系数")
        if len(self.poly) != 8:
            raise ValueError("Polynomial 必须包含8个29-bit系数")
        for index, value in enumerate(self.batch):
            _checked(value, 29, f"batch_a{index}")
        for index, value in enumerate(self.poly):
            _checked(value, 29, f"poly_a{index}")

    def logical_bits(self) -> list[int]:
        """Return MATLAB's pre-flip order, with each multibit field MSB first."""
        self.validate()
        result: list[int] = [self.bypass]
        result += _msb_bits(self.cs, 2, "cs")
        result += [
            self.en_bias,
            self.en_dllbuf,
            self.en_intbuf,
            self.en_sensor,
            self.en_xo,
            self.en_xobufa,
            self.su,
        ]
        result += _msb_bits(self.trim_c_coarse, 3, "trim_c_coarse")
        result += _msb_bits(self.trim_c_fine, 3, "trim_c_fine")
        result += _msb_bits(self.trim_r_xo, 4, "trim_r_xo")
        result += _msb_bits(self.alpha, 18, "alpha")
        for index, value in enumerate(self.batch):
            result += _msb_bits(value, 29, f"batch_a{index}")
        result.append(self.polarity)
        for index, value in enumerate(self.poly):
            result += _msb_bits(value, 29, f"poly_a{index}")
        if len(result) != 474:
            raise AssertionError(f"内部控制字打包错误：得到{len(result)}位")
        return result

    def serial_bits(self) -> list[int]:
        """Return d1..d474, exactly matching MATLAB ``flip(Din_data)``."""
        return list(reversed(self.logical_bits()))

    def words16(self) -> list[int]:
        """Return 30 little-indexed transport words for FPGA shadow RAM."""
        serial = self.serial_bits() + [0] * 6
        return [
            sum(serial[word * 16 + bit] << bit for bit in range(16))
            for word in range(30)
        ]

    def to_json_dict(self) -> dict[str, object]:
        return asdict(self)

    @classmethod
    def from_json_dict(cls, values: dict[str, object]) -> "ControlWord":
        return cls(**values)


def bits_as_text(bits: Iterable[int]) -> str:
    return "".join(str(int(bit)) for bit in bits)
