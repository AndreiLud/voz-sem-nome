"""Check digit and format helpers for Brazilian identifiers.

The detectors mask formatted documents even when the check digits are wrong (recall first).
These helpers only decide between readings of ambiguous digit runs, such as an unformatted
11-digit number that could be a CPF or a mobile phone.
"""
from __future__ import annotations

# Area codes in use by Anatel. Used only for unformatted numbers, where a wrong DDD makes a
# phone reading unlikely.
VALID_DDDS = frozenset(
    {11, 12, 13, 14, 15, 16, 17, 18, 19, 21, 22, 24, 27, 28}
    | {31, 32, 33, 34, 35, 37, 38, 41, 42, 43, 44, 45, 46, 47, 48, 49}
    | {51, 53, 54, 55, 61, 62, 63, 64, 65, 66, 67, 68, 69}
    | {71, 73, 74, 75, 77, 79, 81, 82, 83, 84, 85, 86, 87, 88, 89}
    | {91, 92, 93, 94, 95, 96, 97, 98, 99}
)

_CNPJ_WEIGHTS_1 = (5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2)
_CNPJ_WEIGHTS_2 = (6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2)


def only_digits(value: str) -> str:
    return "".join(ch for ch in value if ch.isdigit())


def cpf_is_valid(value: str) -> bool:
    """True when the 11 digits of `value` carry correct CPF check digits."""
    digits = only_digits(value)
    if len(digits) != 11 or len(set(digits)) == 1:
        return False
    numbers = [int(d) for d in digits]
    for size in (9, 10):
        total = sum(n * w for n, w in zip(numbers[:size], range(size + 1, 1, -1)))
        if (total * 10) % 11 % 10 != numbers[size]:
            return False
    return True


def _cnpj_check_digit(chars: str, weights: tuple[int, ...]) -> int:
    # The 2026 alphanumeric CNPJ uses the ASCII code minus 48 for every character,
    # which gives the same values as before for digits.
    total = sum((ord(ch) - 48) * w for ch, w in zip(chars, weights))
    remainder = total % 11
    return 0 if remainder < 2 else 11 - remainder


def cnpj_is_valid(value: str) -> bool:
    """True for a numeric or alphanumeric (2026 format) CNPJ with correct check digits."""
    chars = "".join(ch for ch in value.upper() if ch.isalnum())
    if len(chars) != 14 or not chars[12:].isdigit() or not chars[:12].isascii():
        return False
    if len(set(chars)) == 1:
        return False
    first = _cnpj_check_digit(chars[:12], _CNPJ_WEIGHTS_1)
    second = _cnpj_check_digit(chars[:12] + str(first), _CNPJ_WEIGHTS_2)
    return chars[12:] == f"{first}{second}"


def is_valid_ddd(value: str) -> bool:
    return len(value) == 2 and value.isdigit() and int(value) in VALID_DDDS


def looks_like_mobile(digits: str) -> bool:
    """11 digits with a valid DDD followed by the mobile prefix 9."""
    return len(digits) == 11 and is_valid_ddd(digits[:2]) and digits[2] == "9"


def looks_like_landline_or_old_mobile(digits: str) -> bool:
    """10 digits with a valid DDD and a subscriber number starting with 2 to 9."""
    return len(digits) == 10 and is_valid_ddd(digits[:2]) and digits[2] in "23456789"
