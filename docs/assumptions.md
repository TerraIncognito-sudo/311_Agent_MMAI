# Cost model assumptions (W7, task 16)

Owner: Saif (G). Drafted Nov 10, 2026. Source IDs refer to docs/sources.md.

Two assumptions drive the result: what an hour of 311 staff time really costs, and what each kind of agent mistake costs. Each one below has a low, base and high value, where it came from, and how sure we are.

## 1. Labour cost per hour

The proposal planned to test $35 to $55 an hour, built up from the Job Bank wage of $17 to $31. That range is too low for Toronto. The City's own posting pays 311 representatives $35.17 to $38.53 an hour before benefits (S17).

| Step | Low | Base | High | Basis |
|---|---|---|---|---|
| Hourly wage | $35.17 | $36.85 | $38.53 | S17. Base is the midpoint |
| Benefits loading | 32.8% | 32.8% | 32.8% | 2026 budget: $5.859M benefits over $17.861M salaries (S16) |
| Loaded cost per paid hour | $46.71 | $48.94 | $51.17 | wage x (1 + loading) |
| Share of paid time spent on contacts | 85% | 70% | 60% | Assumption. No published Toronto figure. Covers breaks, training, wrap up and idle time between contacts |
| Cost per productive hour | $54.95 | $69.91 | $85.28 | loaded cost divided by share of time on contacts |
| Cost of one 312 s intake | $4.76 | $6.06 | $7.39 | cost per productive hour x 312 / 3600 (S7) |

What to carry into the model: test $55 to $85 per productive hour with a base of $70.

Cross check from the top down. The whole division costs $24.423M (S14) and handled about 1.58M interactions (S2), which is $15.46 per interaction. That is well above the $6.06 base because it includes management, technology, counters and capital positions, and because many interactions are self serve. The agent would not remove those costs. So $6.06 is the cost the agent can actually displace, and $15.46 is an upper bound we should mention but not use.

Confidence: wage and benefits are sourced. The share of paid time on contacts is the weak link and should be confirmed with someone who works in a municipal contact centre. It moves the manual cost by about 55% from low to high.

## 2. Cost of each error type

Costs are per occurrence, in dollars. Staff time is priced at the base $69.91 per productive hour.

| Error | Low | Base | High | Basis | Confidence |
|---|---|---|---|---|---|
| Wrong route | $6 | $9 | $18 | Division staff spot the error and bounce it back (about 3 min), then 311 handles the request again (312 s). Low is the rework alone, high adds a resident call back and a second bounce | Medium. Times are our estimate |
| Duplicate ticket | $2 | $4 | $10 | Division staff find and close the extra ticket, 2 to 8 min | Medium |
| Incomplete ticket | $4 | $6 | $9 | One extra contact with the resident to get the missing field, roughly one handle time | Medium |
| Needless escalation | $2 | $3.50 | $6 | A person reviews a prepared handoff package, about 3 min against a full 312 s intake | Medium. Already in the model as human review cost, listed here so it is not counted twice |
| Invented commitment | $0 | $800 | $2,000 | Moffatt v. Air Canada: $812.02 paid for one wrong chatbot statement (S21). Low assumes nobody relies on it, high adds staff and legal time | Low. One precedent, different sector |
| Missed hazard | $100 | $750 | $5,000 | Expected cost, not worst case. Anchor: the average paid pothole vehicle claim in Toronto was $726 in 2025 and the City paid 709 of 2,272 claims (S20). Low assumes most missed hazards are caught by another report before harm. High allows for a share of injury claims, which are far larger than vehicle claims | Low. This is the assumption most likely to flip the recommendation |

Notes on the missed hazard figure.

* A missed hazard does not always cause harm. The cost is the chance of an incident times its size, plus the delay in the City becoming aware of the hazard, which matters because the City is not liable for pothole damage when it has met the provincial maintenance standard.
* We have no data on how often a missed 311 hazard leads to a claim, or on the size of injury claims by cause. The range is a judgment anchored to one sourced number. The report must say so.
* Because the range is wide, the model reports the missed hazard rate at which the agent stops being cheaper than manual intake. That is the scenario that flips the recommendation, which the work plan asks us to show.

## 3. Deployment costs per year

These are the costs the TA will ask about. All are estimates in Canadian dollars and are marked as placeholders in the spreadsheet until someone can confirm them.

| Item | Low | Base | High | Basis |
|---|---|---|---|---|
| Security and privacy review | $20,000 | $40,000 | $80,000 | External assessment once a year, including a privacy impact assessment update |
| Monitoring and evaluation | $12,000 | $24,000 | $48,000 | Logging, dashboards and a monthly sample review of conversations |
| Staff training | $8,000 | $15,000 | $30,000 | Training reviewers on handoff packages. About 300 paid hours at the loaded rate for the base case |
| Policy updates | $10,000 | $20,000 | $40,000 | Keeping the policy file, prompts and tests in step with division rule changes, about 4 updates a year |
| Build cost, spread over 3 years | $30,000 | $50,000 | $100,000 | Integration with the ticket system and initial build, $150,000 base over 3 years |
| Total | $80,000 | $149,000 | $298,000 | |

## 4. What is still a placeholder

Everything about the agent itself: tokens and model cost per request, latency, success rate, escalation share and error rates per setup. These come from the frozen results on Nov 23 (task 35, with Nick). Until then the yellow cells in the spreadsheet hold made up values that only show the model works.

## 5. Notes from the repo (checked Nov 10)

* The event log records `cost_usd` as reported by OpenRouter and 0 for local Ollama models. A setup that runs on Ollama will look free in the results. For those setups the model cost in the spreadsheet has to come from our own hosting estimate (machine cost per hour divided by requests per hour), not from the log.
* The prototype city is the fictional City of Gilead. The Toronto figures here are the status quo baseline for the economics only, and the report should say that once.
* The switch from $35 to $55 an hour to $55 to $85 per productive hour is a model input decision and should get an entry in docs/decisions.md once the team agrees.
