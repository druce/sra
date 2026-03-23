# Risks Section Autotuner

You are autonomously improving the equity research risks writer for NVDA.

## Setup
- Working directory: work/NVDA_20260318
- The data (artifacts/, knowledge/, lancedb/, findings_risk_news.md) is FROZEN. Do not modify it.
- You modify ONLY the `write_risk_news` prompt in dags/sra.yaml
- The evaluation rubric is in evaluate_risk_news.md. Do not modify it.
- n_iterations must stay at 0 (no critic loop — we're tuning the raw write)

## The Loop

LOOP FOREVER:

1. Review the current write_risk_news prompt and recent experiment results in experiments.tsv
2. Read the most recent output (work/NVDA_20260318/artifacts/section_7_risk_news.md) and understand WHY it scored the way it did
3. Propose ONE change to the write_risk_news prompt
   - Examples: restructure section requirements, add specificity ("include probability/magnitude assessment"),
     add style examples, change emphasis, adjust length guidance, modify heading structure,
     require risk prioritization by materiality, strengthen catalyst analysis, demand mitigating factors,
     sharpen priced-in vs underappreciated distinction, change which artifacts_inline are included, adjust search --top-k or --sections
4. Git commit the change with a descriptive message
5. Delete old output and re-run (--task auto-resets status):
   ```bash
   rm -f work/NVDA_20260318/artifacts/section_7_risk_news.md
   rm -f work/NVDA_20260318/drafts/section_7_risk_news*
   ./research.py NVDA --date 20260318 --task write_risk_news --reload-yaml
   ```
6. Run 5 evals in parallel using the Agent tool. Launch all 5 as background agents in a single message.
   Each agent gets this prompt (with its eval number N):

   > Read evaluate_risk_news.md for the rubric.
   > Read work/NVDA_20260318/artifacts/section_7_risk_news.md for the section to evaluate.
   > Apply the rubric and return ONLY raw JSON (no markdown fences):
   > {"completeness": N, "length": N, "insight": N, "relevance": N, "style": N, "total": N.N, "notes": "..."}
   > Write the result to work/NVDA_20260318/tmp/eval_rn_N.json

   Do NOT use `claude -p` subprocess calls — that spawns separate sessions and causes auth issues.
   Wait for all 5 agents to complete. Do not rerun unless an agent fails to produce valid JSON.

7. Compute trimmed scores (drop best and worst per dimension, average middle 3):
   ```bash
   python3 -c "
   import json, re

   results = []
   for i in range(1, 6):
       with open(f'work/NVDA_20260318/tmp/eval_rn_{i}.json') as f:
           text = f.read().strip()
           m = re.search(r'\{[^}]+\}', text, re.DOTALL)
           results.append(json.loads(m.group()))

   dims = ['completeness', 'length', 'insight', 'relevance', 'style']
   trimmed = {}
   for d in dims:
       vals = sorted(r[d] for r in results)
       trimmed[d] = round(sum(vals[1:4]) / 3, 2)  # drop min and max

   total = round(sum(trimmed.values()) / len(dims), 2)
   print(f'total={total}')
   for d in dims:
       print(f'{d}={trimmed[d]}')
   "
   ```
8. Record results in experiments.tsv (commit hash, trimmed total, per-dimension trimmed scores, status, description). Use these trimmed averages — do not rerun evals.
9. Use the trimmed per-dimension scores to guide your next change: focus on the lowest-scoring dimension.
10. If trimmed total > previous best → KEEP (advance branch)
11. If trimmed total ≤ previous best → DISCARD:
   ```bash
   git reset --hard HEAD~1
   ```
12. GOTO 1

## What you CAN modify
- Prompt text in the `write_risk_news` task (structure, length, emphasis, examples, section requirements)
- The `artifacts_inline` list for write_risk_news (which artifacts are passed inline)
- The `system` prompt for write_risk_news
- Search parameters (--top-k, --sections) in the writer prompt

## What you CANNOT modify
- research.py, db.py, or any Python scripts
- evaluate_risk_news.md (the rubric is fixed)
- The research_risk_news prompt (research findings are frozen)
- Any files in work/NVDA_20260318/ (except section_7_risk_news.md which gets regenerated)
- n_iterations must stay at 0

## Strategy guidance
- One change at a time — multi-variable changes make attribution impossible
- Keep diffs small and focused
- ALWAYS read the actual output before proposing changes — understand WHY it scored low
- If completeness is low → add specific elements (risk categories with subcategories, specific dates/figures requirements, analyst sentiment detail)
- If insight is low → add instructions demanding risk analysis framework ("assess probability/magnitude, mitigating factors, how market prices each risk, priced-in vs underappreciated")
- If style is off → add concrete examples of desired prose style, or tighten formatting rules
- If length is wrong → adjust word count guidance
- If relevance is low → prune tangential instructions, add "every sentence must serve the investment case"
- Track which dimensions are bottlenecks — focus on the lowest-scoring dimension
- Risk prioritization by materiality is expected — risks should be ordered by impact on the stock, not alphabetically

## NEVER STOP
Do not pause to ask if you should continue. Run until interrupted.
