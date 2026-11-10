import * as cdk from 'aws-cdk-lib/core';
import { Template, Match } from 'aws-cdk-lib/assertions';
import * as StockScreener from '../lib/stock-screener-stack';

// Skip Docker bundling of PythonFunction assets: synth-only assertions.
const app = new cdk.App({ context: { 'aws:cdk:bundling-stacks': [] } });
const stack = new StockScreener.StockScreenerStack(app, 'TestStack', {
  env: { account: '123456789012', region: 'us-east-2' },
});
const template = Template.fromStack(stack);

test('stack module resolves to TypeScript source, not a stale compiled .js', () => {
  expect(require.resolve('../lib/stock-screener-stack')).toMatch(/\.ts$/);
});

test('daily trigger is a DST-aware Scheduler schedule at 5 PM America/New_York', () => {
  template.resourceCountIs('AWS::Scheduler::Schedule', 1);
  template.hasResourceProperties('AWS::Scheduler::Schedule', {
    Name: 'stock-screener-daily-trigger',
    ScheduleExpression: 'cron(0 17 ? * MON-FRI *)',
    ScheduleExpressionTimezone: 'America/New_York',
    FlexibleTimeWindow: { Mode: 'OFF' },
    Target: Match.objectLike({
      Arn: Match.anyValue(),
      RetryPolicy: { MaximumRetryAttempts: 2, MaximumEventAgeInSeconds: 3600 },
    }),
  });
});

test('schedule targets the pipeline state machine', () => {
  const machines = template.findResources('AWS::StepFunctions::StateMachine');
  const machineIds = Object.keys(machines);
  expect(machineIds).toHaveLength(1);
  const schedules = template.findResources('AWS::Scheduler::Schedule');
  const target = Object.values(schedules)[0].Properties.Target;
  expect(target.Arn).toEqual({ Ref: machineIds[0] });
});

test('old UTC-only Events rule is gone', () => {
  const rules = template.findResources('AWS::Events::Rule');
  const names = Object.values(rules).map((r: any) => r.Properties.Name);
  expect(names).not.toContain('stock-screener-daily-trigger');
  // monthly tag discovery rule is unaffected
  expect(names).toContain('stock-screener-monthly-tag-discovery');
});
