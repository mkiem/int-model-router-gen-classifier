#!/usr/bin/env python3
"""Demo reel: stream 100 unseen prompts through the Intelligent Model Router
and show which model each one routes to. Made for terminal recording.

Usage:
  ROUTER_URL=https://<gateway>/inference/v1/chat/completions python3 demo_reel.py
  python3 demo_reel.py --quick        # first 20 prompts only
  python3 demo_reel.py --delay 0.8    # pause between prompts (default 0.5s)
"""
import argparse
import json
import os
import sys
import time

import boto3
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.httpsession import URLLib3Session

URL = os.environ.get("ROUTER_URL", "")
REGION = os.environ.get("AWS_REGION", "us-east-1")

# tier colors: fast green, balanced blue, coding yellow, frontier magenta
TIER_OF = {
    "luna": ("fast", "\033[92m"), "kimi-k2.5": ("fast", "\033[92m"),
    "terra": ("balanced", "\033[94m"), "sonnet": ("balanced", "\033[94m"),
    "kimi-k3": ("coding", "\033[93m"),
    "sol": ("frontier", "\033[95m"), "opus": ("frontier", "\033[95m"),
}
RESET, DIM, BOLD = "\033[0m", "\033[2m", "\033[1m"
# rough blended $/M tokens (in+out at 300/500 mix) for the savings estimate
TIER_COST = {"fast": 0.83, "balanced": 8.25, "coding": 1.79, "frontier": 14.0}

PROMPTS = [
    # --- simple_qa ---
    "What year did the Berlin Wall fall?",
    "How many ounces are in a pound?",
    "What's the chemical symbol for gold?",
    "Who wrote One Hundred Years of Solitude?",
    "What timezone is Denver in?",
    # --- classification ---
    "Classify this ticket as billing, technical, or account: 'I was charged twice for my subscription this month.'",
    "Is this review positive or negative? 'The battery died within a week and support never responded.'",
    "Tag this email as urgent or routine: 'Server room temperature alarm triggered on rack 4.'",
    "Categorize this expense: 'Uber to airport for client meeting, $54'",
    "Is this sentence formal or informal? 'Hey, gonna bounce early today, cool?'",
    # --- extraction ---
    "Extract the invoice number, date, and total from: 'Invoice #INV-2291 dated March 3, 2026. Amount due: $1,240.50'",
    "Pull all email addresses from this text: 'Contact sara.chen@acme.io or escalate to ops-team@acme.io after hours.'",
    "Extract company names: 'The merger between Northfield Capital and Brightline Logistics closed Tuesday.'",
    "List the dates mentioned: 'The audit runs June 2-6 with findings due June 20 and remediation by Q3.'",
    "Extract the shipping address from: 'Please deliver to 44 Harbor Lane, Suite 210, Portsmouth NH 03801.'",
    # --- summarization ---
    "Summarize in one sentence: 'The quarterly review found that while revenue grew 12%, customer acquisition costs rose 30%, driven primarily by increased paid-search competition, prompting a shift toward referral programs.'",
    "TL;DR this update: 'Migration of the reporting database completed Saturday. Two dashboards showed stale data Sunday morning; caches were flushed and all metrics reconciled by noon. No customer impact.'",
    "Give me the key point: 'After testing three vendors over six weeks, procurement recommends CloudFleet based on 40% lower per-seat cost and SOC 2 compliance, despite weaker mobile support.'",
    "Condense to a headline: 'Local startup raises $30M Series B to expand its battery recycling technology across three new states.'",
    "Summarize for an executive: 'Support ticket volume rose 18% after the pricing change, concentrated in the first 72 hours, with 80% resolved by the new self-service FAQ.'",
    # --- rewriting ---
    "Make this more polite: 'Your report is late again. Send it today.'",
    "Fix the grammar: 'Neither of the options were viable because them requiring approvals we dont have.'",
    "Rewrite for a 5th grader: 'Photosynthesis converts electromagnetic radiation into chemical energy via chlorophyll-mediated reactions.'",
    "Make this tweet-length: 'We are excited to announce that our annual developer conference will return this September in Austin with three days of workshops, talks, and networking.'",
    "Remove the jargon: 'We need to leverage synergies and operationalize our learnings to move the needle on KPIs.'",
    # --- translation ---
    "Translate to Spanish: 'The meeting has been moved to Thursday at 3pm.'",
    "Translate to German: 'Please review the attached contract before Friday.'",
    "Translate to French: 'Our office will be closed for the national holiday.'",
    "Translate to Japanese: 'Thank you for your patience while we resolve this issue.'",
    "Translate to Portuguese: 'The invoice was paid in full last week.'",
    # --- code_simple ---
    "What does this regex match? ^[A-Z]{2}\\d{6}$",
    "Explain this one-liner: numbers.filter(n => n % 2 === 0).reduce((a, b) => a + b, 0)",
    "Write a SQL query to select all users created in the last 7 days.",
    "Add type hints to: def area(radius): return 3.14159 * radius ** 2",
    "What does 'git stash pop' do?",
    # --- coding ---
    "Write a Python function that validates credit card numbers using the Luhn algorithm, with unit tests.",
    "Debug this: my Flask endpoint returns 500 when the JSON body is missing a key. Show defensive parsing.",
    "Write a SQL query joining orders and customers tables to find the top 10 customers by lifetime value.",
    "Implement a rate limiter class in TypeScript using the token bucket algorithm.",
    "Review this function for bugs: def divide(a, b): return a / b if b else 0",
    # --- code_architecture ---
    "Design the database schema for a multi-tenant SaaS invoicing platform. Justify your tenant isolation choice.",
    "Should we use event sourcing or CRUD for an audit-heavy insurance claims system? Analyze the tradeoffs.",
    "Design a novel algorithm to detect near-duplicate documents across 100M files with bounded memory. Analyze complexity.",
    "Our p99 latency spikes every hour on the hour. Walk through diagnosing this in a system with cron jobs, cache TTLs, and connection pools.",
    "Plan the migration of a 2M-line monolith to services without a feature freeze. What do you carve out first and why?",
    # --- sysadmin_scripting ---
    "Write a bash script that alerts if disk usage on any mounted volume exceeds 85%.",
    "PowerShell script to disable AD accounts inactive for 90 days and log the changes.",
    "Write a cron-scheduled script that backs up /etc/nginx and rotates backups older than 30 days.",
    "Bash script to parse nginx access logs and print the top 20 IPs by request count in the last hour.",
    "Script to snapshot all EBS volumes tagged env=prod and delete snapshots older than 14 days.",
    # --- product_requirements ---
    "Write user stories with acceptance criteria for a dark mode feature in our mobile banking app.",
    "Break this epic into stories: 'Customers can manage their own API keys from the dashboard.'",
    "Draft the requirements section of a PRD for bulk CSV import of contacts, including error handling expectations.",
    "Turn this stakeholder ask into requirements: 'Sales wants to see which deals are stuck.'",
    "Write acceptance criteria for a password-reset flow that supports email and SMS.",
    # --- customer_support ---
    "Write a reply to a customer whose order arrived damaged and who wants a refund.",
    "A user says they were double-charged. Draft an empathetic response with next steps.",
    "Respond to: 'Your app deleted all my saved playlists and I am furious.'",
    "Draft a response to a customer asking why their account was flagged for unusual activity.",
    "Write a follow-up to a customer whose support ticket has been open for 10 days.",
    # --- rag_qa ---
    "Based on our returns policy document, can a customer return opened electronics after 20 days?",
    "According to the employee handbook section on PTO, do unused days roll over?",
    "Using the attached lease agreement, who is responsible for HVAC repairs?",
    "Per the API documentation, what is the rate limit for the /search endpoint?",
    "From the onboarding guide, what accounts does a new engineer need on day one?",
    # --- content_generation ---
    "Write a product description for noise-canceling headphones aimed at remote workers.",
    "Draft a LinkedIn post announcing our company's B Corp certification.",
    "Write a welcome email sequence outline for new trial users of a project management tool.",
    "Create a job posting for a senior data engineer, hybrid, fintech.",
    "Write the About Us page for a family-owned coffee roastery founded in 1987.",
    # --- analysis ---
    "Our churn rose from 2.1% to 3.4% after a price increase, but ARPU rose 18%. Analyze whether the change was net positive.",
    "Compare the risks of expanding to Germany vs Japan for a B2B payments startup.",
    "Interpret this A/B test: variant B lifted signups 4% but dropped activation 7%. What would you ship?",
    "Analyze why our mobile conversion is half of desktop despite equal traffic quality.",
    "Given rising interest rates, analyze whether we should lease or buy our next fulfillment center.",
    # --- creative_writing ---
    "Write a six-word story about a lighthouse keeper.",
    "Write the opening paragraph of a mystery set in a data center.",
    "Compose a limerick about a forgetful barista.",
    "Write a short scene where two rival food truck owners get stuck in an elevator.",
    "Write a haiku about deploying on a Friday.",
    # --- math ---
    "If a portfolio returns 7% annually, how long until it doubles? Show the math.",
    "A tank fills at 12L/min and drains at 7L/min. How long to fill 600L from empty?",
    "What's the probability of rolling at least one six in four dice rolls?",
    "Solve: a train leaves at 2pm at 80km/h; another at 3pm at 110km/h on the same route. When does the second catch the first?",
    "Calculate the monthly payment on a $400k mortgage at 6.5% over 30 years.",
    # --- reasoning_planning ---
    "Plan a zero-downtime datacenter-to-cloud migration for a hospital's patient records system. Sequence the phases and justify.",
    "Three suspects each make two statements, one true one false. A: 'B did it. I didn't.' B: 'C did it. A is lying.' C: 'I didn't do it. B did.' Who did it?",
    "Design a fair on-call rotation for 7 engineers across 3 timezones with no one working more than one weekend a month.",
    "We have $2M runway, 14 months, and two products at 40% completion each. Reason through whether to cut one.",
    "Plan the rollback strategy for a database schema change that cannot be reversed with a simple migration.",
    # --- agentic ---
    "Research our top three competitors' pricing pages, compare against ours, and recommend a repositioning.",
    "Go through the attached contract repository, flag all agreements expiring within 90 days, and draft renewal emails for each.",
    "Monitor this error log stream, correlate spikes with our deploy history, and open tickets for the two most likely root causes.",
    "Audit our AWS account for untagged resources, propose a tagging scheme, and generate the remediation commands.",
    "Pull last quarter's sales data, segment by region, build a forecast, and prepare talking points for the board.",
    # --- boundary / fun (let the router decide) ---
    "Explain quantum entanglement to a curious 10-year-old.",
    "Is 'data' singular or plural?",
    "Write a bash one-liner to count lines of Python code in a repo.",
    "Prove that the square root of 2 is irrational.",
    "What should I name my new sourdough starter?",
]


# The prompt list above is grouped by task class (5 per class) for editability.
# For the reel, interleave one prompt per class per round, with classes hand-
# ordered so expected tiers alternate (fast/coding/balanced/frontier) and the
# screen color keeps changing.
GROUPS = [PROMPTS[i:i + 5] for i in range(0, len(PROMPTS), 5)]
# group indices: 0 simple_qa 1 classify 2 extract 3 summarize 4 rewrite
# 5 translate 6 code_simple 7 coding 8 code_arch 9 sysadmin 10 product
# 11 support 12 rag_qa 13 content 14 analysis 15 creative 16 math
# 17 reasoning 18 agentic 19 fun
ORDER = [0, 7, 10, 16, 1, 9, 11, 8, 2, 12, 17, 3, 13, 18, 4, 14, 19, 5, 15, 6]
PROMPTS = [GROUPS[g][i] for i in range(5) for g in ORDER]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true", help="first 20 prompts only")
    ap.add_argument("--delay", type=float, default=0.5)
    args = ap.parse_args()
    if not URL:
        sys.exit("Set ROUTER_URL to your gateway's ChatCompletionsUrl stack output.")

    prompts = PROMPTS[:20] if args.quick else PROMPTS
    creds = boto3.Session().get_credentials().get_frozen_credentials()
    sess = URLLib3Session()
    counts, latencies = {}, []

    print(f"\n{BOLD}  INTELLIGENT MODEL ROUTER — {len(prompts)} unseen prompts, model=\"auto\"{RESET}")
    print(f"{DIM}  every routing decision below is made live, in real time{RESET}\n")

    for i, prompt in enumerate(prompts, 1):
        shown = prompt if len(prompt) <= 78 else prompt[:75] + "..."
        print(f"  {DIM}{i:3d}{RESET}  {shown}")
        req = AWSRequest(method="POST", url=URL, headers={"Content-Type": "application/json"},
            data=json.dumps({"model": "auto", "max_tokens": 1,
                             "messages": [{"role": "user", "content": prompt}]}))
        SigV4Auth(creds, "bedrock-agentcore", REGION).add_auth(req)
        t0 = time.monotonic()
        r = sess.send(req.prepare())
        ms = (time.monotonic() - t0) * 1000
        if r.status_code == 200:
            model = json.loads(r.content).get("model", "?")
            tier, color = next((v for k, v in TIER_OF.items() if k in model), ("?", ""))
            counts[tier] = counts.get(tier, 0) + 1
            latencies.append(ms)
            print(f"       {color}→ {model}{RESET}  {DIM}[{tier}] {ms:.0f}ms{RESET}\n")
        else:
            print(f"       \033[91m→ HTTP {r.status_code}{RESET}\n")
        time.sleep(args.delay)

    n = sum(counts.values()) or 1
    blended = sum(TIER_COST[t] * c for t, c in counts.items() if t in TIER_COST) / n
    savings = (1 - blended / TIER_COST["frontier"]) * 100
    print(f"\n{BOLD}  ══ SUMMARY ═════════════════════════════════════════{RESET}")
    for tier, color in [("fast", "\033[92m"), ("balanced", "\033[94m"),
                        ("coding", "\033[93m"), ("frontier", "\033[95m")]:
        c = counts.get(tier, 0)
        bar = "█" * round(40 * c / n)
        print(f"  {color}{tier:9s}{RESET} {c:3d}  {color}{bar}{RESET}")
    print(f"\n  {BOLD}estimated token-spend savings vs always-frontier: {savings:.0f}%{RESET}")
    print(f"  {DIM}median round trip: {sorted(latencies)[len(latencies)//2]:.0f}ms "
          f"(includes routing decision + first token){RESET}\n")


if __name__ == "__main__":
    main()
