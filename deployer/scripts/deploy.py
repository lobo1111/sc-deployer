#!/usr/bin/env python3
"""
Deploy module - publish and deploy Service Catalog products.

Usage:
    python deploy.py validate -e dev
    python deploy.py plan -e dev
    python deploy.py publish -e dev [--dry-run]
    python deploy.py deploy -e dev [--dry-run]
    python deploy.py status -e dev
"""

import argparse
import json
import subprocess
import sys
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import boto3
import yaml

from config import (
    get_project_root,
    get_repo_root,
    get_products_root,
    load_catalog_config,
    get_environment_config,
)


@dataclass
class DeployContext:
    catalog: dict
    state: dict
    bootstrap_state: dict
    environment: str
    aws_profile: str
    aws_region: str
    dry_run: bool = False


def load_deploy_state(catalog: dict) -> dict:
    state_file = Path(catalog["settings"].get("state_file", ".deploy-state.json"))
    if not state_file.is_absolute():
        state_file = get_project_root() / state_file
    if state_file.exists():
        with open(state_file) as f:
            return json.load(f)
    return {"schema_version": "2.0", "environments": {}}


def save_deploy_state(catalog: dict, state: dict):
    state_file = Path(catalog["settings"].get("state_file", ".deploy-state.json"))
    if not state_file.is_absolute():
        state_file = get_project_root() / state_file
    with open(state_file, "w") as f:
        json.dump(state, f, indent=2)


def load_bootstrap_state() -> dict:
    state_file = get_project_root() / ".bootstrap-state.json"
    if state_file.exists():
        with open(state_file) as f:
            return json.load(f)
    return {"schema_version": "1.0", "environments": {}}


def get_session(ctx: DeployContext):
    return boto3.Session(profile_name=ctx.aws_profile, region_name=ctx.aws_region)


def generate_version(catalog: dict) -> str:
    """Generate version based on current datetime."""
    fmt = catalog["settings"].get("version_format", "%Y.%m.%d.%H%M%S")
    return datetime.now(timezone.utc).strftime(fmt)


def get_current_commit() -> str:
    """Get current git commit hash."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            cwd=get_repo_root(),
        )
        return result.stdout.strip()
    except Exception:
        return "unknown"


def has_uncommitted_changes() -> bool:
    """Check if there are uncommitted changes in git."""
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True,
            text=True,
            cwd=get_repo_root(),
        )
        return bool(result.stdout.strip())
    except Exception:
        return False


def get_uncommitted_files() -> list[str]:
    """Get list of uncommitted files."""
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True,
            text=True,
            cwd=get_repo_root(),
        )
        files = []
        for line in result.stdout.strip().split("\n"):
            if line:
                # Format: "XY filename" where X=index, Y=worktree
                files.append(line[3:] if len(line) > 3 else line)
        return files
    except Exception:
        return []


def commit_all_changes(message: str) -> bool:
    """Commit all changes with the given message."""
    try:
        # Stage all changes
        subprocess.run(
            ["git", "add", "-A"],
            capture_output=True,
            cwd=get_repo_root(),
            check=True,
        )
        
        # Commit
        result = subprocess.run(
            ["git", "commit", "-m", message],
            capture_output=True,
            text=True,
            cwd=get_repo_root(),
        )
        return result.returncode == 0
    except Exception:
        return False


# ============== VALIDATION ==============


def validate_output_param_compatibility(ctx: DeployContext) -> list[str]:
    """Validate that all parameter mappings reference valid outputs."""
    errors = []

    for name, config in ctx.catalog["products"].items():
        mappings = config.get("parameter_mapping", {})

        for param, source in mappings.items():
            if "." not in source:
                errors.append(
                    f"{name}: Invalid mapping '{source}' (expected 'product.output')"
                )
                continue

            dep_name, output_name = source.split(".", 1)

            # Check dependency exists
            if dep_name not in ctx.catalog["products"]:
                errors.append(f"{name}: Unknown dependency '{dep_name}' in mapping")
                continue

            # Check dependency is declared
            if dep_name not in config.get("dependencies", []):
                errors.append(
                    f"{name}: '{dep_name}' used in mapping but not in dependencies"
                )
                continue

            # Check output exists
            dep_outputs = ctx.catalog["products"][dep_name].get("outputs", [])
            if output_name not in dep_outputs:
                errors.append(
                    f"{name}: Output '{output_name}' not found in '{dep_name}' "
                    f"(available: {dep_outputs})"
                )

    return errors


def validate_circular_dependencies(ctx: DeployContext) -> list[str]:
    """Detect circular dependencies."""
    errors = []
    products = ctx.catalog["products"]

    def has_cycle(name: str, visited: set, path: set) -> bool:
        visited.add(name)
        path.add(name)

        for dep in products.get(name, {}).get("dependencies", []):
            if dep not in products:
                continue
            if dep in path:
                return True
            if dep not in visited and has_cycle(dep, visited, path):
                return True

        path.remove(name)
        return False

    visited = set()
    for name in products:
        if name not in visited:
            if has_cycle(name, visited, set()):
                errors.append(f"Circular dependency detected involving '{name}'")

    return errors


def validate_bootstrap(ctx: DeployContext) -> list[str]:
    """Validate bootstrap has been run."""
    errors = []
    env_bootstrap = ctx.bootstrap_state.get("environments", {}).get(
        ctx.environment, {}
    )

    if not env_bootstrap:
        errors.append(
            f"Environment '{ctx.environment}' not bootstrapped. "
            f"Run: python bootstrap.py bootstrap -e {ctx.environment}"
        )
        return errors

    # Check all products exist in bootstrap
    bootstrap_products = env_bootstrap.get("products", {})
    for name in ctx.catalog["products"]:
        if name not in bootstrap_products:
            errors.append(
                f"Product '{name}' not in bootstrap. Re-run bootstrap to create it."
            )

    return errors


# ============== CHANGE DETECTION ==============


def get_changed_products(ctx: DeployContext) -> set:
    """Detect products with code changes since last publish."""
    changed = set()
    env_state = ctx.state.get("environments", {}).get(ctx.environment, {})

    for name, config in ctx.catalog["products"].items():
        product_state = env_state.get(name, {})
        last_commit = product_state.get("published_commit", "")

        # Never published = changed
        if not last_commit:
            changed.add(name)
            continue

        # Git diff since last publish
        product_path = get_products_root() / config["path"]
        try:
            result = subprocess.run(
                ["git", "diff", "--name-only", last_commit, "HEAD", "--", str(product_path)],
                capture_output=True,
                text=True,
                cwd=get_project_root(),
            )
            if result.stdout.strip():
                changed.add(name)
        except Exception:
            changed.add(name)  # Assume changed if can't determine

    return changed


def get_affected_products(ctx: DeployContext, changed: set) -> set:
    """Include all dependents of changed products (cascade)."""
    affected = set(changed)

    # Build reverse dependency map
    dependents = {name: [] for name in ctx.catalog["products"]}
    for name, config in ctx.catalog["products"].items():
        for dep in config.get("dependencies", []):
            if dep in dependents:
                dependents[dep].append(name)

    # BFS cascade
    queue = deque(changed)
    while queue:
        product = queue.popleft()
        for dependent in dependents.get(product, []):
            if dependent not in affected:
                affected.add(dependent)
                queue.append(dependent)

    return affected


def topological_sort(ctx: DeployContext, products: set) -> list:
    """Sort products respecting dependency order."""
    in_degree = {p: 0 for p in products}

    for name in products:
        for dep in ctx.catalog["products"][name].get("dependencies", []):
            if dep in products:
                in_degree[name] += 1

    queue = deque([p for p, d in in_degree.items() if d == 0])
    order = []

    while queue:
        product = queue.popleft()
        order.append(product)
        for name in products:
            if product in ctx.catalog["products"][name].get("dependencies", []):
                in_degree[name] -= 1
                if in_degree[name] == 0:
                    queue.append(name)

    if len(order) != len(products):
        raise ValueError("Circular dependency detected!")

    return order


# ============== PARAMETER RESOLUTION ==============


def resolve_parameters(ctx: DeployContext, product_name: str) -> dict:
    """Resolve parameters from dependency outputs."""
    config = ctx.catalog["products"][product_name]
    mappings = config.get("parameter_mapping", {})
    env_state = ctx.state.get("environments", {}).get(ctx.environment, {})

    resolved = {}

    for param, source in mappings.items():
        dep_name, output_name = source.split(".", 1)
        dep_state = env_state.get(dep_name, {})
        dep_outputs = dep_state.get("outputs", {})

        if output_name not in dep_outputs:
            raise ValueError(
                f"Cannot resolve {product_name}.{param}: "
                f"Output '{output_name}' not found in deployed '{dep_name}'"
            )

        resolved[param] = dep_outputs[output_name]

    return resolved


# ============== PUBLISH ==============


def publish_product(ctx: DeployContext, product_name: str) -> str:
    """Publish a product - upload template and create new version."""
    config = ctx.catalog["products"][product_name]
    version = generate_version(ctx.catalog)
    current_commit = get_current_commit()

    print(f"\n  Publishing: {product_name}")
    print(f"    Version: {version}")
    print(f"    Commit:  {current_commit[:8]}")

    if ctx.dry_run:
        print("    [DRY RUN] Skipping")
        return version

    # Get bootstrap state for bucket and product ID
    env_bootstrap = ctx.bootstrap_state.get("environments", {}).get(
        ctx.environment, {}
    )
    bucket_name = env_bootstrap.get("template_bucket", {}).get("name")
    product_id = env_bootstrap.get("products", {}).get(product_name, {}).get("id")

    if not bucket_name or not product_id:
        raise ValueError(
            f"Bootstrap incomplete for {product_name}. Run bootstrap first."
        )

    session = get_session(ctx)
    s3 = session.client("s3")
    sc = session.client("servicecatalog")

    # Upload template to S3
    template_path = get_products_root() / config["path"] / "template.yaml"
    s3_key = f"{product_name}/{version}/template.yaml"

    with open(template_path, "rb") as f:
        s3.put_object(Bucket=bucket_name, Key=s3_key, Body=f.read())
    print(f"    Uploaded: s3://{bucket_name}/{s3_key}")

    # Create provisioning artifact (version) in Service Catalog
    template_url = f"https://{bucket_name}.s3.{ctx.aws_region}.amazonaws.com/{s3_key}"

    sc.create_provisioning_artifact(
        ProductId=product_id,
        Parameters={
            "Name": version,
            "Description": f"Version {version} (commit: {current_commit[:8]})",
            "Type": "CLOUD_FORMATION_TEMPLATE",
            "Info": {"LoadTemplateFromURL": template_url},
        },
    )
    print(f"    Created artifact: {version}")

    # Update state
    env_state = ctx.state.setdefault("environments", {}).setdefault(
        ctx.environment, {}
    )
    product_state = env_state.setdefault(product_name, {})
    product_state.update(
        {
            "version": version,
            "published_commit": current_commit,
            "published_at": datetime.now(timezone.utc)
            .isoformat()
            .replace("+00:00", "Z"),
        }
    )

    return version


# ============== DEPLOY ==============


def deploy_product(ctx: DeployContext, product_name: str) -> dict:
    """Deploy a published product via CloudFormation."""
    config = ctx.catalog["products"][product_name]
    env_state = ctx.state.get("environments", {}).get(ctx.environment, {})
    product_state = env_state.get(product_name, {})

    version = product_state.get("version")
    if not version:
        raise ValueError(f"Product '{product_name}' has not been published yet")

    stack_name = f"{ctx.environment}-{product_name}"
    template_path = get_products_root() / config["path"] / "template.yaml"

    # Resolve parameters from dependencies
    parameters = resolve_parameters(ctx, product_name)

    # Add Environment parameter
    parameters["Environment"] = ctx.environment

    print(f"\n  Deploying: {product_name}")
    print(f"    Version: {version}")
    print(f"    Stack:   {stack_name}")
    if parameters:
        print(f"    Params:  {list(parameters.keys())}")

    if ctx.dry_run:
        print("    [DRY RUN] Skipping")
        return {}

    session = get_session(ctx)
    cfn = session.client("cloudformation")

    # Build CloudFormation parameters
    cfn_params = [
        {"ParameterKey": k, "ParameterValue": str(v)} for k, v in parameters.items()
    ]

    with open(template_path) as f:
        template_body = f.read()

    try:
        # Try update first
        cfn.update_stack(
            StackName=stack_name,
            TemplateBody=template_body,
            Parameters=cfn_params,
            Capabilities=["CAPABILITY_IAM", "CAPABILITY_NAMED_IAM"],
            Tags=[
                {"Key": "Environment", "Value": ctx.environment},
                {"Key": "Product", "Value": product_name},
                {"Key": "Version", "Value": version},
                {"Key": "ManagedBy", "Value": "sc-deployer"},
            ],
        )
        print("    Updating stack...")
        waiter = cfn.get_waiter("stack_update_complete")
    except cfn.exceptions.ClientError as e:
        error_message = str(e)
        if "does not exist" in error_message:
            cfn.create_stack(
                StackName=stack_name,
                TemplateBody=template_body,
                Parameters=cfn_params,
                Capabilities=["CAPABILITY_IAM", "CAPABILITY_NAMED_IAM"],
                Tags=[
                    {"Key": "Environment", "Value": ctx.environment},
                    {"Key": "Product", "Value": product_name},
                    {"Key": "Version", "Value": version},
                    {"Key": "ManagedBy", "Value": "sc-deployer"},
                ],
            )
            print("    Creating stack...")
            waiter = cfn.get_waiter("stack_create_complete")
        elif "No updates" in error_message:
            print("    No changes to deploy")
            # Still fetch outputs
            response = cfn.describe_stacks(StackName=stack_name)
            outputs = {}
            for output in response["Stacks"][0].get("Outputs", []):
                outputs[output["OutputKey"]] = output["OutputValue"]
            return outputs
        else:
            raise

    # Wait for completion
    try:
        waiter.wait(StackName=stack_name, WaiterConfig={"Delay": 10, "MaxAttempts": 60})
    except Exception as e:
        print(f"    Stack operation failed: {e}")
        raise

    print("    Stack operation complete")

    # Fetch outputs
    response = cfn.describe_stacks(StackName=stack_name)
    outputs = {}
    for output in response["Stacks"][0].get("Outputs", []):
        outputs[output["OutputKey"]] = output["OutputValue"]

    # Update state
    current_commit = get_current_commit()
    product_state = ctx.state["environments"][ctx.environment].setdefault(
        product_name, {}
    )
    product_state.update(
        {
            "deployed_commit": current_commit,
            "deployed_at": datetime.now(timezone.utc)
            .isoformat()
            .replace("+00:00", "Z"),
            "stack_name": stack_name,
            "outputs": outputs,
        }
    )

    if outputs:
        print(f"    Outputs: {list(outputs.keys())}")

    return outputs


# ============== COMMANDS ==============


def cmd_validate(ctx: DeployContext) -> bool:
    """Validate catalog configuration."""
    print("Validating...")

    errors = []
    errors.extend(validate_circular_dependencies(ctx))
    errors.extend(validate_output_param_compatibility(ctx))
    errors.extend(validate_bootstrap(ctx))

    if errors:
        print("\nValidation FAILED:")
        for err in errors:
            print(f"  - {err}")
        return False

    print("Validation passed!")
    return True


def cmd_plan(ctx: DeployContext):
    """Show what would be published/deployed."""
    changed = get_changed_products(ctx)
    affected = get_affected_products(ctx, changed)
    order = topological_sort(ctx, affected) if affected else []

    print(f"\nEnvironment: {ctx.environment}")
    print(f"AWS Profile: {ctx.aws_profile}")
    print(f"AWS Region:  {ctx.aws_region}")
    print(f"\nChanged products:  {sorted(changed) or '(none)'}")
    print(f"Affected products: {sorted(affected) or '(none)'}")

    if order:
        print(f"\nDeployment order:")
        for i, product in enumerate(order, 1):
            marker = "*" if product in changed else "^"
            print(f"  {i}. {product} [{marker}]")
        print("\n  * = changed, ^ = affected by dependency")
    else:
        print("\nNothing to deploy.")


def cmd_publish(ctx: DeployContext, products: list[str] | None = None, auto_commit: bool = True):
    """Publish changed products."""
    if not cmd_validate(ctx):
        return

    # Check for uncommitted changes
    if has_uncommitted_changes():
        uncommitted = get_uncommitted_files()
        print(f"\n⚠️  Uncommitted changes detected ({len(uncommitted)} files):")
        for f in uncommitted[:10]:
            print(f"   • {f}")
        if len(uncommitted) > 10:
            print(f"   ... and {len(uncommitted) - 10} more")
        
        if ctx.dry_run:
            print("\n[DRY RUN] Would commit changes before publishing")
        elif auto_commit:
            print("\nCommitting changes...")
            # Generate commit message based on products being published
            if products:
                commit_msg = f"Publish: {', '.join(products)}"
            else:
                commit_msg = "Publish: auto-commit before publish"
            
            if commit_all_changes(commit_msg):
                print(f"✅ Committed: {commit_msg}")
            else:
                print("❌ Failed to commit changes")
                print("   Please commit manually and try again.")
                return
        else:
            print("\n❌ Please commit changes before publishing.")
            return

    if products:
        to_publish = set(products)
    else:
        to_publish = get_changed_products(ctx)

    if not to_publish:
        print("\nNo changes detected. Nothing to publish.")
        return

    affected = get_affected_products(ctx, to_publish)
    order = topological_sort(ctx, affected)

    print(f"\nPublishing {len(order)} product(s): {' -> '.join(order)}")

    for product in order:
        publish_product(ctx, product)
        if not ctx.dry_run:
            save_deploy_state(ctx.catalog, ctx.state)

    print("\nPublish complete!")


def cmd_deploy(ctx: DeployContext, products: list[str] | None = None):
    """Deploy published products."""
    if not cmd_validate(ctx):
        return

    env_state = ctx.state.get("environments", {}).get(ctx.environment, {})

    # Find products that need deployment
    to_deploy = set()
    for name in ctx.catalog["products"]:
        product_state = env_state.get(name, {})
        published_commit = product_state.get("published_commit")
        deployed_commit = product_state.get("deployed_commit")

        if products and name not in products:
            continue

        if published_commit and published_commit != deployed_commit:
            to_deploy.add(name)

    if not to_deploy:
        print("\nAll published products are already deployed.")
        return

    affected = get_affected_products(ctx, to_deploy)
    order = topological_sort(ctx, affected)

    print(f"\nDeploying {len(order)} product(s): {' -> '.join(order)}")

    for product in order:
        deploy_product(ctx, product)
        if not ctx.dry_run:
            save_deploy_state(ctx.catalog, ctx.state)

    print("\nDeployment complete!")


def cmd_status(ctx: DeployContext):
    """Show publish/deploy status."""
    env_state = ctx.state.get("environments", {}).get(ctx.environment, {})

    print(f"\nStatus: {ctx.environment}")
    print("=" * 85)
    print(
        f"{'Product':<15} {'Version':<20} {'Published':<10} {'Deployed':<10} {'Status'}"
    )
    print("-" * 85)

    for name in ctx.catalog["products"]:
        product_state = env_state.get(name, {})
        version = product_state.get("version", "-")
        pub_commit = (product_state.get("published_commit") or "")[:8] or "-"
        dep_commit = (product_state.get("deployed_commit") or "")[:8] or "-"

        if not product_state.get("version"):
            status = "NOT PUBLISHED"
        elif product_state.get("published_commit") != product_state.get(
            "deployed_commit"
        ):
            status = "PENDING DEPLOY"
        else:
            # Check for code changes
            changed = get_changed_products(ctx)
            if name in changed:
                status = "CODE CHANGED"
            else:
                status = "OK"

        print(f"{name:<15} {version:<20} {pub_commit:<10} {dep_commit:<10} {status}")


def main():
    parser = argparse.ArgumentParser(
        description="AWS Service Catalog Deployer",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python deploy.py validate -e dev
  python deploy.py plan -e dev
  python deploy.py publish -e dev --dry-run
  python deploy.py publish -e dev
  python deploy.py deploy -e dev
  python deploy.py status -e dev
        """,
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Common arguments
    def add_common_args(p):
        p.add_argument("-e", "--environment", default="dev", help="Target environment")
        p.add_argument("--profile", help="Override AWS profile")
        p.add_argument("--region", help="Override AWS region")

    # validate
    validate_parser = subparsers.add_parser("validate", help="Validate configuration")
    add_common_args(validate_parser)

    # plan
    plan_parser = subparsers.add_parser("plan", help="Show deployment plan")
    add_common_args(plan_parser)

    # publish
    publish_parser = subparsers.add_parser("publish", help="Publish products")
    add_common_args(publish_parser)
    publish_parser.add_argument(
        "-p", "--product", action="append", help="Specific product(s)"
    )
    publish_parser.add_argument("--dry-run", action="store_true")

    # deploy
    deploy_parser = subparsers.add_parser("deploy", help="Deploy products")
    add_common_args(deploy_parser)
    deploy_parser.add_argument(
        "-p", "--product", action="append", help="Specific product(s)"
    )
    deploy_parser.add_argument("--dry-run", action="store_true")

    # status
    status_parser = subparsers.add_parser("status", help="Show status")
    add_common_args(status_parser)

    args = parser.parse_args()

    # Load configs
    catalog = load_catalog_config()
    state = load_deploy_state(catalog)
    bootstrap_state = load_bootstrap_state()

    # Get environment config
    try:
        env_config = get_environment_config(catalog, args.environment)
    except ValueError as e:
        print(f"Error: {e}")
        sys.exit(1)

    # Build context
    ctx = DeployContext(
        catalog=catalog,
        state=state,
        bootstrap_state=bootstrap_state,
        environment=args.environment,
        aws_profile=getattr(args, "profile", None)
        or env_config.get("aws_profile", "default"),
        aws_region=getattr(args, "region", None)
        or env_config.get("aws_region", "us-east-1"),
        dry_run=getattr(args, "dry_run", False),
    )

    # Run command
    if args.command == "validate":
        cmd_validate(ctx)
    elif args.command == "plan":
        cmd_plan(ctx)
    elif args.command == "publish":
        cmd_publish(ctx, getattr(args, "product", None))
    elif args.command == "deploy":
        cmd_deploy(ctx, getattr(args, "product", None))
    elif args.command == "status":
        cmd_status(ctx)


if __name__ == "__main__":
    main()
