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
  "risk_flags": [<list of strings, empty if none>]
}}

Scoring guide:
- relevant: Is this article actually about {ticker}? If it mentions the company only in passing, or is about a different topic, mark false.
- sentiment: -1.0 = very negative for stock (fraud, lawsuits, bankruptcy). 0.0 = neutral. +1.0 = very positive (strong earnings beat, major contract win).
- confidence: How confident you are in the sentiment score. Low confidence for short/vague articles.
- risk_flags: Only include if serious. Examples: "SEC_investigation", "lawsuit", "fraud_allegation", "regulatory_risk", "management_departure", "accounting_irregularity", "product_recall"

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
    positive = 0
    negative = 0
    neutral = 0

    for article in relevant_articles:
        analysis = article["analysis"]
        confidence = analysis["confidence"]
        sentiment = analysis["sentiment"]

        weighted_sum += sentiment * confidence
        total_weight += confidence
        all_risk_flags.extend(analysis.get("risk_flags", []))

        if sentiment > 0.1:
            positive += 1
        elif sentiment < -0.1:
            negative += 1
        else:
            neutral += 1

    aggregate_score = weighted_sum / total_weight if total_weight > 0 else 0.0
    aggregate_confidence = total_weight / len(relevant_articles) if relevant_articles else 0.0

    # Deduplicate risk flags
    unique_flags = list(set(all_risk_flags))

    return {
        "sentiment_score": round(aggregate_score, 3),
        "confidence": round(aggregate_confidence, 3),
        "article_count": len(analyzed_articles),
        "relevant_count": len(relevant_articles),
        "risk_flags": unique_flags,
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

    print(f"Analyzing articles for {len(stocks_with_news)} stocks using {model_id}")

    stocks_with_sentiment = []
    total_articles_analyzed = 0

    for stock in stocks_with_news:
        symbol = stock.get("symbol", "?")
        company_name = stock.get("company_name", symbol)
        articles = stock.get("articles", [])

        if not articles:
            stocks_with_sentiment.append({
                **stock,
                "sentiment": calculate_aggregate_sentiment([]),
            })
            continue

        print(f"  {symbol}: analyzing {len(articles)} articles...")

        # Analyze each article with Claude
        analyzed_articles = []
        for article in articles:
            result = analyze_article(article, symbol, company_name, model_id)
            analyzed_articles.append(result)
            total_articles_analyzed += 1

            # Small delay between Bedrock calls (avoid throttling)
            time.sleep(0.1)

        # Calculate aggregate sentiment for this stock
        aggregate = calculate_aggregate_sentiment(analyzed_articles)

        stocks_with_sentiment.append({
            **stock,
            "articles": analyzed_articles,  # Now includes per-article analysis
            "sentiment": aggregate,
        })

        print(f"    Score: {aggregate['sentiment_score']}, "
              f"Confidence: {aggregate['confidence']}, "
              f"Flags: {aggregate['risk_flags']}")

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
