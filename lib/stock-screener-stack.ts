import * as cdk from 'aws-cdk-lib/core';
import { Construct } from 'constructs';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as path from 'path';
import { PythonFunction } from '@aws-cdk/aws-lambda-python-alpha';

/**
 * StockScreenerStack
 *
 * Pipeline Step 1: fundamentals-fetcher — fetches data from FMP API
 * Pipeline Step 2: stock-screener — applies value filters to identify passing stocks
 *
 * Both Lambdas are provider-agnostic. The fundamentals-fetcher reads PROVIDER
 * env var to select data source. The stock-screener reads filter thresholds
 * from a bundled config file (screener-filters.json).
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
    // Pure filtering logic — no external API calls, no dependencies.
    // Takes the fundamentals-fetcher output, applies value filters,
    // returns passing stocks with scores.
    //
    // Uses lambda.Function (not PythonFunction) because there are no
    // pip dependencies to install — just pure Python + a JSON config file.
    const stockScreener = new lambda.Function(this, 'StockScreener', {
      functionName: 'stock-screener-filter',
      runtime: lambda.Runtime.PYTHON_3_12,
      architecture: lambda.Architecture.ARM_64,
      handler: 'handler.handler',
      code: lambda.Code.fromAsset(path.join(__dirname, '../lambdas/stock-screener')),
      timeout: cdk.Duration.seconds(30), // Pure computation, should be fast
      memorySize: 128, // Minimal — just filtering in-memory data
      description: 'Step 2: Applies value investing filters and scores stocks',
    });

    // ==========================================
    // PERMISSIONS
    // ==========================================

    // Fundamentals Fetcher: write to S3 + read SSM secrets
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
  }
}
