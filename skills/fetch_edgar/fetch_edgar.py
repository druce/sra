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
import sys
import traceback
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# Add skills directory to path for local imports
_SKILLS_DIR = Path(__file__).resolve().parent.parent
if str(_SKILLS_DIR) not in sys.path:
    sys.path.insert(0, str(_SKILLS_DIR))

# isort: split

from config import SEC_LOOKBACK_DAYS  # noqa: E402
from utils import setup_logging, validate_symbol, ensure_directory, load_environment, default_workdir  # noqa: E402
from fetch_edgar.filing_items import (  # noqa: E402
    init_edgar as _init_edgar,
    get_company as _get_company,
    get_filing_index,
    get_10k_items,
    get_10q_items,
    _10K_ITEM_MAP,
    _10Q_ITEM_MAP,
)


load_environment()

# ---------------------------------------------------------------------------
# Module-level logger — all output goes to stderr via StreamHandler
# ---------------------------------------------------------------------------
logger = setup_logging(__name__, "INFO")


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
                txt_summary = f"{attr_name} ({label}) as text"
                saved.append({
                    "name": f"{attr_name}_{label}",
                    "path": f"artifacts/{text_path.name}",
                    "format": "txt",
                    "source": "sec-edgar",
                    "description": txt_summary,
                    "summary": txt_summary,
                })
                continue

            out_path = artifacts_dir / filename
            df.to_csv(out_path, index=True)
            rows = len(df)
            logger.info("Saved %s (%d rows) -> %s", attr_name, rows, filename)
            csv_summary = f"{attr_name} ({label}), {rows} rows"
            saved.append({
                "name": f"{attr_name}_{label}",
                "path": f"artifacts/{filename}",
                "format": "csv",
                "source": "sec-edgar",
                "description": csv_summary,
                "summary": csv_summary,
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

        out_path = artifacts_dir / "8k_summary.json"
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
        summary = f"{len(filings)} filings in past year"
        artifacts.append({
            "name": "filings_index",
            "path": "artifacts/filings_index.json",
            "format": "json",
            "source": "sec-edgar",
            "description": summary,
            "summary": summary,
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
        summary_10k_meta = f"10-K metadata, filed {filing_year}"
        artifacts.append({
            "name": "10k_metadata",
            "path": "artifacts/sec_10k_metadata.json",
            "format": "json",
            "source": "sec-edgar",
            "description": summary_10k_meta,
            "summary": summary_10k_meta,
        })

        # Add each extracted item as an artifact
        for item_key, text in extracted_10k.items():
            if item_key in _10K_ITEM_MAP:
                suffix, label = _10K_ITEM_MAP[item_key]
            else:
                suffix = item_key.lower().replace(" ", "").replace(".", "")
                label = item_key
            filename = f"sec_10k_{suffix}.md"
            item_summary = f"{label} from {filing_year} 10-K ({len(text)} chars)"
            artifacts.append({
                "name": f"10k_{suffix}",
                "path": f"artifacts/{filename}",
                "format": "md",
                "source": "sec-edgar",
                "description": item_summary,
                "summary": item_summary,
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
        summary_10q_meta = f"10-Q metadata, filed {filing_date_10q}"
        artifacts.append({
            "name": "10q_metadata",
            "path": "artifacts/sec_10q_metadata.json",
            "format": "json",
            "source": "sec-edgar",
            "description": summary_10q_meta,
            "summary": summary_10q_meta,
        })

        # Add each extracted item
        for item_key, text in extracted_10q.items():
            if item_key in _10Q_ITEM_MAP:
                suffix, label = _10Q_ITEM_MAP[item_key]
            else:
                suffix = item_key.lower().replace(" ", "").replace(".", "")
                label = item_key
            filename = f"sec_10q_{suffix}.md"
            item_summary = f"{label} from 10-Q filed {filing_date_10q} ({len(text)} chars)"
            artifacts.append({
                "name": f"10q_{suffix}",
                "path": f"artifacts/{filename}",
                "format": "md",
                "source": "sec-edgar",
                "description": item_summary,
                "summary": item_summary,
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
            summary_8k = f"{len(summaries_8k)} 8-K filings in past year"
            artifacts.append({
                "name": "8k_summary",
                "path": "artifacts/8k_summary.json",
                "format": "json",
                "source": "sec-edgar",
                "description": summary_8k,
                "summary": summary_8k,
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
