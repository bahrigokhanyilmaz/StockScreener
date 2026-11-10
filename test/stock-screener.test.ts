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

const schedule = () => Object.values(template.findResources('AWS::Scheduler::Schedule'))[0] as any;
const machineId = () => Object.keys(template.findResources('AWS::StepFunctions::StateMachine'))[0];

test('schedule input is the same empty JSON object the old Events rule passed', () => {
  expect(JSON.parse(schedule().Properties.Target.Input)).toEqual({});
});

test('schedule target role is a scheduler.amazonaws.com role', () => {
  const roleRef = schedule().Properties.Target.RoleArn['Fn::GetAtt'][0];
  const role = template.findResources('AWS::IAM::Role')[roleRef] as any;
  expect(role.Properties.AssumeRolePolicyDocument.Statement).toEqual([
    expect.objectContaining({
      Action: 'sts:AssumeRole',
      Effect: 'Allow',
      Principal: { Service: 'scheduler.amazonaws.com' },
    }),
  ]);
  expect(Object.keys(role.Properties)).not.toContain('ManagedPolicyArns');
});

test('scheduler role may states:StartExecution on exactly the pipeline state machine', () => {
  const roleRef = schedule().Properties.Target.RoleArn['Fn::GetAtt'][0];
  const policies = Object.values(template.findResources('AWS::IAM::Policy')) as any[];
  const attached = policies.filter((p) => p.Properties.Roles?.some((r: any) => r.Ref === roleRef));
  expect(attached).toHaveLength(1);
  const statements = attached[0].Properties.PolicyDocument.Statement as any[];
  const start = statements.filter((st) => ([] as string[]).concat(st.Action).includes('states:StartExecution'));
  expect(start).toHaveLength(1);
  expect(start[0].Effect).toBe('Allow');
  expect(start[0].Action).toBe('states:StartExecution');
  expect(start[0].Resource).toEqual({ Ref: machineId() });
});

test('schedule target role is wired to that role', () => {
  const roleRef = schedule().Properties.Target.RoleArn;
  const roles = Object.keys(template.findResources('AWS::IAM::Role'));
  expect(roles).toContain(roleRef['Fn::GetAtt'][0]);
});

test('a failed schedule start lands in a DLQ that the role can write to', () => {
  const queues = template.findResources('AWS::SQS::Queue');
  const dlqId = Object.keys(queues).find(
    (k) => (queues[k] as any).Properties.QueueName === 'stock-screener-schedule-dlq',
  )!;
  expect(dlqId).toBeDefined();
  expect(schedule().Properties.Target.DeadLetterConfig).toEqual({ Arn: { 'Fn::GetAtt': [dlqId, 'Arn'] } });
  template.hasResourceProperties('AWS::IAM::Policy', {
    PolicyDocument: {
      Statement: Match.arrayWith([
        Match.objectLike({
          Action: Match.arrayWith(['sqs:SendMessage']),
          Resource: { 'Fn::GetAtt': [dlqId, 'Arn'] },
        }),
      ]),
    },
  });
});

test('DLQ alarm notifies the existing alerts topic', () => {
  const topics = template.findResources('AWS::SNS::Topic');
  const topicId = Object.keys(topics).find(
    (k) => (topics[k] as any).Properties.TopicName === 'stock-screener-alerts',
  )!;
  template.hasResourceProperties('AWS::CloudWatch::Alarm', {
    AlarmName: 'stock-screener-schedule-failed',
    MetricName: 'ApproximateNumberOfMessagesVisible',
    Namespace: 'AWS/SQS',
    Threshold: 1,
    ComparisonOperator: 'GreaterThanOrEqualToThreshold',
    TreatMissingData: 'notBreaching',
    AlarmActions: [{ Ref: topicId }],
  });
});
