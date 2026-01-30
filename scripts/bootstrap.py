#!/usr/bin/env python3
"""
Bootstrap module - creates foundational AWS resources for Service Catalog.

Usage:
    python bootstrap.py bootstrap -e dev [--dry-run]
    python bootstrap.py status -e dev
"""

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import boto3
import yaml


@dataclass
class BootstrapContext:
    config: dict
    state: dict
    environment: str
    aws_profile: str
    aws_region: str
    account_id: str
    dry_run: bool = False


def load_bootstrap_config(path: Path = None) -> dict:
    if path is None:
        path = Path(__file__).parent.parent / "bootstrap.yaml"
    with open(path) as f:
        return yaml.safe_load(f)


def load_catalog_config(path: Path = None) -> dict:
    if path is None:
        path = Path(__file__).parent.parent / "catalog.yaml"
    if not path.exists():
        return {}
    with open(path) as f:
        return yaml.safe_load(f)


def load_bootstrap_state(config: dict) -> dict:
    state_file = Path(config["settings"].get("state_file", ".bootstrap-state.json"))
    # Resolve relative to project root
    if not state_file.is_absolute():
        state_file = Path(__file__).parent.parent / state_file
    if state_file.exists():
        with open(state_file) as f:
            return json.load(f)
    return {"schema_version": "1.0", "environments": {}}


def save_bootstrap_state(config: dict, state: dict):
    state_file = Path(config["settings"].get("state_file", ".bootstrap-state.json"))
    if not state_file.is_absolute():
        state_file = Path(__file__).parent.parent / state_file
    with open(state_file, "w") as f:
        json.dump(state, f, indent=2)


def get_session(ctx: BootstrapContext):
    return boto3.Session(profile_name=ctx.aws_profile, region_name=ctx.aws_region)


def interpolate(value: str, ctx: BootstrapContext) -> str:
    """Replace ${var} placeholders in config values."""
    return (
        value.replace("${account_id}", ctx.account_id)
        .replace("${region}", ctx.aws_region)
        .replace("${environment}", ctx.environment)
    )


# ============== S3 TEMPLATE BUCKET ==============


def bootstrap_template_bucket(ctx: BootstrapContext) -> dict:
    """Create S3 bucket for CloudFormation templates."""
    config = ctx.config.get("template_bucket", {})
    if not config:
        print("\n[S3] No template bucket configured")
        return {}

    bucket_name = f"{config['name_prefix']}-{ctx.account_id}-{ctx.aws_region}"
    print(f"\n[S3] Template bucket: {bucket_name}")

    if ctx.dry_run:
        print("  [DRY RUN] Would create bucket")
        return {"name": bucket_name, "arn": f"arn:aws:s3:::{bucket_name}"}

    session = get_session(ctx)
    s3 = session.client("s3")

    try:
        create_args = {"Bucket": bucket_name}
        if ctx.aws_region != "us-east-1":
            create_args["CreateBucketConfiguration"] = {
                "LocationConstraint": ctx.aws_region
            }
        s3.create_bucket(**create_args)
        print("  Created bucket")
    except s3.exceptions.BucketAlreadyOwnedByYou:
        print("  Bucket already exists")
    except Exception as e:
        if "BucketAlreadyOwnedByYou" in str(e):
            print("  Bucket already exists")
        else:
            raise

    # Enable versioning
    if config.get("versioning"):
        s3.put_bucket_versioning(
            Bucket=bucket_name, VersioningConfiguration={"Status": "Enabled"}
        )
        print("  Enabled versioning")

    # Enable encryption
    if config.get("encryption"):
        s3.put_bucket_encryption(
            Bucket=bucket_name,
            ServerSideEncryptionConfiguration={
                "Rules": [
                    {
                        "ApplyServerSideEncryptionByDefault": {
                            "SSEAlgorithm": config["encryption"]
                        }
                    }
                ]
            },
        )
        print(f"  Enabled encryption: {config['encryption']}")

    # Block public access
    s3.put_public_access_block(
        Bucket=bucket_name,
        PublicAccessBlockConfiguration={
            "BlockPublicAcls": True,
            "IgnorePublicAcls": True,
            "BlockPublicPolicy": True,
            "RestrictPublicBuckets": True,
        },
    )
    print("  Blocked public access")

    return {"name": bucket_name, "arn": f"arn:aws:s3:::{bucket_name}"}


# ============== ECR REPOSITORIES ==============


def bootstrap_ecr_repositories(ctx: BootstrapContext) -> dict:
    """Create ECR repositories for container images."""
    repos_config = ctx.config.get("ecr_repositories", [])
    results = {}

    if not repos_config:
        print("\n[ECR] No repositories configured")
        return results

    print(f"\n[ECR] {len(repos_config)} repositories")

    if ctx.dry_run:
        for repo in repos_config:
            name = repo["name"]
            print(f"  [DRY RUN] {name}")
            results[name] = {
                "arn": f"arn:aws:ecr:{ctx.aws_region}:{ctx.account_id}:repository/{name}",
                "uri": f"{ctx.account_id}.dkr.ecr.{ctx.aws_region}.amazonaws.com/{name}",
            }
        return results

    session = get_session(ctx)
    ecr = session.client("ecr")

    for repo in repos_config:
        name = repo["name"]

        try:
            response = ecr.create_repository(
                repositoryName=name,
                imageScanningConfiguration={
                    "scanOnPush": repo.get("scan_on_push", True)
                },
                imageTagMutability=repo.get("image_tag_mutability", "MUTABLE"),
                encryptionConfiguration={"encryptionType": "AES256"},
            )
            arn = response["repository"]["repositoryArn"]
            uri = response["repository"]["repositoryUri"]
            print(f"  Created: {name}")
        except ecr.exceptions.RepositoryAlreadyExistsException:
            response = ecr.describe_repositories(repositoryNames=[name])
            arn = response["repositories"][0]["repositoryArn"]
            uri = response["repositories"][0]["repositoryUri"]
            print(f"  Exists: {name}")

        results[name] = {"arn": arn, "uri": uri}

    return results


# ============== SERVICE CATALOG PORTFOLIOS ==============


def bootstrap_portfolios(ctx: BootstrapContext) -> dict:
    """Create Service Catalog portfolios."""
    portfolios_config = ctx.config.get("portfolios", {})
    results = {}

    if not portfolios_config:
        print("\n[Portfolios] No portfolios configured")
        return results

    print(f"\n[Portfolios] {len(portfolios_config)} portfolios")

    if ctx.dry_run:
        for key, config in portfolios_config.items():
            display_name = f"{config['display_name']} ({ctx.environment})"
            print(f"  [DRY RUN] {display_name}")
            results[key] = {"id": "port-dryrun", "arn": "arn:dry:run"}
        return results

    session = get_session(ctx)
    sc = session.client("servicecatalog")

    # Get existing portfolios
    existing = {}
    paginator = sc.get_paginator("list_portfolios")
    for page in paginator.paginate():
        for p in page["PortfolioDetails"]:
            existing[p["DisplayName"]] = p

    for key, config in portfolios_config.items():
        display_name = f"{config['display_name']} ({ctx.environment})"

        # Build tags
        tags = [{"Key": k, "Value": v} for k, v in config.get("tags", {}).items()]
        tags.append({"Key": "Environment", "Value": ctx.environment})
        tags.append({"Key": "ManagedBy", "Value": "sc-deployer"})

        if display_name in existing:
            portfolio_id = existing[display_name]["Id"]
            portfolio_arn = existing[display_name]["ARN"]
            print(f"  Exists: {key} ({portfolio_id})")
        else:
            response = sc.create_portfolio(
                DisplayName=display_name,
                Description=config.get("description", ""),
                ProviderName=config.get("provider_name", "Platform Team"),
                Tags=tags,
            )
            portfolio_id = response["PortfolioDetail"]["Id"]
            portfolio_arn = response["PortfolioDetail"]["ARN"]
            print(f"  Created: {key} ({portfolio_id})")

        # Associate principals
        for principal in config.get("principals", []):
            principal_arn = interpolate(principal, ctx)
            try:
                sc.associate_principal_with_portfolio(
                    PortfolioId=portfolio_id,
                    PrincipalARN=principal_arn,
                    PrincipalType="IAM",
                )
                print(f"    + Principal: {principal_arn.split('/')[-1]}")
            except Exception:
                pass  # Already associated

        results[key] = {"id": portfolio_id, "arn": portfolio_arn}

    return results


# ============== SERVICE CATALOG PRODUCTS ==============


def bootstrap_products(ctx: BootstrapContext, portfolios: dict) -> dict:
    """Create Service Catalog product definitions from catalog.yaml."""
    catalog = load_catalog_config()
    products_config = catalog.get("products", {})

    if not products_config:
        print("\n[Products] No products in catalog.yaml")
        return {}

    results = {}
    print(f"\n[Products] {len(products_config)} products")

    if ctx.dry_run:
        for name in products_config:
            print(f"  [DRY RUN] {name}-{ctx.environment}")
            results[name] = {"id": "prod-dryrun", "arn": "arn:dry:run"}
        return results

    session = get_session(ctx)
    sc = session.client("servicecatalog")

    # Get existing products
    existing = {}
    paginator = sc.get_paginator("search_products_as_admin")
    for page in paginator.paginate():
        for p in page.get("ProductViewDetails", []):
            existing[p["ProductViewSummary"]["Name"]] = p

    for name, config in products_config.items():
        product_name = f"{name}-{ctx.environment}"
        portfolio_key = config.get("portfolio")

        if product_name in existing:
            product_id = existing[product_name]["ProductViewSummary"]["ProductId"]
            product_arn = existing[product_name]["ProductARN"]
            print(f"  Exists: {name} ({product_id})")
        else:
            # Load product.yaml for description
            product_yaml = (
                Path(__file__).parent.parent / config["path"] / "product.yaml"
            )
            description = f"Service Catalog product: {name}"
            if product_yaml.exists():
                with open(product_yaml) as f:
                    meta = yaml.safe_load(f)
                    description = meta.get("description", description)

            response = sc.create_product(
                Name=product_name,
                Owner="Platform Team",
                Description=description,
                ProductType="CLOUD_FORMATION_TEMPLATE",
                Tags=[
                    {"Key": "Environment", "Value": ctx.environment},
                    {"Key": "ManagedBy", "Value": "sc-deployer"},
                    {"Key": "ProductKey", "Value": name},
                ],
                ProvisioningArtifactParameters={
                    "Name": "Initial",
                    "Description": "Placeholder - will be updated on first publish",
                    "Type": "CLOUD_FORMATION_TEMPLATE",
                    "Info": {"LoadTemplateFromURL": ""},
                },
            )
            product_id = response["ProductViewDetail"]["ProductViewSummary"][
                "ProductId"
            ]
            product_arn = response["ProductViewDetail"]["ProductARN"]
            print(f"  Created: {name} ({product_id})")

        # Associate with portfolio
        if portfolio_key and portfolio_key in portfolios:
            portfolio_id = portfolios[portfolio_key]["id"]
            try:
                sc.associate_product_with_portfolio(
                    ProductId=product_id, PortfolioId=portfolio_id
                )
                print(f"    + Portfolio: {portfolio_key}")
            except Exception:
                pass  # Already associated

        results[name] = {"id": product_id, "arn": product_arn}

    return results


# ============== COMMANDS ==============


def cmd_bootstrap(ctx: BootstrapContext):
    """Run full bootstrap process."""
    print(f"\n{'='*60}")
    print(f"BOOTSTRAP: {ctx.environment}")
    print(f"Account:   {ctx.account_id}")
    print(f"Region:    {ctx.aws_region}")
    print(f"Profile:   {ctx.aws_profile}")
    print(f"Dry Run:   {ctx.dry_run}")
    print(f"{'='*60}")

    # Initialize environment state
    env_state = ctx.state.setdefault("environments", {}).setdefault(
        ctx.environment, {}
    )
    env_state["account_id"] = ctx.account_id
    env_state["region"] = ctx.aws_region

    # 1. Template bucket
    env_state["template_bucket"] = bootstrap_template_bucket(ctx)

    # 2. ECR repositories
    env_state["ecr_repositories"] = bootstrap_ecr_repositories(ctx)

    # 3. Portfolios
    env_state["portfolios"] = bootstrap_portfolios(ctx)

    # 4. Products
    env_state["products"] = bootstrap_products(ctx, env_state["portfolios"])

    # Save state
    if not ctx.dry_run:
        env_state["bootstrapped_at"] = (
            datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        )
        save_bootstrap_state(ctx.config, ctx.state)
        print(f"\n{'='*60}")
        print("Bootstrap complete!")
        print(f"State saved to: {ctx.config['settings'].get('state_file')}")
    else:
        print(f"\n{'='*60}")
        print("[DRY RUN] Bootstrap simulation complete")


def cmd_status(ctx: BootstrapContext):
    """Show bootstrap status."""
    env_state = ctx.state.get("environments", {}).get(ctx.environment, {})

    print(f"\nBootstrap Status: {ctx.environment}")
    print("=" * 60)

    if not env_state:
        print(f"Not bootstrapped. Run: python bootstrap.py bootstrap -e {ctx.environment}")
        return

    print(f"Account:      {env_state.get('account_id', '-')}")
    print(f"Region:       {env_state.get('region', '-')}")
    print(f"Bootstrapped: {env_state.get('bootstrapped_at', '-')}")

    bucket = env_state.get("template_bucket", {})
    print(f"\nTemplate Bucket: {bucket.get('name', '-')}")

    print("\nECR Repositories:")
    for name, info in env_state.get("ecr_repositories", {}).items():
        print(f"  {name}: {info.get('uri', '-')}")

    print("\nPortfolios:")
    for name, info in env_state.get("portfolios", {}).items():
        print(f"  {name}: {info.get('id', '-')}")

    print("\nProducts:")
    for name, info in env_state.get("products", {}).items():
        print(f"  {name}: {info.get('id', '-')}")


def main():
    parser = argparse.ArgumentParser(
        description="AWS Service Catalog Bootstrap",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python bootstrap.py bootstrap -e dev --dry-run
  python bootstrap.py bootstrap -e dev
  python bootstrap.py status -e dev
        """,
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # bootstrap command
    bootstrap_parser = subparsers.add_parser(
        "bootstrap", help="Create foundational resources"
    )
    bootstrap_parser.add_argument(
        "-e", "--environment", default="dev", help="Target environment"
    )
    bootstrap_parser.add_argument(
        "--dry-run", action="store_true", help="Preview without creating resources"
    )
    bootstrap_parser.add_argument("--profile", help="Override AWS profile")
    bootstrap_parser.add_argument("--region", help="Override AWS region")

    # status command
    status_parser = subparsers.add_parser("status", help="Show bootstrap status")
    status_parser.add_argument(
        "-e", "--environment", default="dev", help="Target environment"
    )

    args = parser.parse_args()

    # Load config
    config = load_bootstrap_config()
    state = load_bootstrap_state(config)

    # Get environment config
    env_config = config.get("profiles", {}).get(args.environment, {})
    if not env_config:
        print(f"Error: Environment '{args.environment}' not found in bootstrap.yaml")
        sys.exit(1)

    # Build context
    ctx = BootstrapContext(
        config=config,
        state=state,
        environment=args.environment,
        aws_profile=getattr(args, "profile", None)
        or env_config.get("aws_profile", "default"),
        aws_region=getattr(args, "region", None)
        or env_config.get("aws_region", "us-east-1"),
        account_id=env_config.get("account_id", ""),
        dry_run=getattr(args, "dry_run", False),
    )

    if not ctx.account_id:
        print(f"Error: account_id not configured for environment '{args.environment}'")
        sys.exit(1)

    # Run command
    if args.command == "bootstrap":
        cmd_bootstrap(ctx)
    elif args.command == "status":
        cmd_status(ctx)


if __name__ == "__main__":
    main()
