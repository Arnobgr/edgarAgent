# Eval label review checklist

Automatic cross-check of all 80 seed labels against the corpus, mirroring `SectionRef.matches()` (case-insensitive substring; empty section = matches any chunk). Markers:

- **⚠ FLAG** — a hard problem (missing filing / section not present). Fix before verifying.
- **· review** — a judgment call (loose label or a heuristic guess). Valid as-is; tighten if you want.
- **✓** — passed every check; almost certainly fine, a glance is enough.

After reviewing a line, set its `label_status` to `verified` in the source `.jsonl`.


## A. Retrieval queries — 0/50 flagged

| id | query | labeled refs | check | notes |
|---|---|---|---|---|
| rq-001 | Tesla automotive segment revenue drivers most recent fiscal year | TSLA 10-K Item 7 | ✓ |  |
| rq-002 | Tesla management discussion of gross margin changes | TSLA 10-K Item 7 | ✓ |  |
| rq-003 | Tesla risk factors related to lithium and battery raw material supply | TSLA 10-K Item 1A | ✓ |  |
| rq-004 | Tesla regulatory credits revenue recognition | TSLA 10-K Item 7; TSLA 10-K Item 8 | ✓ |  |
| rq-005 | Tesla Full Self-Driving regulatory risks | TSLA 10-K Item 1A | ✓ |  |
| rq-006 | Tesla energy generation and storage business description | TSLA 10-K Item 1 | ✓ |  |
| rq-007 | Tesla manufacturing facilities Gigafactory locations | TSLA 10-K Item 1; TSLA 10-K Item 2 | ✓ |  |
| rq-008 | Tesla legal proceedings pending litigation | TSLA 10-K Item 3 | ✓ |  |
| rq-009 | Tesla income statement total revenues cost of revenues | TSLA 10-K Item 8 | ✓ |  |
| rq-010 | Tesla balance sheet cash and investments | TSLA 10-K Item 8 | ✓ |  |
| rq-011 | Tesla key personnel dependence on Elon Musk risk | TSLA 10-K Item 1A | ✓ |  |
| rq-012 | Tesla quantitative disclosures about market risk interest rates | TSLA 10-K Item 7A | ✓ |  |
| rq-013 | Tesla most recent quarter results of operations | TSLA 10-Q (any) | · review | empty section on 10-Q → matches ANY chunk in the filing (valid but loose; consider narrowing, e.g. Part I, Item 2 (MD&A)) |
| rq-014 | Tesla quarterly vehicle production and delivery numbers press release | TSLA 8-K EX-99 | ✓ |  |
| rq-015 | Tesla energy storage deployments latest quarter announcement | TSLA 8-K EX-99 | ✓ |  |
| rq-016 | Apple services revenue growth discussion | AAPL 10-K Item 7 | ✓ |  |
| rq-017 | Apple iPhone net sales by product category | AAPL 10-K Item 7; AAPL 10-K Item 8 | ✓ |  |
| rq-018 | Apple risk factors App Store regulatory scrutiny | AAPL 10-K Item 1A | ✓ |  |
| rq-019 | Apple dependence on outsourcing partners in Asia supply chain risk | AAPL 10-K Item 1A | ✓ |  |
| rq-020 | Apple capital return program share repurchases | AAPL 10-K Item 7; AAPL 10-K Item 5 | ✓ |  |
| rq-021 | Apple segment reporting Americas Europe Greater China | AAPL 10-K Item 8; AAPL 10-K Item 7 | ✓ |  |
| rq-022 | Apple research and development expense increase explanation | AAPL 10-K Item 7 | ✓ |  |
| rq-023 | Apple description of products and services lineup | AAPL 10-K Item 1 | ✓ |  |
| rq-024 | Apple effective tax rate and deferred taxes | AAPL 10-K Item 8 | ✓ |  |
| rq-025 | Apple commercial paper and term debt disclosures | AAPL 10-K Item 8; AAPL 10-K Item 7 | ✓ |  |
| rq-026 | Apple gross margin by products and services | AAPL 10-K Item 7 | ✓ |  |
| rq-027 | Apple most recent quarterly revenue and earnings announcement | AAPL 8-K EX-99 | ✓ |  |
| rq-028 | Apple condensed financial statements latest quarter | AAPL 10-Q (any) | · review | empty section on 10-Q → matches ANY chunk in the filing (valid but loose; consider narrowing, e.g. Part I, Item 2 (MD&A)) |
| rq-029 | Nvidia data center revenue growth drivers | NVDA 10-K Item 7 | ✓ |  |
| rq-030 | Nvidia risk factors concentration of revenue from limited customers | NVDA 10-K Item 1A | ✓ |  |
| rq-031 | Nvidia export controls China restrictions risk | NVDA 10-K Item 1A; NVDA 10-K Item 7 | ✓ |  |
| rq-032 | Nvidia gaming segment performance discussion | NVDA 10-K Item 7 | ✓ |  |
| rq-033 | Nvidia CUDA software ecosystem competitive advantages | NVDA 10-K Item 1 | ✓ |  |
| rq-034 | Nvidia supply commitments and purchase obligations | NVDA 10-K Item 8; NVDA 10-K Item 7 | ✓ |  |
| rq-035 | Nvidia inventory provisions and write-downs | NVDA 10-K Item 8; NVDA 10-K Item 7 | ✓ |  |
| rq-036 | Nvidia stock-based compensation expense | NVDA 10-K Item 8 | ✓ |  |
| rq-037 | Nvidia automotive segment revenue | NVDA 10-K Item 7 | ✓ |  |
| rq-038 | Nvidia competition from AMD Intel and custom AI accelerators | NVDA 10-K Item 1; NVDA 10-K Item 1A | ✓ |  |
| rq-039 | Nvidia latest quarterly results announcement data center | NVDA 8-K EX-99 | ✓ |  |
| rq-040 | Nvidia most recent 10-Q management discussion | NVDA 10-Q (any) | · review | empty section on 10-Q → matches ANY chunk in the filing (valid but loose; consider narrowing, e.g. Part I, Item 2 (MD&A)) |
| rq-041 | Tesla deferred revenue balance | TSLA 10-K Item 8 | ✓ |  |
| rq-042 | Apple weighted average shares outstanding diluted | AAPL 10-K Item 8 | ✓ |  |
| rq-043 | Nvidia goodwill and intangible assets balance | NVDA 10-K Item 8 | ✓ |  |
| rq-044 | Tesla operating lease obligations | TSLA 10-K Item 8 | ✓ |  |
| rq-045 | Apple net sales fiscal 2024 versus 2023 comparison | AAPL 10-K Item 7 | ✓ |  |
| rq-046 | Nvidia dividend and share repurchase authorization | NVDA 10-K Item 5; NVDA 10-K Item 7 | ✓ |  |
| rq-047 | Tesla accounts receivable and credit risk concentration | TSLA 10-K Item 8 | · review | heuristic: query sounds narrative — confirm Item 8 is right (fine if it's a financial-statement footnote topic) |
| rq-048 | Apple contingencies legal proceedings Epic Games | AAPL 10-K Item 3; AAPL 10-K Item 1A | ✓ |  |
| rq-049 | Nvidia revenue by geography United States China Taiwan | NVDA 10-K Item 8 | ✓ |  |
| rq-050 | Tesla cash flow from operations capital expenditures | TSLA 10-K Item 7; TSLA 10-K Item 8 | ✓ |  |

## B. Gold set — 0/30 flagged

| id | question | category | ans | expected fact | check | notes |
|---|---|---|---|---|---|---|
| g-001 | What was Tesla's total revenue in fiscal year 2025? | single_numeric | Y | TSLA RevenueFromContractWithCustomer FY2025 | ✓ |  |
| g-002 | What was Apple's net income in fiscal year 2025? | single_numeric | Y | AAPL NetIncomeLoss FY2025 | ✓ |  |
| g-003 | How much did Nvidia spend on research and development in its | single_numeric | Y | NVDA ResearchAndDevelopmentExpense FY2026 | ✓ |  |
| g-004 | What was Tesla's gross profit in fiscal year 2024? | single_numeric | Y | TSLA GrossProfit FY2024 | ✓ |  |
| g-005 | What was Apple's total net sales in fiscal year 2024? | single_numeric | Y | AAPL RevenueFromContractWithCustomer FY2024 | ✓ |  |
| g-006 | What were Nvidia's total assets at the end of its latest fis | single_numeric | Y | NVDA us-gaap:Assets FY2026 | ✓ |  |
| g-007 | What was Tesla's operating income in fiscal year 2025? | single_numeric | Y | TSLA OperatingIncomeLoss FY2025 | ✓ |  |
| g-008 | How much cash and cash equivalents did Apple report at the e | single_numeric | Y | AAPL CashAndCashEquivalents FY2025 | ✓ |  |
| g-009 | How has Tesla's total revenue changed over its last three fi | multi_period_trend | Y | TSLA RevenueFromContractWithCustomer FY2023; TSLA  | ✓ |  |
| g-010 | What is the trend in Apple's research and development spendi | multi_period_trend | Y | AAPL ResearchAndDevelopmentExpense FY2023; AAPL Re | ✓ |  |
| g-011 | How has Nvidia's data center business contributed to its rev | multi_period_trend | Y | — | ✓ |  |
| g-012 | How did Tesla's automotive gross margin evolve over the last | multi_period_trend | Y | — | ✓ |  |
| g-013 | Compare Apple's services revenue growth rate to its products | multi_period_trend | Y | — | ✓ |  |
| g-014 | How has Nvidia's net income evolved over its last three fisc | multi_period_trend | Y | NVDA NetIncomeLoss FY2024; NVDA NetIncomeLoss FY20 | ✓ |  |
| g-015 | Which of Tesla, Apple, and Nvidia had the highest revenue in | cross_company | Y | TSLA RevenueFromContractWithCustomer FY2025; AAPL  | ✓ |  |
| g-016 | Compare R&D spending as a share of revenue across Tesla, App | cross_company | Y | TSLA ResearchAndDevelopmentExpense FY2025; AAPL Re | ✓ |  |
| g-017 | How do the China-related risks disclosed by Apple and Nvidia | cross_company | Y | — | ✓ |  |
| g-018 | Compare the net income of Apple and Nvidia in their most rec | cross_company | Y | AAPL NetIncomeLoss FY2025; NVDA NetIncomeLoss FY20 | ✓ |  |
| g-019 | Which company among the three carries the largest total asse | cross_company | Y | TSLA us-gaap:Assets FY2025; AAPL us-gaap:Assets FY | ✓ |  |
| g-020 | Compare how Tesla and Nvidia describe supply chain concentra | cross_company | Y | — | ✓ |  |
| g-021 | What are the main regulatory risks Tesla discloses about its | qualitative | Y | — | ✓ |  |
| g-022 | What does Apple say about its dependence on third-party manu | qualitative | Y | — | ✓ |  |
| g-023 | How does Nvidia characterize the impact of export controls o | qualitative | Y | — | ✓ |  |
| g-024 | What litigation involving the App Store does Apple disclose, | qualitative | Y | — | ✓ |  |
| g-025 | What does Tesla disclose about its dependence on Elon Musk? | qualitative | Y | — | ✓ |  |
| g-026 | How does Nvidia describe competition for its data center pro | qualitative | Y | — | ✓ |  |
| g-027 | What was Microsoft's Azure revenue growth last quarter? | unanswerable | N | — | ✓ |  |
| g-028 | What did Tesla's CFO say about 2027 margin guidance on the l | unanswerable | N | — | ✓ |  |
| g-029 | What is Apple's current market capitalization and today's st | unanswerable | N | — | ✓ |  |
| g-030 | How many Cybertrucks did Tesla deliver in each month of the  | unanswerable | N | — | ✓ |  |
