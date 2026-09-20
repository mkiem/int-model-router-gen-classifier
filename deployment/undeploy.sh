#!/usr/bin/env bash
# Tears down the Intelligent Model Router stack, including the versioned config bucket.
set -euo pipefail
STACK_NAME="${STACK_NAME:-intelligent-model-router}"
REGION="${AWS_REGION:-us-east-1}"

BUCKET=$(aws cloudformation describe-stacks --stack-name "$STACK_NAME" --region "$REGION" \
  --query "Stacks[0].Outputs[?OutputKey=='ConfigLocation'].OutputValue" --output text | sed 's|s3://||;s|/.*||')

if [[ -n "$BUCKET" && "$BUCKET" != "None" ]]; then
  echo "==> Emptying versioned bucket ${BUCKET}"
  while :; do
    VERSIONS=$(aws s3api list-object-versions --bucket "$BUCKET" --region "$REGION" --max-keys 500 \
      --query '{Objects: [Versions[].{Key:Key,VersionId:VersionId}, DeleteMarkers[].{Key:Key,VersionId:VersionId}][] | [0:500]}' \
      --output json)
    [[ "$(echo "$VERSIONS" | grep -c VersionId || true)" == "0" ]] && break
    aws s3api delete-objects --bucket "$BUCKET" --region "$REGION" --delete "$VERSIONS" >/dev/null
  done
fi

echo "==> Deleting stack ${STACK_NAME}"
aws cloudformation delete-stack --stack-name "$STACK_NAME" --region "$REGION"
aws cloudformation wait stack-delete-complete --stack-name "$STACK_NAME" --region "$REGION"
echo "==> Stack deleted"
