import * as cdk from 'aws-cdk-lib/core';
import { Construct } from 'constructs';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as sfn from 'aws-cdk-lib/aws-stepfunctions';
import * as tasks from 'aws-cdk-lib/aws-stepfunctions-tasks';
import * as events from 'aws-cdk-lib/aws-events';
import * as targets from 'aws-cdk-lib/aws-events-targets';
import * as path from 'path';
import { PythonFunction } from '@aws-cdk/aws-lambda-python-alpha';

/**
 * StockScreenerStack
 *
 * Defines the full pipeline:
 * - S3 bucket (raw data lake)
 * - Lambda: fundamentals-fetcher (Step 1)
 * - Lambda: stock-screener (Step 2)
 * - Step Functions state machine (orchestrates the pipeline)
 * - EventBridge rule (triggers daily at market close)
 *
 * Step Functions handles batching: the universe (~1,000 stocks) is split
 * into batches of 100. Each batch is processed by a separate Lambda invocation.
 * After all batches complete, the screener filters the combined results.
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
      description: 'Step 1: Fetches fundamental stock data via configurable provider',
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
      memorySize: 256, // Filtering 1,000 stocks needs some memory
      description: 'Step 2: Applies value investing filters and scores stocks',
    });

    // ==========================================
    // PERMISSIONS
    // ==========================================
    rawDataBucket.grantWrite(fundamentalsFetcher);

    fundamentalsFetcher.addToRolePolicy(new iam.PolicyStatement({
      effect: iam.Effect.ALLOW,
      actions: ['ssm:GetParameter'],
      resources: [
        `arn:aws:ssm:${this.region}:${this.account}:parameter/stock-screener/*`,
      ],
    }));

    fundamentalsFetcher.addToRolePolicy(new iam.PolicyStatement({
      effect: iam.Effect.ALLOW,
      actions: ['kms:Decrypt'],
      resources: ['*'],
      conditions: {
        StringEquals: {
          'kms:ViaService': `ssm.${this.region}.amazonaws.com`,
        },
      },
    }));

    // ==========================================
    // STEP FUNCTIONS — Pipeline Orchestration
    // ==========================================
    // The state machine orchestrates the pipeline:
    // 1. Invoke fundamentals-fetcher with batch_size to get one batch
    // 2. Collect the results
    // 3. Loop if has_more batches remain
    // 4. Combine all results and pass to the screener
    //
    // Why Step Functions instead of just chaining Lambdas?
    // - Visual monitoring (you can see each step's status in the console)
    // - Built-in retry logic and error handling
    // - Handles the batching loop (Lambda can't loop over itself)
    // - Timeout spans the full pipeline (not limited to 5 min per Lambda)
    // - Easy to add more steps later (news, sentiment, alerts)

    // Step 1: Fetch fundamentals (single batch — Step Functions handles batching)
    const fetchFundamentals = new tasks.LambdaInvoke(this, 'FetchFundamentals', {
      lambdaFunction: fundamentalsFetcher,
      comment: 'Fetch fundamental data for a batch of stocks',
      // Pass the full state as input (includes batch_start, batch_size)
      payloadResponseOnly: true, // Unwrap Lambda response (no metadata wrapper)
      retryOnServiceExceptions: true,
    });

    // Step 2: Screen the fetched stocks
    const screenStocks = new tasks.LambdaInvoke(this, 'ScreenStocks', {
      lambdaFunction: stockScreener,
      comment: 'Apply value filters to identify passing stocks',
      payloadResponseOnly: true,
      retryOnServiceExceptions: true,
    });

    // Define the state machine workflow:
    // For now, a simple sequential flow (fetch → screen).
    // The fundamentals-fetcher handles batching internally via batch_start/batch_size.
    // We'll start with a single batch (batch_size = 50) to stay within Lambda timeout.
    // Later, we'll add a Map state for parallel batch processing.
    const definition = fetchFundamentals
      .next(screenStocks);

    const stateMachine = new sfn.StateMachine(this, 'PipelineStateMachine', {
      stateMachineName: 'stock-screener-pipeline',
      definitionBody: sfn.DefinitionBody.fromChainable(definition),
      timeout: cdk.Duration.minutes(15), // Full pipeline timeout
      comment: 'Daily stock screening pipeline: fetch fundamentals → apply value filters',
    });

    // ==========================================
    // EVENTBRIDGE — Daily Schedule
    // ==========================================
    // Trigger the pipeline daily at 8 PM UTC (4 PM ET — market close).
    // The rule passes the initial input to the state machine.
    const dailyRule = new events.Rule(this, 'DailyTrigger', {
      ruleName: 'stock-screener-daily-trigger',
      schedule: events.Schedule.cron({
        minute: '0',
        hour: '20',    // 8 PM UTC = 4 PM ET
        weekDay: 'MON-FRI',  // Only on trading days
      }),
      description: 'Triggers the stock screener pipeline daily at market close (4 PM ET)',
    });

    // Pass initial configuration to the state machine
    dailyRule.addTarget(new targets.SfnStateMachine(stateMachine, {
      input: events.RuleTargetInput.fromObject({
        batch_start: 0,
        batch_size: 50,  // Process 50 stocks per run (conservative for free tier)
      }),
    }));

    // ==========================================
    // OUTPUTS
    // ==========================================
    new cdk.CfnOutput(this, 'RawDataBucketName', {
      value: rawDataBucket.bucketName,
      description: 'S3 bucket for raw financial data',
    });

    new cdk.CfnOutput(this, 'FundamentalsFetcherArn', {
      value: fundamentalsFetcher.functionArn,
      description: 'ARN of the fundamentals fetcher Lambda',
    });

    new cdk.CfnOutput(this, 'StockScreenerArn', {
      value: stockScreener.functionArn,
      description: 'ARN of the stock screener Lambda',
    });

    new cdk.CfnOutput(this, 'StateMachineArn', {
      value: stateMachine.stateMachineArn,
      description: 'ARN of the pipeline state machine',
    });
  }
}
