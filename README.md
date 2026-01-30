# SC Deployer

AWS Service Catalog deployment orchestrator with dependency management, change detection, and AI-assisted development workflow.

## Features

### Deployment
- **Service Catalog Provisioning**: Deploy products through AWS Service Catalog (not direct CloudFormation)
- **Dependency Graph**: Products can depend on other products
- **Output → Parameter Mapping**: Pass outputs from one product as parameters to dependents
- **Change Detection**: Git-based (or hash-based) detection of which products have changed
- **Cascade Updates**: Changed products trigger redeployment of all dependents
- **Version Tracking**: Auto-generated versions based on timestamp
- **Bootstrap**: One-time setup of portfolios, products, IAM launch roles, S3 bucket
- **Full CLI Support**: Both interactive menu and non-interactive commands

### Development Workflow
- **AI-Assisted Development**: Skills and sub-agents for automated implementation
- **Capability Documentation**: Business + functional + technical documentation per product
- **Work Order Management**: GitHub Issues for tracking work
- **Automated Testing**: Unit tests for templates, integration tests for deployments
- **Multi-Environment**: Dev → Stage → Prod promotion with quality gates

## Development Workflow

### Human Interaction Points

The workflow has **4 human interaction points**. AI agents handle everything else:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  1. REQUIREMENT        2. WORK ORDERS       3. PR REVIEW      4. PROD       │
│  ┌─────────┐          ┌─────────┐          ┌─────────┐      ┌─────────┐    │
│  │ Define  │ ──────▶  │ Approve │ ───────▶ │ Approve │ ───▶ │ Approve │    │
│  │ & Shape │          │ Plan    │          │ Code    │      │ Release │    │
│  └─────────┘          └─────────┘          └─────────┘      └─────────┘    │
│       │                    │                    │                │          │
│       ▼                    ▼                    ▼                ▼          │
│  Agent asks           Agent creates        Agent reviews    Agent shows    │
│  questions to         GitHub issues &      code first,      test results,  │
│  clarify need         implementation       human approves   human approves │
│                       plan                                                  │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Environments & Quality Gates

| Environment | Branch | Deploy Trigger | Quality Gate |
|-------------|--------|----------------|--------------|
| **dev** | `develop` | Auto on merge | Agent PR review |
| **stage** | `main` | Auto on merge | Human PR review + unit tests |
| **prod** | `main` (tagged) | Manual approval | Integration tests + human approval |

### Product Capability Documentation

Each product has a `CAPABILITY.md` that describes:
- **Business Context**: Why this product exists, who uses it
- **Functional Capabilities**: What it does from a business perspective
- **Technical Implementation**: How it works (AWS resources, architecture)
- **Interfaces**: Parameters, outputs, dependencies
- **Constraints**: Current limitations

See [products/api/CAPABILITY.md](products/api/CAPABILITY.md) for an example.

## Quick Start

```powershell
# Initialize a new project
.\cli.ps1 init /path/to/my-project
cd /path/to/my-project

# Configure AWS profiles for all environments
.\cli.ps1 profiles add dev --aws-profile my-aws-dev --region eu-west-1
.\cli.ps1 profiles add stage --aws-profile my-aws-stage --region eu-west-1
.\cli.ps1 profiles add prod --aws-profile my-aws-prod --region eu-west-1

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
│   │   ├── CAPABILITY.md       # Business + functional + technical docs
│   │   ├── product.yaml
│   │   └── template.yaml       # CloudFormation template
│   └── database/
│       ├── CAPABILITY.md
│       ├── product.yaml
│       └── template.yaml
├── deployer/
│   ├── profiles.yaml           # AWS profiles (dev, stage, prod)
│   ├── bootstrap.yaml          # Bootstrap config (portfolios, S3)
│   ├── catalog.yaml            # Products and dependencies
│   ├── requirements.txt
│   └── scripts/
│       ├── config.py
│       ├── manage.py           # Management CLI
│       ├── bootstrap.py        # Bootstrap script
│       └── deploy.py           # Deploy script
├── tests/
│   ├── unit/                   # Template validation tests
│   │   ├── test_networking.py
│   │   ├── test_database.py
│   │   └── test_api.py
│   └── integration/            # Deployed resource tests
│       ├── test_networking.py
│       ├── test_database.py
│       └── test_api.py
├── skills/                     # AI agent skill definitions
│   ├── guide.yaml
│   ├── requirement-shaper.yaml
│   ├── work-order-planner.yaml
│   ├── implementer.yaml
│   ├── capability-writer.yaml
│   ├── test-writer.yaml
│   ├── pr-reviewer.yaml
│   ├── deployer.yaml
│   └── release-manager.yaml
└── .github/
    ├── workflows/
    │   ├── validate.yml        # PR validation
    │   ├── deploy-dev.yml      # Auto-deploy to dev
    │   ├── deploy-stage.yml    # Auto-deploy to stage
    │   └── deploy-prod.yml     # Manual deploy to prod
    └── ISSUE_TEMPLATE/
        ├── product-feature.yml
        ├── product-change.yml
        └── bug.yml
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

## Skills & Sub-Agents

The `skills/` directory defines AI agent behaviors for the development workflow:

| Skill | Purpose |
|-------|---------|
| `guide` | Routes human interactions to correct workflow point |
| `requirement-shaper` | Conversational refinement of business requirements |
| `work-order-planner` | Creates work orders with parallelization strategy |
| `orchestrator` | Manages parallel sub-agents and branch synchronization |
| `implementer` | Writes CloudFormation templates (can be sub-agent) |
| `capability-writer` | Updates CAPABILITY.md documentation |
| `test-writer` | Creates unit and integration tests |
| `pr-reviewer` | Reviews PRs before human review |
| `deployer` | Executes publish/deploy commands |
| `release-manager` | Manages production releases |

### Parallelization

When requirements affect multiple products, work is parallelized:

```
         feature/123-integration
              /          \
             /            \
   Lane A (agent-1)   Lane B (agent-2)
      networking         database
            \            /
             \          /
           [merge & sync]
                 |
          Lane C (agent-1)
              api
                 |
          PR to develop
```

- Independent products work in parallel (separate lanes, separate branches)
- Dependent products wait for dependencies (phased execution)
- Orchestrator manages synchronization and merges

See [skills/README.md](skills/README.md) for detailed skill documentation.

## Testing

### Unit Tests (Template Validation)

```bash
# Run all unit tests
pytest tests/unit/ -v

# Run tests for specific product
pytest tests/unit/test_networking.py -v
```

### Integration Tests (Deployed Resources)

```bash
# Run against stage environment
TEST_ENVIRONMENT=stage pytest tests/integration/ -v

# Run against prod (smoke tests only)
TEST_ENVIRONMENT=prod pytest tests/integration/ -v -m smoke
```

## GitHub Workflows

| Workflow | Trigger | Actions |
|----------|---------|---------|
| `validate.yml` | PR to develop/main | Lint, validate, unit tests |
| `deploy-dev.yml` | Merge to develop | Publish & deploy to dev |
| `deploy-stage.yml` | Merge to main | Publish, deploy to stage, integration tests |
| `deploy-prod.yml` | Manual + approval | Publish, deploy to prod, create release |

## Requirements

- Python 3.10+
- AWS CLI configured with appropriate credentials
- Permissions for: Service Catalog, S3, IAM, CloudFormation
- pytest (for tests)
- cfn-lint (for template validation)

## License

MIT
