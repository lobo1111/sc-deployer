# SC Deployer

AWS Service Catalog deployment orchestrator with dependency management, change detection, and Service Catalog provisioning.

## Features

- **Service Catalog Provisioning**: Deploy products through AWS Service Catalog (not direct CloudFormation)
- **Dependency Graph**: Products can depend on other products
- **Output → Parameter Mapping**: Pass outputs from one product as parameters to dependents
- **Change Detection**: Git-based (or hash-based) detection of which products have changed
- **Cascade Updates**: Changed products trigger redeployment of all dependents
- **Version Tracking**: Auto-generated versions based on timestamp
- **Bootstrap**: One-time setup of portfolios, products, IAM launch roles, S3 bucket
- **Full CLI Support**: Both interactive menu and non-interactive commands

## Quick Start

```powershell
# Initialize a new project
.\cli.ps1 init /path/to/my-project
cd /path/to/my-project

# Configure AWS profile
.\cli.ps1 profiles add dev --aws-profile my-aws-profile --region eu-central-1

# Add a portfolio
.\cli.ps1 portfolios add infrastructure --display-name "Infrastructure" -e dev

# Add products
.\cli.ps1 products add networking --portfolio infrastructure --output VpcId --output SubnetIds
.\cli.ps1 products add database --portfolio infrastructure --dependency networking \
  --param-mapping "VpcId=networking.VpcId" --output DatabaseEndpoint

# Bootstrap AWS resources
.\cli.ps1 bootstrap dev

# Publish and deploy
.\cli.ps1 deploy publish dev
.\cli.ps1 deploy deploy dev

# Check status
.\cli.ps1 deploy status dev
```

## Directory Structure

```
my-project/
├── cli.ps1                     # Windows PowerShell wrapper
├── cli.sh                      # Linux/macOS Bash wrapper
├── products/                   # Product definitions
│   ├── networking/
│   │   ├── product.yaml
│   │   └── template.yaml       # CloudFormation template
│   └── database/
│       ├── product.yaml
│       └── template.yaml
└── deployer/
    ├── profiles.yaml           # AWS profiles configuration
    ├── bootstrap.yaml          # Bootstrap config (portfolios, S3)
    ├── catalog.yaml            # Products and dependencies
    ├── requirements.txt
    └── scripts/
        ├── config.py
        ├── manage.py           # Management CLI
        ├── bootstrap.py        # Bootstrap script
        └── deploy.py           # Deploy script
```

## CLI Reference

### Interactive Mode

```bash
.\cli.ps1              # Opens interactive menu
```

### Non-Interactive Commands

```bash
# Project initialization
manage.py init /path/to/project

# Profile management
manage.py profiles list
manage.py profiles scan
manage.py profiles add <env> --aws-profile <profile> --region <region>
manage.py profiles login <env>
manage.py profiles whoami <env>

# Portfolio management
manage.py portfolios list
manage.py portfolios add <name> --display-name "Name" --description "..." -e <env>

# Product management
manage.py products list
manage.py products add <name> --portfolio <portfolio> \
  --dependency <dep> --output <output> --param-mapping "Param=dep.Output"

# Bootstrap
manage.py bootstrap <env>
manage.py bootstrap <env> --dry-run
manage.py bootstrap <env> --destroy --force

# Deploy workflow
manage.py deploy plan <env>
manage.py deploy publish <env>
manage.py deploy publish <env> --force       # Publish even if no changes
manage.py deploy deploy <env>
manage.py deploy status <env>
manage.py deploy terminate <env> --force     # Terminate provisioned products

# Status
manage.py status
manage.py graph
```

## Configuration

### profiles.yaml

```yaml
profiles:
  dev:
    aws_profile: my-aws-dev
    aws_region: eu-central-1
    account_id: "111111111111"

  prod:
    aws_profile: my-aws-prod
    aws_region: eu-west-1
    account_id: "222222222222"
```

### bootstrap.yaml

```yaml
settings:
  state_file: .bootstrap-state.json
  profiles_file: profiles.yaml

template_bucket:
  name_prefix: sc-templates
  versioning: true
  encryption: AES256

ecr_repositories: []

portfolios:
  infrastructure:
    display_name: Infrastructure Services
    description: Core infrastructure products
    provider_name: Platform Team
    principals:
      - arn:aws:iam::${account_id}:role/DevOpsRole
    tags:
      Team: Platform
```

### catalog.yaml

```yaml
settings:
  state_file: .deploy-state.json
  version_format: "%Y.%m.%d.%H%M%S"
  profiles_file: profiles.yaml

products:
  networking:
    path: networking
    portfolio: infrastructure
    dependencies: []
    outputs:
      - VpcId
      - SubnetIds

  database:
    path: database
    portfolio: infrastructure
    dependencies:
      - networking
    parameter_mapping:
      VpcId: networking.VpcId
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
  Creates:            Uploads:            Provisions:
  - S3 bucket         - Templates         - Service Catalog
  - IAM launch role   - SC artifacts        products
  - Portfolios                            - Captures outputs
  - Products
  - Launch constraints
```

## Change Detection

1. Compares current git commit (or file hash) with last published state
2. If files in product path changed → product is marked changed
3. Only changed products are published (use `--force` to override)
4. All dependents of changed products are marked for redeployment
5. Topological sort ensures dependencies deploy before dependents

## Service Catalog Provisioning

Unlike direct CloudFormation deployment, SC Deployer provisions products through AWS Service Catalog:

- Creates IAM launch role with necessary permissions
- Creates launch constraints for each product
- Uses `ProvisionProduct` and `UpdateProvisionedProduct` APIs
- Tracks provisioned product IDs in state
- Supports `terminate` command to clean up provisioned products

## Requirements

- Python 3.10+
- AWS CLI configured with appropriate credentials
- Permissions for: Service Catalog, S3, IAM, CloudFormation

## License

MIT
