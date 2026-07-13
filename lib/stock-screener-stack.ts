import * as cdk from 'aws-cdk-lib/core';
import { Construct } from 'constructs';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import * as sns from 'aws-cdk-lib/aws-sns';
import * as snsSubscriptions from 'aws-cdk-lib/aws-sns-subscriptions';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as sfn from 'aws-cdk-lib/aws-stepfunctions';
import * as tasks from 'aws-cdk-lib/aws-stepfunctions-tasks';
import * as events from 'aws-cdk-lib/aws-events';
import * as targets from 'aws-cdk-lib/aws-events-targets';
import * as apigateway from 'aws-cdk-lib/aws-apigateway';
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
    // DYNAMODB — Single-Table Design
    // ==========================================
    // One table serves all data needs via different PK/SK patterns:
    //
    // Access patterns and key design:
    //   PK: STOCK#AAPL         SK: LATEST          → Current fundamentals + score
    //   PK: STOCK#AAPL         SK: SCORE#2026-07-12 → Historical score for date
    //   PK: STOCK#AAPL         SK: TRACKING        → Tracking status + grace period
    //   PK: PIPELINE#2026-07-12 SK: RESULT          → Daily pipeline summary
    //   PK: ALERT_RULE#uuid    SK: CONFIG          → User alert rules
    //   PK: PRESET#name        SK: CONFIG          → Saved filter presets
    //
    // GSI1 (tracking_status-last_updated-index):
    //   Lets us query "all stocks with status=ACTIVE" efficiently
    //   GSI1PK: tracking_status (ACTIVE/GRACE/MANUAL)
    //   GSI1SK: last_updated (ISO date — for sorting by recency)
    //
    // Why single-table?
    //   DynamoDB charges per table (for on-demand) and per read/write.
    //   One table with multiple item types is the recommended pattern
    //   for applications with related data and known access patterns.
    const dataTable = new dynamodb.Table(this, 'DataTable', {
      tableName: 'stock-screener-data',
      partitionKey: { name: 'PK', type: dynamodb.AttributeType.STRING },
      sortKey: { name: 'SK', type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST, // No capacity planning needed
      removalPolicy: cdk.RemovalPolicy.DESTROY, // Dev only
      timeToLiveAttribute: 'ttl', // Auto-delete old items (optional, set per item)
    });

    // GSI for querying stocks by tracking status
    dataTable.addGlobalSecondaryIndex({
      indexName: 'tracking-status-index',
      partitionKey: { name: 'tracking_status', type: dynamodb.AttributeType.STRING },
      sortKey: { name: 'last_updated', type: dynamodb.AttributeType.STRING },
      projectionType: dynamodb.ProjectionType.ALL,
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
      memorySize: 512, // EDGAR frames are large JSON responses (~5MB total)
      environment: {
        PROVIDER: 'edgar',
        RAW_DATA_BUCKET: rawDataBucket.bucketName,
        ALPHA_VANTAGE_KEY_PARAM: '/stock-screener/alpha-vantage-api-key',
        MIN_MARKET_CAP: '300000000',
      },
      description: 'Step 1: EDGAR bulk fundamentals + Alpha Vantage price enrichment',
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
        DATA_TABLE_NAME: dataTable.tableName,
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
        DATA_TABLE_NAME: dataTable.tableName,
      },
      description: 'Step 6: Checks thresholds and sends alert notifications',
    });

    // ==========================================
    // LAMBDA — API Handler
    // ==========================================
    // Single Lambda that handles all REST API routes.
    // API Gateway proxies all requests to this function.
    // The handler routes internally based on path + method.
    const apiHandler = new PythonFunction(this, 'ApiHandler', {
      functionName: 'stock-screener-api',
      entry: path.join(__dirname, '../lambdas/api'),
      index: 'handler.py',
      handler: 'handler',
      runtime: lambda.Runtime.PYTHON_3_12,
      architecture: lambda.Architecture.ARM_64,
      timeout: cdk.Duration.seconds(15),
      memorySize: 128,
      environment: {
        DATA_TABLE_NAME: dataTable.tableName,
      },
      description: 'REST API handler for the stock screener dashboard',
    });

    // ==========================================
    // API GATEWAY — REST API
    // ==========================================
    // Creates a public REST API that the React frontend will call.
    // All requests are proxied to the apiHandler Lambda.
    //
    // "proxy integration" means API Gateway passes the full HTTP request
    // to Lambda and returns Lambda's response directly to the client.
    // No request/response mapping needed.
    const api = new apigateway.RestApi(this, 'StockScreenerApi', {
      restApiName: 'stock-screener-api',
      description: 'REST API for the stock screener dashboard',
      defaultCorsPreflightOptions: {
        // CORS: Allow the React frontend (any origin during dev) to call this API
        allowOrigins: apigateway.Cors.ALL_ORIGINS,
        allowMethods: apigateway.Cors.ALL_METHODS,
        allowHeaders: ['Content-Type'],
      },
    });

    // Proxy all requests to the API Lambda
    const lambdaIntegration = new apigateway.LambdaIntegration(apiHandler);

    // /stocks
    const stocksResource = api.root.addResource('stocks');
    stocksResource.addMethod('GET', lambdaIntegration);

    // /stocks/{ticker}
    const singleStockResource = stocksResource.addResource('{ticker}');
    singleStockResource.addMethod('GET', lambdaIntegration);

    // /stocks/{ticker}/history
    const historyResource = singleStockResource.addResource('history');
    historyResource.addMethod('GET', lambdaIntegration);

    // /stocks/{ticker}/track
    const trackResource = singleStockResource.addResource('track');
    trackResource.addMethod('POST', lambdaIntegration);
    trackResource.addMethod('DELETE', lambdaIntegration);

    // /pipeline
    const pipelineResource = api.root.addResource('pipeline');
    // /pipeline/status
    const statusResource = pipelineResource.addResource('status');
    statusResource.addMethod('GET', lambdaIntegration);

    // ==========================================
    // PERMISSIONS
    // ==========================================

    // API Lambda: read from DynamoDB
    dataTable.grantReadWriteData(apiHandler);

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

    // Steps 5 & 6: DynamoDB read/write (scores, tracking status)
    dataTable.grantReadWriteData(scoreCalculator);
    dataTable.grantReadWriteData(alertChecker);

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
    new cdk.CfnOutput(this, 'DataTableName', {
      value: dataTable.tableName,
    });
    new cdk.CfnOutput(this, 'AlertTopicArn', {
      value: alertTopic.topicArn,
    });
    new cdk.CfnOutput(this, 'StateMachineArn', {
      value: stateMachine.stateMachineArn,
    });
    new cdk.CfnOutput(this, 'ApiUrl', {
      value: api.url,
      description: 'REST API base URL',
    });
  }
}
