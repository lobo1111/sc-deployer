#!/usr/bin/env bash
set -euo pipefail

# E2E runner for real AWS. This will create and destroy resources.
# Requires: scd in PATH, AWS CLI profile "sandbox" configured, and permissions.

REGION="${REGION:-us-east-1}"
ENVIRONMENT="${ENVIRONMENT:-sandbox}"
AWS_PROFILE_NAME="${AWS_PROFILE_NAME:-sandbox}"

tmp="$(mktemp -d)"
cleanup() {
  if [[ -d "$tmp/scd-e2e" ]]; then
    (cd "$tmp/scd-e2e" && scd destroy -e "${ENVIRONMENT}" --force) || true
  fi
  rm -rf "$tmp"
}
trap cleanup EXIT

cd "$tmp"
scd init --name scd-e2e --sample
cd scd-e2e

# SSO profiles don't need account id provided; `connect` will discover it via STS.
scd connect -e "${ENVIRONMENT}" --aws-profile "${AWS_PROFILE_NAME}" --region "${REGION}" --sso-login

# Derive an IAM principal ARN for Service Catalog portfolio association.
# For SSO this is typically an assumed role ARN, convert it to the IAM role ARN.
caller_arn="$(aws sts get-caller-identity --profile "${AWS_PROFILE_NAME}" --query Arn --output text)"
principal_arn="${caller_arn}"
if [[ "${caller_arn}" == *":assumed-role/"* ]]; then
  account_id="$(echo "${caller_arn}" | cut -d: -f5)"
  role_name="$(echo "${caller_arn}" | sed -n 's#^arn:aws:sts::[0-9]\+:assumed-role/\([^/]\+\)/.*#\1#p')"
  principal_arn="arn:aws:iam::${account_id}:role/${role_name}"
fi

# Minimal portfolio so products have a launch path.
cat > .deployer/bootstrap.yaml <<'YAML'
settings:
  state_file: .bootstrap-state.json

template_bucket:
  name_prefix: sc-templates
  versioning: true
  encryption: AES256

ecr_repositories: []

portfolios:
  infra:
    display_name: Infrastructure
    description: E2E test portfolio
    provider_name: scd
    principals:
      - __PRINCIPAL_ARN__
    tags: {}
YAML

# Substitute derived principal into YAML.
perl -0777 -i -pe "s#__PRINCIPAL_ARN__#${principal_arn}#g" .deployer/bootstrap.yaml

# Add a couple products for dependency validation.
scd products add --name networking --portfolio infra --output VpcId
scd products add --name database --portfolio infra --dependency networking --param-mapping VpcId=networking.VpcId --output DbEndpoint

scd sync -e "${ENVIRONMENT}"
scd deploy publish -e "${ENVIRONMENT}"
scd deploy apply -e "${ENVIRONMENT}"
scd deploy status -e "${ENVIRONMENT}"

echo "E2E OK (cleanup will run automatically)"

