import * as cdk from 'aws-cdk-lib/core';
import { Construct } from 'constructs';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as sns from 'aws-cdk-lib/aws-sns';
import * as snsSubscriptions from 'aws-cdk-lib/aws-sns-subscriptions';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as sfn from 'aws-cdk-lib/aws-stepfunctions';
import * as tasks from 'aws-cdk-lib/aws-stepfunctions-tasks';
import * as events from 'aws-cdk-lib/aws-events';
import * as targets from 'aws-cdk-lib/aws-events-targets';
import * as path from 'path';
import { PythonFunction } from '@aws-cdk/aws-lambda-python-alpha';

/**
 * StockScreenerStack — Full Pipeline
 *
 * 6-step pipeline orchestrated by Step Functions:
 * 1. fundamentals-fetcher — Fetch financial data (FMP + NASDAQ)
 * 2. stock-screener — Apply value filters, score fundamentals
 * 3. news-fetcher — Fetch news for passing stocks (TickerTick)
 * 4. sentiment-analyzer — Analyze news via Bedrock/Claude
 * 5. score-calculator — Combine fundamental + sentiment scores
 * 6. alert-checker — Check thresholds, send notifications (SNS)
 *
 * Triggered daily at market close (4 PM ET) via EventBridge.
 */
export class StockScreenerStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);

    // ==========================================
    // S3 BUCKET — Raw Data Lake
    // ==========================================
    const rawDataBucket = new s3.Bucket(this, 'RawDataBucket', {
      bucketName: `stock-screener-raw-data-${this.account}`,
      versioned: true,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
      autoDeleteObjects: true,
      lifecycleRules: [
        {
          transitions: [
            {
              storageClass: s3.StorageClass.INFREQUENT_ACCESS,
              transitionAfter: cdk.Duration.days(90),
            },
          ],
        },
      ],
    });

    // ==========================================
    // SNS TOPIC — Alert Notifications
    // ==========================================
    const alertTopic = new sns.Topic(this, 'AlertTopic', {
      topicName: 'stock-screener-alerts',
      displayName: 'Stock Screener Alerts',
    });

    // Email subscription — you'll receive a confirmation email after deploy
    alertTopic.addSubscription(
      new snsSubscriptions.EmailSubscription('bahrigokhanyilmaz@gmail.com')
    );

    // ==========================================
    // LAMBDA — Step 1: Fundamentals Fetcher
    // ==========================================
    const fundamentalsFetcher = new PythonFunction(this, 'FundamentalsFetcher', {
      functionName: 'stock-screener-fundamentals-fetcher',
      entry: path.join(__dirname, '../lambdas/fundamentals-fetcher'),
      index: 'handler.py',
      handler: 'handler',
      runtime: lambda.Runtime.PYTHON_3_12,
      architecture: lambda.Architecture.ARM_64,
      timeout: cdk.Duration.minutes(5),
      memorySize: 256,
      environment: {
        PROVIDER: 'fmp',
        RAW_DATA_BUCKET: rawDataBucket.bucketName,
        FMP_API_KEY_PARAM: '/stock-screener/fmp-api-key',
        MIN_MARKET_CAP: '300000000',
      },
      description: 'Step 1: Fetches fundamental stock data via FMP + NASDAQ',
    });

    // ==========================================
    // LAMBDA — Step 2: Stock Screener
    // ==========================================
    const stockScreener = new lambda.Function(this, 'StockScreener', {
      functionName: 'stock-screener-filter',
      runtime: lambda.Runtime.PYTHON_3_12,
      architecture: lambda.Architecture.ARM_64,
      handler: 'handler.handler',
      code: lambda.Code.fromAsset(path.join(__dirname, '../lambdas/stock-screener')),
      timeout: cdk.Duration.seconds(60),
      memorySize: 256,
      description: 'Step 2: Applies value investing filters and scores stocks',
    });

    // ==========================================
    // LAMBDA — Step 3: News Fetcher
    // ==========================================
    const newsFetcher = new PythonFunction(this, 'NewsFetcher', {
      functionName: 'stock-screener-news-fetcher',
      entry: path.join(__dirname, '../lambdas/news-fetcher'),
      index: 'handler.py',
      handler: 'handler',
      runtime: lambda.Runtime.PYTHON_3_12,
      architecture: lambda.Architecture.ARM_64,
      timeout: cdk.Duration.minutes(10), // 30-50 stocks × 6.5s rate limit = ~5 min
      memorySize: 256,
      environment: {
        RAW_DATA_BUCKET: rawDataBucket.bucketName,
        NEWS_LOOKBACK_HOURS: '168', // 7 days
      },
      description: 'Step 3: Fetches news articles for tracked stocks via TickerTick',
    });

    // ==========================================
    // LAMBDA — Step 4: Sentiment Analyzer
    // ==========================================
    const sentimentAnalyzer = new PythonFunction(this, 'SentimentAnalyzer', {
      functionName: 'stock-screener-sentiment-analyzer',
      entry: path.join(__dirname, '../lambdas/sentiment-analyzer'),
      index: 'handler.py',
      handler: 'handler',
      runtime: lambda.Runtime.PYTHON_3_12,
      architecture: lambda.Architecture.ARM_64,
      timeout: cdk.Duration.minutes(10), // ~300 articles × Bedrock latency
      memorySize: 256,
      environment: {
        BEDROCK_MODEL_ID: 'us.anthropic.claude-haiku-4-5-20251001-v1:0',
        RAW_DATA_BUCKET: rawDataBucket.bucketName,
      },
      description: 'Step 4: Analyzes news sentiment via Bedrock/Claude',
    });

    // ==========================================
    // LAMBDA — Step 5: Score Calculator
    // ==========================================
    const scoreCalculator = new lambda.Function(this, 'ScoreCalculator', {
      functionName: 'stock-screener-score-calculator',
      runtime: lambda.Runtime.PYTHON_3_12,
      architecture: lambda.Architecture.ARM_64,
      handler: 'handler.handler',
      code: lambda.Code.fromAsset(path.join(__dirname, '../lambdas/score-calculator')),
      timeout: cdk.Duration.seconds(30),
      memorySize: 128,
      environment: {
        FUNDAMENTAL_WEIGHT: '0.7',
        SENTIMENT_WEIGHT: '0.3',
      },
      description: 'Step 5: Combines fundamental + sentiment into investability score',
    });

    // ==========================================
    // LAMBDA — Step 6: Alert Checker
    // ==========================================
    const alertChecker = new PythonFunction(this, 'AlertChecker', {
      functionName: 'stock-screener-alert-checker',
      entry: path.join(__dirname, '../lambdas/alert-checker'),
      index: 'handler.py',
      handler: 'handler',
      runtime: lambda.Runtime.PYTHON_3_12,
      architecture: lambda.Architecture.ARM_64,
      timeout: cdk.Duration.seconds(30),
      memorySize: 128,
      environment: {
        ALERT_SNS_TOPIC_ARN: alertTopic.topicArn,
        SENTIMENT_DROP_THRESHOLD: '-0.3',
      },
      description: 'Step 6: Checks thresholds and sends alert notifications',
    });

    // ==========================================
    // PERMISSIONS
    // ==========================================

    // Step 1: S3 write + SSM read
    rawDataBucket.grantWrite(fundamentalsFetcher);
    fundamentalsFetcher.addToRolePolicy(new iam.PolicyStatement({
      effect: iam.Effect.ALLOW,
      actions: ['ssm:GetParameter'],
      resources: [`arn:aws:ssm:${this.region}:${this.account}:parameter/stock-screener/*`],
    }));
    fundamentalsFetcher.addToRolePolicy(new iam.PolicyStatement({
      effect: iam.Effect.ALLOW,
      actions: ['kms:Decrypt'],
      resources: ['*'],
      conditions: { StringEquals: { 'kms:ViaService': `ssm.${this.region}.amazonaws.com` } },
    }));

    // Step 3: S3 write (raw news storage)
    rawDataBucket.grantWrite(newsFetcher);

    // Step 4: Bedrock invoke + S3 write
    sentimentAnalyzer.addToRolePolicy(new iam.PolicyStatement({
      effect: iam.Effect.ALLOW,
      actions: ['bedrock:InvokeModel'],
      resources: ['*'], // Bedrock model ARNs are complex; wildcard is standard practice
    }));
    rawDataBucket.grantWrite(sentimentAnalyzer);

    // Step 6: SNS publish
    alertTopic.grantPublish(alertChecker);

    // ==========================================
    // STEP FUNCTIONS — Full Pipeline
    // ==========================================

    const fetchFundamentals = new tasks.LambdaInvoke(this, 'FetchFundamentals', {
      lambdaFunction: fundamentalsFetcher,
      comment: 'Step 1: Fetch fundamental data for a batch of stocks',
      payloadResponseOnly: true,
      retryOnServiceExceptions: true,
    });

    const screenStocks = new tasks.LambdaInvoke(this, 'ScreenStocks', {
      lambdaFunction: stockScreener,
      comment: 'Step 2: Apply value filters to identify passing stocks',
      payloadResponseOnly: true,
      retryOnServiceExceptions: true,
    });

    const fetchNews = new tasks.LambdaInvoke(this, 'FetchNews', {
      lambdaFunction: newsFetcher,
      comment: 'Step 3: Fetch news articles for passing stocks',
      payloadResponseOnly: true,
      retryOnServiceExceptions: true,
    });

    const analyzeSentiment = new tasks.LambdaInvoke(this, 'AnalyzeSentiment', {
      lambdaFunction: sentimentAnalyzer,
      comment: 'Step 4: Analyze news sentiment via Bedrock/Claude',
      payloadResponseOnly: true,
      retryOnServiceExceptions: true,
    });

    const calculateScores = new tasks.LambdaInvoke(this, 'CalculateScores', {
      lambdaFunction: scoreCalculator,
      comment: 'Step 5: Calculate final investability scores',
      payloadResponseOnly: true,
      retryOnServiceExceptions: true,
    });

    const checkAlerts = new tasks.LambdaInvoke(this, 'CheckAlerts', {
      lambdaFunction: alertChecker,
      comment: 'Step 6: Check thresholds and send alerts',
      payloadResponseOnly: true,
      retryOnServiceExceptions: true,
    });

    // Chain all 6 steps
    const definition = fetchFundamentals
      .next(screenStocks)
      .next(fetchNews)
      .next(analyzeSentiment)
      .next(calculateScores)
      .next(checkAlerts);

    const stateMachine = new sfn.StateMachine(this, 'PipelineStateMachine', {
      stateMachineName: 'stock-screener-pipeline',
      definitionBody: sfn.DefinitionBody.fromChainable(definition),
      timeout: cdk.Duration.minutes(30), // Full 6-step pipeline timeout
      comment: 'Daily stock screening pipeline: fundamentals → screen → news → sentiment → score → alerts',
    });

    // ==========================================
    // EVENTBRIDGE — Daily Schedule
    // ==========================================
    const dailyRule = new events.Rule(this, 'DailyTrigger', {
      ruleName: 'stock-screener-daily-trigger',
      schedule: events.Schedule.cron({
        minute: '0',
        hour: '20',
        weekDay: 'MON-FRI',
      }),
      description: 'Triggers the stock screener pipeline daily at market close (4 PM ET)',
    });

    dailyRule.addTarget(new targets.SfnStateMachine(stateMachine, {
      input: events.RuleTargetInput.fromObject({
        batch_start: 0,
        batch_size: 50,
      }),
    }));

    // ==========================================
    // OUTPUTS
    // ==========================================
    new cdk.CfnOutput(this, 'RawDataBucketName', {
      value: rawDataBucket.bucketName,
    });
    new cdk.CfnOutput(this, 'AlertTopicArn', {
      value: alertTopic.topicArn,
    });
    new cdk.CfnOutput(this, 'StateMachineArn', {
      value: stateMachine.stateMachineArn,
    });
  }
}
