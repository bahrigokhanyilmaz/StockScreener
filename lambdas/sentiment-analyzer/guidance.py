"""
Earnings guidance extraction (Layer 3), one Bedrock call per earnings event.

HLIO fell 26% after Q3 earnings while every stored article was positive: the
guide below consensus, margin down and pulled-forward demand only appeared in
long texts (transcript, full release) that the per-article scorer never sees.
This module reads the best long text plus analyst snippets once per event and
returns a strict, evidence-gated signal. Step 7 turns it into a penalty.

Pure helpers except extract_guidance, which takes the Bedrock client as an argument.
"""

import json
import math
import re
from datetime import date, datetime

from market_time import new_york_date
from news_text import published_utc

GUIDANCE_MAX_CALLS = 12          # peak seen: 9 new earnings events in one day
MAX_INPUT_CHARS = 16_000         # ~4-5k tokens: 10,000-char transcript + 8 snippets x 500 + slack
SNIPPET_CHARS = 500
MAX_SNIPPETS = 8
MAX_OUTPUT_TOKENS = 700
REEXTRACT_MIN_GROWTH = 2_000     # the transcript usually lands a day after the release

VERDICTS = ("positive", "neutral", "negative")
ENUM_FIELDS = {
    "guidance_vs_prior": ("raised", "maintained", "narrowed", "lowered", "not_stated"),
    "guidance_vs_consensus": ("above", "in_line", "below", "not_stated"),
    "margin_outlook": ("up", "flat", "down", "not_stated"),
    "demand_outlook": ("up", "flat", "down", "not_stated"),
}
BOOL_FIELD = "one_time_or_pull_forward_flag"
EVIDENCE_FIELDS = tuple(ENUM_FIELDS) + (BOOL_FIELD,)
MAX_ANALYST_ACTIONS = 8
MAX_QUOTES = 3
QUOTE_CHARS = 160

_PRIMARY_TITLE = re.compile(
    r"earnings|results|guidance|outlook|transcript|quarter|\bq[1-4]\b|surprises|sales|revenue", re.IGNORECASE)
_SNIPPET_TITLE = re.compile(
    r"price target|downgrade|upgrade|guidance|outlook|\bcuts?\b|\braises?\b|reiterat|earnings|results", re.IGNORECASE)

GUIDANCE_PROMPT = """You are an equity analyst reading the earnings coverage of {ticker} ({company_name}) for the event dated {event_date}.

Extract what the text says about the company's outlook. Return ONLY valid JSON (no markdown) with exactly this structure:
{{
  "guidance_vs_prior": "raised|maintained|narrowed|lowered|not_stated",
  "guidance_vs_consensus": "above|in_line|below|not_stated",
  "margin_outlook": "up|flat|down|not_stated",
  "demand_outlook": "up|flat|down|not_stated",
  "one_time_or_pull_forward_flag": true/false,
  "analyst_actions": [{{"firm": "<name>", "action": "<upgrade|downgrade|target cut|target raise|reiterate>", "target": "<price target or empty>"}}],
  "key_quotes_or_numbers": ["<up to 3 short paraphrased items, each under 160 characters>"],
  "evidence_fields_present": [<names of the five fields above whose value the text actually states>],
  "verdict": "positive|neutral|negative",
  "confidence": <float 0.0 to 1.0>
}}

Rules:
- Use "not_stated" rather than guessing. Judge guidance versus consensus or versus prior guidance ONLY if the text itself states the consensus or the prior figure; never use your own memory of consensus.
- guidance_vs_prior and guidance_vs_consensus describe FORWARD guidance, not the quarter just reported. A beat on reported results says nothing about the outlook.
- one_time_or_pull_forward_flag is true only if the text says demand was pulled forward or the results include one-time items.
- List a field in evidence_fields_present only when the text states it. Fields not listed are discarded.
- analyst_actions: only actions named in the text, at most 8.
- verdict: negative if the outlook is weaker than prior guidance or consensus, even when the reported quarter beat.
- Company-issued releases are promotional: lower confidence when no independent source corroborates the outlook.

Text:
{text}
"""


def _reject_constant(name):
    raise ValueError(f"non-finite number {name}")


def _strip_fences(text: str) -> str:
    content = (text or "").strip()
    if content.startswith("```"):
        content = content.split("\n", 1)[1] if "\n" in content else content[3:]
    if content.endswith("```"):
        content = content[:-3].strip()
    return content


def _clean_str(value, limit: int) -> str:
    return value.strip()[:limit] if isinstance(value, str) else ""


def parse_guidance_response(text: str):
    """
    Parse the model reply into a validated signal body, or None if it is not
    usable (invalid JSON, not an object, NaN/Infinity). An invalid enum value
    becomes "not_stated"; any component not listed in evidence_fields_present
    is coerced to "not_stated" (the flag to False), so the model cannot invent consensus.
    """
    try:
        raw = json.loads(_strip_fences(text), parse_constant=_reject_constant)
    except (ValueError, TypeError):
        return None
    if not isinstance(raw, dict):
        return None

    listed = raw.get("evidence_fields_present")
    present = [f for f in EVIDENCE_FIELDS if isinstance(listed, list) and f in listed]

    parsed = {}
    for name, allowed in ENUM_FIELDS.items():
        value = raw.get(name)
        parsed[name] = value if name in present and value in allowed else "not_stated"
    parsed[BOOL_FIELD] = BOOL_FIELD in present and raw.get(BOOL_FIELD) is True

    actions = []
    for item in raw.get("analyst_actions") if isinstance(raw.get("analyst_actions"), list) else []:
        if not isinstance(item, dict):
            continue
        firm = _clean_str(item.get("firm"), 60)
        action = _clean_str(item.get("action"), 40)
        target = item.get("target")
        target = _clean_str(target, 30) if isinstance(target, str) else (
            str(target) if isinstance(target, (int, float)) and not isinstance(target, bool)
            and math.isfinite(target) else "")
        if firm and action:
            actions.append({"firm": firm, "action": action, "target": target})
    parsed["analyst_actions"] = actions[:MAX_ANALYST_ACTIONS]

    quotes = raw.get("key_quotes_or_numbers")
    parsed["key_quotes_or_numbers"] = [q for q in (_clean_str(x, QUOTE_CHARS)
                                                   for x in (quotes if isinstance(quotes, list) else []))
                                       if q][:MAX_QUOTES]
    parsed["evidence_fields_present"] = present
    parsed["verdict"] = raw.get("verdict") if raw.get("verdict") in VERDICTS else "neutral"

    confidence = raw.get("confidence")
    ok = isinstance(confidence, (int, float)) and not isinstance(confidence, bool) and math.isfinite(confidence)
    parsed["confidence"] = round(min(1.0, max(0.0, float(confidence))), 3) if ok else 0.0
    return parsed


def _description(article: dict) -> str:
    return article.get("description") or ""


def _stamp(article: dict) -> str:
    dt = published_utc(article.get("published_at"))
    return dt.strftime("%Y-%m-%d") if dt else "undated"


def build_guidance_input(articles: list, max_chars: int = MAX_INPUT_CHARS) -> tuple:
    """
    Assemble the extraction text: the longest earnings-titled description (transcript
    or full release) first, then up to 8 analyst/guidance snippets cut to 500 chars,
    hard-capped at max_chars. Returns (text, {"input_chars", "input_titles"});
    text is "" when no article has a description.
    """
    primary_pool = [a for a in articles if _description(a) and _PRIMARY_TITLE.search(a.get("title") or "")]
    primary = max(primary_pool, key=lambda a: len(_description(a)), default=None)
    snippets = [a for a in articles
                if a is not primary and _description(a) and _SNIPPET_TITLE.search(a.get("title") or "")]

    parts, titles = [], []
    if primary is not None:
        parts.append(f"PRIMARY SOURCE: {primary.get('title', '')} ({primary.get('source', '')}, "
                     f"{_stamp(primary)})\n{_description(primary)}")
        titles.append(primary.get("title", ""))
    if snippets:
        parts.append("OTHER COVERAGE:")
    for a in snippets[:MAX_SNIPPETS]:
        parts.append(f"- {a.get('title', '')} ({a.get('source', '')}, {_stamp(a)}): "
                     f"{_description(a)[:SNIPPET_CHARS]}")
        titles.append(a.get("title", ""))

    text = "\n\n".join(parts)[:max_chars]
    return text, {"input_chars": len(text), "input_titles": titles[:5]}


def select_guidance_targets(stocks: list, max_calls: int = GUIDANCE_MAX_CALLS) -> tuple:
    """
    Stocks that need an extraction this run: an earnings event with usable text, and
    either no extraction for that event key yet, or one extraction whose input has
    since grown by 2,000+ chars (the transcript arrived). Divergence-flagged stocks
    first, then newest event. Returns (targets, deferred_symbols).
    """
    eligible = []
    for stock in stocks:
        event = stock.get("earnings_event")
        if not event:
            continue
        _, meta = build_guidance_input(stock.get("articles", []))
        if not meta["input_chars"]:
            continue
        prior = stock.get("prior_guidance")
        if prior and prior.get("event_key") == event.get("event_key"):
            grew = meta["input_chars"] - (prior.get("input_chars") or 0) >= REEXTRACT_MIN_GROWTH
            if not ((prior.get("extraction_count") or 1) <= 1 and grew):
                continue
        eligible.append(stock)

    def key(stock):
        event_date = date.fromisoformat(stock["earnings_event"]["event_date"]).toordinal()
        return (not stock.get("news_price_divergence"), -event_date, stock.get("symbol", ""))

    eligible.sort(key=key)
    return eligible[:max_calls], [s.get("symbol", "") for s in eligible[max_calls:]]


def build_guidance_signal(parsed: dict, event: dict, meta: dict, model_id: str,
                          now_utc: datetime, prior: dict = None) -> dict:
    """The stored signal: the parsed body plus event identity and extraction provenance."""
    same_event = bool(prior) and prior.get("event_key") == event.get("event_key")
    return {
        **parsed,
        "event_key": event["event_key"],
        "event_date": event["event_date"],
        "extracted_at": now_utc.isoformat(),
        "extracted_on": new_york_date(now_utc),
        "extraction_count": ((prior.get("extraction_count") or 1) + 1) if same_event else 1,
        "input_chars": meta["input_chars"],
        "input_titles": meta["input_titles"],
        "model": model_id,
    }


def extract_guidance(stock: dict, bedrock, model_id: str, now_utc: datetime):
    """One Bedrock call for one stock's earnings event. None when it fails (retried next run)."""
    symbol = stock.get("symbol", "?")
    text, meta = build_guidance_input(stock.get("articles", []))
    if not text:
        return None
    event = stock["earnings_event"]
    prompt = GUIDANCE_PROMPT.format(ticker=symbol, company_name=stock.get("company_name", symbol),
                                    event_date=event["event_date"], text=text)
    try:
        response = bedrock.invoke_model(
            modelId=model_id,
            contentType="application/json",
            accept="application/json",
            body=json.dumps({
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": MAX_OUTPUT_TOKENS,
                "temperature": 0.0,
                "messages": [{"role": "user", "content": prompt}],
            }),
        )
        reply = json.loads(response["body"].read()).get("content", [{}])[0].get("text", "")
    except Exception as e:
        print(f"    Warning: guidance call failed for {symbol}: {e}")
        return None
    parsed = parse_guidance_response(reply)
    if parsed is None:
        print(f"    Warning: unparseable guidance reply for {symbol}: {reply[:100]}")
        return None
    return build_guidance_signal(parsed, event, meta, model_id, now_utc, stock.get("prior_guidance"))
