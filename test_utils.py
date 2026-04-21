"""Test suite for the ``cut_trailing_zeros`` function using pytest.

This suite validates proper handling of numeric strings by the
``cut_trailing_zeros`` function.  It covers a variety of valid inputs
and ensures that invalid strings raise appropriate exceptions.  The
test cases are supplied via ``pytest.mark.parametrize`` to concisely
express multiple scenarios.
"""

import pytest

import rebuildap as ra
from rebuildap.utils import cut_trailing_zeros


def test_process_lines():
    assert ra.process_lines(
        [
            "3.14",
            "2.71\tfoo",
            "1.0\t2.0\tbar",
            "1.00\t2.00\tbar",
            "hello\tworld",
            "5.5\t6.6\t7.7\tignored",
        ]
    ) == [
        "3.14\t3.14\n",
        "2.71\t2.71\tfoo\n",
        "1\t2\tbar\n",
        "1\t2\tbar\n",
        "hello\tworld",
        "5.5\t6.6\t7.7\tignored",
    ]


@pytest.mark.parametrize(
    "input_str,expected",
    [
        # Basic integers and zeros
        ("123", "123"),
        ("0", "0"),
        ("00", "0"),
        ("000", "0"),
        ("000123", "123"),
        ("000123.000", "123"),
        # Trailing zeros in fractional part
        ("123.45000", "123.45"),
        ("10.0", "10"),
        ("10.00", "10"),
        ("10.10", "10.1"),
        ("0.000", "0"),
        ("0.00100", "0.001"),
        ("000.000", "0"),
        ("000123.45000", "123.45"),
        # Negative numbers
        ("-123.4500", "-123.45"),
        ("-000.0000", "0"),
        ("-000123.450", "-123.45"),
        ("-0.100", "-0.1"),
        ("-.50", "-0.5"),
        ("-0.0", "0"),
        # Positive sign
        ("+12.3400", "12.34"),
        ("+.500", "0.5"),
        ("+0.0", "0"),
        # Whitespace handling
        ("   12.3400   ", "12.34"),
        ("\t\n0.00100  ", "0.001"),
        # Missing integer or fractional part
        (".12300", "0.123"),
        ("123.", "123"),
        ("0.", "0"),
        # Leading zeros with fractional part
        ("0010.2300", "10.23"),
        ("0000.100", "0.1"),
    ],
)
def test_valid_cases(input_str: str, expected: str) -> None:
    """Ensure that valid numeric strings are normalised correctly."""
    assert cut_trailing_zeros(input_str) == expected


@pytest.mark.parametrize(
    "invalid_input",
    [
        "",
        "+",
        "-",
        "abc",
        "1.2.3",
        "1..2",
        "1e3",
        "1E3",
        "1.0e2",
        "1.0E2",
        "1e-2",
        "1E-2",
        "-e10",
        "++.1",
        "+-0.1",
        "--0.1",
        "+-",
        "-+",
        ".",
        "..",
        "1,234",  # comma is invalid
    ],
)
def test_invalid_inputs(invalid_input: str) -> None:
    """Invalid formats should raise ``ValueError`` or ``TypeError``."""
    with pytest.raises((ValueError, TypeError)):  # type: ignore[arg-type]
        cut_trailing_zeros(invalid_input)
