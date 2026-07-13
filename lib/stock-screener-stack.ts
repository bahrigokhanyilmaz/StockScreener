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
 * StockScreenerStack
 *
 * 8-step pipeline (no artificial limits on stock selection):
 * 1. EDGAR Fetch — bulk fundamentals for ~5,000 stocks (~10 API calls)
 * 2. Pre-Screen — filter by EDGAR-available metrics (D/E, QR, OpMargin) → ~233
 * 3. Price Enrichment — Twelve Data prices for ALL ~233 passers (800/day limit OK)
 * 4. Full Screen — re-filter with price-based metrics (P/E, Price/FCF)
 * 5. News Fetch — articles for final passers
 * 6. Sentiment — Bedrock/Claude per article
 * 7. Score Calculator — investability score + DynamoDB persistence
 * 8. Alert Checker — threshold monitoring + SNS notifications
 *
 * Triggered daily Mon-Fri at 4PM ET (8PM UTC).
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
      lifecycleRules: [{
        transitions: [{
          storageClass: s3.StorageClass.INFREQUENT_ACCESS,
          transitionAfter: cdk.Duration.days(90),
        }],
      }],
    });

    // ==========================================
    // DYNAMODB — Single-Table Design
    // ==========================================
    const dataTable = new dynamodb.Table(this, 'DataTable', {
      tableName: 'stock-screener-data',
      partitionKey: { name: 'PK', type: dynamodb.AttributeType.STRING },
      sortKey: { name: 'SK', type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
      timeToLiveAttribute: 'ttl',
    });

    dataTable.addGlobalSecondaryIndex({
      indexName: 'tracking-status-index',
      partitionKey: { name: 'tracking_status', type: dynamodb.AttributeType.STRING },
      sortKey: { name: 'last_updated', type: dynamodb.AttributeType.STRING },
      projectionType: dynamodb.ProjectionType.ALL,
    });

    // ==========================================
    // SNS TOPIC — Alerts
    // ==========================================
    const alertTopic = new sns.Topic(this, 'AlertTopic', {
      topicName: 'stock-screener-alerts',
      displayName: 'Stock Screener Alerts',
    });

    alertTopic.addSubscription(
      new snsSubscriptions.EmailSubscription('bahrigokhanyilmaz@gmail.com')
    );

    // ==========================================
    // LAMBDAS
    // ==========================================

    // Step 1: EDGAR Bulk Fundamentals (pure data fetch, no filtering)
    const fundamentalsFetcher = new PythonFunction(this, 'FundamentalsFetcher', {
      functionName: 'stock-screener-fundamentals-fetcher',
      entry: path.join(__dirname, '../lambdas/fundamentals-fetcher'),
      index: 'handler.py',
      handler: 'handler',
      runtime: lambda.Runtime.PYTHON_3_12,
      architecture: lambda.Architecture.ARM_64,
      timeout: cdk.Duration.minutes(5),
      memorySize: 512,
      environment: {
        PROVIDER: 'edgar',
        RAW_DATA_BUCKET: rawDataBucket.bucketName,
      },
      description: 'Step 1: EDGAR bulk fundamentals for entire US market',
    });

    // Step 2 & 4: Stock Screener (same Lambda, called twice — pre-screen and full screen)
    const stockScreener = new lambda.Function(this, 'StockScreener', {
      functionName: 'stock-screener-filter',
      runtime: lambda.Runtime.PYTHON_3_12,
      architecture: lambda.Architecture.ARM_64,
      handler: 'handler.handler',
      code: lambda.Code.fromAsset(path.join(__dirname, '../lambdas/stock-screener')),
      timeout: cdk.Duration.seconds(60),
      memorySize: 256,
      description: 'Steps 2 & 4: Value filter (called twice — pre-screen + full screen)',
    });

    // Step 3: Price Enrichment (Twelve Data — 800 calls/day, 8/min)
    const priceEnrichment = new PythonFunction(this, 'PriceEnrichment', {
      functionName: 'stock-screener-price-enrichment',
      entry: path.join(__dirname, '../lambdas/enrichment'),
      index: 'handler.py',
      handler: 'handler',
      runtime: lambda.Runtime.PYTHON_3_12,
      architecture: lambda.Architecture.ARM_64,
      timeout: cdk.Duration.minutes(15), // ~233 stocks ÷ 8/min = ~30 min worst case
      memorySize: 128,
      environment: {
        TWELVE_DATA_KEY_PARAM: '/stock-screener/twelve-data-api-key',
      },
      description: 'Step 3: Twelve Data prices for all EDGAR pre-screen passers',
    });

    // Step 5: News Fetcher (TickerTick)
    const newsFetcher = new PythonFunction(this, 'NewsFetcher', {
      functionName: 'stock-screener-news-fetcher',
      entry: path.join(__dirname, '../lambdas/news-fetcher'),
      index: 'handler.py',
      handler: 'handler',
      runtime: lambda.Runtime.PYTHON_3_12,
      architecture: lambda.Architecture.ARM_64,
      timeout: cdk.Duration.minutes(10),
      memorySize: 256,
      environment: {
        RAW_DATA_BUCKET: rawDataBucket.bucketName,
        NEWS_LOOKBACK_HOURS: '168',
      },
      description: 'Step 5: News articles for final passing stocks (TickerTick)',
    });

    // Step 6: Sentiment Analyzer (Bedrock/Claude)
    const sentimentAnalyzer = new PythonFunction(this, 'SentimentAnalyzer', {
      functionName: 'stock-screener-sentiment-analyzer',
      entry: path.join(__dirname, '../lambdas/sentiment-analyzer'),
      index: 'handler.py',
      handler: 'handler',
      runtime: lambda.Runtime.PYTHON_3_12,
      architecture: lambda.Architecture.ARM_64,
      timeout: cdk.Duration.minutes(10),
      memorySize: 256,
      environment: {
        BEDROCK_MODEL_ID: 'us.anthropic.claude-haiku-4-5-20251001-v1:0',
        RAW_DATA_BUCKET: rawDataBucket.bucketName,
      },
      description: 'Step 6: Bedrock/Claude sentiment analysis per article',
    });

    // Step 7: Score Calculator (+ DynamoDB persistence)
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
      description: 'Step 7: Investability score + DynamoDB persistence',
    });

    // Step 8: Alert Checker (+ tracking lifecycle)
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
      description: 'Step 8: Threshold monitoring + tracking lifecycle + SNS alerts',
    });

    // API Handler (REST — serves React dashboard)
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
      description: 'REST API for the stock screener dashboard',
    });

    // ==========================================
    // API GATEWAY
    // ==========================================
    const api = new apigateway.RestApi(this, 'StockScreenerApi', {
      restApiName: 'stock-screener-api',
      description: 'REST API for the stock screener dashboard',
      defaultCorsPreflightOptions: {
        allowOrigins: apigateway.Cors.ALL_ORIGINS,
        allowMethods: apigateway.Cors.ALL_METHODS,
        allowHeaders: ['Content-Type'],
      },
    });

    const lambdaIntegration = new apigateway.LambdaIntegration(apiHandler);

    const stocksResource = api.root.addResource('stocks');
    stocksResource.addMethod('GET', lambdaIntegration);

    const singleStockResource = stocksResource.addResource('{ticker}');
    singleStockResource.addMethod('GET', lambdaIntegration);

    const historyResource = singleStockResource.addResource('history');
    historyResource.addMethod('GET', lambdaIntegration);

    const trackResource = singleStockResource.addResource('track');
    trackResource.addMethod('POST', lambdaIntegration);
    trackResource.addMethod('DELETE', lambdaIntegration);

    const pipelineResource = api.root.addResource('pipeline');
    const statusResource = pipelineResource.addResource('status');
    statusResource.addMethod('GET', lambdaIntegration);

    // ==========================================
    // PERMISSIONS
    // ==========================================

    // Step 1: S3 write
    rawDataBucket.grantWrite(fundamentalsFetcher);

    // Step 3: SSM read (Twelve Data key)
    priceEnrichment.addToRolePolicy(new iam.PolicyStatement({
      effect: iam.Effect.ALLOW,
      actions: ['ssm:GetParameter'],
      resources: [`arn:aws:ssm:${this.region}:${this.account}:parameter/stock-screener/*`],
    }));
    priceEnrichment.addToRolePolicy(new iam.PolicyStatement({
      effect: iam.Effect.ALLOW,
      actions: ['kms:Decrypt'],
      resources: ['*'],
      conditions: { StringEquals: { 'kms:ViaService': `ssm.${this.region}.amazonaws.com` } },
    }));

    // Step 5: S3 write
    rawDataBucket.grantWrite(newsFetcher);

    // Step 6: Bedrock + S3
    sentimentAnalyzer.addToRolePolicy(new iam.PolicyStatement({
      effect: iam.Effect.ALLOW,
      actions: ['bedrock:InvokeModel'],
      resources: ['*'],
    }));
    rawDataBucket.grantWrite(sentimentAnalyzer);

    // Steps 7 & 8: DynamoDB
    dataTable.grantReadWriteData(scoreCalculator);
    dataTable.grantReadWriteData(alertChecker);

    // Step 8: SNS
    alertTopic.grantPublish(alertChecker);

    // API: DynamoDB read/write
    dataTable.grantReadWriteData(apiHandler);

    // ==========================================
    // STEP FUNCTIONS — 8-Step Pipeline
    // ==========================================

    const step1_fetchFundamentals = new tasks.LambdaInvoke(this, 'FetchFundamentals', {
      lambdaFunction: fundamentalsFetcher,
      comment: 'Step 1: EDGAR bulk fundamentals',
      payloadResponseOnly: true,
      retryOnServiceExceptions: true,
    });

    const step2_preScreen = new tasks.LambdaInvoke(this, 'PreScreen', {
      lambdaFunction: stockScreener,
      comment: 'Step 2: Pre-screen with EDGAR-only filters (D/E, QR, OpMargin)',
      payloadResponseOnly: true,
      retryOnServiceExceptions: true,
    });

    const step3_enrichPrices = new tasks.LambdaInvoke(this, 'EnrichPrices', {
      lambdaFunction: priceEnrichment,
      comment: 'Step 3: Twelve Data prices for all pre-screen passers',
      payloadResponseOnly: true,
      retryOnServiceExceptions: true,
    });

    const step4_fullScreen = new tasks.LambdaInvoke(this, 'FullScreen', {
      lambdaFunction: stockScreener,
      comment: 'Step 4: Full screen with price-based filters (P/E, Price/FCF)',
      payloadResponseOnly: true,
      retryOnServiceExceptions: true,
    });

    const step5_fetchNews = new tasks.LambdaInvoke(this, 'FetchNews', {
      lambdaFunction: newsFetcher,
      comment: 'Step 5: News articles for final passers',
      payloadResponseOnly: true,
      retryOnServiceExceptions: true,
    });

    const step6_analyzeSentiment = new tasks.LambdaInvoke(this, 'AnalyzeSentiment', {
      lambdaFunction: sentimentAnalyzer,
      comment: 'Step 6: Bedrock/Claude sentiment per article',
      payloadResponseOnly: true,
      retryOnServiceExceptions: true,
    });

    const step7_calculateScores = new tasks.LambdaInvoke(this, 'CalculateScores', {
      lambdaFunction: scoreCalculator,
      comment: 'Step 7: Investability scores + DynamoDB',
      payloadResponseOnly: true,
      retryOnServiceExceptions: true,
    });

    const step8_checkAlerts = new tasks.LambdaInvoke(this, 'CheckAlerts', {
      lambdaFunction: alertChecker,
      comment: 'Step 8: Thresholds + tracking + alerts',
      payloadResponseOnly: true,
      retryOnServiceExceptions: true,
    });

    // Chain: EDGAR → PreScreen → Enrich → FullScreen → News → Sentiment → Score → Alerts
    const definition = step1_fetchFundamentals
      .next(step2_preScreen)
      .next(step3_enrichPrices)
      .next(step4_fullScreen)
      .next(step5_fetchNews)
      .next(step6_analyzeSentiment)
      .next(step7_calculateScores)
      .next(step8_checkAlerts);

    const stateMachine = new sfn.StateMachine(this, 'PipelineStateMachine', {
      stateMachineName: 'stock-screener-pipeline',
      definitionBody: sfn.DefinitionBody.fromChainable(definition),
      timeout: cdk.Duration.minutes(60),
      comment: 'Daily: EDGAR → PreScreen → Enrich → FullScreen → News → Sentiment → Score → Alerts',
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
      description: 'Triggers pipeline daily at market close (4 PM ET / 8 PM UTC)',
    });

    dailyRule.addTarget(new targets.SfnStateMachine(stateMachine, {
      input: events.RuleTargetInput.fromObject({}),
    }));

    // ==========================================
    // OUTPUTS
    // ==========================================
    new cdk.CfnOutput(this, 'RawDataBucketName', { value: rawDataBucket.bucketName });
    new cdk.CfnOutput(this, 'DataTableName', { value: dataTable.tableName });
    new cdk.CfnOutput(this, 'AlertTopicArn', { value: alertTopic.topicArn });
    new cdk.CfnOutput(this, 'StateMachineArn', { value: stateMachine.stateMachineArn });
    new cdk.CfnOutput(this, 'ApiUrl', { value: api.url });
  }
}
