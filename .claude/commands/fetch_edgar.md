---
description: Fetch SEC filings (10-K, 10-Q, 8-K) from EDGAR for a ticker
---

# Fetch EDGAR

Run the fetch_edgar skill for the given ticker.

**Arguments:** $ARGUMENTS (expects: SYMBOL --workdir DIR [--skip-financials] [--skip-8k])

```bash
uv run ./skills/fetch_edgar/fetch_edgar.py $ARGUMENTS
```

Execute the command above. Report the results: what artifacts were produced, any errors or warnings, and the JSON manifest output.
