"""
Shared utility functions for stock research skills.

This module provides common functionality used across all research skills including:
- Logging setup
- Path handling
- Date formatting
- Input validation
- File operations
"""

import os
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional, Union

from dotenv import load_dotenv

from config import (
    WORK_DIR,
    ARTIFACTS_DIR,
    DATE_FORMAT_FILE,
    DATE_FORMAT_DISPLAY,
    DATE_FORMAT_ISO,
    LOG_FORMAT,
    LOG_DATE_FORMAT,
)


def load_environment() -> None:
    """Load .env from project root (skills/../.env), regardless of cwd."""
    env_path = Path(__file__).resolve().parent.parent / ".env"
    load_dotenv(dotenv_path=env_path)


def setup_logging(
    name: str,
    level: str = 'INFO',
    log_file: Optional[Path] = None
) -> logging.Logger:
    """
    Set up logging for a skill.

    Args:
        name: Logger name (typically __name__)
        level: Log level ('DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL')
        log_file: Optional path to log file

    Returns:
        Configured logger instance

    Example:
        >>> logger = setup_logging(__name__, 'INFO')
        >>> logger.info("Starting analysis")
    """
    logger = logging.getLogger(name)
    logger.setLevel(getattr(logging, level.upper()))

    # Remove existing handlers
    logger.handlers.clear()

    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_formatter = logging.Formatter(LOG_FORMAT, datefmt=LOG_DATE_FORMAT)
    console_handler.setFormatter(console_formatter)
    logger.addHandler(console_handler)

    # File handler (if specified)
    if log_file:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_file)
        file_handler.setLevel(logging.DEBUG)
        file_formatter = logging.Formatter(LOG_FORMAT, datefmt=LOG_DATE_FORMAT)
        file_handler.setFormatter(file_formatter)
        logger.addHandler(file_handler)

    return logger


def create_work_directory(
    symbol: str,
    base_dir: Union[str, Path] = WORK_DIR,
    date: Optional[datetime] = None
) -> Path:
    """
    Create standardized work directory for a symbol.

    Args:
        symbol: Stock ticker symbol
        base_dir: Base directory for work directories
        date: Optional date for directory name (default: current date)

    Returns:
        Path to created work directory

    Example:
        >>> work_dir = create_work_directory('TSLA')
        >>> print(work_dir)
        work/TSLA_20260116
    """
    date_str = (date or datetime.now()).strftime(DATE_FORMAT_FILE)
    work_dir = Path(base_dir) / f"{symbol}_{date_str}"
    work_dir.mkdir(parents=True, exist_ok=True)
    return work_dir


def validate_symbol(symbol: str) -> str:
    """
    Validate and normalize stock ticker symbol.

    Args:
        symbol: Raw ticker symbol input

    Returns:
        Normalized ticker symbol (uppercase, stripped)

    Raises:
        ValueError: If symbol is invalid

    Example:
        >>> validate_symbol("  tsla  ")
        'TSLA'
    """
    if not symbol or not isinstance(symbol, str):
        raise ValueError("Symbol must be a non-empty string")

    normalized = symbol.strip().upper()

    if not normalized:
        raise ValueError("Symbol cannot be empty")

    # Basic validation - allow alphanumeric and dots (for international tickers)
    if not all(c.isalnum() or c == '.' for c in normalized):
        raise ValueError(f"Invalid symbol format: {symbol}")

    return normalized


def format_currency(value: float, precision: int = 2) -> str:
    """
    Format value as currency string with appropriate suffix.

    Args:
        value: Numeric value to format
        precision: Decimal places to show (default: 2)

    Returns:
        Formatted currency string

    Example:
        >>> format_currency(1234567890)
        '$1.23B'
        >>> format_currency(5678901.23, precision=1)
        '$5.7M'
    """
    try:
        value = float(value)
    except (ValueError, TypeError):
        return 'N/A'

    if value >= 1e12:
        return f"${value/1e12:.{precision}f}T"
    elif value >= 1e9:
        return f"${value/1e9:.{precision}f}B"
    elif value >= 1e6:
        return f"${value/1e6:.{precision}f}M"
    elif value >= 1e3:
        return f"${value/1e3:.{precision}f}K"
    else:
        return f"${value:.{precision}f}"


def format_number(value: Union[int, float], precision: int = 0) -> str:
    """
    Format number with commas for thousands.

    Args:
        value: Numeric value to format
        precision: Decimal places to show (default: 0)

    Returns:
        Formatted number string

    Example:
        >>> format_number(1234567)
        '1,234,567'
        >>> format_number(1234.5678, precision=2)
        '1,234.57'
    """
    try:
        value = float(value)
    except (ValueError, TypeError):
        return 'N/A'

    if precision == 0:
        return f"{int(value):,}"
    else:
        return f"{value:,.{precision}f}"


def format_percentage(value: float, precision: int = 2) -> str:
    """
    Format decimal as percentage string.

    Args:
        value: Decimal value (e.g., 0.15 for 15%)
        precision: Decimal places to show (default: 2)

    Returns:
        Formatted percentage string

    Example:
        >>> format_percentage(0.1567)
        '15.67%'
        >>> format_percentage(0.05, precision=1)
        '5.0%'
    """
    try:
        value = float(value)
        return f"{value * 100:.{precision}f}%"
    except (ValueError, TypeError):
        return 'N/A'


def format_date(
    date: Union[str, datetime],
    format_type: str = 'display'
) -> str:
    """
    Format date according to specified format type.

    Args:
        date: Date string or datetime object
        format_type: Format type ('display', 'file', 'iso')

    Returns:
        Formatted date string

    Example:
        >>> from datetime import datetime
        >>> dt = datetime(2026, 1, 16)
        >>> format_date(dt, 'display')
        '2026-01-16 00:00:00'
        >>> format_date(dt, 'file')
        '20260116'
    """
    if isinstance(date, str):
        # Try to parse common date formats
        for fmt in ['%Y-%m-%d', '%Y%m%d', '%Y-%m-%d %H:%M:%S']:
            try:
                date = datetime.strptime(date, fmt)
                break
            except ValueError:
                continue

    if not isinstance(date, datetime):
        return str(date)

    formats = {
        'display': DATE_FORMAT_DISPLAY,
        'file': DATE_FORMAT_FILE,
        'iso': DATE_FORMAT_ISO,
    }

    return date.strftime(formats.get(format_type, DATE_FORMAT_DISPLAY))


def safe_get(
    data: dict,
    key: str,
    default: str = 'N/A',
    formatter: Optional[callable] = None
) -> str:
    """
    Safely get value from dictionary with optional formatting.

    Args:
        data: Dictionary to get value from
        key: Key to look up
        default: Default value if key not found or value is None
        formatter: Optional function to format the value

    Returns:
        Formatted value or default

    Example:
        >>> data = {'price': 123.45, 'name': 'Apple'}
        >>> safe_get(data, 'price', formatter=lambda x: f"${x:.2f}")
        '$123.45'
        >>> safe_get(data, 'missing')
        'N/A'
    """
    value = data.get(key)

    if value is None or value == 'N/A':
        return default

    if formatter:
        try:
            return formatter(value)
        except Exception:
            return default

    return str(value)


def ensure_directory(path: Union[str, Path]) -> Path:
    """
    Ensure directory exists, creating if necessary.

    Args:
        path: Directory path

    Returns:
        Path object for the directory

    Example:
        >>> output_dir = ensure_directory('work/TSLA_20260116/output')
        >>> output_dir.exists()
        True
    """
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)
    return path


def print_section_header(title: str, width: int = 60) -> None:
    """
    Print a formatted section header.

    Args:
        title: Section title
        width: Width of the header line

    Example:
        >>> print_section_header("Data Analysis")
        ============================================================
        Data Analysis
        ============================================================
    """
    print("=" * width)
    print(title)
    print("=" * width)


def print_success(message: str) -> None:
    """Print success message with checkmark."""
    print(f"✓ {message}")


def print_error(message: str) -> None:
    """Print error message with X mark."""
    print(f"❌ {message}")


def print_warning(message: str) -> None:
    """Print warning message with warning symbol."""
    print(f"⚠ {message}")


def print_info(message: str) -> None:
    """Print info message with circle symbol."""
    print(f"⊘ {message}")
