#!/usr/bin/env python3
"""
SEC EDGAR Filing Research — edgartools-based SEC data extraction.

Retrieves and extracts structured data from SEC EDGAR filings using the
edgartools library. Produces filing indexes, 10-K/10-Q item extractions,
financial statements, and 8-K summaries.

Usage:
    ./skills/research_sec_edgar.py SYMBOL --workdir DIR [--skip-financials] [--skip-8k]

Exit codes:
    0 — all steps succeeded
    1 — partial success (some artifacts produced)
    2 — total failure (no artifacts produced)

Stdout: JSON manifest of produced artifacts.
Stderr: All progress/diagnostic logging.
"""

import argparse
import json
import os
import sys
import traceback
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import edgar

# Add skills directory to path for local imports
_SKILLS_DIR = Path(__file__).resolve().parent.parent
if str(_SKILLS_DIR) not in sys.path:
    sys.path.insert(0, str(_SKILLS_DIR))

# isort: split

from config import SEC_FILING_FORMS, SEC_LOOKBACK_DAYS, SEC_10K_ITEMS, SEC_10Q_ITEMS  # noqa: E402
from utils import setup_logging, validate_symbol, ensure_directory, load_environment, default_workdir  # noqa: E402


load_environment()

# ---------------------------------------------------------------------------
# Module-level logger — all output goes to stderr via StreamHandler
# ---------------------------------------------------------------------------
logger = setup_logging(__name__, "INFO")

# ---------------------------------------------------------------------------
# Item key -> (filename_suffix, human_label) mappings
# ---------------------------------------------------------------------------
_10K_ITEM_MAP: Dict[str, Tuple[str, str]] = {
    "Item 1":  ("item1_business",      "Business"),
    "Item 1A": ("item1a_risk_factors", "Risk Factors"),
    "Item 1B": ("item1b_unresolved",   "Unresolved Staff Comments"),
    "Item 2":  ("item2_properties",    "Properties"),
    "Item 3":  ("item3_legal",         "Legal Proceedings"),
    "Item 5":  ("item5_market",        "Market Information"),
    "Item 6":  ("item6_financials",    "Selected Financial Data"),
    "Item 7":  ("item7_mda",           "MD&A"),
    "Item 7A": ("item7a_market_risk",  "Market Risk Disclosures"),
    "Item 8":  ("item8_financial_statements", "Financial Statements"),
    "Item 9":  ("item9_disagreements", "Disagreements with Accountants"),
    "Item 9A": ("item9a_controls",     "Controls and Procedures"),
}

_10Q_ITEM_MAP: Dict[str, Tuple[str, str]] = {
    "Item 1":  ("item1_financials",    "Financial Statements"),
    "Item 2":  ("item2_mda",           "MD&A"),
    "Item 3":  ("item3_market_risk",   "Market Risk"),
    "Item 4":  ("item4_controls",      "Controls and Procedures"),
}


# ============================================================================
# Helper: initialise edgartools identity
# ============================================================================

def _init_edgar() -> bool:
    """Set SEC EDGAR identity from environment variables.

    Returns True on success, False if required env vars are missing.
    """
    sec_firm = os.environ.get("SEC_FIRM", "").strip()
    sec_user = os.environ.get("SEC_USER", "").strip()

    if not sec_firm or not sec_user:
        logger.error(
            "SEC_FIRM and SEC_USER environment variables are required")
        return False

    try:
        identity_str = f"{sec_firm} {sec_user}"
        edgar.set_identity(identity_str)
        logger.info("SEC EDGAR identity set: %s", identity_str)
        return True
    except Exception as exc:
        logger.error("Failed to initialise edgartools: %s", exc)
        return False


def _get_company(symbol: str):
    """Create an edgartools Company object. Returns None on failure."""
    try:
        company = edgar.Company(symbol)
        logger.info("Resolved company for %s: %s", symbol,
                    getattr(company, "name", symbol))
        return company
    except Exception as exc:
        logger.error("Could not find company for symbol %s: %s", symbol, exc)
        return None


# ============================================================================
# 1. Filing Index
# ============================================================================

def get_filing_index(
    symbol: str,
    workdir: Path,
    lookback_days: int = SEC_LOOKBACK_DAYS,
) -> Tuple[bool, Optional[List[Dict]], Optional[str]]:
    """Retrieve all 10-K, 10-Q, and 8-K filings within the lookback period.

    Returns:
        (success, list_of_filing_dicts | None, error_message | None)
    """
    logger.info("Fetching filing index for %s (lookback=%d days)",
                symbol, lookback_days)
    artifacts_dir = ensure_directory(workdir / "artifacts")

    try:
        company = _get_company(symbol)
        if company is None:
            return False, None, f"Company not found for {symbol}"

        cutoff = datetime.now() - timedelta(days=lookback_days)
        filings_list: List[Dict] = []

        for form_type in SEC_FILING_FORMS:
            try:
                filings = company.get_filings(form=form_type)
                if filings is None:
                    logger.warning(
                        "No %s filings object returned for %s", form_type, symbol)
                    continue

                for filing in filings:
                    try:
                        # filing_date may be a string or date object
                        fdate = filing.filing_date
                        if isinstance(fdate, str):
                            fdate = datetime.strptime(fdate, "%Y-%m-%d")
                        elif hasattr(fdate, "year"):
                            # date or datetime — normalise to datetime for comparison
                            fdate = datetime(
                                fdate.year, fdate.month, fdate.day)

                        if fdate < cutoff:
                            # Past the lookback window — filings are newest-first,
                            # so we can stop for this form type.
                            break

                        filings_list.append({
                            "form": str(getattr(filing, "form", form_type)),
                            "filing_date": fdate.strftime("%Y-%m-%d"),
                            "accession_number": str(getattr(filing, "accession_number", getattr(filing, "accession_no", ""))),
                            "description": str(getattr(filing, "description", "")),
                        })
                    except Exception as inner_exc:
                        logger.warning(
                            "Error processing filing entry for %s %s: %s", symbol, form_type, inner_exc)
                        continue

            except Exception as form_exc:
                logger.warning("Error fetching %s filings for %s: %s",
                               form_type, symbol, form_exc)
                continue

        # Sort by filing_date descending
        filings_list.sort(key=lambda x: x["filing_date"], reverse=True)

        out_path = artifacts_dir / "sec_filings_index.json"
        with open(out_path, "w") as f:
            json.dump(filings_list, f, indent=2)

        logger.info("Saved %d filings to %s", len(filings_list), out_path)
        return True, filings_list, None

    except Exception as exc:
        msg = f"Filing index failed: {exc}"
        logger.error(msg)
        logger.debug(traceback.format_exc())
        return False, None, msg


# ============================================================================
# 2. 10-K Item Extraction
# ============================================================================

def get_10k_items(
    symbol: str,
    workdir: Path,
    items: Optional[List[str]] = None,
) -> Tuple[bool, Optional[Dict[str, str]], Optional[str]]:
    """Extract items from the latest 10-K filing.

    Returns:
        (success, dict_of_item_key_to_text | None, error_message | None)
    """
    if items is None:
        items = list(SEC_10K_ITEMS)

    logger.info("Extracting 10-K items %s for %s", items, symbol)
    artifacts_dir = ensure_directory(workdir / "artifacts")

    try:
        company = _get_company(symbol)
        if company is None:
            return False, None, f"Company not found for {symbol}"

        filings = company.get_filings(form="10-K")
        if filings is None or len(filings) == 0:
            msg = f"No 10-K filings found for {symbol}"
            logger.warning(msg)
            return False, None, msg

        latest_10k_filing = filings[0]
        filing_date = str(getattr(latest_10k_filing, "filing_date", "unknown"))
        accession = str(getattr(latest_10k_filing, "accession_number",
                        getattr(latest_10k_filing, "accession_no", "unknown")))

        logger.info("Latest 10-K: filed %s, accession %s",
                    filing_date, accession)

        # Convert to typed object
        try:
            tenk = latest_10k_filing.obj()
        except Exception as obj_exc:
            msg = f"Failed to parse 10-K object: {obj_exc}"
            logger.error(msg)
            return False, None, msg

        extracted: Dict[str, str] = {}
        saved_files: Dict[str, str] = {}

        for item_key in items:
            try:
                text = tenk[item_key]
                if text is None:
                    logger.warning("10-K %s returned None for %s",
                                   accession, item_key)
                    continue

                text = str(text).strip()
                if not text:
                    logger.warning("10-K %s is empty for %s",
                                   accession, item_key)
                    continue

                extracted[item_key] = text

                # Determine filename
                if item_key in _10K_ITEM_MAP:
                    suffix, label = _10K_ITEM_MAP[item_key]
                else:
                    suffix = item_key.lower().replace(" ", "").replace(".", "")
                    label = item_key

                filename = f"sec_10k_{suffix}.md"
                out_path = artifacts_dir / filename

                with open(out_path, "w") as f:
                    f.write(f"# {label} (10-K filed {filing_date})\n\n")
                    f.write(text)

                saved_files[item_key] = filename
                logger.info("Saved 10-K %s (%d chars) -> %s",
                            item_key, len(text), filename)

            except Exception as item_exc:
                logger.warning("Failed to extract 10-K %s: %s",
                               item_key, item_exc)
                continue

        # Save metadata
        metadata = {
            "form": "10-K",
            "filing_date": filing_date,
            "accession_number": accession,
            "company": str(getattr(company, "name", symbol)),
            "items_extracted": list(extracted.keys()),
            "items_requested": items,
            "files": saved_files,
        }
        meta_path = artifacts_dir / "sec_10k_metadata.json"
        with open(meta_path, "w") as f:
            json.dump(metadata, f, indent=2)

        if not extracted:
            msg = "No 10-K items could be extracted"
            logger.warning(msg)
            return False, None, msg

        return True, extracted, None

    except Exception as exc:
        msg = f"10-K extraction failed: {exc}"
        logger.error(msg)
        logger.debug(traceback.format_exc())
        return False, None, msg


# ============================================================================
# 3. 10-Q Item Extraction
# ============================================================================

def get_10q_items(
    symbol: str,
    workdir: Path,
) -> Tuple[bool, Optional[Dict[str, str]], Optional[str]]:
    """Extract items from the latest 10-Q filing.

    Returns:
        (success, dict_of_item_key_to_text | None, error_message | None)
    """
    items = list(SEC_10Q_ITEMS)
    logger.info("Extracting 10-Q items %s for %s", items, symbol)
    artifacts_dir = ensure_directory(workdir / "artifacts")

    try:
        company = _get_company(symbol)
        if company is None:
            return False, None, f"Company not found for {symbol}"

        filings = company.get_filings(form="10-Q")
        if filings is None or len(filings) == 0:
            msg = f"No 10-Q filings found for {symbol}"
            logger.warning(msg)
            return False, None, msg

        latest_10q_filing = filings[0]
        filing_date = str(getattr(latest_10q_filing, "filing_date", "unknown"))
        accession = str(getattr(latest_10q_filing, "accession_number",
                        getattr(latest_10q_filing, "accession_no", "unknown")))

        logger.info("Latest 10-Q: filed %s, accession %s",
                    filing_date, accession)

        # Convert to typed object
        try:
            tenq = latest_10q_filing.obj()
        except Exception as obj_exc:
            msg = f"Failed to parse 10-Q object: {obj_exc}"
            logger.error(msg)
            return False, None, msg

        extracted: Dict[str, str] = {}
        saved_files: Dict[str, str] = {}

        for item_key in items:
            try:
                text = tenq[item_key]
                if text is None:
                    logger.warning("10-Q %s returned None for %s",
                                   accession, item_key)
                    continue

                text = str(text).strip()
                if not text:
                    logger.warning("10-Q %s is empty for %s",
                                   accession, item_key)
                    continue

                extracted[item_key] = text

                # Determine filename
                if item_key in _10Q_ITEM_MAP:
                    suffix, label = _10Q_ITEM_MAP[item_key]
                else:
                    suffix = item_key.lower().replace(" ", "").replace(".", "")
                    label = item_key

                filename = f"sec_10q_{suffix}.md"
                out_path = artifacts_dir / filename

                with open(out_path, "w") as f:
                    f.write(f"# {label} (10-Q filed {filing_date})\n\n")
                    f.write(text)

                saved_files[item_key] = filename
                logger.info("Saved 10-Q %s (%d chars) -> %s",
                            item_key, len(text), filename)

            except Exception as item_exc:
                logger.warning("Failed to extract 10-Q %s: %s",
                               item_key, item_exc)
                continue

        # Save metadata
        metadata = {
            "form": "10-Q",
            "filing_date": filing_date,
            "accession_number": accession,
            "company": str(getattr(company, "name", symbol)),
            "items_extracted": list(extracted.keys()),
            "items_requested": items,
            "files": saved_files,
        }
        meta_path = artifacts_dir / "sec_10q_metadata.json"
        with open(meta_path, "w") as f:
            json.dump(metadata, f, indent=2)

        if not extracted:
            msg = "No 10-Q items could be extracted"
            logger.warning(msg)
            return False, None, msg

        return True, extracted, None

    except Exception as exc:
        msg = f"10-Q extraction failed: {exc}"
        logger.error(msg)
        logger.debug(traceback.format_exc())
        return False, None, msg


# ============================================================================
# 4. Financial Statements
# ============================================================================

def _save_financials_object(financials_obj, label: str, artifacts_dir: Path) -> List[Dict]:
    """Extract income_statement, balance_sheet, cash_flow_statement from a
    financials object and save each as CSV.

    Returns list of artifact metadata dicts for the manifest.
    """
    saved: List[Dict] = []

    statement_map = {
        "income_statement":     f"sec_income_{label}.csv",
        "balance_sheet":        f"sec_balance_{label}.csv",
        "cash_flow_statement":  f"sec_cashflow_{label}.csv",
    }

    for attr_name, filename in statement_map.items():
        try:
            statement = getattr(financials_obj, attr_name, None)
            if statement is None:
                logger.warning("Financials %s.%s is None", label, attr_name)
                continue

            # edgartools may return a DataFrame directly or an object with
            # .to_dataframe().  Try the most common patterns.
            df = None
            if hasattr(statement, "to_dataframe"):
                df = statement.to_dataframe()
            elif hasattr(statement, "to_pandas"):
                df = statement.to_pandas()
            else:
                # It might already be a DataFrame
                try:
                    import pandas as pd
                    if isinstance(statement, pd.DataFrame):
                        df = statement
                except ImportError:
                    pass

            if df is None:
                # Last resort: try converting string representation
                logger.warning(
                    "Could not convert %s.%s to DataFrame, saving as text", label, attr_name)
                text_path = artifacts_dir / filename.replace(".csv", ".txt")
                with open(text_path, "w") as f:
                    f.write(str(statement))
                saved.append({
                    "name": f"{attr_name}_{label}",
                    "path": f"artifacts/{text_path.name}",
                    "format": "txt",
                    "source": "sec-edgar",
                    "summary": f"{attr_name} ({label}) as text",
                })
                continue

            out_path = artifacts_dir / filename
            df.to_csv(out_path, index=True)
            rows = len(df)
            logger.info("Saved %s (%d rows) -> %s", attr_name, rows, filename)
            saved.append({
                "name": f"{attr_name}_{label}",
                "path": f"artifacts/{filename}",
                "format": "csv",
                "source": "sec-edgar",
                "summary": f"{attr_name} ({label}), {rows} rows",
            })

        except Exception as exc:
            logger.warning("Failed to extract %s.%s: %s",
                           label, attr_name, exc)
            continue

    return saved


def get_financials(
    symbol: str,
    workdir: Path,
) -> Tuple[bool, Optional[Dict], Optional[str]]:
    """Extract financial statements from latest annual and quarterly filings.

    Returns:
        (success, dict_with_details | None, error_message | None)
    """
    logger.info("Extracting financial statements for %s", symbol)
    artifacts_dir = ensure_directory(workdir / "artifacts")

    try:
        company = _get_company(symbol)
        if company is None:
            return False, None, f"Company not found for {symbol}"

        all_saved: List[Dict] = []

        # --- Annual financials ---
        try:
            annual = None
            # edgartools offers multiple access patterns; try each defensively
            if hasattr(company, "financials"):
                annual = company.financials
            elif hasattr(company, "get_financials"):
                annual = company.get_financials()

            if annual is not None:
                saved = _save_financials_object(
                    annual, "annual", artifacts_dir)
                all_saved.extend(saved)
            else:
                logger.warning("No annual financials available for %s", symbol)
        except Exception as ann_exc:
            logger.warning("Annual financials extraction failed: %s", ann_exc)

        # --- Quarterly financials ---
        try:
            quarterly = None
            if hasattr(company, "quarterly_financials"):
                quarterly = company.quarterly_financials
            elif hasattr(company, "get_quarterly_financials"):
                quarterly = company.get_quarterly_financials()

            if quarterly is not None:
                saved = _save_financials_object(
                    quarterly, "quarterly", artifacts_dir)
                all_saved.extend(saved)
            else:
                logger.warning(
                    "No quarterly financials available for %s", symbol)
        except Exception as q_exc:
            logger.warning("Quarterly financials extraction failed: %s", q_exc)

        if not all_saved:
            msg = "No financial statements could be extracted"
            logger.warning(msg)
            return False, None, msg

        return True, {"statements": all_saved}, None

    except Exception as exc:
        msg = f"Financials extraction failed: {exc}"
        logger.error(msg)
        logger.debug(traceback.format_exc())
        return False, None, msg


# ============================================================================
# 5. Recent 8-K Filings
# ============================================================================

def get_recent_8k(
    symbol: str,
    workdir: Path,
    lookback_days: int = SEC_LOOKBACK_DAYS,
) -> Tuple[bool, Optional[List[Dict]], Optional[str]]:
    """Summarise recent 8-K filings within the lookback period.

    Returns:
        (success, list_of_8k_summary_dicts | None, error_message | None)
    """
    logger.info("Fetching 8-K filings for %s (lookback=%d days)",
                symbol, lookback_days)
    artifacts_dir = ensure_directory(workdir / "artifacts")

    try:
        company = _get_company(symbol)
        if company is None:
            return False, None, f"Company not found for {symbol}"

        cutoff = datetime.now() - timedelta(days=lookback_days)
        summaries: List[Dict] = []

        try:
            filings = company.get_filings(form="8-K")
        except Exception as fetch_exc:
            msg = f"Failed to fetch 8-K filings: {fetch_exc}"
            logger.error(msg)
            return False, None, msg

        if filings is None:
            msg = f"No 8-K filings returned for {symbol}"
            logger.warning(msg)
            return False, None, msg

        for filing in filings:
            try:
                fdate = filing.filing_date
                if isinstance(fdate, str):
                    fdate = datetime.strptime(fdate, "%Y-%m-%d")
                elif hasattr(fdate, "year"):
                    fdate = datetime(fdate.year, fdate.month, fdate.day)

                if fdate < cutoff:
                    break  # oldest-first would need continue, but edgartools is newest-first

                summary_entry: Dict = {
                    "filing_date": fdate.strftime("%Y-%m-%d"),
                    "accession_number": str(getattr(filing, "accession_number",
                                                    getattr(filing, "accession_no", ""))),
                    "description": str(getattr(filing, "description", "")),
                }

                # Try to get items reported from the filing object
                try:
                    eightk = filing.obj()
                    if eightk is not None:
                        # Some 8-K objects expose items as a list or dict
                        items_reported = []
                        if hasattr(eightk, "items"):
                            raw_items = eightk.items
                            if callable(raw_items):
                                raw_items = raw_items()
                            if isinstance(raw_items, (list, tuple)):
                                items_reported = [str(i) for i in raw_items]
                            elif isinstance(raw_items, dict):
                                items_reported = list(raw_items.keys())
                            elif raw_items is not None:
                                items_reported = [str(raw_items)]
                        summary_entry["items_reported"] = items_reported
                except Exception:
                    # 8-K obj() parsing is best-effort
                    summary_entry["items_reported"] = []

                summaries.append(summary_entry)

            except Exception as entry_exc:
                logger.warning("Error processing 8-K entry: %s", entry_exc)
                continue

        out_path = artifacts_dir / "sec_8k_summary.json"
        with open(out_path, "w") as f:
            json.dump(summaries, f, indent=2)

        logger.info("Saved %d 8-K summaries to %s", len(summaries), out_path)
        return True, summaries, None

    except Exception as exc:
        msg = f"8-K summary failed: {exc}"
        logger.error(msg)
        logger.debug(traceback.format_exc())
        return False, None, msg


# ============================================================================
# Manifest Builder
# ============================================================================

def _build_manifest(
    status: str,
    artifacts: List[Dict],
    error: Optional[str] = None,
) -> Dict:
    """Build the JSON manifest that goes to stdout."""
    return {
        "status": status,
        "artifacts": artifacts,
        "error": error,
    }


# ============================================================================
# Main / CLI
# ============================================================================

def main() -> int:
    """CLI entry point. Runs all SEC EDGAR extraction steps in sequence.

    Returns exit code: 0 (all succeed), 1 (partial), 2 (nothing produced).
    """
    parser = argparse.ArgumentParser(
        description="SEC EDGAR filing research via edgartools"
    )
    parser.add_argument("symbol", help="Stock ticker symbol (e.g. AAPL)")
    parser.add_argument("--workdir", default=None, help="Work directory path (default: work/SYMBOL_YYYYMMDD)")
    parser.add_argument("--skip-financials", action="store_true",
                        help="Skip financial statement extraction")
    parser.add_argument("--skip-8k", action="store_true",
                        help="Skip 8-K filing summary")
    args = parser.parse_args()

    # ---- Validate inputs ----
    try:
        symbol = validate_symbol(args.symbol)
    except ValueError as ve:
        logger.error("Invalid symbol: %s", ve)
        print(json.dumps(_build_manifest("error", [], str(ve))))
        return 2

    workdir = Path(args.workdir or default_workdir(symbol))
    ensure_directory(workdir / "artifacts")

    # ---- Initialise edgartools ----
    if not _init_edgar():
        msg = "Cannot initialise SEC EDGAR identity (check SEC_FIRM / SEC_USER env vars)"
        logger.error(msg)
        print(json.dumps(_build_manifest("error", [], msg)))
        return 2

    # ---- Track results ----
    artifacts: List[Dict] = []
    total_steps = 0
    success_steps = 0
    errors: List[str] = []

    # ======================================================================
    # Step 1: Filing Index (always runs)
    # ======================================================================
    total_steps += 1
    ok, filings, err = get_filing_index(symbol, workdir)
    if ok and filings is not None:
        success_steps += 1
        artifacts.append({
            "name": "filings_index",
            "path": "artifacts/sec_filings_index.json",
            "format": "json",
            "source": "sec-edgar",
            "summary": f"{len(filings)} filings in past year",
        })
    else:
        errors.append(err or "Filing index failed")

    # ======================================================================
    # Step 2: 10-K Item Extraction
    # ======================================================================
    total_steps += 1
    ok_10k, extracted_10k, err_10k = get_10k_items(symbol, workdir)
    if ok_10k and extracted_10k is not None:
        success_steps += 1

        # Read metadata for filing year
        meta_path = workdir / "artifacts" / "sec_10k_metadata.json"
        filing_year = "?"
        try:
            with open(meta_path) as f:
                meta = json.load(f)
            filing_year = meta.get("filing_date", "?")[:4]
        except Exception:
            pass

        # Add metadata artifact
        artifacts.append({
            "name": "10k_metadata",
            "path": "artifacts/sec_10k_metadata.json",
            "format": "json",
            "source": "sec-edgar",
            "summary": f"10-K metadata, filed {filing_year}",
        })

        # Add each extracted item as an artifact
        for item_key, text in extracted_10k.items():
            if item_key in _10K_ITEM_MAP:
                suffix, label = _10K_ITEM_MAP[item_key]
            else:
                suffix = item_key.lower().replace(" ", "").replace(".", "")
                label = item_key
            filename = f"sec_10k_{suffix}.md"
            artifacts.append({
                "name": f"10k_{suffix}",
                "path": f"artifacts/{filename}",
                "format": "md",
                "source": "sec-edgar",
                "summary": f"{label} from {filing_year} 10-K ({len(text)} chars)",
            })
    else:
        if err_10k:
            errors.append(err_10k)
        logger.warning("10-K extraction skipped or failed: %s", err_10k)

    # ======================================================================
    # Step 3: 10-Q Item Extraction
    # ======================================================================
    total_steps += 1
    ok_10q, extracted_10q, err_10q = get_10q_items(symbol, workdir)
    if ok_10q and extracted_10q is not None:
        success_steps += 1

        # Read metadata for filing date
        meta_path = workdir / "artifacts" / "sec_10q_metadata.json"
        filing_date_10q = "?"
        try:
            with open(meta_path) as f:
                meta = json.load(f)
            filing_date_10q = meta.get("filing_date", "?")
        except Exception:
            pass

        # Add metadata artifact
        artifacts.append({
            "name": "10q_metadata",
            "path": "artifacts/sec_10q_metadata.json",
            "format": "json",
            "source": "sec-edgar",
            "summary": f"10-Q metadata, filed {filing_date_10q}",
        })

        # Add each extracted item
        for item_key, text in extracted_10q.items():
            if item_key in _10Q_ITEM_MAP:
                suffix, label = _10Q_ITEM_MAP[item_key]
            else:
                suffix = item_key.lower().replace(" ", "").replace(".", "")
                label = item_key
            filename = f"sec_10q_{suffix}.md"
            artifacts.append({
                "name": f"10q_{suffix}",
                "path": f"artifacts/{filename}",
                "format": "md",
                "source": "sec-edgar",
                "summary": f"{label} from 10-Q filed {filing_date_10q} ({len(text)} chars)",
            })
    else:
        if err_10q:
            errors.append(err_10q)
        logger.warning("10-Q extraction skipped or failed: %s", err_10q)

    # ======================================================================
    # Step 4: Financial Statements (unless --skip-financials)
    # ======================================================================
    if not args.skip_financials:
        total_steps += 1
        ok_fin, fin_data, err_fin = get_financials(symbol, workdir)
        if ok_fin and fin_data is not None:
            success_steps += 1
            for stmt in fin_data.get("statements", []):
                artifacts.append(stmt)
        else:
            if err_fin:
                errors.append(err_fin)
            logger.warning(
                "Financials extraction skipped or failed: %s", err_fin)

    # ======================================================================
    # Step 5: 8-K Summary (unless --skip-8k)
    # ======================================================================
    if not args.skip_8k:
        total_steps += 1
        ok_8k, summaries_8k, err_8k = get_recent_8k(symbol, workdir)
        if ok_8k and summaries_8k is not None:
            success_steps += 1
            artifacts.append({
                "name": "8k_summary",
                "path": "artifacts/sec_8k_summary.json",
                "format": "json",
                "source": "sec-edgar",
                "summary": f"{len(summaries_8k)} 8-K filings in past year",
            })
        else:
            if err_8k:
                errors.append(err_8k)
            logger.warning("8-K summary skipped or failed: %s", err_8k)

    # ======================================================================
    # Determine exit status
    # ======================================================================
    if success_steps == total_steps:
        status = "complete"
        exit_code = 0
    elif success_steps > 0:
        status = "partial"
        exit_code = 1
    else:
        status = "error"
        exit_code = 2

    combined_error = "; ".join(errors) if errors else None

    logger.info(
        "SEC EDGAR research %s: %d/%d steps succeeded, %d artifacts produced",
        status, success_steps, total_steps, len(artifacts),
    )

    # ---- Emit manifest to stdout ----
    manifest = _build_manifest(status, artifacts, combined_error)
    print(json.dumps(manifest, indent=2))

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
