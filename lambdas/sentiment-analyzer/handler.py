"""
Sentiment Analyzer Lambda
==========================
Step 4 in the pipeline.

Takes stocks with their news articles (from news-fetcher, Step 3) and
sends each article to Amazon Bedrock (Claude) for sentiment analysis.

Claude evaluates each article and returns:
- relevance: Is this actually about the company? (filters noise)
- sentiment: Score from -1.0 (very negative) to +1.0 (very positive)
- confidence: How confident the model is in its assessment (0-1)
- summary: One-sentence summary of the article's implication
- risk_flags: Any detected red flags (lawsuits, fraud, regulatory, etc.)

The per-article sentiments are aggregated into a single sentiment score
per stock (weighted by recency and confidence).

Environment Variables:
    BEDROCK_MODEL_ID - Which Claude model to use (default: claude-3-haiku)
    RAW_DATA_BUCKET  - S3 bucket for storing raw sentiment results

Cost Estimate:
    ~300 articles/day × ~500 tokens input × $0.25/M = ~$0.04/day
    Output: ~200 tokens × $1.25/M × 300 = ~$0.08/day
    Total: ~$0.12/day = ~$3.60/month
"""

import json
import os
import time
from datetime import datetime, timezone

import boto3

# AWS clients
s3_client = boto3.client("s3")
bedrock_client = boto3.client("bedrock-runtime")

# Default model — Claude Haiku 4.5 (successor to Claude 3 Haiku)
# Cheapest current Claude model, fast, good at classification tasks.
DEFAULT_MODEL_ID = "us.anthropic.claude-haiku-4-5-20251001-v1:0"

# Sentiment analysis prompt template
SENTIMENT_PROMPT = """You are a financial sentiment analyzer for stock investors. Analyze the following news article about {ticker} ({company_name}).

Return ONLY valid JSON (no markdown, no explanation) with this exact structure:
{{
  "relevant": true/false,
  "sentiment": <float from -1.0 to 1.0>,
  "confidence": <float from 0.0 to 1.0>,
  "summary": "<one sentence about the article's implication for investors>",
  "risk_flags": [<list of strings from allowed values only, empty if none>]
}}

Scoring guide:
- relevant: Is this article actually about {ticker} AND does it contain substantive information about the company's business, financials, or prospects? Mark FALSE for: scheduling announcements (conference attendance, earnings date notices), press release date notifications, articles that mention the company only in passing, or stub articles with no real content.
- sentiment: -1.0 = very negative for stock (fraud, lawsuits, bankruptcy). 0.0 = neutral. +1.0 = very positive (strong earnings beat, major contract win).
- confidence: How confident you are in the sentiment score. Low confidence for short/vague articles.
- risk_flags: Only include if the article describes one of these SPECIFIC situations. Use ONLY these exact values — do not invent new ones:
  "SEC_investigation" — active SEC enforcement or investigation
  "fraud_allegation" — alleged fraud by the company or executives
  "accounting_irregularity" — restatements, audit concerns, material weakness
  "lawsuit" — material litigation (class action, patent, antitrust)
  "regulatory_risk" — new regulation threatening the business model
  "management_departure" — sudden CEO/CFO exit without succession plan
  "product_recall" — major product safety issue or recall
  "revenue_risk" — concrete threat to future revenue: lowered guidance, major contract non-renewal announced, key customer publicly departing, regulatory ban on a revenue stream, or pricing collapse in core market. Do NOT flag for: past revenue declines already reflected in financials, one-time items normalizing, divestitures, or accounting reclassifications. A quarter-over-quarter decline alone is not a flag — there must be evidence of ongoing or future deterioration.

Article title: {title}
Article source: {source}
Article text: {description}
"""


def analyze_article(article: dict, ticker: str, company_name: str, model_id: str) -> dict:
    """
    Send a single article to Claude via Bedrock for sentiment analysis.

    Args:
        article: Dict with title, description, source, url
        ticker: Stock symbol
        company_name: Company name for context
        model_id: Bedrock model ID

    Returns:
        Dict with sentiment analysis results + original article data
    """
    prompt = SENTIMENT_PROMPT.format(
        ticker=ticker,
        company_name=company_name,
        title=article.get("title", ""),
        source=article.get("source", ""),
        description=article.get("description", "")[:2000],  # Limit text to save tokens
    )

    try:
        response = bedrock_client.invoke_model(
            modelId=model_id,
            contentType="application/json",
            accept="application/json",
            body=json.dumps({
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": 300,
                "temperature": 0.0,  # Deterministic — we want consistent scoring
                "messages": [
                    {"role": "user", "content": prompt}
                ],
            }),
        )

        response_body = json.loads(response["body"].read())
        content = response_body.get("content", [{}])[0].get("text", "")

        # Strip markdown code fences if Claude wraps its JSON response
        content = content.strip()
        if content.startswith("```"):
            # Remove opening fence (```json or ```)
            content = content.split("\n", 1)[1] if "\n" in content else content[3:]
        if content.endswith("```"):
            content = content[:-3].strip()

        # Parse Claude's JSON response
        analysis = json.loads(content)

        return {
            **article,
            "analysis": {
                "relevant": analysis.get("relevant", True),
                "sentiment": analysis.get("sentiment", 0.0),
                "confidence": analysis.get("confidence", 0.5),
                "summary": analysis.get("summary", ""),
                "risk_flags": analysis.get("risk_flags", []),
            },
        }

    except json.JSONDecodeError:
        # Claude returned something we couldn't parse
        print(f"    Warning: Could not parse Claude response for {ticker}: {content[:100]}")
        return {
            **article,
            "analysis": {
                "relevant": True,
                "sentiment": 0.0,
                "confidence": 0.0,
                "summary": "Analysis failed — could not parse model response",
                "risk_flags": [],
            },
        }
    except Exception as e:
        print(f"    Warning: Bedrock error for {ticker}: {e}")
        return {
            **article,
            "analysis": {
                "relevant": True,
                "sentiment": 0.0,
                "confidence": 0.0,
                "summary": f"Analysis failed — {str(e)[:50]}",
                "risk_flags": [],
            },
        }


COMPETITION_PROMPT = """You are a financial analyst assessing the competitive landscape for {ticker} ({company_name}).

CONTEXT:
- SEC SIC Industry: {sic_industry}
- HHI-based competition score: {hhi_score}/5 (1=concentrated/low competition, 5=fragmented/high competition)
- NOTE: The HHI score is computed from SEC SIC industry classifications which are BROAD. A company may dominate a specific niche within a broadly classified industry. Adjust accordingly.
- NOTE: Your training data may not reflect very recent market entries, exits, mergers, or competitive shifts. If the article summaries below mention new competitors, market share changes, or industry consolidation, factor that into your adjustment.

Recent article summaries for this stock:
{article_summaries}

TASK: Assess the ACTUAL competitive intensity this specific company faces (not just its broad industry).

Consider:
- Does this company have a moat? (brand, network effects, switching costs, patents, regulatory barriers)
- How many direct competitors operate in its specific niche (not just the broad SIC category)?
- Is competition increasing or decreasing based on recent news?
- Does the company have pricing power?
- AI disruption threat: How easily can this company's core products/services be replicated, automated, or replaced by others using modern AI? Offerings that are largely software, content, generic analysis, routine services, or easily-copied digital products face LOW barriers to entry in the AI era — AI lets new entrants replicate them cheaply and fast, eroding moats and pushing the market toward commoditization (higher score). Conversely, companies are AI-RESILIENT (and may even benefit from AI) when they are protected by proprietary/hard-to-obtain data, deep regulatory barriers, physical assets or infrastructure, entrenched network effects, high switching costs, or trusted brand/distribution that AI cannot easily replicate. Weigh both erosion and resilience — do not blindly penalize AI exposure.

Return ONLY valid JSON (no markdown):
{{
  "competition_score": <integer 1-5: 1=dominant/near-monopoly, 2=strong position/few competitors, 3=moderate competition, 4=competitive market, 5=highly competitive/commoditized>,
  "ai_threat": "<one of: low | moderate | high — how easily AI enables competitors to replicate/replace this company's offerings>",
  "reasoning": "<2-3 sentences explaining your adjustment from the HHI score (or why you agree with it), and explicitly noting how AI disruption threat factored into the score>"
}}
"""


def assess_competition(stock: dict, analyzed_articles: list, model_id: str) -> dict:
    """
    Make one Claude call per stock to assess competitive landscape.
    
    Receives the HHI-based score and article summaries, returns adjusted score.
    """
    ticker = stock.get("symbol", "?")
    company_name = stock.get("company_name", ticker)
    sic_industry = stock.get("sic_industry", stock.get("_sic_industry", "Unknown"))
    hhi_score = stock.get("hhi_score", 3)  # Default to middle if not available

    # Build article summaries for context
    summaries = []
    for art in analyzed_articles:
        analysis = art.get("analysis", {})
        if analysis.get("relevant") and analysis.get("summary"):
            summaries.append(f"- {analysis['summary']}")
    article_summaries = "\n".join(summaries[:8]) if summaries else "No recent articles available."

    prompt = COMPETITION_PROMPT.format(
        ticker=ticker,
        company_name=company_name,
        sic_industry=sic_industry,
        hhi_score=hhi_score,
        article_summaries=article_summaries,
    )

    try:
        response = bedrock_client.invoke_model(
            modelId=model_id,
            contentType="application/json",
            accept="application/json",
            body=json.dumps({
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": 300,
                "messages": [{"role": "user", "content": prompt}],
            }),
        )
        result = json.loads(response["body"].read())
        text = result["content"][0]["text"]

        # Strip markdown fences if present
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()

        analysis = json.loads(text)
        return {
            "hhi_score": hhi_score,
            "competition_score": int(analysis.get("competition_score", hhi_score)),
            "ai_threat": analysis.get("ai_threat", ""),
            "competition_reasoning": analysis.get("reasoning", ""),
        }
    except Exception as e:
        print(f"    Warning: Competition assessment failed for {ticker}: {e}")
        return {
            "hhi_score": hhi_score,
            "competition_score": hhi_score,  # Fall back to HHI
            "ai_threat": "",
            "competition_reasoning": f"Assessment failed, using HHI default: {e}",
        }


def calculate_aggregate_sentiment(analyzed_articles: list) -> dict:
    """
    Aggregate per-article sentiments into a single score for the stock.

    Weighting:
    - Only relevant articles are counted
    - Higher confidence articles weigh more
    - More recent articles weigh more (recency decay)

    Returns:
        Dict with aggregate sentiment score and breakdown
    """
    relevant_articles = [a for a in analyzed_articles if a["analysis"]["relevant"]]

    if not relevant_articles:
        return {
            "sentiment_score": 0.0,
            "confidence": 0.0,
            "article_count": 0,
            "relevant_count": 0,
            "risk_flags": [],
            "positive_count": 0,
            "negative_count": 0,
            "neutral_count": 0,
        }

    # Weighted average: weight = confidence
    total_weight = 0.0
    weighted_sum = 0.0
    all_risk_flags = []
    risk_flag_dates = {}  # flag → earliest article publication date
    positive = 0
    negative = 0
    neutral = 0

    for article in relevant_articles:
        analysis = article["analysis"]
        confidence = analysis["confidence"]
        sentiment = analysis["sentiment"]

        weighted_sum += sentiment * confidence
        total_weight += confidence

        # Track risk flags with the article's publication date
        for flag in analysis.get("risk_flags", []):
            all_risk_flags.append(flag)
            pub_time = article.get("published_at", "")
            # Handle both formats:
            # - FMP: "2026-08-02 08:53:01" (string)
            # - Legacy TickerTick: Unix timestamp in ms (integer)
            pub_date = ""
            if isinstance(pub_time, str) and pub_time:
                # FMP format: "2026-08-02 08:53:01" → take date part
                pub_date = pub_time[:10]
            elif isinstance(pub_time, (int, float)) and pub_time > 0:
                from datetime import datetime, timezone
                ts = pub_time / 1000 if pub_time > 1e12 else pub_time
                pub_date = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
            # Keep the earliest publication date per flag
            if flag not in risk_flag_dates or (pub_date and pub_date < risk_flag_dates[flag]):
                risk_flag_dates[flag] = pub_date

        if sentiment > 0.1:
            positive += 1
        elif sentiment < -0.1:
            negative += 1
        else:
            neutral += 1

    aggregate_score = weighted_sum / total_weight if total_weight > 0 else 0.0
    aggregate_confidence = total_weight / len(relevant_articles) if relevant_articles else 0.0

    # Deduplicate risk flags, attach earliest article date
    unique_flags = list(set(all_risk_flags))
    risk_flags_with_dates = [
        {"flag": f, "article_date": risk_flag_dates.get(f, "")}
        for f in unique_flags
    ]

    return {
        "sentiment_score": round(aggregate_score, 3),
        "confidence": round(aggregate_confidence, 3),
        "article_count": len(analyzed_articles),
        "relevant_count": len(relevant_articles),
        "risk_flags": unique_flags,
        "risk_flags_with_dates": risk_flags_with_dates,
        "positive_count": positive,
        "negative_count": negative,
        "neutral_count": neutral,
    }


def handler(event, context):
    """
    Lambda entry point. Called by Step Functions after news-fetcher.

    Input event:
        event["stocks_with_news"] — stocks with articles from news-fetcher

    Output:
        List of stocks with sentiment scores and per-article analysis.
    """
    from pipeline_io import read_pipeline_input, write_pipeline_output

    start_time = datetime.now(timezone.utc)
    print(f"Starting sentiment analysis at {start_time.isoformat()}")

    # Read input from S3 if needed (Step Functions payload limit workaround)
    data = read_pipeline_input(event)

    stocks_with_news = data.get("stocks_with_news", [])
    if not stocks_with_news:
        return {
            "stocks_with_sentiment": [],
            "metadata": {"error": "No stocks with news provided"},
        }

    model_id = os.environ.get("BEDROCK_MODEL_ID", DEFAULT_MODEL_ID)
    bucket_name = os.environ.get("RAW_DATA_BUCKET")

    # Load HHI scores from DynamoDB industry averages (for competition assessment)
    hhi_by_industry = {}
    table_name = os.environ.get("DATA_TABLE_NAME", "")
    if table_name:
        try:
            dynamodb = boto3.resource("dynamodb")
            table = dynamodb.Table(table_name)
            # Scan for INDUSTRY_AVG items that have hhi_score
            response = table.scan(
                FilterExpression="begins_with(PK, :pk)",
                ExpressionAttributeValues={":pk": "INDUSTRY_AVG#"},
                ProjectionExpression="industry, hhi_score",
            )
            for item in response.get("Items", []):
                industry = item.get("industry", "")
                hhi = item.get("hhi_score")
                if industry and hhi is not None:
                    hhi_by_industry[industry] = int(hhi)
            print(f"  Loaded HHI scores for {len(hhi_by_industry)} industries")
        except Exception as e:
            print(f"  Warning: Could not load HHI scores: {e}")

    # Attach HHI score to each stock based on its sic_industry
    for stock in stocks_with_news:
        sic_industry = stock.get("sic_industry", "")
        if sic_industry and sic_industry in hhi_by_industry:
            stock["hhi_score"] = hhi_by_industry[sic_industry]

    print(f"Analyzing articles for {len(stocks_with_news)} stocks using {model_id}")

    from concurrent.futures import ThreadPoolExecutor, as_completed

    stocks_with_sentiment = []
    total_articles_analyzed = 0

    for stock in stocks_with_news:
        symbol = stock.get("symbol", "?")
        company_name = stock.get("company_name", symbol)
        articles = stock.get("articles", [])

        if not articles:
            competition = assess_competition(stock, [], model_id)
            stocks_with_sentiment.append({
                **stock,
                "sentiment": calculate_aggregate_sentiment([]),
                "competition": competition,
            })
            continue

        print(f"  {symbol}: analyzing {len(articles)} articles...")

        # Analyze articles in parallel (4 concurrent Bedrock calls per stock)
        analyzed_articles = []
        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = {executor.submit(analyze_article, article, symbol, company_name, model_id): article 
                       for article in articles}
            for future in as_completed(futures):
                result = future.result()
                analyzed_articles.append(result)
                total_articles_analyzed += 1

        # Calculate aggregate sentiment for this stock
        aggregate = calculate_aggregate_sentiment(analyzed_articles)

        # Assess competitive landscape (one call per stock)
        competition = assess_competition(stock, analyzed_articles, model_id)

        stocks_with_sentiment.append({
            **stock,
            "articles": analyzed_articles,  # Now includes per-article analysis
            "sentiment": aggregate,
            "competition": competition,
        })

        print(f"    Score: {aggregate['sentiment_score']}, "
              f"Confidence: {aggregate['confidence']}, "
              f"Flags: {aggregate['risk_flags']}, "
              f"Competition: HHI={competition['hhi_score']}→Adj={competition['competition_score']}")

    # Store raw results in S3
    if bucket_name and stocks_with_sentiment:
        now = datetime.now(timezone.utc)
        key = (
            f"raw/sentiment/{now.strftime('%Y/%m/%d')}/"
            f"sentiment_{now.strftime('%Y%m%d_%H%M%S')}.json"
        )
        s3_client.put_object(
            Bucket=bucket_name,
            Key=key,
            Body=json.dumps(
                [{"symbol": s["symbol"], "sentiment": s["sentiment"]}
                 for s in stocks_with_sentiment],
                default=str
            ),
            ContentType="application/json",
        )
        print(f"  Stored sentiment results to s3://{bucket_name}/{key}")

    # Build response
    end_time = datetime.now(timezone.utc)
    duration = (end_time - start_time).total_seconds()

    result = {
        "stocks_with_sentiment": stocks_with_sentiment,
        "metadata": {
            "stocks_analyzed": len(stocks_with_news),
            "total_articles_analyzed": total_articles_analyzed,
            "model": model_id,
            "duration_seconds": duration,
            "timestamp": end_time.isoformat(),
        },
    }

    print(f"Done in {duration:.1f}s. Analyzed {total_articles_analyzed} articles "
          f"for {len(stocks_with_news)} stocks.")

    return write_pipeline_output(result, step_name="step6_sentiment")
