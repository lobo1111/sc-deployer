#!/usr/bin/env python3
"""
Bootstrap module - creates foundational AWS resources for Service Catalog.

Usage:
    python bootstrap.py bootstrap -e dev [--dry-run]
    python bootstrap.py destroy -e dev [--dry-run] [--force]
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

from config import (
    get_project_root,
    get_products_root,
    load_bootstrap_config,
    load_catalog_config,
    get_environment_config,
)


@dataclass
class BootstrapContext:
    config: dict
    state: dict
    environment: str
    aws_profile: str
    aws_region: str
    account_id: str
    dry_run: bool = False


def load_bootstrap_state(config: dict) -> dict:
    state_file = Path(config["settings"].get("state_file", ".bootstrap-state.json"))
    if not state_file.is_absolute():
        state_file = get_project_root() / state_file
    if state_file.exists():
        with open(state_file) as f:
            return json.load(f)
    return {"schema_version": "1.0", "environments": {}}


def save_bootstrap_state(config: dict, state: dict):
    state_file = Path(config["settings"].get("state_file", ".bootstrap-state.json"))
    if not state_file.is_absolute():
        state_file = get_project_root() / state_file
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


# ============== LAUNCH ROLE ==============


def bootstrap_launch_role(ctx: BootstrapContext) -> dict:
    """Create IAM role for Service Catalog to launch products."""
    role_name = f"sc-deployer-launch-role-{ctx.environment}"
    
    print(f"\n[IAM] Launch role: {role_name}")
    
    if ctx.dry_run:
        print("  [DRY RUN] Would create launch role")
        return {"name": role_name, "arn": f"arn:aws:iam::{ctx.account_id}:role/{role_name}"}
    
    session = get_session(ctx)
    iam = session.client("iam")
    
    # Trust policy for Service Catalog
    trust_policy = json.dumps({
        "Version": "2012-10-17",
        "Statement": [{
            "Effect": "Allow",
            "Principal": {"Service": "servicecatalog.amazonaws.com"},
            "Action": "sts:AssumeRole"
        }]
    })
    
    try:
        response = iam.create_role(
            RoleName=role_name,
            AssumeRolePolicyDocument=trust_policy,
            Description=f"Service Catalog launch role for {ctx.environment}",
            Tags=[
                {"Key": "Environment", "Value": ctx.environment},
                {"Key": "ManagedBy", "Value": "sc-deployer"},
            ]
        )
        role_arn = response["Role"]["Arn"]
        print(f"  Created: {role_name}")
    except iam.exceptions.EntityAlreadyExistsException:
        response = iam.get_role(RoleName=role_name)
        role_arn = response["Role"]["Arn"]
        print(f"  Exists: {role_name}")
    
    # Attach policies for CloudFormation and common AWS services
    policies_to_attach = [
        "arn:aws:iam::aws:policy/AWSCloudFormationFullAccess",
        "arn:aws:iam::aws:policy/AmazonS3FullAccess",
        "arn:aws:iam::aws:policy/AmazonEC2FullAccess",
        "arn:aws:iam::aws:policy/IAMFullAccess",
    ]
    
    for policy_arn in policies_to_attach:
        try:
            iam.attach_role_policy(RoleName=role_name, PolicyArn=policy_arn)
        except Exception:
            pass  # Already attached
    
    print(f"  Attached policies")
    
    return {"name": role_name, "arn": role_arn}


def create_launch_constraint(ctx: BootstrapContext, product_id: str, portfolio_id: str, role_arn: str, product_name: str):
    """Create a launch constraint for a product."""
    session = get_session(ctx)
    sc = session.client("servicecatalog")
    
    # Check if constraint already exists
    try:
        response = sc.list_constraints_for_portfolio(PortfolioId=portfolio_id, ProductId=product_id)
        for constraint in response.get("ConstraintDetails", []):
            if constraint["Type"] == "LAUNCH":
                # Already has a launch constraint
                return
    except Exception:
        pass
    
    try:
        sc.create_constraint(
            PortfolioId=portfolio_id,
            ProductId=product_id,
            Type="LAUNCH",
            Parameters=json.dumps({"RoleArn": role_arn}),
            Description=f"Launch constraint for {product_name}",
        )
        print(f"    + Launch constraint")
    except Exception as e:
        if "already exists" not in str(e).lower():
            print(f"    [!] Launch constraint failed: {e}")


# ============== SERVICE CATALOG PRODUCTS ==============


def upload_placeholder_template(ctx: BootstrapContext, bucket_name: str, product_name: str) -> str:
    """Upload a minimal placeholder template to S3 and return URL."""
    placeholder_template = """AWSTemplateFormatVersion: '2010-09-09'
Description: Placeholder template - will be replaced on first publish

Resources:
  PlaceholderWaitHandle:
    Type: AWS::CloudFormation::WaitConditionHandle

Outputs:
  Status:
    Description: Placeholder status
    Value: "Pending first publish"
"""
    
    session = get_session(ctx)
    s3 = session.client("s3")
    
    s3_key = f"_placeholders/{product_name}/placeholder.yaml"
    s3.put_object(
        Bucket=bucket_name,
        Key=s3_key,
        Body=placeholder_template.encode("utf-8"),
        ContentType="application/x-yaml"
    )
    
    # Return HTTPS URL
    return f"https://{bucket_name}.s3.{ctx.aws_region}.amazonaws.com/{s3_key}"


def bootstrap_products(ctx: BootstrapContext, portfolios: dict, template_bucket: dict, launch_role: dict) -> dict:
    """Create Service Catalog product definitions from catalog.yaml."""
    catalog = load_catalog_config()
    products_config = catalog.get("products", {})
    products_root = get_products_root()
    launch_role_arn = launch_role.get("arn", "")

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
    
    bucket_name = template_bucket.get("name")
    if not bucket_name:
        print("  ⚠️  No template bucket available, skipping product creation")
        return {}

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
            product_yaml = products_root / config["path"] / "product.yaml"
            description = f"Service Catalog product: {name}"
            if product_yaml.exists():
                with open(product_yaml) as f:
                    meta = yaml.safe_load(f)
                    description = meta.get("description", description)

            # Upload placeholder template
            template_url = upload_placeholder_template(ctx, bucket_name, product_name)

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
                    "Name": "v0.0.0-placeholder",
                    "Description": "Placeholder - will be replaced on first publish",
                    "Type": "CLOUD_FORMATION_TEMPLATE",
                    "Info": {"LoadTemplateFromURL": template_url},
                },
            )
            product_id = response["ProductViewDetail"]["ProductViewSummary"][
                "ProductId"
            ]
            product_arn = response["ProductViewDetail"]["ProductARN"]
            print(f"  Created: {name} ({product_id})")

        # Associate with portfolio and create launch constraint
        if portfolio_key and portfolio_key in portfolios:
            portfolio_id = portfolios[portfolio_key]["id"]
            try:
                sc.associate_product_with_portfolio(
                    ProductId=product_id, PortfolioId=portfolio_id
                )
                print(f"    + Portfolio: {portfolio_key}")
            except Exception:
                pass  # Already associated
            
            # Create launch constraint
            if launch_role_arn:
                create_launch_constraint(ctx, product_id, portfolio_id, launch_role_arn, name)

        results[name] = {"id": product_id, "arn": product_arn}

    return results


# ============== DESTROY FUNCTIONS ==============


def destroy_products(ctx: BootstrapContext) -> int:
    """Delete Service Catalog products."""
    env_state = ctx.state.get("environments", {}).get(ctx.environment, {})
    products = env_state.get("products", {})
    
    if not products:
        print("\n[Products] No products to delete")
        return 0
    
    print(f"\n[Products] Deleting {len(products)} products")
    
    if ctx.dry_run:
        for name, info in products.items():
            print(f"  [DRY RUN] Would delete: {name} ({info.get('id')})")
        return len(products)
    
    session = get_session(ctx)
    sc = session.client("servicecatalog")
    deleted = 0
    
    for name, info in products.items():
        product_id = info.get("id")
        if not product_id:
            continue
        
        try:
            # First, disassociate from all portfolios
            try:
                response = sc.list_portfolios_for_product(ProductId=product_id)
                for portfolio in response.get("PortfolioDetails", []):
                    sc.disassociate_product_from_portfolio(
                        ProductId=product_id,
                        PortfolioId=portfolio["Id"]
                    )
                    print(f"  Disassociated {name} from portfolio {portfolio['Id']}")
            except Exception:
                pass
            
            # Delete all provisioning artifacts first
            try:
                artifacts = sc.list_provisioning_artifacts(ProductId=product_id)
                for artifact in artifacts.get("ProvisioningArtifactDetails", []):
                    try:
                        sc.delete_provisioning_artifact(
                            ProductId=product_id,
                            ProvisioningArtifactId=artifact["Id"]
                        )
                    except Exception:
                        pass  # May fail if it's the last one or active
            except Exception:
                pass
            
            # Delete the product
            sc.delete_product(Id=product_id)
            print(f"  Deleted: {name} ({product_id})")
            deleted += 1
        except sc.exceptions.ResourceNotFoundException:
            print(f"  Not found: {name} ({product_id})")
        except Exception as e:
            print(f"  [ERROR] {name}: {e}")
    
    return deleted


def destroy_portfolios(ctx: BootstrapContext) -> int:
    """Delete Service Catalog portfolios."""
    env_state = ctx.state.get("environments", {}).get(ctx.environment, {})
    portfolios = env_state.get("portfolios", {})
    
    if not portfolios:
        print("\n[Portfolios] No portfolios to delete")
        return 0
    
    print(f"\n[Portfolios] Deleting {len(portfolios)} portfolios")
    
    if ctx.dry_run:
        for name, info in portfolios.items():
            print(f"  [DRY RUN] Would delete: {name} ({info.get('id')})")
        return len(portfolios)
    
    session = get_session(ctx)
    sc = session.client("servicecatalog")
    deleted = 0
    
    for name, info in portfolios.items():
        portfolio_id = info.get("id")
        if not portfolio_id:
            continue
        
        try:
            # Disassociate all principals
            try:
                response = sc.list_principals_for_portfolio(PortfolioId=portfolio_id)
                for principal in response.get("Principals", []):
                    sc.disassociate_principal_from_portfolio(
                        PortfolioId=portfolio_id,
                        PrincipalARN=principal["PrincipalARN"]
                    )
            except Exception:
                pass
            
            # Disassociate all products (should already be done)
            try:
                paginator = sc.get_paginator("search_products_as_admin")
                for page in paginator.paginate(PortfolioId=portfolio_id):
                    for product in page.get("ProductViewDetails", []):
                        product_id = product["ProductViewSummary"]["ProductId"]
                        sc.disassociate_product_from_portfolio(
                            ProductId=product_id,
                            PortfolioId=portfolio_id
                        )
            except Exception:
                pass
            
            # Delete the portfolio
            sc.delete_portfolio(Id=portfolio_id)
            print(f"  Deleted: {name} ({portfolio_id})")
            deleted += 1
        except sc.exceptions.ResourceNotFoundException:
            print(f"  Not found: {name} ({portfolio_id})")
        except Exception as e:
            print(f"  [ERROR] {name}: {e}")
    
    return deleted


def destroy_ecr_repositories(ctx: BootstrapContext) -> int:
    """Delete ECR repositories."""
    env_state = ctx.state.get("environments", {}).get(ctx.environment, {})
    repos = env_state.get("ecr_repositories", {})
    
    if not repos:
        print("\n[ECR] No repositories to delete")
        return 0
    
    print(f"\n[ECR] Deleting {len(repos)} repositories")
    
    if ctx.dry_run:
        for name in repos:
            print(f"  [DRY RUN] Would delete: {name}")
        return len(repos)
    
    session = get_session(ctx)
    ecr = session.client("ecr")
    deleted = 0
    
    for name in repos:
        try:
            ecr.delete_repository(repositoryName=name, force=True)
            print(f"  Deleted: {name}")
            deleted += 1
        except ecr.exceptions.RepositoryNotFoundException:
            print(f"  Not found: {name}")
        except Exception as e:
            print(f"  [ERROR] {name}: {e}")
    
    return deleted


def destroy_template_bucket(ctx: BootstrapContext) -> bool:
    """Delete S3 template bucket and all contents."""
    env_state = ctx.state.get("environments", {}).get(ctx.environment, {})
    bucket_info = env_state.get("template_bucket", {})
    bucket_name = bucket_info.get("name")
    
    if not bucket_name:
        print("\n[S3] No template bucket to delete")
        return False
    
    print(f"\n[S3] Deleting bucket: {bucket_name}")
    
    if ctx.dry_run:
        print(f"  [DRY RUN] Would delete bucket and all contents")
        return True
    
    session = get_session(ctx)
    s3 = session.resource("s3")
    s3_client = session.client("s3")
    
    try:
        bucket = s3.Bucket(bucket_name)
        
        # Delete all objects (including versions)
        print("  Deleting objects...")
        try:
            bucket.object_versions.delete()
        except Exception:
            # Fallback for non-versioned buckets
            bucket.objects.delete()
        
        # Delete the bucket
        s3_client.delete_bucket(Bucket=bucket_name)
        print(f"  Deleted: {bucket_name}")
        return True
    except s3_client.exceptions.NoSuchBucket:
        print(f"  Not found: {bucket_name}")
        return False
    except Exception as e:
        print(f"  [ERROR] {bucket_name}: {e}")
        return False


def destroy_launch_role(ctx: BootstrapContext) -> bool:
    """Delete the Service Catalog launch role."""
    env_state = ctx.state.get("environments", {}).get(ctx.environment, {})
    role_info = env_state.get("launch_role", {})
    role_name = role_info.get("name")
    
    if not role_name:
        print("\n[IAM] No launch role to delete")
        return False
    
    print(f"\n[IAM] Deleting launch role: {role_name}")
    
    if ctx.dry_run:
        print("  [DRY RUN] Would delete launch role")
        return True
    
    session = get_session(ctx)
    iam = session.client("iam")
    
    try:
        # Detach all policies first
        try:
            response = iam.list_attached_role_policies(RoleName=role_name)
            for policy in response.get("AttachedPolicies", []):
                iam.detach_role_policy(RoleName=role_name, PolicyArn=policy["PolicyArn"])
        except Exception:
            pass
        
        # Delete inline policies
        try:
            response = iam.list_role_policies(RoleName=role_name)
            for policy_name in response.get("PolicyNames", []):
                iam.delete_role_policy(RoleName=role_name, PolicyName=policy_name)
        except Exception:
            pass
        
        # Delete the role
        iam.delete_role(RoleName=role_name)
        print(f"  Deleted: {role_name}")
        return True
    except iam.exceptions.NoSuchEntityException:
        print(f"  Not found: {role_name}")
        return False
    except Exception as e:
        print(f"  [ERROR] {role_name}: {e}")
        return False


# ============== COMMANDS ==============


def cmd_destroy(ctx: BootstrapContext, force: bool = False):
    """Destroy all bootstrapped resources."""
    env_state = ctx.state.get("environments", {}).get(ctx.environment, {})
    
    if not env_state:
        print(f"\nNo bootstrap state found for environment '{ctx.environment}'")
        print("Nothing to destroy.")
        return
    
    print(f"\n{'='*60}")
    print(f"DESTROY: {ctx.environment}")
    print(f"Account:   {ctx.account_id}")
    print(f"Region:    {ctx.aws_region}")
    print(f"Profile:   {ctx.aws_profile}")
    print(f"Dry Run:   {ctx.dry_run}")
    print(f"{'='*60}")
    
    # Show what will be deleted
    print("\nResources to be deleted:")
    products = env_state.get("products", {})
    portfolios = env_state.get("portfolios", {})
    ecr_repos = env_state.get("ecr_repositories", {})
    bucket = env_state.get("template_bucket", {}).get("name")
    launch_role = env_state.get("launch_role", {}).get("name")
    
    print(f"  - {len(products)} Service Catalog product(s)")
    print(f"  - {len(portfolios)} Service Catalog portfolio(s)")
    print(f"  - {len(ecr_repos)} ECR repository(ies)")
    print(f"  - S3 bucket: {bucket or '(none)'}")
    print(f"  - IAM launch role: {launch_role or '(none)'}")
    
    # Confirmation
    if not ctx.dry_run and not force:
        print(f"\n[WARNING] This will permanently delete all resources!")
        response = input("Type 'destroy' to confirm: ")
        if response != "destroy":
            print("Aborted.")
            return
    
    # Delete in reverse order of creation
    # 1. Products first (need to be disassociated from portfolios)
    destroy_products(ctx)
    
    # 2. Portfolios
    destroy_portfolios(ctx)
    
    # 3. ECR repositories
    destroy_ecr_repositories(ctx)
    
    # 4. S3 bucket
    destroy_template_bucket(ctx)
    
    # 5. IAM launch role (last, as it may have been used by constraints)
    destroy_launch_role(ctx)
    
    # Clear state
    if not ctx.dry_run:
        if ctx.environment in ctx.state.get("environments", {}):
            del ctx.state["environments"][ctx.environment]
        save_bootstrap_state(ctx.config, ctx.state)
        print(f"\n{'='*60}")
        print("Destroy complete!")
        print(f"State cleared for environment: {ctx.environment}")
    else:
        print(f"\n{'='*60}")
        print("[DRY RUN] Destroy simulation complete")


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

    # 4. Launch role for Service Catalog
    env_state["launch_role"] = bootstrap_launch_role(ctx)

    # 5. Products (with launch constraints)
    env_state["products"] = bootstrap_products(ctx, env_state["portfolios"], env_state["template_bucket"], env_state["launch_role"])

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
  python bootstrap.py destroy -e dev --dry-run
  python bootstrap.py destroy -e dev --force
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

    # destroy command
    destroy_parser = subparsers.add_parser(
        "destroy", help="Delete all bootstrapped resources"
    )
    destroy_parser.add_argument(
        "-e", "--environment", default="dev", help="Target environment"
    )
    destroy_parser.add_argument(
        "--dry-run", action="store_true", help="Preview without deleting resources"
    )
    destroy_parser.add_argument(
        "--force", action="store_true", help="Skip confirmation prompt"
    )
    destroy_parser.add_argument("--profile", help="Override AWS profile")
    destroy_parser.add_argument("--region", help="Override AWS region")

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
    try:
        env_config = get_environment_config(config, args.environment)
    except ValueError as e:
        print(f"Error: {e}")
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
    elif args.command == "destroy":
        cmd_destroy(ctx, force=getattr(args, "force", False))
    elif args.command == "status":
        cmd_status(ctx)


if __name__ == "__main__":
    main()
