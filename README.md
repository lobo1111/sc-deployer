# SC Deployer

AWS Service Catalog deployment orchestrator with dependency management.

## Features

- **Dependency Graph**: Products can depend on other products
- **Output → Parameter Mapping**: Pass outputs from one product as parameters to dependents
- **Change Detection**: Git-based detection of which products have changed
- **Cascade Updates**: Changed products trigger redeployment of all dependents
- **Version Tracking**: Auto-generated versions based on timestamp
- **Bootstrap**: One-time setup of portfolios, products, ECR repos, S3 bucket

## Directory Structure

```
sc-deployer/
├── profiles.yaml           # AWS profiles (shared config)
├── bootstrap.yaml          # Bootstrap configuration (portfolios, ECR, S3)
├── catalog.yaml            # Products and dependencies
├── .bootstrap-state.json   # Bootstrap state (generated)
├── .deploy-state.json      # Deploy state (generated)
├── products/
│   ├── networking/
│   │   ├── product.yaml    # Product metadata
│   │   └── template.yaml   # CloudFormation template
│   ├── database/
│   │   ├── product.yaml
│   │   └── template.yaml
│   └── api/
│       ├── product.yaml
│       └── template.yaml
└── scripts/
    ├── config.py           # Shared config loader
    ├── manage.py           # Management CLI (profiles, portfolios, products)
    ├── bootstrap.py        # Bootstrap script
    └── deploy.py           # Deploy script
```

## Setup

**Quick start (recommended):**

```powershell
# Windows PowerShell
.\cli.ps1
```

```bash
# Linux/macOS
./cli.sh
```

The wrapper scripts automatically:
- Check Python 3.10+ is installed
- Create virtual environment (optional)
- Install dependencies
- Launch interactive menu

**Manual setup:**

```bash
# Create virtual environment
python -m venv .venv

# Activate (Windows)
.\.venv\Scripts\Activate.ps1

# Activate (Linux/macOS)
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

## Usage

### 0. Configure Profiles

```bash
# Scan available AWS profiles from ~/.aws/config
python scripts/manage.py profiles scan

# Add a profile to profiles.yaml (interactive)
python scripts/manage.py profiles add dev
python scripts/manage.py profiles add prod

# List configured profiles
python scripts/manage.py profiles list

# Login via SSO
python scripts/manage.py profiles login dev

# Check identity
python scripts/manage.py profiles whoami dev
```

### 1. Bootstrap (one-time per environment)

Creates foundational resources: S3 bucket, ECR repos, portfolios, products.

```bash
# Preview
python scripts/bootstrap.py bootstrap -e dev --dry-run

# Execute
python scripts/bootstrap.py bootstrap -e dev

# Check status
python scripts/bootstrap.py status -e dev
```

### 2. Publish & Deploy

```bash
# Validate configuration
python scripts/deploy.py validate -e dev

# See what changed and deployment order
python scripts/deploy.py plan -e dev

# Publish changed products (upload templates, create versions)
python scripts/deploy.py publish -e dev

# Deploy published products (create/update CloudFormation stacks)
python scripts/deploy.py deploy -e dev

# Check status
python scripts/deploy.py status -e dev
```

### Options

```bash
# Dry run (preview without changes)
python scripts/deploy.py publish -e dev --dry-run

# Specific product (and its dependents)
python scripts/deploy.py publish -e dev -p database

# Override AWS profile/region
python scripts/deploy.py deploy -e prod --profile my-prod --region us-east-1
```

### 3. Add Portfolios & Products

```bash
# List existing portfolios
python scripts/manage.py portfolios list

# Add a new portfolio (interactive)
python scripts/manage.py portfolios add security -e dev

# List existing products
python scripts/manage.py products list

# Add a new product (interactive, creates directory and templates)
python scripts/manage.py products add monitoring
```

## Configuration

### profiles.yaml (shared)

```yaml
profiles:
  dev:
    aws_profile: my-aws-dev
    aws_region: eu-west-1
    account_id: "111111111111"

  prod:
    aws_profile: my-aws-prod
    aws_region: eu-west-1
    account_id: "222222222222"
```

### bootstrap.yaml

```yaml
settings:
  profiles_file: profiles.yaml

template_bucket:
  name_prefix: sc-templates
  versioning: true

ecr_repositories:
  - name: api-service
    scan_on_push: true

portfolios:
  infrastructure:
    display_name: Infrastructure Services
    principals:
      - arn:aws:iam::${account_id}:role/DevOpsRole
```

### catalog.yaml

```yaml
settings:
  profiles_file: profiles.yaml

products:
  networking:
    path: products/networking
    portfolio: infrastructure
    dependencies: []
    outputs:
      - VpcId
      - SubnetIds

  database:
    path: products/database
    portfolio: data-services
    dependencies:
      - networking
    parameter_mapping:
      VpcId: networking.VpcId        # Maps networking.VpcId → database.VpcId
      SubnetIds: networking.SubnetIds
    outputs:
      - DatabaseEndpoint
```

## Workflow

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│  bootstrap  │ ──▶ │   publish   │ ──▶ │   deploy    │
└─────────────┘     └─────────────┘     └─────────────┘
       │                   │                   │
       ▼                   ▼                   ▼
  Creates:            Uploads:            Creates:
  - S3 bucket         - Templates         - CloudFormation
  - ECR repos         - SC versions         stacks
  - Portfolios                            - Captures outputs
  - Products
```

## Version Format

Versions are auto-generated at publish time:

```
2026.01.30.143052
```

Format configurable in `catalog.yaml`:

```yaml
settings:
  version_format: "%Y.%m.%d.%H%M%S"
```

## Change Detection

1. Compares current git commit with last published commit per product
2. If files in product path changed → product is marked changed
3. All dependents of changed products are also marked for redeployment
4. Topological sort ensures dependencies deploy before dependents

## License

MIT
