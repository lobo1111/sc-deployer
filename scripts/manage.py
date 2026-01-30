#!/usr/bin/env python3
"""
Management CLI for SC Deployer.

Usage:
    python manage.py                    # Interactive menu
    python manage.py profiles list
    python manage.py profiles scan
    python manage.py profiles add <name>
    python manage.py profiles login <name>
    python manage.py portfolios list
    python manage.py portfolios add <name>
    python manage.py products list
    python manage.py products add <name>
"""

import argparse
import configparser
import json
import subprocess
import sys
from pathlib import Path
from typing import Callable

import boto3
import yaml

from config import get_project_root, load_bootstrap_config, load_catalog_config


# ============== INTERACTIVE MENU ==============


def clear_screen():
    """Clear terminal screen."""
    print("\033[H\033[J", end="")


def print_header(title: str):
    """Print a styled header."""
    print()
    print("=" * 50)
    print(f"  {title}")
    print("=" * 50)
    print()


def print_menu(title: str, options: list[tuple[str, str]], back: bool = True) -> str:
    """
    Display a menu and get user selection.
    
    Args:
        title: Menu title
        options: List of (key, description) tuples
        back: Whether to show back/quit option
    
    Returns:
        Selected key or 'back'/'quit'
    """
    print_header(title)
    
    for key, desc in options:
        print(f"  [{key}] {desc}")
    
    if back:
        print()
        print("  [b] Back")
        print("  [q] Quit")
    
    print()
    choice = input("Select option: ").strip().lower()
    
    if choice == "q":
        print("\nGoodbye!")
        sys.exit(0)
    
    if choice == "b" and back:
        return "back"
    
    return choice


def interactive_menu():
    """Main interactive menu loop."""
    while True:
        clear_screen()
        choice = print_menu("SC Deployer - Management CLI", [
            ("1", "Profiles - Manage AWS profiles"),
            ("2", "Portfolios - Manage Service Catalog portfolios"),
            ("3", "Products - Manage products"),
            ("4", "Status - Show overall status"),
        ], back=False)
        
        if choice == "1":
            profiles_menu()
        elif choice == "2":
            portfolios_menu()
        elif choice == "3":
            products_menu()
        elif choice == "4":
            status_overview()
        elif choice == "q":
            print("\nGoodbye!")
            break
        else:
            input("Invalid option. Press Enter to continue...")


def profiles_menu():
    """Profiles submenu."""
    while True:
        clear_screen()
        choice = print_menu("Profiles", [
            ("1", "List configured profiles"),
            ("2", "Scan available AWS profiles"),
            ("3", "Add new profile"),
            ("4", "Login (SSO)"),
            ("5", "Who am I? (check identity)"),
        ])
        
        if choice == "back":
            return
        elif choice == "1":
            clear_screen()
            cmd_profiles_list()
            input("\nPress Enter to continue...")
        elif choice == "2":
            clear_screen()
            cmd_profiles_scan()
            input("\nPress Enter to continue...")
        elif choice == "3":
            clear_screen()
            print_header("Add Profile")
            name = input("Environment name (e.g., dev, prod): ").strip()
            if name:
                cmd_profiles_add(name)
            input("\nPress Enter to continue...")
        elif choice == "4":
            clear_screen()
            print_header("SSO Login")
            cmd_profiles_list()
            print()
            name = input("Environment name to login: ").strip()
            if name:
                cmd_profiles_login(name)
            input("\nPress Enter to continue...")
        elif choice == "5":
            clear_screen()
            print_header("Who Am I?")
            cmd_profiles_list()
            print()
            name = input("Environment name (or Enter for default): ").strip()
            cmd_profiles_whoami(name if name else None)
            input("\nPress Enter to continue...")


def portfolios_menu():
    """Portfolios submenu."""
    while True:
        clear_screen()
        choice = print_menu("Portfolios", [
            ("1", "List portfolios"),
            ("2", "Add new portfolio"),
        ])
        
        if choice == "back":
            return
        elif choice == "1":
            clear_screen()
            cmd_portfolios_list()
            input("\nPress Enter to continue...")
        elif choice == "2":
            clear_screen()
            print_header("Add Portfolio")
            name = input("Portfolio name (e.g., security, monitoring): ").strip()
            if name:
                # Ask for environment
                cmd_profiles_list()
                print()
                env = input("Environment for principal lookup (or Enter to skip): ").strip()
                cmd_portfolios_add(name, env if env else None)
            input("\nPress Enter to continue...")


def products_menu():
    """Products submenu."""
    while True:
        clear_screen()
        choice = print_menu("Products", [
            ("1", "List products"),
            ("2", "Add new product"),
            ("3", "Show dependency graph"),
        ])
        
        if choice == "back":
            return
        elif choice == "1":
            clear_screen()
            cmd_products_list()
            input("\nPress Enter to continue...")
        elif choice == "2":
            clear_screen()
            print_header("Add Product")
            name = input("Product name (e.g., monitoring, cache): ").strip()
            if name:
                cmd_products_add(name)
            input("\nPress Enter to continue...")
        elif choice == "3":
            clear_screen()
            show_dependency_graph()
            input("\nPress Enter to continue...")


def status_overview():
    """Show overall status."""
    clear_screen()
    print_header("Status Overview")
    
    # Profiles
    print("PROFILES:")
    profiles_path = get_project_root() / "profiles.yaml"
    if profiles_path.exists():
        with open(profiles_path) as f:
            data = yaml.safe_load(f)
        profiles = data.get("profiles", {})
        for name, cfg in profiles.items():
            print(f"  {name}: {cfg.get('aws_profile')} ({cfg.get('aws_region')})")
    else:
        print("  No profiles configured")
    
    # Portfolios
    print("\nPORTFOLIOS:")
    config = load_bootstrap_config()
    portfolios = config.get("portfolios", {})
    for name in portfolios:
        print(f"  {name}")
    if not portfolios:
        print("  No portfolios configured")
    
    # Products
    print("\nPRODUCTS:")
    catalog = load_catalog_config()
    products = catalog.get("products", {})
    for name, cfg in products.items():
        deps = cfg.get("dependencies", [])
        dep_str = f" -> depends on: {', '.join(deps)}" if deps else ""
        print(f"  {name}{dep_str}")
    if not products:
        print("  No products configured")
    
    # Bootstrap state
    print("\nBOOTSTRAP STATE:")
    bootstrap_state_path = get_project_root() / ".bootstrap-state.json"
    if bootstrap_state_path.exists():
        with open(bootstrap_state_path) as f:
            state = json.load(f)
        for env in state.get("environments", {}):
            bootstrapped = state["environments"][env].get("bootstrapped_at", "unknown")
            print(f"  {env}: bootstrapped at {bootstrapped}")
    else:
        print("  Not bootstrapped yet")
    
    # Deploy state
    print("\nDEPLOY STATE:")
    deploy_state_path = get_project_root() / ".deploy-state.json"
    if deploy_state_path.exists():
        with open(deploy_state_path) as f:
            state = json.load(f)
        for env, env_state in state.get("environments", {}).items():
            print(f"  {env}:")
            for product, pstate in env_state.items():
                version = pstate.get("version", "-")
                deployed = "deployed" if pstate.get("deployed_commit") else "not deployed"
                print(f"    {product}: v{version} ({deployed})")
    else:
        print("  No deployments yet")
    
    input("\nPress Enter to continue...")


def show_dependency_graph():
    """Show ASCII dependency graph."""
    print_header("Dependency Graph")
    
    catalog = load_catalog_config()
    products = catalog.get("products", {})
    
    if not products:
        print("No products configured")
        return
    
    # Find root products (no dependencies)
    roots = [name for name, cfg in products.items() if not cfg.get("dependencies")]
    
    # Build reverse dependency map
    dependents = {name: [] for name in products}
    for name, cfg in products.items():
        for dep in cfg.get("dependencies", []):
            if dep in dependents:
                dependents[dep].append(name)
    
    # Print tree
    printed = set()
    
    def print_tree(name: str, prefix: str = "", is_last: bool = True):
        if name in printed:
            connector = "└── " if is_last else "├── "
            print(f"{prefix}{connector}{name} (circular ref)")
            return
        
        printed.add(name)
        connector = "└── " if is_last else "├── "
        
        cfg = products.get(name, {})
        outputs = len(cfg.get("outputs", []))
        portfolio = cfg.get("portfolio", "")
        
        print(f"{prefix}{connector}{name} [{portfolio}] ({outputs} outputs)")
        
        children = dependents.get(name, [])
        for i, child in enumerate(children):
            is_child_last = i == len(children) - 1
            new_prefix = prefix + ("    " if is_last else "│   ")
            print_tree(child, new_prefix, is_child_last)
    
    print("Product dependency tree:\n")
    
    if roots:
        for i, root in enumerate(roots):
            print_tree(root, "", i == len(roots) - 1)
    else:
        print("No root products found (possible circular dependencies)")
        for name in products:
            print(f"  - {name}")
    
    print("\nLegend: product [portfolio] (outputs count)")


# ============== AWS PROFILE UTILITIES ==============


def get_aws_config_path() -> Path:
    """Get path to AWS config file."""
    return Path.home() / ".aws" / "config"


def get_aws_credentials_path() -> Path:
    """Get path to AWS credentials file."""
    return Path.home() / ".aws" / "credentials"


def scan_aws_profiles() -> list[dict]:
    """Scan available AWS profiles from ~/.aws/config and credentials."""
    profiles = []
    
    # Parse config file
    config_path = get_aws_config_path()
    if config_path.exists():
        config = configparser.ConfigParser()
        config.read(config_path)
        
        for section in config.sections():
            # Sections are like [profile my-profile] or [default]
            if section.startswith("profile "):
                profile_name = section.replace("profile ", "")
            elif section == "default":
                profile_name = "default"
            else:
                continue
            
            profile_data = dict(config[section])
            profile_info = {
                "name": profile_name,
                "region": profile_data.get("region", ""),
                "sso_start_url": profile_data.get("sso_start_url", ""),
                "sso_account_id": profile_data.get("sso_account_id", ""),
                "sso_role_name": profile_data.get("sso_role_name", ""),
                "is_sso": "sso_start_url" in profile_data,
                "source": "config",
            }
            profiles.append(profile_info)
    
    # Parse credentials file for non-SSO profiles
    creds_path = get_aws_credentials_path()
    if creds_path.exists():
        creds = configparser.ConfigParser()
        creds.read(creds_path)
        
        existing_names = {p["name"] for p in profiles}
        for section in creds.sections():
            if section not in existing_names:
                profiles.append({
                    "name": section,
                    "region": "",
                    "is_sso": False,
                    "source": "credentials",
                })
    
    return sorted(profiles, key=lambda x: x["name"])


def get_caller_identity(profile_name: str) -> dict | None:
    """Get AWS caller identity for a profile."""
    try:
        session = boto3.Session(profile_name=profile_name)
        sts = session.client("sts")
        identity = sts.get_caller_identity()
        return {
            "account_id": identity["Account"],
            "arn": identity["Arn"],
            "user_id": identity["UserId"],
        }
    except Exception as e:
        return None


def sso_login(profile_name: str) -> bool:
    """Initiate SSO login for a profile."""
    print(f"Opening SSO login for profile: {profile_name}")
    result = subprocess.run(
        ["aws", "sso", "login", "--profile", profile_name],
        capture_output=False,
    )
    return result.returncode == 0


def get_profile_principal(profile_name: str) -> str | None:
    """Get the IAM principal ARN for a profile."""
    identity = get_caller_identity(profile_name)
    if identity:
        # Convert user/role ARN to a usable principal
        arn = identity["arn"]
        # If it's an assumed role, extract the role ARN
        if ":assumed-role/" in arn:
            # arn:aws:sts::123:assumed-role/RoleName/session -> arn:aws:iam::123:role/RoleName
            parts = arn.split(":")
            account = parts[4]
            role_part = parts[5].split("/")
            role_name = role_part[1]
            return f"arn:aws:iam::{account}:role/{role_name}"
        return arn
    return None


# ============== PROFILES COMMANDS ==============


def cmd_profiles_list():
    """List profiles configured in profiles.yaml."""
    profiles_path = get_project_root() / "profiles.yaml"
    
    if not profiles_path.exists():
        print("No profiles.yaml found")
        return
    
    with open(profiles_path) as f:
        data = yaml.safe_load(f)
    
    profiles = data.get("profiles", {})
    
    if not profiles:
        print("No profiles configured in profiles.yaml")
        return
    
    print("\nConfigured profiles (profiles.yaml):")
    print("-" * 70)
    print(f"{'Name':<15} {'AWS Profile':<20} {'Region':<15} {'Account ID'}")
    print("-" * 70)
    
    for name, config in profiles.items():
        print(
            f"{name:<15} "
            f"{config.get('aws_profile', '-'):<20} "
            f"{config.get('aws_region', '-'):<15} "
            f"{config.get('account_id', '-')}"
        )


def cmd_profiles_scan():
    """Scan available AWS profiles from ~/.aws/config."""
    profiles = scan_aws_profiles()
    
    if not profiles:
        print("No AWS profiles found in ~/.aws/config or ~/.aws/credentials")
        return
    
    print("\nAvailable AWS profiles:")
    print("-" * 80)
    print(f"{'Name':<25} {'Region':<15} {'SSO':<5} {'Account ID':<15} {'Source'}")
    print("-" * 80)
    
    for p in profiles:
        sso = "Yes" if p["is_sso"] else "No"
        account = p.get("sso_account_id", "") or ""
        print(
            f"{p['name']:<25} "
            f"{p.get('region', '-'):<15} "
            f"{sso:<5} "
            f"{account:<15} "
            f"{p['source']}"
        )
    
    print(f"\nTotal: {len(profiles)} profiles")
    print("\nTo add a profile: python manage.py profiles add <environment-name>")


def cmd_profiles_add(env_name: str, aws_profile: str = None):
    """Add or update a profile in profiles.yaml."""
    profiles_path = get_project_root() / "profiles.yaml"
    
    # Load existing profiles
    if profiles_path.exists():
        with open(profiles_path) as f:
            data = yaml.safe_load(f) or {}
    else:
        data = {}
    
    if "profiles" not in data:
        data["profiles"] = {}
    
    # If profile already exists, confirm update
    if env_name in data["profiles"]:
        confirm = input(f"Profile '{env_name}' already exists. Update? [y/N]: ")
        if confirm.lower() != "y":
            print("Aborted")
            return
    
    # Scan available AWS profiles
    aws_profiles = scan_aws_profiles()
    aws_profile_names = [p["name"] for p in aws_profiles]
    
    if not aws_profile:
        print("\nAvailable AWS profiles:")
        for i, p in enumerate(aws_profiles, 1):
            sso = " (SSO)" if p["is_sso"] else ""
            print(f"  {i}. {p['name']}{sso}")
        
        choice = input("\nSelect AWS profile number (or enter name): ").strip()
        
        if choice.isdigit():
            idx = int(choice) - 1
            if 0 <= idx < len(aws_profiles):
                aws_profile = aws_profiles[idx]["name"]
            else:
                print("Invalid selection")
                return
        else:
            aws_profile = choice
    
    if aws_profile not in aws_profile_names:
        print(f"Warning: AWS profile '{aws_profile}' not found in ~/.aws/config")
        confirm = input("Continue anyway? [y/N]: ")
        if confirm.lower() != "y":
            print("Aborted")
            return
    
    # Get profile details
    aws_profile_data = next((p for p in aws_profiles if p["name"] == aws_profile), {})
    
    # Try to get region
    region = aws_profile_data.get("region", "")
    if not region:
        region = input("AWS region [eu-west-1]: ").strip() or "eu-west-1"
    
    # Try to get account ID
    account_id = aws_profile_data.get("sso_account_id", "")
    
    if not account_id:
        print(f"\nAttempting to get account ID from AWS...")
        identity = get_caller_identity(aws_profile)
        if identity:
            account_id = identity["account_id"]
            print(f"  Account ID: {account_id}")
        else:
            print("  Could not retrieve account ID (credentials may be expired)")
            account_id = input("Enter AWS account ID: ").strip()
    
    # Save profile
    data["profiles"][env_name] = {
        "aws_profile": aws_profile,
        "aws_region": region,
        "account_id": account_id,
    }
    
    with open(profiles_path, "w") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False)
    
    print(f"\nProfile '{env_name}' saved to profiles.yaml")
    print(f"  AWS Profile: {aws_profile}")
    print(f"  Region:      {region}")
    print(f"  Account ID:  {account_id}")


def cmd_profiles_login(env_name: str):
    """Login to AWS SSO for a profile."""
    profiles_path = get_project_root() / "profiles.yaml"
    
    if not profiles_path.exists():
        print("No profiles.yaml found. Run: python manage.py profiles add")
        return
    
    with open(profiles_path) as f:
        data = yaml.safe_load(f)
    
    if env_name not in data.get("profiles", {}):
        # Maybe they passed the AWS profile name directly
        aws_profile = env_name
    else:
        aws_profile = data["profiles"][env_name].get("aws_profile")
    
    if not aws_profile:
        print(f"Profile '{env_name}' not found")
        return
    
    # Check if it's an SSO profile
    aws_profiles = scan_aws_profiles()
    profile_data = next((p for p in aws_profiles if p["name"] == aws_profile), None)
    
    if profile_data and profile_data.get("is_sso"):
        if sso_login(aws_profile):
            print("\nSSO login successful!")
            
            # Verify and show identity
            identity = get_caller_identity(aws_profile)
            if identity:
                print(f"  Account: {identity['account_id']}")
                print(f"  ARN:     {identity['arn']}")
        else:
            print("\nSSO login failed")
    else:
        print(f"Profile '{aws_profile}' is not an SSO profile")
        print("Checking credentials...")
        identity = get_caller_identity(aws_profile)
        if identity:
            print(f"  Account: {identity['account_id']}")
            print(f"  ARN:     {identity['arn']}")
        else:
            print("  Credentials not valid or expired")


def cmd_profiles_whoami(env_name: str = None):
    """Show current AWS identity for a profile."""
    if env_name:
        profiles_path = get_project_root() / "profiles.yaml"
        if profiles_path.exists():
            with open(profiles_path) as f:
                data = yaml.safe_load(f)
            if env_name in data.get("profiles", {}):
                aws_profile = data["profiles"][env_name].get("aws_profile")
            else:
                aws_profile = env_name
        else:
            aws_profile = env_name
    else:
        aws_profile = None
    
    print(f"\nAWS Identity for profile: {aws_profile or 'default'}")
    print("-" * 50)
    
    identity = get_caller_identity(aws_profile)
    if identity:
        print(f"Account ID: {identity['account_id']}")
        print(f"ARN:        {identity['arn']}")
        print(f"User ID:    {identity['user_id']}")
        
        principal = get_profile_principal(aws_profile)
        if principal:
            print(f"Principal:  {principal}")
    else:
        print("Could not retrieve identity (credentials expired or invalid)")
        print(f"\nTry: python manage.py profiles login {env_name or 'default'}")


# ============== PORTFOLIOS COMMANDS ==============


def cmd_portfolios_list():
    """List portfolios in bootstrap.yaml."""
    config = load_bootstrap_config()
    portfolios = config.get("portfolios", {})
    
    if not portfolios:
        print("No portfolios configured")
        return
    
    print("\nConfigured portfolios (bootstrap.yaml):")
    print("-" * 70)
    
    for name, cfg in portfolios.items():
        print(f"\n  {name}:")
        print(f"    Display Name: {cfg.get('display_name', '-')}")
        print(f"    Description:  {cfg.get('description', '-')}")
        print(f"    Provider:     {cfg.get('provider_name', '-')}")
        principals = cfg.get("principals", [])
        if principals:
            print(f"    Principals:   {len(principals)}")
            for p in principals[:3]:
                print(f"      - {p}")
            if len(principals) > 3:
                print(f"      ... and {len(principals) - 3} more")


def cmd_portfolios_add(name: str, env_name: str = None):
    """Add a new portfolio to bootstrap.yaml."""
    bootstrap_path = get_project_root() / "bootstrap.yaml"
    
    with open(bootstrap_path) as f:
        config = yaml.safe_load(f)
    
    if "portfolios" not in config:
        config["portfolios"] = {}
    
    if name in config["portfolios"]:
        print(f"Portfolio '{name}' already exists")
        return
    
    # Get display name
    default_display = name.replace("-", " ").replace("_", " ").title()
    display_name = input(f"Display name [{default_display}]: ").strip() or default_display
    
    # Get description
    description = input("Description: ").strip()
    
    # Get provider name
    provider = input("Provider name [Platform Team]: ").strip() or "Platform Team"
    
    # Get principals
    principals = []
    
    if env_name:
        # Try to get principal from the specified environment
        profiles_path = get_project_root() / "profiles.yaml"
        if profiles_path.exists():
            with open(profiles_path) as f:
                profiles_data = yaml.safe_load(f)
            
            if env_name in profiles_data.get("profiles", {}):
                aws_profile = profiles_data["profiles"][env_name].get("aws_profile")
                print(f"\nDeriving principal from profile '{env_name}' ({aws_profile})...")
                
                principal = get_profile_principal(aws_profile)
                if principal:
                    print(f"  Found: {principal}")
                    use_it = input("Use this principal? [Y/n]: ").strip().lower()
                    if use_it != "n":
                        principals.append(principal)
    
    if not principals:
        print("\nEnter principals (IAM role/user ARNs). Empty line to finish.")
        print("  Tip: Use ${account_id} placeholder for account ID")
        while True:
            p = input("  Principal ARN: ").strip()
            if not p:
                break
            principals.append(p)
    
    # Get tags
    print("\nTags (key=value format, empty line to finish):")
    tags = {}
    while True:
        tag = input("  Tag: ").strip()
        if not tag:
            break
        if "=" in tag:
            k, v = tag.split("=", 1)
            tags[k.strip()] = v.strip()
    
    # Save portfolio
    config["portfolios"][name] = {
        "display_name": display_name,
        "description": description,
        "provider_name": provider,
        "principals": principals,
        "tags": tags,
    }
    
    with open(bootstrap_path, "w") as f:
        yaml.dump(config, f, default_flow_style=False, sort_keys=False)
    
    print(f"\nPortfolio '{name}' added to bootstrap.yaml")
    print("Run bootstrap to create it in AWS:")
    print(f"  python bootstrap.py bootstrap -e {env_name or 'dev'}")


# ============== PRODUCTS COMMANDS ==============


def cmd_products_list():
    """List products in catalog.yaml."""
    config = load_catalog_config()
    products = config.get("products", {})
    
    if not products:
        print("No products configured")
        return
    
    print("\nConfigured products (catalog.yaml):")
    print("-" * 80)
    print(f"{'Name':<20} {'Portfolio':<20} {'Dependencies':<25} {'Outputs'}")
    print("-" * 80)
    
    for name, cfg in products.items():
        deps = ", ".join(cfg.get("dependencies", [])) or "-"
        outputs = len(cfg.get("outputs", []))
        portfolio = cfg.get("portfolio", "-")
        print(f"{name:<20} {portfolio:<20} {deps:<25} {outputs} outputs")


def cmd_products_add(name: str):
    """Add a new product to catalog.yaml and create directory structure."""
    catalog_path = get_project_root() / "catalog.yaml"
    products_dir = get_project_root() / "products" / name
    
    with open(catalog_path) as f:
        config = yaml.safe_load(f)
    
    if "products" not in config:
        config["products"] = {}
    
    if name in config["products"]:
        print(f"Product '{name}' already exists")
        return
    
    # Get portfolio
    bootstrap_config = load_bootstrap_config()
    portfolios = list(bootstrap_config.get("portfolios", {}).keys())
    
    if portfolios:
        print("\nAvailable portfolios:")
        for i, p in enumerate(portfolios, 1):
            print(f"  {i}. {p}")
        
        choice = input("\nSelect portfolio number (or enter name): ").strip()
        if choice.isdigit():
            idx = int(choice) - 1
            if 0 <= idx < len(portfolios):
                portfolio = portfolios[idx]
            else:
                portfolio = ""
        else:
            portfolio = choice
    else:
        portfolio = input("Portfolio name: ").strip()
    
    # Get dependencies
    existing_products = list(config.get("products", {}).keys())
    dependencies = []
    
    if existing_products:
        print(f"\nExisting products: {', '.join(existing_products)}")
        deps_input = input("Dependencies (comma-separated, or empty): ").strip()
        if deps_input:
            dependencies = [d.strip() for d in deps_input.split(",")]
    
    # Get parameter mappings from dependencies
    parameter_mapping = {}
    if dependencies:
        print("\nParameter mappings (maps dependency outputs to this product's parameters)")
        print("Format: param_name=dependency.output_name")
        print("Empty line to finish.")
        
        # Show available outputs from dependencies
        for dep in dependencies:
            if dep in config.get("products", {}):
                outputs = config["products"][dep].get("outputs", [])
                if outputs:
                    print(f"  {dep} outputs: {', '.join(outputs)}")
        
        while True:
            mapping = input("  Mapping: ").strip()
            if not mapping:
                break
            if "=" in mapping:
                param, source = mapping.split("=", 1)
                parameter_mapping[param.strip()] = source.strip()
    
    # Get outputs
    print("\nOutputs this product will expose (empty line to finish):")
    outputs = []
    while True:
        output = input("  Output name: ").strip()
        if not output:
            break
        outputs.append(output)
    
    # Get description
    description = input("\nProduct description: ").strip()
    
    # Create product directory
    products_dir.mkdir(parents=True, exist_ok=True)
    
    # Create product.yaml
    product_yaml = {
        "name": name,
        "description": description,
        "portfolio": portfolio,
        "parameters": {},
        "outputs": {o: {"description": f"{o} output", "export": True} for o in outputs},
    }
    
    # Add parameters based on mappings
    for param in parameter_mapping:
        product_yaml["parameters"][param] = {
            "type": "String",
            "description": f"Mapped from {parameter_mapping[param]}",
            "required": True,
        }
    
    with open(products_dir / "product.yaml", "w") as f:
        yaml.dump(product_yaml, f, default_flow_style=False, sort_keys=False)
    
    # Create template.yaml skeleton
    template = f"""AWSTemplateFormatVersion: '2010-09-09'
Description: {description or f'CloudFormation template for {name}'}

Parameters:
  Environment:
    Type: String
    Default: dev
"""
    
    # Add parameters
    for param in parameter_mapping:
        template += f"""
  {param}:
    Type: String
"""
    
    template += """
Resources:
  # TODO: Add your resources here
  PlaceholderResource:
    Type: AWS::CloudFormation::WaitConditionHandle

Outputs:
"""
    
    # Add outputs
    for output in outputs:
        template += f"""  {output}:
    Description: {output}
    Value: !Ref PlaceholderResource
    Export:
      Name: !Sub "${{Environment}}-{output}"
"""
    
    with open(products_dir / "template.yaml", "w") as f:
        f.write(template)
    
    # Update catalog.yaml
    product_config = {
        "path": f"products/{name}",
        "portfolio": portfolio,
        "dependencies": dependencies,
    }
    
    if parameter_mapping:
        product_config["parameter_mapping"] = parameter_mapping
    
    if outputs:
        product_config["outputs"] = outputs
    
    config["products"][name] = product_config
    
    with open(catalog_path, "w") as f:
        yaml.dump(config, f, default_flow_style=False, sort_keys=False)
    
    print(f"\nProduct '{name}' created:")
    print(f"  - catalog.yaml updated")
    print(f"  - products/{name}/product.yaml created")
    print(f"  - products/{name}/template.yaml created (skeleton)")
    print(f"\nNext steps:")
    print(f"  1. Edit products/{name}/template.yaml with your CloudFormation resources")
    print(f"  2. Run: python bootstrap.py bootstrap -e dev  (if new portfolio)")
    print(f"  3. Run: python deploy.py publish -e dev -p {name}")


# ============== MAIN ==============


def main():
    # If no arguments, launch interactive menu
    if len(sys.argv) == 1:
        try:
            interactive_menu()
        except KeyboardInterrupt:
            print("\n\nGoodbye!")
        return
    
    parser = argparse.ArgumentParser(
        description="SC Deployer Management CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Run without arguments for interactive menu:
    python manage.py

Or use commands directly:
    python manage.py profiles list
    python manage.py profiles scan
    python manage.py profiles add dev
    python manage.py profiles login dev
    python manage.py profiles whoami dev
    python manage.py portfolios list
    python manage.py portfolios add security -e dev
    python manage.py products list
    python manage.py products add monitoring
        """,
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    
    # profiles
    profiles_parser = subparsers.add_parser("profiles", help="Manage AWS profiles")
    profiles_sub = profiles_parser.add_subparsers(dest="subcommand", required=True)
    
    profiles_sub.add_parser("list", help="List configured profiles")
    profiles_sub.add_parser("scan", help="Scan available AWS profiles")
    
    profiles_add = profiles_sub.add_parser("add", help="Add a profile")
    profiles_add.add_argument("name", help="Environment name (e.g., dev, prod)")
    profiles_add.add_argument("--aws-profile", help="AWS profile name")
    
    profiles_login = profiles_sub.add_parser("login", help="Login via SSO")
    profiles_login.add_argument("name", help="Environment or AWS profile name")
    
    profiles_whoami = profiles_sub.add_parser("whoami", help="Show AWS identity")
    profiles_whoami.add_argument("name", nargs="?", help="Environment name")
    
    # portfolios
    portfolios_parser = subparsers.add_parser("portfolios", help="Manage portfolios")
    portfolios_sub = portfolios_parser.add_subparsers(dest="subcommand", required=True)
    
    portfolios_sub.add_parser("list", help="List portfolios")
    
    portfolios_add = portfolios_sub.add_parser("add", help="Add a portfolio")
    portfolios_add.add_argument("name", help="Portfolio name")
    portfolios_add.add_argument("-e", "--environment", help="Environment for principal lookup")
    
    # products
    products_parser = subparsers.add_parser("products", help="Manage products")
    products_sub = products_parser.add_subparsers(dest="subcommand", required=True)
    
    products_sub.add_parser("list", help="List products")
    
    products_add = products_sub.add_parser("add", help="Add a product")
    products_add.add_argument("name", help="Product name")
    
    # status
    subparsers.add_parser("status", help="Show overall status")
    
    # graph
    subparsers.add_parser("graph", help="Show dependency graph")
    
    args = parser.parse_args()
    
    # Route commands
    if args.command == "profiles":
        if args.subcommand == "list":
            cmd_profiles_list()
        elif args.subcommand == "scan":
            cmd_profiles_scan()
        elif args.subcommand == "add":
            cmd_profiles_add(args.name, getattr(args, "aws_profile", None))
        elif args.subcommand == "login":
            cmd_profiles_login(args.name)
        elif args.subcommand == "whoami":
            cmd_profiles_whoami(getattr(args, "name", None))
    
    elif args.command == "portfolios":
        if args.subcommand == "list":
            cmd_portfolios_list()
        elif args.subcommand == "add":
            cmd_portfolios_add(args.name, getattr(args, "environment", None))
    
    elif args.command == "products":
        if args.subcommand == "list":
            cmd_products_list()
        elif args.subcommand == "add":
            cmd_products_add(args.name)
    
    elif args.command == "status":
        status_overview()
    
    elif args.command == "graph":
        show_dependency_graph()


if __name__ == "__main__":
    main()
