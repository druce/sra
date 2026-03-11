"""Tests for skills/utils.py — formatting, validation, and variable substitution."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "skills"))

from utils import (
    format_market_cap,
    format_number,
    format_currency,
    format_percentage,
    validate_symbol,
    substitute_vars,
    resolve_company_name,
    safe_get,
    default_workdir,
)


# ---------------------------------------------------------------------------
# format_market_cap
# ---------------------------------------------------------------------------


def test_format_market_cap_trillions():
    assert format_market_cap(3.5e12) == "3.50T"


def test_format_market_cap_billions():
    assert format_market_cap(250.3e9) == "250.3B"


def test_format_market_cap_millions():
    assert format_market_cap(45.2e6) == "45.2M"


def test_format_market_cap_small():
    assert format_market_cap(999_999) == "999,999"


def test_format_market_cap_zero():
    assert format_market_cap(0) == "0"


def test_format_market_cap_string_input():
    assert format_market_cap("1500000000") == "1.5B"


def test_format_market_cap_invalid():
    assert format_market_cap("not a number") == "N/A"


def test_format_market_cap_none():
    assert format_market_cap(None) == "N/A"


# ---------------------------------------------------------------------------
# format_number
# ---------------------------------------------------------------------------


def test_format_number_integer():
    assert format_number(1234567) == "1,234,567"


def test_format_number_with_precision():
    assert format_number(1234.5678, precision=2) == "1,234.57"


def test_format_number_invalid():
    assert format_number("abc") == "N/A"


def test_format_number_float_truncated():
    assert format_number(1234.99) == "1,234"


# ---------------------------------------------------------------------------
# format_currency
# ---------------------------------------------------------------------------


def test_format_currency_billions():
    assert format_currency(1.23e9) == "$1.23B"


def test_format_currency_millions():
    assert format_currency(5.678e6, precision=1) == "$5.7M"


def test_format_currency_thousands():
    assert format_currency(45_678) == "$45.68K"


def test_format_currency_invalid():
    assert format_currency("bad") == "N/A"


# ---------------------------------------------------------------------------
# format_percentage
# ---------------------------------------------------------------------------


def test_format_percentage():
    assert format_percentage(0.1567) == "15.67%"


def test_format_percentage_precision():
    assert format_percentage(0.05, precision=1) == "5.0%"


# ---------------------------------------------------------------------------
# validate_symbol
# ---------------------------------------------------------------------------


def test_validate_symbol_basic():
    assert validate_symbol("AAPL") == "AAPL"


def test_validate_symbol_lowercase():
    assert validate_symbol("aapl") == "AAPL"


def test_validate_symbol_with_dot():
    assert validate_symbol("BRK.B") == "BRK.B"


def test_validate_symbol_with_dash():
    assert validate_symbol("BF-B") == "BF-B"


def test_validate_symbol_empty():
    with pytest.raises(ValueError):
        validate_symbol("")


def test_validate_symbol_invalid_chars():
    with pytest.raises(ValueError):
        validate_symbol("A@PL")


# ---------------------------------------------------------------------------
# substitute_vars
# ---------------------------------------------------------------------------


def test_substitute_vars_string():
    result = substitute_vars("Hello ${name}", {"name": "World"})
    assert result == "Hello World"


def test_substitute_vars_dict():
    result = substitute_vars(
        {"ticker": "${ticker}", "path": "work/${ticker}_${date}"},
        {"ticker": "AAPL", "date": "20260101"},
    )
    assert result == {"ticker": "AAPL", "path": "work/AAPL_20260101"}


def test_substitute_vars_list():
    result = substitute_vars(["${a}", "${b}"], {"a": "1", "b": "2"})
    assert result == ["1", "2"]


def test_substitute_vars_nested():
    result = substitute_vars(
        {"config": {"prompt": "Analyze ${ticker}"}},
        {"ticker": "TSLA"},
    )
    assert result == {"config": {"prompt": "Analyze TSLA"}}


def test_substitute_vars_no_match():
    """Unresolved variables are left as-is."""
    result = substitute_vars("${unknown}", {"ticker": "AAPL"})
    assert result == "${unknown}"


def test_substitute_vars_non_string_passthrough():
    """Non-string, non-dict, non-list values are returned unchanged."""
    assert substitute_vars(42, {"x": "y"}) == 42
    assert substitute_vars(None, {"x": "y"}) is None


def test_substitute_vars_multiple_in_one_string():
    result = substitute_vars("${a} and ${b}", {"a": "X", "b": "Y"})
    assert result == "X and Y"


# ---------------------------------------------------------------------------
# resolve_company_name
# ---------------------------------------------------------------------------


def test_resolve_company_name_from_profile(tmp_path):
    """Reads company_name from profile.json."""
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    profile = {"company_name": "Apple Inc.", "symbol": "AAPL"}
    (artifacts / "profile.json").write_text(json.dumps(profile))
    assert resolve_company_name("AAPL", tmp_path) == "Apple Inc."


def test_resolve_company_name_longname_fallback(tmp_path):
    """Falls back to longName if company_name is missing."""
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    profile = {"longName": "Tesla, Inc.", "symbol": "TSLA"}
    (artifacts / "profile.json").write_text(json.dumps(profile))
    assert resolve_company_name("TSLA", tmp_path) == "Tesla, Inc."


def test_resolve_company_name_no_profile(tmp_path):
    """Returns symbol when no profile.json exists."""
    assert resolve_company_name("NVDA", tmp_path) == "NVDA"


def test_resolve_company_name_invalid_json(tmp_path):
    """Returns symbol when profile.json is invalid JSON."""
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    (artifacts / "profile.json").write_text("not json {{{")
    assert resolve_company_name("AMD", tmp_path) == "AMD"


def test_resolve_company_name_na_value(tmp_path):
    """Returns symbol when company_name is 'N/A'."""
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    profile = {"company_name": "N/A", "symbol": "XYZ"}
    (artifacts / "profile.json").write_text(json.dumps(profile))
    assert resolve_company_name("XYZ", tmp_path) == "XYZ"


# ---------------------------------------------------------------------------
# safe_get
# ---------------------------------------------------------------------------


def test_safe_get_existing_key():
    assert safe_get({"price": 123.45}, "price") == "123.45"


def test_safe_get_missing_key():
    assert safe_get({"price": 123.45}, "volume") == "N/A"


def test_safe_get_none_value():
    assert safe_get({"price": None}, "price") == "N/A"


def test_safe_get_with_formatter():
    result = safe_get({"price": 123.45}, "price", formatter=lambda x: f"${x:.2f}")
    assert result == "$123.45"


def test_safe_get_formatter_error():
    """Formatter that raises returns default."""
    result = safe_get({"val": "not_a_number"}, "val", formatter=lambda x: f"{float(x):.2f}")
    assert result == "N/A"


# ---------------------------------------------------------------------------
# default_workdir
# ---------------------------------------------------------------------------


def test_default_workdir_format():
    wd = default_workdir("AAPL")
    # Should be work/AAPL_YYYYMMDD
    assert wd.startswith("work/AAPL_")
    date_part = wd.split("_")[1]
    assert len(date_part) == 8
    assert date_part.isdigit()
