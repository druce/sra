"""
Configuration constants for stock research skills.

This module centralizes all configuration values used across the research skills.
"""
from typing import Dict

# ============================================================================
# Directory Configuration
# ============================================================================
WORK_DIR = 'work'
ARTIFACTS_DIR = 'artifacts'
TEMP_DIR = 'temp'
DATA_DIR = 'data'
TEMPLATES_DIR = 'templates'

# ============================================================================
# Phase Execution Configuration
# ============================================================================
# Timeout values in seconds for each phase
PHASE_TIMEOUTS: Dict[str, int] = {
    'deep': 1800,      # 30 minutes for deep research with Claude
    'technical': 300,   # 5 minutes
    'fundamental': 300,
    'research': 300,
    'analysis': 300,
    'sec': 300,
    'wikipedia': 300,
    'report': 300,
    'final': 300,
}

# Maximum number of parallel workers for phase execution
MAX_PARALLEL_WORKERS = 6

# Required API keys for each phase
PHASE_API_KEYS: Dict[str, list] = {
    'technical': ['OPENBB_PAT'],
    'fundamental': ['OPENBB_PAT'],
    'sec': ['SEC_FIRM', 'SEC_USER'],
    'wikipedia': [],
    'report': [],
    'final': [],
}

# ============================================================================
# Ticker Lookup Configuration
# ============================================================================
DEFAULT_TICKER_LIMIT = 10
MAX_TICKER_LENGTH = 5

# ============================================================================
# Peer Analysis Configuration
# ============================================================================
MAX_PEERS_TO_FETCH = 15      # Maximum peers to fetch from API
MAX_PEERS_IN_REPORT = 10     # Maximum peers to include in reports
MAX_PEERS_FOR_RATIOS = 15    # Maximum peers for ratio comparison

# ============================================================================
# Technical Analysis Configuration
# ============================================================================
# Moving average periods
SMA_SHORT_PERIOD = 20
SMA_MEDIUM_PERIOD = 50
SMA_LONG_PERIOD = 200
MA_WEEKLY_SHORT = 13  # 13-week (quarterly) MA
MA_WEEKLY_LONG = 52   # 52-week (annual) MA

# Technical indicator periods
RSI_PERIOD = 14
MACD_FAST_PERIOD = 12
MACD_SLOW_PERIOD = 26
MACD_SIGNAL_PERIOD = 9
ATR_PERIOD = 14
BOLLINGER_PERIOD = 20
BOLLINGER_STD_DEV = 2

# Chart configuration
CHART_HISTORY_YEARS = 4      # Years of data for weekly charts
CHART_HISTORY_DAYS = 365     # Days of data for daily analysis
CHART_WIDTH = 800
CHART_HEIGHT = 600
CHART_SCALE = 2              # Image export scale

# Volume averaging period
VOLUME_AVERAGE_DAYS = 20

# ============================================================================
# Fundamental Analysis Configuration
# ============================================================================
MAX_ANALYST_RECOMMENDATIONS = 20
MAX_NEWS_ARTICLES = 10
INCOME_STATEMENT_EXCERPT_LENGTH = 5000

# ============================================================================
# Embedding / Chunk Configuration (OpenAI + LanceDB)
# ============================================================================
EMBED_MODEL = "text-embedding-3-small"
EMBED_DIM = 1536
CHUNK_TARGET_TOKENS = 600
CHUNK_MAX_TOKENS = 800
CHUNK_OVERLAP_TOKENS = 100  # ~12% overlap to preserve context at chunk boundaries

# ============================================================================
# Deep Research Configuration (Claude)
# ============================================================================
CLAUDE_MODEL = 'claude-sonnet-4-5-20250929'
CLAUDE_EXTENDED_THINKING_TOKENS = 10000
CLAUDE_MAX_OUTPUT_TOKENS = 16000

# ============================================================================
# SEC Filing Configuration (edgartools)
# ============================================================================
SEC_FILING_FORMS = ['10-K', '10-Q', '8-K']
SEC_LOOKBACK_DAYS = 365
SEC_10K_ITEMS = ['Item 1', 'Item 1A', 'Item 7']
SEC_10Q_ITEMS = ['Item 2']  # MD&A (Item 2 in 10-Q maps to Item 7 in 10-K)

# ============================================================================
# Report Generation Configuration
# ============================================================================
DEFAULT_REPORT_TEMPLATE = 'equity_research_report.md.j2'
FINAL_REPORT_TEMPLATE = 'final_report.md.j2'
REPORT_FORMATS = ['markdown', 'html', 'docx']

# ============================================================================
# Retry Configuration
# ============================================================================
MAX_RETRIES = 3
RETRY_DELAY_SECONDS = 5
RETRY_BACKOFF_MULTIPLIER = 2

# ============================================================================
# Logging Configuration
# ============================================================================
LOG_FORMAT = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
LOG_DATE_FORMAT = '%Y-%m-%d %H:%M:%S'
DEFAULT_LOG_LEVEL = 'INFO'

# ============================================================================
# Date Format Configuration
# ============================================================================
DATE_FORMAT_DISPLAY = '%Y-%m-%d %H:%M:%S'
DATE_FORMAT_FILE = '%Y%m%d'
DATE_FORMAT_ISO = '%Y-%m-%d'
