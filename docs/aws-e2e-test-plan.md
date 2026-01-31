## AWS e2e test plan (sandbox)

This is a **manual / CI-on-demand** plan to validate the full `scd` functionality against real AWS.

### Preconditions
- AWS CLI configured with profile **`sandbox`** (`~/.aws/config`)
- The profile has permissions for:
  - Service Catalog admin actions
  - S3 bucket create/tag/version/encryption
  - ECR repo create/delete
  - IAM role create/delete + attach/detach policies
  - CloudFormation (via Service Catalog launch role)

### Safety rules
- Always run in a dedicated sandbox AWS account.
- Always ensure cleanup runs even on failure.
- Prefer `--dry-run` first for destructive actions.

### End-to-end flow
1. Create a temp workspace:
   - `tmp=$(mktemp -d)`
   - `cd "$tmp"`
2. Init project:
   - `scd init --name scd-e2e --sample`
   - `cd scd-e2e`
3. Configure environment (uses AWS profile `sandbox`):
   - `scd connect -e sandbox --aws-profile sandbox --region us-east-1 --sso-login`
4. Define desired state:
   - edit `.deployer/bootstrap.yaml`:
     - add portfolios
     - add ecr repositories
     - ensure the portfolio has a principal associated (otherwise `list_launch_paths` will be empty)
   - add products:
     - ensure products are assigned to a portfolio so they have a launch path
     - `scd products add --name networking --portfolio infra --output VpcId`
     - `scd products add --name database --portfolio infra --dependency networking --param-mapping VpcId=networking.VpcId --output DbEndpoint`
5. Sync desired state to AWS:
   - `scd sync -e sandbox`
6. Publish + apply:
   - `scd deploy publish -e sandbox`
   - `scd deploy apply -e sandbox`
   - `scd deploy status -e sandbox`
7. Cleanup (must run even on failure):
   - `scd destroy -e sandbox --force`
   - `cd / && rm -rf "$tmp"`

### Recommended automation approach
Run with a shell trap so cleanup always happens:

```bash
set -euo pipefail
tmp="$(mktemp -d)"
cleanup() {
  if [ -d "$tmp/scd-e2e" ]; then
    (cd "$tmp/scd-e2e" && scd destroy -e sandbox --force) || true
  fi
  rm -rf "$tmp"
}
trap cleanup EXIT

cd "$tmp"
scd init --name scd-e2e
cd scd-e2e
scd connect -e sandbox --aws-profile sandbox --region us-east-1 --sso-login
scd sync -e sandbox
scd deploy publish -e sandbox
scd deploy apply -e sandbox
```

