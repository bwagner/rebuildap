"""
Module providing a utility to remove unnecessary trailing zeros from a
floating‑point number represented as a string.  The function accepts a
string and returns a new string with redundant trailing zeros in the
fractional part removed.  It also normalises leading zeros in the
integral part and strips any sign on a zero result.

The implementation performs purely string based manipulation and
deliberately avoids converting the input into a Python float to
preserve arbitrary precision and to reject scientific notation.
"""

from typing import Optional


def cut_trailing_zeros(value: str) -> str:
    """Remove unnecessary trailing zeros from a floating‑point number string.

    This function takes a string that is expected to represent a decimal
    number in non‑scientific notation.  It trims whitespace, handles
    optional leading '+' or '-' signs, normalises the integral part by
    removing redundant leading zeros and strips trailing zeros in the
    fractional part.  If the fractional part becomes empty after
    stripping, the decimal point itself is removed.  A negative zero
    result is normalised to ``"0"`` with no sign.

    Parameters
    ----------
    value: str
        A string representation of a decimal number.  The string may
        include optional leading and trailing whitespace and a leading
        sign character ('+' or '-').  The function will not accept
        scientific notation (containing 'e' or 'E') or multiple
        decimal points.  If the cleaned string does not represent a
        valid decimal number, a ``ValueError`` is raised.

    Returns
    -------
    str
        A canonical string representation of the input number with
        trailing zeros removed.  The returned string will not include
        a decimal point unless there is a fractional portion.  Leading
        zeros in the integral part are collapsed to a single zero
        unless a non‑zero digit appears.  If the number is zero, it
        will be returned as ``"0"`` regardless of input sign.

    Raises
    ------
    ValueError
        If the input is not a valid non‑scientific decimal number.
    """
    if not isinstance(value, str):
        raise TypeError("cut_trailing_zeros expects a string argument")

    # Strip leading and trailing whitespace
    s = value.strip()
    if not s:
        raise ValueError("empty string is not a valid number")

    # Extract and remember optional sign
    sign: Optional[str] = None
    if s[0] in "+-":
        sign = s[0]
        s = s[1:]
        if not s:
            # A string consisting solely of a sign is invalid
            raise ValueError(f"{value!r} is not a valid number")

    # Disallow scientific notation
    if "e" in s or "E" in s:
        raise ValueError(f"scientific notation is not supported: {value!r}")

    # There should be at most one decimal point
    if s.count(".") > 1:
        raise ValueError(f"invalid decimal format: {value!r}")

    # Split into integer and fractional parts
    if "." in s:
        int_part, frac_part = s.split(".", 1)
    else:
        int_part, frac_part = s, ""

    # Validate characters: only digits allowed in each part
    if not int_part and not frac_part:
        raise ValueError(f"invalid number format: {value!r}")
    if int_part and not int_part.isdigit():
        raise ValueError(f"invalid characters in integer part: {value!r}")
    if frac_part and not frac_part.isdigit():
        raise ValueError(f"invalid characters in fractional part: {value!r}")

    # Normalise integer part: remove leading zeros but preserve at least one digit
    # For numbers like '.5', int_part is empty; treat as '0'
    if int_part:
        int_normalised = int_part.lstrip("0")
    else:
        int_normalised = ""
    if int_normalised == "":
        int_normalised = "0"

    # Remove trailing zeros from fractional part
    frac_normalised = frac_part.rstrip("0") if frac_part else ""

    # Construct the result
    if frac_normalised:
        result = f"{int_normalised}.{frac_normalised}"
    else:
        # No fractional digits remain; result is just the integer part
        result = int_normalised

    # If the number is zero (i.e. integer part '0' and no fractional), drop any sign
    if result == "0":
        sign = None

    # If there was a negative sign and the result is non-zero, prepend it.
    if sign == "-":
        return "-" + result
    else:
        return result
