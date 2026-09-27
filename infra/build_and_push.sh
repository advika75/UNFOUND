#!/usr/bin/env bash
# Build the Lambda container image (repo root Dockerfile) and push it to the stack's
# ECR repository. Run this before `cdk deploy` on a first deploy, and again any time
# the image changes -- `cdk deploy` alone does not build or push anything.
set -euo pipefail

REGION="${AWS_REGION:-ap-northeast-1}"
REPO_NAME="unfound-api"
TAG="${1:-latest}"
ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
REPO_URI="${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com/${REPO_NAME}"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

aws ecr get-login-password --region "$REGION" | docker login --username AWS --password-stdin "${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com"

docker build -t "${REPO_NAME}:${TAG}" -f "${ROOT_DIR}/Dockerfile" "${ROOT_DIR}"
docker tag "${REPO_NAME}:${TAG}" "${REPO_URI}:${TAG}"
docker push "${REPO_URI}:${TAG}"

echo "Pushed ${REPO_URI}:${TAG}"
echo "Now run: cdk deploy -c imageTag=${TAG} [-c enablePersonalization=true] [-c enableAdmin=true]"
