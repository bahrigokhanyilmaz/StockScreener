#!/usr/bin/env node
import * as cdk from 'aws-cdk-lib/core';
import { StockScreenerStack } from '../lib/stock-screener-stack';

const app = new cdk.App();
new StockScreenerStack(app, 'StockScreenerStack', {
  // Pinned to your AWS account and region
  env: {
    account: '116488731375',
    region: 'us-east-2',
  },
});
