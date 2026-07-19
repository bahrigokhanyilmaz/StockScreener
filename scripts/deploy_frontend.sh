#!/bin/bash
# Deploy the React frontend to AWS Amplify.
# Run from project root: ./scripts/deploy_frontend.sh

set -e

APP_ID="d2ned6rk557ndc"
BRANCH="main"
PROFILE="stock-screener"
REGION="us-east-2"

echo "Building frontend..."
cd frontend
npm run build
cd dist

echo "Packaging..."
zip -r /tmp/frontend-deploy.zip .

echo "Creating deployment..."
RESULT=$(aws amplify create-deployment --app-id $APP_ID --branch-name $BRANCH --profile $PROFILE --region $REGION --output json)
JOB_ID=$(echo $RESULT | python3 -c "import sys,json; print(json.load(sys.stdin)['jobId'])")
UPLOAD_URL=$(echo $RESULT | python3 -c "import sys,json; print(json.load(sys.stdin)['zipUploadUrl'])")

echo "Uploading (job $JOB_ID)..."
curl -s -T /tmp/frontend-deploy.zip "$UPLOAD_URL"

echo "Starting deployment..."
aws amplify start-deployment --app-id $APP_ID --branch-name $BRANCH --job-id $JOB_ID --profile $PROFILE --region $REGION --output json > /dev/null

echo "Waiting for deployment..."
sleep 10
STATUS=$(aws amplify get-job --app-id $APP_ID --branch-name $BRANCH --job-id $JOB_ID --profile $PROFILE --region $REGION --output json | python3 -c "import sys,json; print(json.load(sys.stdin)['job']['summary']['status'])")

echo "Status: $STATUS"
if [ "$STATUS" == "SUCCEED" ]; then
    echo "Live at: https://main.$APP_ID.amplifyapp.com"
else
    echo "Check status: aws amplify get-job --app-id $APP_ID --branch-name $BRANCH --job-id $JOB_ID --profile $PROFILE --region $REGION"
fi
