import * as cdk from 'aws-cdk-lib/core';
import { Construct } from 'constructs';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as path from 'path';
import { PythonFunction } from '@aws-cdk/aws-lambda-python-alpha';

/**
 * StockScreenerStack — Phase 1
 *
 * This stack defines the AWS resources for the fundamentals pipeline:
 *
 * 1. S3 Bucket (data lake) — stores raw financial data for historical analysis
 * 2. Lambda Function (fundamentals-fetcher) — provider-agnostic data fetcher
 *
 * The Lambda uses a provider abstraction layer. The active provider is set
 * via the PROVIDER environment variable:
 *   "yfinance" — free, no API key (current POC)
 *   "fmp"      — paid, stable API (future upgrade)
 *
 * Switching providers is a one-line change here + redeploy. No application
 * code changes needed.
 */
export class StockScreenerStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);

    // ==========================================
    // S3 BUCKET — Raw Data Lake
    // ==========================================
    // Stores all raw API responses, date-partitioned.
    // Permanent historical record for retroactive analysis via Athena.
    //
    // - versioned: keeps old versions if overwritten (safety net)
    // - lifecycleRules: moves old data to cheaper storage after 90 days
    // - removalPolicy: DESTROY for development (use RETAIN in production)
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
    // LAMBDA — Fundamentals Fetcher
    // ==========================================
    // PythonFunction: Docker-based bundling that installs requirements.txt
    // in a Linux container matching Lambda's runtime. Production-grade:
    //   - Works for compiled C extensions (pandas, numpy)
    //   - Reproducible across any machine or CI/CD
    //   - Just add packages to requirements.txt, CDK handles the rest
    //
    // The handler is provider-agnostic — it reads the PROVIDER env var
    // and initializes the corresponding data provider via factory pattern.
    const fundamentalsFetcher = new PythonFunction(this, 'FundamentalsFetcher', {
      functionName: 'stock-screener-fundamentals-fetcher',
      entry: path.join(__dirname, '../lambdas/fundamentals-fetcher'),
      index: 'handler.py',
      handler: 'handler',
      runtime: lambda.Runtime.PYTHON_3_12,
      architecture: lambda.Architecture.ARM_64,
      timeout: cdk.Duration.minutes(5),
      memorySize: 256, // Lightweight — only requests + boto3 now
      environment: {
        // Active data provider — change this to switch sources.
        // Currently using FMP (free tier: per-symbol endpoints + NASDAQ ticker list)
        PROVIDER: 'fmp',
        // S3 bucket for raw data storage
        RAW_DATA_BUCKET: rawDataBucket.bucketName,
        // SSM path for FMP API key
        FMP_API_KEY_PARAM: '/stock-screener/fmp-api-key',
        // Minimum market cap for universe inclusion (configurable)
        MIN_MARKET_CAP: '300000000',
      },
      description: 'Fetches fundamental stock data via configurable provider (yfinance/fmp)',
    });

    // ==========================================
    // PERMISSIONS
    // ==========================================

    // S3: write raw data to the data lake
    rawDataBucket.grantWrite(fundamentalsFetcher);

    // SSM: read the FMP API key (for when PROVIDER=fmp is activated)
    // We grant this now so switching to FMP requires only an env var change.
    fundamentalsFetcher.addToRolePolicy(new iam.PolicyStatement({
      effect: iam.Effect.ALLOW,
      actions: ['ssm:GetParameter'],
      resources: [
        `arn:aws:ssm:${this.region}:${this.account}:parameter/stock-screener/*`,
      ],
    }));

    // KMS: decrypt SecureString parameters (SSM uses AWS-managed KMS key)
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
  }
}
