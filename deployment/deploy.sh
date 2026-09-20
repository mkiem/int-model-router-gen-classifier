#!/usr/bin/env bash
# Deploys the Intelligent Model Router. Requires: AWS CLI v2, credentials with
# admin-level access, a region where Bedrock AgentCore is available.
set -euo pipefail

STACK_NAME="${STACK_NAME:-intelligent-model-router}"
REGION="${AWS_REGION:-us-east-1}"
ENGINE="${DECISION_ENGINE:-jev}"           # jev | bedrock
JEV_API_KEY="${JEV_API_KEY:-}"             # required when ENGINE=jev
JEV_ENDPOINT="${JEV_ENDPOINT:-https://openrouter.ai/api/alpha/decisions}"
JEV_MODEL="${JEV_MODEL:-typesafe/jev-1.13}"

if [[ "$ENGINE" == "jev" && -z "$JEV_API_KEY" ]]; then
  echo "ERROR: set JEV_API_KEY (or DECISION_ENGINE=bedrock for the in-account engine)"; exit 1
fi

cd "$(dirname "$0")/.."
ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
ASSET_BUCKET="imr-deploy-assets-${ACCOUNT}-${REGION}"
aws s3 mb "s3://${ASSET_BUCKET}" --region "$REGION" 2>/dev/null || true

echo "==> Building provisioner (needs current boto3; Lambda's bundled botocore predates inference targets)"
BUILD_DIR=$(mktemp -d)
cp source/lambda/provisioner/provisioner.py "$BUILD_DIR/"
# find a working pip (python3 may be a minimal build without pip)
PIP=""
for c in "python3 -m pip" "pip3" "pip" "python3.12 -m pip" "python3.11 -m pip"; do
  if $c --version >/dev/null 2>&1; then PIP="$c"; break; fi
done
[[ -z "$PIP" ]] && { echo "ERROR: no pip found; install python3-pip"; exit 1; }
$PIP install -q -r source/lambda/provisioner/requirements.txt -t "$BUILD_DIR"
# keep the build template beside the original so relative Code paths resolve
sed "s|../lambda/provisioner/|${BUILD_DIR}/|" source/infrastructure/template.yaml > source/infrastructure/.build-template.yaml

echo "==> Packaging Lambda code"
aws cloudformation package \
  --template-file source/infrastructure/.build-template.yaml \
  --s3-bucket "$ASSET_BUCKET" --s3-prefix lambda \
  --output-template-file /tmp/imr-packaged.yaml --region "$REGION"

echo "==> Deploying stack ${STACK_NAME}"
aws cloudformation deploy \
  --template-file /tmp/imr-packaged.yaml \
  --stack-name "$STACK_NAME" --region "$REGION" \
  --capabilities CAPABILITY_NAMED_IAM \
  --parameter-overrides \
    DecisionEngine="$ENGINE" JevApiKey="$JEV_API_KEY" \
    JevEndpoint="$JEV_ENDPOINT" JevModel="$JEV_MODEL"

echo "==> Uploading routing configuration"
CONFIG_URI=$(aws cloudformation describe-stacks --stack-name "$STACK_NAME" --region "$REGION" \
  --query "Stacks[0].Outputs[?OutputKey=='ConfigLocation'].OutputValue" --output text)
aws s3 cp source/config/routing_config.json "$CONFIG_URI" --region "$REGION"

echo "==> Done. Endpoints:"
aws cloudformation describe-stacks --stack-name "$STACK_NAME" --region "$REGION" \
  --query "Stacks[0].Outputs" --output table
