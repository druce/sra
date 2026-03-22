# Profile Section Evaluation Rubric

Score each dimension 1-10. Be harsh — a 7 is good, an 8 is exceptional, a 9-10 is nearly impossible. Most professional equity research scores 5-7. Return JSON only.

## Dimensions

### 1. Completeness (does it cover every required element?)
Check for presence and depth of each element. For each missing or shallow element, deduct 1 point from 10:
- [ ] Executive summary (what company does, scale, why interesting) — must include revenue, market cap, employee count
- [ ] Founding story (specific date, named founders, early vision)
- [ ] History and key milestones (IPO date, major acquisitions with $ amounts, pivots, product launches with dates)
- [ ] Core business (revenue segments with approximate sizes and growth rates)
- [ ] Business model (how the company actually makes money — not vague generalities)
- [ ] Competitive advantages (specific moat sources with evidence, not just "strong brand")
- [ ] Key investor metrics (specific numbers: margins, growth rates, returns on capital)
- [ ] Recent developments (last 12 months, with specific figures, dates, and why each matters)
- [ ] Missing analysis: are there significant topics covered in source material (SEC filings, research findings) that are absent from the draft? Deduct for major omissions.

Score: 10 = all 8 elements present with specific numbers/dates, no major source topics omitted. 7 = all present but some lack specifics. 5 = 4+ elements missing or generic. 3 = mostly boilerplate.

### 2. Correct length
Count the words. Target: 800-1200 words.
- 10 = 800-1200 words
- 8 = 700-800 or 1200-1400
- 5 = 500-700 or 1400-1700
- 3 = under 500 or over 1700
- 1 = under 300 or over 2000

### 3. Insight quality
- Does it explain *why* facts matter for the investment case, not just state them?
- Does it connect history to current competitive position (cause and effect)?
- Does it identify the central bull/bear debate?
- Are numbers contextualized (vs. peers, vs. history, vs. expectations)?
- Are factual claims grounded in sources? Deduct for unsupported claims or speculation not traceable to indexed sources or structured artifacts.
- Are opinions clearly distinguished from facts? Analysis should use framing like "this suggests/indicates/implies" rather than presenting interpretations as objective data.
- Deduct points for: paragraphs that only restate facts without analysis, generic statements that could apply to any company, missing "so what?" on key data points, claims that appear fabricated or unverifiable

Score: 10 = every claim has analytical framing and source grounding. 7 = mostly analytical with some flat spots. 5 = half restatement, half analysis. 3 = reads like a Wikipedia summary.

### 4. Relevance
- Would a portfolio manager skip any paragraph? If yes, deduct points.
- Is there any filler, throat-clearing, or generic industry context that doesn't serve the investment case?
- Does it prioritize material information (things that move the stock) over trivia?
- Is there any repetition — the same point made in different words across paragraphs? Deduct for each instance.
- Deduct 1 point for each paragraph that doesn't directly serve an investment decision

Score: 10 = zero filler or repetition, every sentence earns its place. 7 = tight but 1-2 soft spots. 5 = noticeable padding. 3 = half the content is skippable.

### 5. Professional style
- Reads like a Goldman/Morgan Stanley initiation, not a blog post or press release?
- Confident, direct assertions — not hedging with "it should be noted" or "it is worth mentioning"?
- No bullet-point lists where prose is expected?
- Clean heading hierarchy (## 1. Company Profile, then ## subheadings)?
- Section MUST start with `## 1. Company Profile` as the first line
- No LLM tells: "In conclusion", "Overall", "It's important to note", "comprehensive", "robust"
- Deduct points for: passive voice, weasel words, unnecessary qualifiers, repetition of the same point in different words
- Number formatting: stock prices to 2 decimals ($328.47), market cap in billions with 1 decimal ($24.3B), percentages to 1 decimal (23.4%), ratios to 1 decimal (18.3x)
- Large numbers use readable form ("$34.3 billion" not "$34,300,000,000")
- First reference uses full legal name + ticker in parens, e.g., "NVIDIA Corporation (NASDAQ: NVDA)"
- Revenue labels fiscal year explicitly
- Uses specific numbers throughout, not vague qualifiers
- Acknowledges uncertainty where it exists — does not oversell or use marketing language

Score: 10 = indistinguishable from a top-tier analyst's writing. 7 = professional but slightly formulaic. 5 = competent but reads like AI. 3 = obviously machine-generated.

## Output format
```json
{
  "completeness": N,
  "length": N,
  "insight": N,
  "relevance": N,
  "style": N,
  "total": N.N,
  "notes": "one-line summary of biggest issue"
}
```
`total` = average of the 5 scores.
