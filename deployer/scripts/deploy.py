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
import hashlib
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


def is_git_repo() -> bool:
    """Check if current directory is a git repository."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--git-dir"],
            capture_output=True,
            text=True,
            cwd=get_repo_root(),
        )
        return result.returncode == 0
    except Exception:
        return False


def get_current_commit() -> str:
    """Get current git commit hash."""
    if not is_git_repo():
        return ""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            cwd=get_repo_root(),
        )
        if result.returncode == 0:
            return result.stdout.strip()
        return ""
    except Exception:
        return ""


def compute_product_hash(product_path: Path) -> str:
    """Compute a hash of all files in the product directory."""
    if not product_path.exists():
        return ""
    
    hasher = hashlib.sha256()
    
    # Get all files sorted for consistent ordering
    files = sorted(product_path.rglob("*"))
    
    for file_path in files:
        if file_path.is_file():
            # Include relative path in hash for structure changes
            rel_path = file_path.relative_to(product_path)
            hasher.update(str(rel_path).encode())
            
            # Include file content
            try:
                with open(file_path, "rb") as f:
                    hasher.update(f.read())
            except Exception:
                pass
    
    return hasher.hexdigest()[:16]  # Short hash for readability


def has_uncommitted_changes() -> bool:
    """Check if there are uncommitted changes in git."""
    if not is_git_repo():
        return False
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
    use_git = is_git_repo()

    for name, config in ctx.catalog["products"].items():
        product_state = env_state.get(name, {})
        product_path = get_products_root() / config["path"]
        
        if use_git:
            # Git-based change detection
            last_commit = product_state.get("published_commit", "")
            
            # Never published = changed
            if not last_commit:
                changed.add(name)
                continue

            # Git diff since last publish
            try:
                result = subprocess.run(
                    ["git", "diff", "--name-only", last_commit, "HEAD", "--", str(product_path)],
                    capture_output=True,
                    text=True,
                    cwd=get_repo_root(),
                )
                if result.stdout.strip():
                    changed.add(name)
            except Exception:
                changed.add(name)  # Assume changed if can't determine
        else:
            # Hash-based change detection (fallback when git unavailable)
            last_hash = product_state.get("published_hash", "")
            current_hash = compute_product_hash(product_path)
            
            # Never published or hash changed
            if not last_hash or last_hash != current_hash:
                changed.add(name)

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
    product_path = get_products_root() / config["path"]
    current_hash = compute_product_hash(product_path)

    print(f"\n  Publishing: {product_name}")
    print(f"    Version: {version}")
    if current_commit:
        print(f"    Commit:  {current_commit[:8]}")
    else:
        print(f"    Hash:    {current_hash}")

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

    # Build description based on available tracking info
    if current_commit:
        description = f"Version {version} (commit: {current_commit[:8]})"
    else:
        description = f"Version {version} (hash: {current_hash})"

    sc.create_provisioning_artifact(
        ProductId=product_id,
        Parameters={
            "Name": version,
            "Description": description,
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
            "published_hash": current_hash,
            "published_at": datetime.now(timezone.utc)
            .isoformat()
            .replace("+00:00", "Z"),
        }
    )

    return version


# ============== DEPLOY ==============


def get_provisioning_artifact_id(ctx: DeployContext, product_id: str, version: str) -> str:
    """Get the provisioning artifact ID for a specific version."""
    session = get_session(ctx)
    sc = session.client("servicecatalog")
    
    response = sc.list_provisioning_artifacts(ProductId=product_id)
    for artifact in response.get("ProvisioningArtifactDetails", []):
        if artifact["Name"] == version:
            return artifact["Id"]
    
    raise ValueError(f"Provisioning artifact not found for version: {version}")


def wait_for_provisioned_product(ctx: DeployContext, record_id: str, timeout: int = 600) -> dict:
    """Wait for a provisioned product operation to complete."""
    import time
    session = get_session(ctx)
    sc = session.client("servicecatalog")
    
    start_time = time.time()
    while time.time() - start_time < timeout:
        response = sc.describe_record(Id=record_id)
        status = response["RecordDetail"]["Status"]
        
        if status == "SUCCEEDED":
            # Get outputs from record outputs
            outputs = {}
            for output in response["RecordDetail"].get("RecordOutputs", []):
                outputs[output["OutputKey"]] = output["OutputValue"]
            return {"status": "SUCCEEDED", "outputs": outputs}
        elif status in ["FAILED", "IN_PROGRESS_IN_ERROR"]:
            errors = response["RecordDetail"].get("RecordErrors", [])
            error_msg = "; ".join([e.get("Description", "Unknown error") for e in errors])
            return {"status": "FAILED", "error": error_msg}
        
        time.sleep(10)
    
    return {"status": "TIMEOUT", "error": "Operation timed out"}


def get_provisioned_product_outputs(ctx: DeployContext, provisioned_product_id: str) -> dict:
    """Get outputs from a provisioned product's CloudFormation stack."""
    session = get_session(ctx)
    sc = session.client("servicecatalog")
    cfn = session.client("cloudformation")
    
    # Get the CloudFormation stack ID from the provisioned product
    try:
        response = sc.describe_provisioned_product(Id=provisioned_product_id)
        pp_detail = response.get("ProvisionedProductDetail", {})
        
        # The physical ID contains the CloudFormation stack ARN/ID
        stack_id = pp_detail.get("PhysicalId")
        if not stack_id:
            return {}
        
        # Get outputs from CloudFormation
        stack_response = cfn.describe_stacks(StackName=stack_id)
        outputs = {}
        for output in stack_response["Stacks"][0].get("Outputs", []):
            outputs[output["OutputKey"]] = output["OutputValue"]
        return outputs
    except Exception:
        return {}


def deploy_product(ctx: DeployContext, product_name: str) -> dict:
    """Deploy a published product via Service Catalog provisioning."""
    config = ctx.catalog["products"][product_name]
    env_state = ctx.state.get("environments", {}).get(ctx.environment, {})
    product_state = env_state.get(product_name, {})

    version = product_state.get("version")
    if not version:
        raise ValueError(f"Product '{product_name}' has not been published yet")

    # Get product ID from bootstrap state
    env_bootstrap = ctx.bootstrap_state.get("environments", {}).get(ctx.environment, {})
    product_id = env_bootstrap.get("products", {}).get(product_name, {}).get("id")
    
    if not product_id:
        raise ValueError(f"Product '{product_name}' not found in bootstrap state. Run bootstrap first.")

    provisioned_product_name = f"{ctx.environment}-{product_name}"

    # Resolve parameters from dependencies
    parameters = resolve_parameters(ctx, product_name)

    # Add Environment parameter
    parameters["Environment"] = ctx.environment

    print(f"\n  Deploying: {product_name}")
    print(f"    Version: {version}")
    print(f"    Provisioned Product: {provisioned_product_name}")
    if parameters:
        print(f"    Params:  {list(parameters.keys())}")

    if ctx.dry_run:
        print("    [DRY RUN] Skipping")
        return {}

    session = get_session(ctx)
    sc = session.client("servicecatalog")

    # Get provisioning artifact ID for the version
    artifact_id = get_provisioning_artifact_id(ctx, product_id, version)

    # Build Service Catalog parameters
    sc_params = [
        {"Key": k, "Value": str(v)} for k, v in parameters.items()
    ]

    # Check if already provisioned
    existing_pp_id = product_state.get("provisioned_product_id")
    record_id = None
    
    if existing_pp_id:
        # Try to update existing provisioned product
        try:
            # Verify it still exists
            sc.describe_provisioned_product(Id=existing_pp_id)
            
            response = sc.update_provisioned_product(
                ProvisionedProductId=existing_pp_id,
                ProductId=product_id,
                ProvisioningArtifactId=artifact_id,
                ProvisioningParameters=sc_params,
                Tags=[
                    {"Key": "Environment", "Value": ctx.environment},
                    {"Key": "ManagedBy", "Value": "sc-deployer"},
                ],
            )
            record_id = response["RecordDetail"]["RecordId"]
            print("    Updating provisioned product...")
        except sc.exceptions.ResourceNotFoundException:
            existing_pp_id = None  # Will provision new
        except Exception as e:
            if "No updates" in str(e) or "AVAILABLE" in str(e):
                print("    No changes to deploy")
                outputs = get_provisioned_product_outputs(ctx, existing_pp_id)
                return outputs
            raise
    
    if not existing_pp_id:
        # Provision new product
        try:
            response = sc.provision_product(
                ProductId=product_id,
                ProvisioningArtifactId=artifact_id,
                ProvisionedProductName=provisioned_product_name,
                ProvisioningParameters=sc_params,
                Tags=[
                    {"Key": "Environment", "Value": ctx.environment},
                    {"Key": "ManagedBy", "Value": "sc-deployer"},
                ],
            )
            record_id = response["RecordDetail"]["RecordId"]
            existing_pp_id = response["RecordDetail"]["ProvisionedProductId"]
            print("    Provisioning new product...")
        except sc.exceptions.DuplicateResourceException:
            # Product with this name already exists, find it
            search_response = sc.search_provisioned_products(
                Filters={"SearchQuery": [f"name:{provisioned_product_name}"]}
            )
            for pp in search_response.get("ProvisionedProducts", []):
                if pp["Name"] == provisioned_product_name:
                    existing_pp_id = pp["Id"]
                    # Update it instead
                    response = sc.update_provisioned_product(
                        ProvisionedProductId=existing_pp_id,
                        ProductId=product_id,
                        ProvisioningArtifactId=artifact_id,
                        ProvisioningParameters=sc_params,
                        Tags=[
                            {"Key": "Environment", "Value": ctx.environment},
                            {"Key": "ManagedBy", "Value": "sc-deployer"},
                        ],
                    )
                    record_id = response["RecordDetail"]["RecordId"]
                    print("    Updating existing provisioned product...")
                    break
            else:
                raise ValueError(f"Could not find existing provisioned product: {provisioned_product_name}")

    # Wait for completion
    if record_id:
        result = wait_for_provisioned_product(ctx, record_id)
        
        if result["status"] == "SUCCEEDED":
            print("    Provisioning complete")
            outputs = result.get("outputs", {})
            
            # If no outputs from record, try to get from CloudFormation
            if not outputs and existing_pp_id:
                outputs = get_provisioned_product_outputs(ctx, existing_pp_id)
        elif result["status"] == "FAILED":
            print(f"    Provisioning FAILED: {result.get('error', 'Unknown error')}")
            raise RuntimeError(f"Provisioning failed: {result.get('error')}")
        else:
            print(f"    Provisioning timed out")
            raise RuntimeError("Provisioning timed out")
    else:
        outputs = {}

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
            "provisioned_product_id": existing_pp_id,
            "provisioned_product_name": provisioned_product_name,
            "outputs": outputs,
        }
    )

    if outputs:
        print(f"    Outputs: {list(outputs.keys())}")

    return outputs


def terminate_product(ctx: DeployContext, product_name: str) -> bool:
    """Terminate a provisioned product."""
    env_state = ctx.state.get("environments", {}).get(ctx.environment, {})
    product_state = env_state.get(product_name, {})
    
    pp_id = product_state.get("provisioned_product_id")
    pp_name = product_state.get("provisioned_product_name", f"{ctx.environment}-{product_name}")
    
    print(f"\n  Terminating: {product_name}")
    print(f"    Provisioned Product: {pp_name}")
    
    if ctx.dry_run:
        print("    [DRY RUN] Skipping")
        return True
    
    if not pp_id:
        print("    Not deployed (no provisioned product ID)")
        return True
    
    session = get_session(ctx)
    sc = session.client("servicecatalog")
    
    try:
        response = sc.terminate_provisioned_product(
            ProvisionedProductId=pp_id,
            TerminateToken=f"terminate-{product_name}-{datetime.now().strftime('%Y%m%d%H%M%S')}"
        )
        record_id = response["RecordDetail"]["RecordId"]
        print("    Terminating...")
        
        # Wait for termination
        result = wait_for_provisioned_product(ctx, record_id)
        
        if result["status"] == "SUCCEEDED":
            print("    Terminated successfully")
            # Clear state
            if product_name in ctx.state.get("environments", {}).get(ctx.environment, {}):
                product_state = ctx.state["environments"][ctx.environment][product_name]
                product_state.pop("provisioned_product_id", None)
                product_state.pop("provisioned_product_name", None)
                product_state.pop("deployed_commit", None)
                product_state.pop("deployed_at", None)
                product_state.pop("outputs", None)
            return True
        else:
            print(f"    Termination failed: {result.get('error', 'Unknown')}")
            return False
    except sc.exceptions.ResourceNotFoundException:
        print("    Not found (already terminated)")
        return True
    except Exception as e:
        print(f"    Error: {e}")
        return False


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


def cmd_publish(ctx: DeployContext, products: list[str] | None = None, auto_commit: bool = True, force: bool = False):
    """Publish changed products."""
    if not cmd_validate(ctx):
        return

    # Check for uncommitted changes (only if git repo exists)
    if has_uncommitted_changes():
        uncommitted = get_uncommitted_files()
        print(f"\n[!] Uncommitted changes detected ({len(uncommitted)} files):")
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
                print(f"[OK] Committed: {commit_msg}")
            else:
                print("[ERROR] Failed to commit changes")
                print("   Please commit manually and try again.")
                return
        else:
            print("\n[ERROR] Please commit changes before publishing.")
            return

    # Get products that actually have code changes
    changed_products = get_changed_products(ctx)
    
    if products:
        # Filter specified products to only those with changes (unless forced)
        if force:
            to_publish = set(products)
        else:
            to_publish = set(products) & changed_products
            skipped = set(products) - changed_products
            if skipped:
                print(f"\nSkipping unchanged products: {', '.join(sorted(skipped))}")
                print("  (Use --force to publish anyway)")
    else:
        to_publish = changed_products

    if not to_publish:
        print("\nNo changes detected. Nothing to publish.")
        return

    # Sort by dependency order (dependencies first), but only publish products with actual changes
    order = topological_sort(ctx, to_publish)

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


def cmd_terminate(ctx: DeployContext, products: list[str] | None = None, force: bool = False):
    """Terminate deployed products."""
    env_state = ctx.state.get("environments", {}).get(ctx.environment, {})
    
    # Get products to terminate
    if products:
        to_terminate = set(products)
    else:
        # Find all deployed products
        to_terminate = set()
        for name in ctx.catalog["products"]:
            product_state = env_state.get(name, {})
            if product_state.get("provisioned_product_id"):
                to_terminate.add(name)
    
    if not to_terminate:
        print("\nNo deployed products to terminate.")
        return
    
    # Reverse topological order (dependents first)
    order = topological_sort(ctx, to_terminate)
    order.reverse()
    
    print(f"\nTerminating {len(order)} product(s): {' -> '.join(order)}")
    
    if not ctx.dry_run and not force:
        response = input("Are you sure? Type 'yes' to confirm: ")
        if response.lower() != "yes":
            print("Aborted.")
            return
    
    for product in order:
        terminate_product(ctx, product)
        if not ctx.dry_run:
            save_deploy_state(ctx.catalog, ctx.state)
    
    print("\nTermination complete!")


def cmd_status(ctx: DeployContext):
    """Show publish/deploy status."""
    env_state = ctx.state.get("environments", {}).get(ctx.environment, {})
    use_git = is_git_repo()

    print(f"\nStatus: {ctx.environment}")
    if not use_git:
        print("(No git repo - using file hash for change detection)")
    print("=" * 85)
    
    # Column header depends on tracking method
    track_col = "Commit" if use_git else "Hash"
    print(
        f"{'Product':<15} {'Version':<20} {track_col:<12} {'Deployed':<10} {'Status'}"
    )
    print("-" * 85)

    for name in ctx.catalog["products"]:
        product_state = env_state.get(name, {})
        version = product_state.get("version", "-")
        
        # Show commit or hash depending on what's available
        if use_git:
            pub_track = (product_state.get("published_commit") or "")[:8] or "-"
            dep_track = (product_state.get("deployed_commit") or "")[:8] or "-"
        else:
            pub_track = (product_state.get("published_hash") or "")[:8] or "-"
            dep_track = "-"  # Hash tracking only applies to publish

        if not product_state.get("version"):
            status = "NOT PUBLISHED"
        elif use_git and product_state.get("published_commit") != product_state.get(
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

        print(f"{name:<15} {version:<20} {pub_track:<12} {dep_track:<10} {status}")


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
  python deploy.py terminate -e dev --force
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
    publish_parser.add_argument(
        "--force", action="store_true",
        help="Publish even if no changes detected"
    )

    # deploy
    deploy_parser = subparsers.add_parser("deploy", help="Deploy products via Service Catalog")
    add_common_args(deploy_parser)
    deploy_parser.add_argument(
        "-p", "--product", action="append", help="Specific product(s)"
    )
    deploy_parser.add_argument("--dry-run", action="store_true")

    # terminate
    terminate_parser = subparsers.add_parser("terminate", help="Terminate provisioned products")
    add_common_args(terminate_parser)
    terminate_parser.add_argument(
        "-p", "--product", action="append", help="Specific product(s)"
    )
    terminate_parser.add_argument("--dry-run", action="store_true")
    terminate_parser.add_argument(
        "--force", action="store_true", help="Skip confirmation"
    )

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
        cmd_publish(ctx, getattr(args, "product", None), force=getattr(args, "force", False))
    elif args.command == "deploy":
        cmd_deploy(ctx, getattr(args, "product", None))
    elif args.command == "terminate":
        cmd_terminate(ctx, getattr(args, "product", None), force=getattr(args, "force", False))
    elif args.command == "status":
        cmd_status(ctx)


if __name__ == "__main__":
    main()
