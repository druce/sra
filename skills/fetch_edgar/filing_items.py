"""
SEC EDGAR filing access and item extraction.

Provides initialization of the edgartools SEC identity, company lookup,
filing index retrieval, and extraction of individual items from 10-K and
10-Q filings. Each extracted item is saved as a Markdown file in the
artifacts directory alongside a JSON metadata file.
"""

import json
import logging
import os
import traceback
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import edgar

from config import SEC_FILING_FORMS, SEC_LOOKBACK_DAYS, SEC_10K_ITEMS, SEC_10Q_ITEMS
from utils import ensure_directory
from fetch_edgar.sec_text_cleaner import clean_sec_text


logger = logging.getLogger(__name__)


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

_8K_ITEM_MAP: Dict[str, str] = {
    "Item 1.01": "Entry into a Material Definitive Agreement",
    "Item 1.02": "Termination of a Material Definitive Agreement",
    "Item 1.03": "Bankruptcy or Receivership",
    "Item 2.01": "Completion of Acquisition or Disposition of Assets",
    "Item 2.02": "Results of Operations and Financial Condition",
    "Item 2.03": "Creation of a Direct Financial Obligation",
    "Item 2.04": "Triggering Events That Accelerate an Obligation",
    "Item 2.05": "Costs Associated with Exit or Disposal Activities",
    "Item 2.06": "Material Impairments",
    "Item 3.01": "Notice of Delisting or Transfer",
    "Item 3.02": "Unregistered Sales of Equity Securities",
    "Item 3.03": "Material Modification to Rights of Security Holders",
    "Item 4.01": "Changes in Registrant's Certifying Accountant",
    "Item 4.02": "Non-Reliance on Previously Issued Financial Statements",
    "Item 5.01": "Changes in Control of Registrant",
    "Item 5.02": "Departure/Appointment of Directors or Officers",
    "Item 5.03": "Amendments to Articles of Incorporation or Bylaws",
    "Item 5.04": "Temporary Suspension of Trading Under Employee Benefit Plans",
    "Item 5.05": "Amendments to Code of Ethics",
    "Item 5.06": "Change in Shell Company Status",
    "Item 5.07": "Submission of Matters to a Vote of Security Holders",
    "Item 5.08": "Shareholder Nominations",
    "Item 7.01": "Regulation FD Disclosure",
    "Item 8.01": "Other Events",
    "Item 9.01": "Financial Statements and Exhibits",
}


# ============================================================================
# Shared helpers
# ============================================================================

def init_edgar() -> bool:
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


def get_company(symbol: str):
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
# Filing Index
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
        company = get_company(symbol)
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

        out_path = artifacts_dir / "filings_index.json"
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
# 10-K Item Extraction
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
    knowledge_dir = ensure_directory(workdir / "knowledge")

    try:
        company = get_company(symbol)
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

                text = clean_sec_text(text, form_type="10-K")
                extracted[item_key] = text

                # Determine filename
                if item_key in _10K_ITEM_MAP:
                    suffix, label = _10K_ITEM_MAP[item_key]
                else:
                    suffix = item_key.lower().replace(" ", "").replace(".", "")
                    label = item_key

                filename = f"sec_10k_{suffix}.md"
                out_path = knowledge_dir / filename

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
# 10-Q Item Extraction
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
    knowledge_dir = ensure_directory(workdir / "knowledge")

    try:
        company = get_company(symbol)
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

                text = clean_sec_text(text, form_type="10-Q")
                extracted[item_key] = text

                # Determine filename
                if item_key in _10Q_ITEM_MAP:
                    suffix, label = _10Q_ITEM_MAP[item_key]
                else:
                    suffix = item_key.lower().replace(" ", "").replace(".", "")
                    label = item_key

                filename = f"sec_10q_{suffix}.md"
                out_path = knowledge_dir / filename

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
