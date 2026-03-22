# Competitive Landscape Section Evaluation Rubric

Score each dimension 1-10. Be harsh — a 7 is good, an 8 is excellent, a 9-10 is exceptional. Most professional equity research scores 5-7. Return JSON only.

## Dimensions

### 1. Completeness (does it cover every required element?)
Check for presence and depth of each element. For each missing or shallow element, deduct 1 point from 10:
- [ ] Main competitors identified — direct, adjacent, and emerging — with their value propositions
- [ ] Per-competitor comparison: revenue scale, market share, product lineup differences, specific advantages/threats, vulnerabilities relative to subject company
- [ ] Competitive environment trends: where unit and dollar market share is being gained or lost, whether shifts are structural or cyclical, evidence of increasing/decreasing pricing power
- [ ] Emerging or potential disruptors and risk of disruption from adjacent markets
- [ ] Peer comparison table with key metrics (revenue, margins, growth, valuation multiples)
- [ ] Source triangulation: claims confirmed by 2+ sources stated with confidence; single-source claims qualified with attribution; conflicting sources acknowledged with weighting rationale
- [ ] Missing analysis: are there significant competitive topics covered in source material (SEC filings, research findings, indexed chunks) that are absent from the draft? Deduct for major omissions.

Score: 10 = all 7 elements present with specific numbers/data and peer table, no major source topics omitted. 7 = all present but some lack specifics or table is thin. 5 = 3+ elements missing or generic. 3 = mostly boilerplate.

### 2. Correct length
Count the words. Target: 1400-2200 words.
- 10 = 1400-2200 words
- 8 = 1200-1400 or 2200-2500
- 5 = 900-1200 or 2500-2800
- 3 = under 900 or over 2800
- 1 = under 600 or over 3200

### 3. Insight quality
- Does it explain *why* competitive dynamics matter for the investment case, not just list competitors?
- Does it analyze dynamics: share shifts, pricing power trends, structural vs cyclical changes, competitive moat durability?
- Are competitive advantages evidenced with data (market share numbers, margin comparisons, scale metrics) rather than asserted?
- Are numbers contextualized (vs. peers, vs. history, vs. expectations)?
- Are factual claims grounded in sources? Deduct for unsupported claims or speculation not traceable to indexed sources or structured artifacts.
- Are opinions clearly distinguished from facts? Analysis should use framing like "this suggests/indicates/implies" rather than presenting interpretations as objective data.
- Deduct points for: paragraphs that only restate facts without analysis, generic statements that could apply to any industry, missing "so what?" on competitive data points, claims that appear fabricated or unverifiable

Score: 10 = every claim has analytical framing with dynamics analysis and source grounding. 7 = mostly analytical with some flat spots. 5 = half restatement, half analysis. 3 = reads like a company website competitor list.

### 4. Relevance
- Would a portfolio manager skip any paragraph? If yes, deduct points.
- Is there any filler, throat-clearing, or generic industry context that doesn't serve the investment case?
- Does it prioritize material competitive threats (things that move the stock) over trivial competitors?
- Is there any repetition — the same competitive point made in different words across paragraphs or subsections? Deduct for each instance.
- Deduct 1 point for each paragraph that doesn't directly serve an investment decision

Score: 10 = zero filler or repetition, every sentence earns its place. 7 = tight but 1-2 soft spots. 5 = noticeable padding. 3 = half the content is skippable.

### 5. Professional style
- Reads like a Goldman/Morgan Stanley initiation, not a blog post or press release?
- Confident, direct assertions — not hedging with "it should be noted" or "it is worth mentioning"?
- No bullet-point lists where prose is expected (tables are fine and encouraged)?
- Clean heading hierarchy (## 3. Competitive Landscape, then ## subheadings)?
- Section MUST start with `## 3. Competitive Landscape` as the first line
- No LLM tells: "In conclusion", "Overall", "It's important to note", "comprehensive", "robust"
- Deduct points for: passive voice, weasel words, unnecessary qualifiers, repetition of the same point in different words
- Number formatting: stock prices to 2 decimals ($328.47), market cap in billions with 1 decimal ($24.3B), percentages to 1 decimal (23.4%), ratios to 1 decimal (18.3x)
- Large numbers use readable form ("$34.3 billion" not "$34,300,000,000")
- Revenue labels fiscal year explicitly
- Uses specific numbers throughout, not vague qualifiers
- Acknowledges uncertainty where it exists — does not oversell or use marketing language
- Data tables are well-formatted with aligned columns and clear labels

Score: 10 = indistinguishable from a top-tier analyst's writing. 7 = professional but slightly formulaic. 5 = competent but reads like AI. 3 = obviously machine-generated.

## Output format

MUST RETURN JSON ONLY, NO MARKDOWN OR CODE FENCES.

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
