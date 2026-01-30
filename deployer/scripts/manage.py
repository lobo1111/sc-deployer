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

import boto3
import questionary
import yaml
from questionary import Style

from config import get_project_root, get_products_root, load_bootstrap_config, load_catalog_config


# ============== STYLING ==============

custom_style = Style([
    ("qmark", "fg:cyan bold"),
    ("question", "fg:white bold"),
    ("answer", "fg:green bold"),
    ("pointer", "fg:cyan bold"),
    ("highlighted", "fg:cyan bold"),
    ("selected", "fg:green"),
    ("separator", "fg:gray"),
    ("instruction", "fg:gray"),
])


# ============== UTILITIES ==============


def print_header(title: str):
    """Print a styled header."""
    print()
    print("=" * 60)
    print(f"  {title}")
    print("=" * 60)
    print()


def print_command_hint(command: str):
    """Print hint for non-interactive usage."""
    print()
    print("-" * 60)
    print(f"  CLI: .\\cli.ps1 {command.replace('.py ', ' ').replace('manage ', '')}")
    print(f"  Direct: python deployer/scripts/{command}")
    print("-" * 60)


def confirm_continue():
    """Ask to continue."""
    print()
    questionary.press_any_key_to_continue(
        message="Press any key to continue...",
        style=custom_style
    ).ask()


def clear_screen():
    """Clear terminal screen."""
    print("\033[H\033[J", end="")


# ============== AWS PROFILE UTILITIES ==============


def get_aws_config_path() -> Path:
    return Path.home() / ".aws" / "config"


def get_aws_credentials_path() -> Path:
    return Path.home() / ".aws" / "credentials"


def scan_aws_profiles() -> list[dict]:
    """Scan available AWS profiles from ~/.aws/config and credentials."""
    profiles = []
    
    config_path = get_aws_config_path()
    if config_path.exists():
        config = configparser.ConfigParser()
        config.read(config_path)
        
        for section in config.sections():
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
    except Exception:
        return None


def sso_login(profile_name: str) -> bool:
    """Initiate SSO login for a profile."""
    print(f"\nOpening SSO login for profile: {profile_name}")
    result = subprocess.run(
        ["aws", "sso", "login", "--profile", profile_name],
        capture_output=False,
    )
    return result.returncode == 0


def get_profile_principal(profile_name: str) -> str | None:
    """Get the IAM principal ARN for a profile."""
    identity = get_caller_identity(profile_name)
    if identity:
        arn = identity["arn"]
        if ":assumed-role/" in arn:
            parts = arn.split(":")
            account = parts[4]
            role_part = parts[5].split("/")
            role_name = role_part[1]
            return f"arn:aws:iam::{account}:role/{role_name}"
        return arn
    return None


def get_configured_profiles() -> dict:
    """Get profiles from profiles.yaml."""
    profiles_path = get_project_root() / "profiles.yaml"
    if profiles_path.exists():
        with open(profiles_path) as f:
            data = yaml.safe_load(f)
        return data.get("profiles", {})
    return {}


def get_configured_environments() -> list[str]:
    """Get list of configured environment names."""
    return list(get_configured_profiles().keys())


# ============== INTERACTIVE MENU ==============


def interactive_menu():
    """Main interactive menu loop."""
    while True:
        clear_screen()
        print_header("SC Deployer")
        
        choices = [
            questionary.Choice("🚀 Quick Start - First time setup wizard", value="quickstart"),
            questionary.Choice("📋 Status - View current configuration", value="status"),
            questionary.Separator(),
            questionary.Choice("👤 Profiles - Manage AWS profiles & credentials", value="profiles"),
            questionary.Choice("📁 Portfolios - Manage Service Catalog portfolios", value="portfolios"),
            questionary.Choice("📦 Products - Manage products & dependencies", value="products"),
            questionary.Separator(),
            questionary.Choice("🔐 Login - Authenticate with AWS SSO", value="login"),
            questionary.Choice("🔄 Deploy - Publish & deploy products", value="deploy"),
            questionary.Separator(),
            questionary.Choice("❌ Exit", value="exit"),
        ]
        
        action = questionary.select(
            "What would you like to do?",
            choices=choices,
            style=custom_style,
            use_shortcuts=True,
        ).ask()
        
        if action is None or action == "exit":
            print("\nGoodbye!")
            break
        elif action == "quickstart":
            quick_start_wizard()
        elif action == "status":
            show_status()
        elif action == "profiles":
            profiles_menu()
        elif action == "portfolios":
            portfolios_menu()
        elif action == "products":
            products_menu()
        elif action == "login":
            quick_login()
        elif action == "deploy":
            deploy_menu()


def quick_start_wizard():
    """First-time setup wizard."""
    clear_screen()
    print_header("Quick Start Wizard")
    
    print("This wizard will help you set up SC Deployer.\n")
    
    # Step 1: Check for AWS profiles
    aws_profiles = scan_aws_profiles()
    configured = get_configured_profiles()
    
    if not configured:
        print("Step 1: No environments configured yet.\n")
        
        if not aws_profiles:
            print("❌ No AWS profiles found in ~/.aws/config")
            print("   Please configure AWS CLI first: aws configure sso")
            confirm_continue()
            return
        
        if questionary.confirm(
            "Would you like to add an environment now?",
            default=True,
            style=custom_style
        ).ask():
            interactive_add_profile()
    else:
        print(f"Step 1: ✅ {len(configured)} environment(s) configured\n")
        for name, cfg in configured.items():
            print(f"   • {name}: {cfg.get('aws_profile')} ({cfg.get('aws_region')})")
    
    print()
    
    # Step 2: Check bootstrap state
    bootstrap_state_path = get_project_root() / ".bootstrap-state.json"
    if bootstrap_state_path.exists():
        with open(bootstrap_state_path) as f:
            state = json.load(f)
        bootstrapped_envs = list(state.get("environments", {}).keys())
        if bootstrapped_envs:
            print(f"Step 2: ✅ Bootstrap complete for: {', '.join(bootstrapped_envs)}\n")
        else:
            print("Step 2: ⚠️  Bootstrap not run yet\n")
    else:
        print("Step 2: ⚠️  Bootstrap not run yet\n")
        
        envs = get_configured_environments()
        if envs:
            print("   Run bootstrap to create AWS resources:")
            for env in envs:
                print(f"   python deployer/scripts/bootstrap.py bootstrap -e {env}")
    
    print()
    
    # Step 3: Products
    catalog = load_catalog_config()
    products = catalog.get("products", {})
    print(f"Step 3: {len(products)} product(s) configured\n")
    for name, cfg in products.items():
        deps = cfg.get("dependencies", [])
        dep_str = f" → {', '.join(deps)}" if deps else ""
        print(f"   • {name}{dep_str}")
    
    print()
    print_command_hint("manage.py status")
    confirm_continue()


def quick_login():
    """Quick SSO login flow."""
    clear_screen()
    print_header("AWS Login")
    
    configured = get_configured_profiles()
    
    if not configured:
        print("No environments configured. Add one first.")
        confirm_continue()
        return
    
    choices = [
        questionary.Choice(
            f"{name} ({cfg.get('aws_profile')} - {cfg.get('aws_region')})",
            value=name
        )
        for name, cfg in configured.items()
    ]
    choices.append(questionary.Choice("← Back", value=None))
    
    env = questionary.select(
        "Select environment to login:",
        choices=choices,
        style=custom_style,
    ).ask()
    
    if env is None:
        return
    
    aws_profile = configured[env].get("aws_profile")
    
    # Check if SSO profile
    aws_profiles = scan_aws_profiles()
    profile_data = next((p for p in aws_profiles if p["name"] == aws_profile), None)
    
    if profile_data and profile_data.get("is_sso"):
        if sso_login(aws_profile):
            print("\n✅ SSO login successful!")
            identity = get_caller_identity(aws_profile)
            if identity:
                print(f"   Account: {identity['account_id']}")
                print(f"   ARN:     {identity['arn']}")
        else:
            print("\n❌ SSO login failed")
    else:
        print(f"\nProfile '{aws_profile}' is not an SSO profile.")
        print("Checking credentials...")
        identity = get_caller_identity(aws_profile)
        if identity:
            print(f"✅ Credentials valid")
            print(f"   Account: {identity['account_id']}")
            print(f"   ARN:     {identity['arn']}")
        else:
            print("❌ Credentials not valid or expired")
    
    print_command_hint(f"manage.py profiles login {env}")
    confirm_continue()


def deploy_menu():
    """Quick deploy flow."""
    clear_screen()
    print_header("Deploy")
    
    configured = get_configured_profiles()
    
    if not configured:
        print("No environments configured. Run Quick Start first.")
        confirm_continue()
        return
    
    # Select environment
    env_choices = [
        questionary.Choice(f"{name} ({cfg.get('aws_region')})", value=name)
        for name, cfg in configured.items()
    ]
    env_choices.append(questionary.Choice("← Back", value=None))
    
    env = questionary.select(
        "Select environment:",
        choices=env_choices,
        style=custom_style,
    ).ask()
    
    if env is None:
        return
    
    # Select action
    action = questionary.select(
        "What would you like to do?",
        choices=[
            questionary.Choice("📋 Plan - See what would be deployed", value="plan"),
            questionary.Choice("📤 Publish - Upload templates to Service Catalog", value="publish"),
            questionary.Choice("🚀 Deploy - Deploy CloudFormation stacks", value="deploy"),
            questionary.Choice("📊 Status - Check deployment status", value="status"),
            questionary.Choice("← Back", value=None),
        ],
        style=custom_style,
    ).ask()
    
    if action is None:
        return deploy_menu()
    
    cmd_map = {
        "plan": f"deploy.py plan -e {env}",
        "publish": f"deploy.py publish -e {env}",
        "deploy": f"deploy.py deploy -e {env}",
        "status": f"deploy.py status -e {env}",
    }
    
    if action in cmd_map:
        dry_run = False
        if action in ["publish", "deploy"]:
            dry_run = questionary.confirm(
                "Dry run first?",
                default=True,
                style=custom_style
            ).ask()
        
        cmd = cmd_map[action]
        if dry_run:
            cmd += " --dry-run"
        
        print(f"\nRunning: python scripts/{cmd}\n")
        print("-" * 60)
        
        script_path = get_project_root() / "scripts" / cmd.split()[0]
        args = cmd.split()[1:]
        subprocess.run([sys.executable, str(script_path)] + args)
        
        print_command_hint(cmd)
    
    confirm_continue()


# ============== PROFILES MENU ==============


def profiles_menu():
    """Profiles management menu."""
    while True:
        clear_screen()
        print_header("Profiles Management")
        
        action = questionary.select(
            "Select action:",
            choices=[
                questionary.Choice("📋 List configured environments", value="list"),
                questionary.Choice("🔍 Scan available AWS profiles", value="scan"),
                questionary.Choice("➕ Add new environment", value="add"),
                questionary.Choice("🔐 Login (SSO)", value="login"),
                questionary.Choice("👤 Who am I?", value="whoami"),
                questionary.Separator(),
                questionary.Choice("← Back to main menu", value="back"),
            ],
            style=custom_style,
        ).ask()
        
        if action is None or action == "back":
            return
        elif action == "list":
            clear_screen()
            cmd_profiles_list()
            print_command_hint("manage.py profiles list")
            confirm_continue()
        elif action == "scan":
            clear_screen()
            cmd_profiles_scan()
            print_command_hint("manage.py profiles scan")
            confirm_continue()
        elif action == "add":
            clear_screen()
            interactive_add_profile()
        elif action == "login":
            quick_login()
        elif action == "whoami":
            interactive_whoami()


def interactive_add_profile():
    """Interactive profile addition."""
    print_header("Add Environment")
    
    # Get environment name
    name = questionary.text(
        "Environment name (e.g., dev, staging, prod):",
        style=custom_style,
    ).ask()
    
    if not name:
        return
    
    # Check if exists
    existing = get_configured_profiles()
    if name in existing:
        if not questionary.confirm(
            f"Environment '{name}' already exists. Update it?",
            default=False,
            style=custom_style
        ).ask():
            return
    
    # Scan AWS profiles
    aws_profiles = scan_aws_profiles()
    
    if not aws_profiles:
        print("\n❌ No AWS profiles found in ~/.aws/config")
        print("   Configure AWS CLI first: aws configure sso")
        confirm_continue()
        return
    
    # Select AWS profile
    profile_choices = [
        questionary.Choice(
            f"{p['name']}" + (" (SSO)" if p['is_sso'] else "") + 
            (f" - {p['region']}" if p['region'] else ""),
            value=p['name']
        )
        for p in aws_profiles
    ]
    
    aws_profile = questionary.select(
        "Select AWS profile:",
        choices=profile_choices,
        style=custom_style,
    ).ask()
    
    if not aws_profile:
        return
    
    # Get profile details
    aws_profile_data = next((p for p in aws_profiles if p["name"] == aws_profile), {})
    
    # Region
    default_region = aws_profile_data.get("region") or "eu-west-1"
    region = questionary.text(
        "AWS region:",
        default=default_region,
        style=custom_style,
    ).ask()
    
    # Account ID
    account_id = aws_profile_data.get("sso_account_id", "")
    
    if not account_id:
        print("\nFetching account ID from AWS...")
        identity = get_caller_identity(aws_profile)
        if identity:
            account_id = identity["account_id"]
            print(f"✅ Found: {account_id}")
        else:
            print("⚠️  Could not fetch (credentials may be expired)")
            account_id = questionary.text(
                "Enter AWS account ID:",
                style=custom_style,
            ).ask() or ""
    
    # Save
    profiles_path = get_project_root() / "profiles.yaml"
    
    if profiles_path.exists():
        with open(profiles_path) as f:
            data = yaml.safe_load(f) or {}
    else:
        data = {}
    
    data.setdefault("profiles", {})[name] = {
        "aws_profile": aws_profile,
        "aws_region": region,
        "account_id": account_id,
    }
    
    with open(profiles_path, "w") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False)
    
    print(f"\n✅ Environment '{name}' saved!")
    print(f"   AWS Profile: {aws_profile}")
    print(f"   Region:      {region}")
    print(f"   Account ID:  {account_id}")
    
    print_command_hint(f"manage.py profiles add {name}")
    confirm_continue()


def interactive_whoami():
    """Interactive who am I."""
    clear_screen()
    print_header("Who Am I?")
    
    configured = get_configured_profiles()
    
    if not configured:
        print("No environments configured.")
        confirm_continue()
        return
    
    choices = [
        questionary.Choice(f"{name} ({cfg.get('aws_profile')})", value=name)
        for name, cfg in configured.items()
    ]
    choices.append(questionary.Choice("← Back", value=None))
    
    env = questionary.select(
        "Select environment:",
        choices=choices,
        style=custom_style,
    ).ask()
    
    if env is None:
        return
    
    aws_profile = configured[env].get("aws_profile")
    cmd_profiles_whoami(env)
    
    print_command_hint(f"manage.py profiles whoami {env}")
    confirm_continue()


# ============== PORTFOLIOS MENU ==============


def portfolios_menu():
    """Portfolios management menu."""
    while True:
        clear_screen()
        print_header("Portfolios Management")
        
        action = questionary.select(
            "Select action:",
            choices=[
                questionary.Choice("📋 List portfolios", value="list"),
                questionary.Choice("➕ Add new portfolio", value="add"),
                questionary.Separator(),
                questionary.Choice("← Back to main menu", value="back"),
            ],
            style=custom_style,
        ).ask()
        
        if action is None or action == "back":
            return
        elif action == "list":
            clear_screen()
            cmd_portfolios_list()
            print_command_hint("manage.py portfolios list")
            confirm_continue()
        elif action == "add":
            clear_screen()
            interactive_add_portfolio()


def interactive_add_portfolio():
    """Interactive portfolio addition."""
    print_header("Add Portfolio")
    
    # Name
    name = questionary.text(
        "Portfolio name (e.g., security, monitoring):",
        style=custom_style,
    ).ask()
    
    if not name:
        return
    
    # Check if exists
    config = load_bootstrap_config()
    if name in config.get("portfolios", {}):
        print(f"❌ Portfolio '{name}' already exists")
        confirm_continue()
        return
    
    # Display name
    default_display = name.replace("-", " ").replace("_", " ").title()
    display_name = questionary.text(
        "Display name:",
        default=default_display,
        style=custom_style,
    ).ask()
    
    # Description
    description = questionary.text(
        "Description:",
        style=custom_style,
    ).ask()
    
    # Provider
    provider = questionary.text(
        "Provider name:",
        default="Platform Team",
        style=custom_style,
    ).ask()
    
    # Principals - derive from environment
    principals = []
    configured = get_configured_profiles()
    
    if configured:
        derive = questionary.confirm(
            "Derive principal from an environment profile?",
            default=True,
            style=custom_style
        ).ask()
        
        if derive:
            env_choices = list(configured.keys())
            env = questionary.select(
                "Select environment:",
                choices=env_choices,
                style=custom_style,
            ).ask()
            
            if env:
                aws_profile = configured[env].get("aws_profile")
                print(f"\nFetching principal from {aws_profile}...")
                principal = get_profile_principal(aws_profile)
                if principal:
                    print(f"✅ Found: {principal}")
                    principals.append(principal)
                else:
                    print("⚠️  Could not fetch principal (try logging in first)")
    
    if not principals:
        add_manual = questionary.confirm(
            "Add principals manually?",
            default=True,
            style=custom_style
        ).ask()
        
        if add_manual:
            print("\nTip: Use ${account_id} for account ID placeholder")
            while True:
                principal = questionary.text(
                    "Principal ARN (empty to finish):",
                    style=custom_style,
                ).ask()
                if not principal:
                    break
                principals.append(principal)
    
    # Tags
    tags = {}
    add_tags = questionary.confirm(
        "Add tags?",
        default=False,
        style=custom_style
    ).ask()
    
    if add_tags:
        while True:
            key = questionary.text(
                "Tag key (empty to finish):",
                style=custom_style,
            ).ask()
            if not key:
                break
            value = questionary.text(
                f"Tag value for '{key}':",
                style=custom_style,
            ).ask()
            tags[key] = value
    
    # Save
    bootstrap_path = get_project_root() / "bootstrap.yaml"
    
    with open(bootstrap_path) as f:
        config = yaml.safe_load(f)
    
    config.setdefault("portfolios", {})[name] = {
        "display_name": display_name,
        "description": description,
        "provider_name": provider,
        "principals": principals,
        "tags": tags,
    }
    
    with open(bootstrap_path, "w") as f:
        yaml.dump(config, f, default_flow_style=False, sort_keys=False)
    
    print(f"\n✅ Portfolio '{name}' added!")
    print(f"\nNext: Run bootstrap to create it in AWS:")
    
    envs = get_configured_environments()
    if envs:
        print(f"   python deployer/scripts/bootstrap.py bootstrap -e {envs[0]}")
    
    print_command_hint(f"manage.py portfolios add {name}")
    confirm_continue()


# ============== PRODUCTS MENU ==============


def products_menu():
    """Products management menu."""
    while True:
        clear_screen()
        print_header("Products Management")
        
        action = questionary.select(
            "Select action:",
            choices=[
                questionary.Choice("📋 List products", value="list"),
                questionary.Choice("➕ Add new product", value="add"),
                questionary.Choice("🌳 Show dependency graph", value="graph"),
                questionary.Separator(),
                questionary.Choice("← Back to main menu", value="back"),
            ],
            style=custom_style,
        ).ask()
        
        if action is None or action == "back":
            return
        elif action == "list":
            clear_screen()
            cmd_products_list()
            print_command_hint("manage.py products list")
            confirm_continue()
        elif action == "add":
            clear_screen()
            interactive_add_product()
        elif action == "graph":
            clear_screen()
            show_dependency_graph()
            print_command_hint("manage.py graph")
            confirm_continue()


def interactive_add_product():
    """Interactive product addition."""
    print_header("Add Product")
    
    # Name
    name = questionary.text(
        "Product name (e.g., monitoring, cache, queue):",
        style=custom_style,
    ).ask()
    
    if not name:
        return
    
    # Check if exists
    catalog = load_catalog_config()
    if name in catalog.get("products", {}):
        print(f"❌ Product '{name}' already exists")
        confirm_continue()
        return
    
    # Portfolio
    config = load_bootstrap_config()
    portfolios = list(config.get("portfolios", {}).keys())
    
    if portfolios:
        portfolio = questionary.select(
            "Select portfolio:",
            choices=portfolios + ["(none)"],
            style=custom_style,
        ).ask()
        if portfolio == "(none)":
            portfolio = ""
    else:
        portfolio = questionary.text(
            "Portfolio name (or empty):",
            style=custom_style,
        ).ask()
    
    # Dependencies
    existing_products = list(catalog.get("products", {}).keys())
    dependencies = []
    
    if existing_products:
        deps = questionary.checkbox(
            "Select dependencies:",
            choices=existing_products,
            style=custom_style,
        ).ask()
        dependencies = deps or []
    
    # Parameter mappings
    parameter_mapping = {}
    if dependencies:
        print("\n📎 Parameter Mappings")
        print("   Map outputs from dependencies to this product's parameters.\n")
        
        # Show available outputs
        for dep in dependencies:
            if dep in catalog.get("products", {}):
                outputs = catalog["products"][dep].get("outputs", [])
                if outputs:
                    print(f"   {dep} outputs: {', '.join(outputs)}")
        
        print()
        
        add_mappings = questionary.confirm(
            "Add parameter mappings?",
            default=bool(dependencies),
            style=custom_style
        ).ask()
        
        if add_mappings:
            while True:
                param = questionary.text(
                    "Parameter name (empty to finish):",
                    style=custom_style,
                ).ask()
                if not param:
                    break
                
                # Build source choices
                source_choices = []
                for dep in dependencies:
                    if dep in catalog.get("products", {}):
                        for out in catalog["products"][dep].get("outputs", []):
                            source_choices.append(f"{dep}.{out}")
                
                if source_choices:
                    source = questionary.select(
                        f"Source for '{param}':",
                        choices=source_choices + ["(enter manually)"],
                        style=custom_style,
                    ).ask()
                    
                    if source == "(enter manually)":
                        source = questionary.text(
                            "Source (format: product.output):",
                            style=custom_style,
                        ).ask()
                else:
                    source = questionary.text(
                        "Source (format: product.output):",
                        style=custom_style,
                    ).ask()
                
                if source:
                    parameter_mapping[param] = source
    
    # Outputs
    print("\n📤 Outputs")
    print("   Define outputs this product will expose.\n")
    
    outputs = []
    while True:
        output = questionary.text(
            "Output name (empty to finish):",
            style=custom_style,
        ).ask()
        if not output:
            break
        outputs.append(output)
    
    # Description
    description = questionary.text(
        "Product description:",
        style=custom_style,
    ).ask()
    
    # Create directory and files
    products_dir = get_products_root() / name
    products_dir.mkdir(parents=True, exist_ok=True)
    
    # product.yaml
    product_yaml = {
        "name": name,
        "description": description,
        "portfolio": portfolio,
        "parameters": {},
        "outputs": {o: {"description": f"{o} output", "export": True} for o in outputs},
    }
    
    for param in parameter_mapping:
        product_yaml["parameters"][param] = {
            "type": "String",
            "description": f"Mapped from {parameter_mapping[param]}",
            "required": True,
        }
    
    with open(products_dir / "product.yaml", "w") as f:
        yaml.dump(product_yaml, f, default_flow_style=False, sort_keys=False)
    
    # template.yaml
    template = f"""AWSTemplateFormatVersion: '2010-09-09'
Description: {description or f'CloudFormation template for {name}'}

Parameters:
  Environment:
    Type: String
    Default: dev
"""
    
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
    
    catalog.setdefault("products", {})[name] = product_config
    
    catalog_path = get_project_root() / "catalog.yaml"
    with open(catalog_path, "w") as f:
        yaml.dump(catalog, f, default_flow_style=False, sort_keys=False)
    
    print(f"\n✅ Product '{name}' created!")
    print(f"   • products/{name}/product.yaml")
    print(f"   • products/{name}/template.yaml")
    print(f"   • catalog.yaml updated")
    print(f"\nNext steps:")
    print(f"   1. Edit products/{name}/template.yaml")
    print(f"   2. python deployer/scripts/deploy.py publish -e dev -p {name}")
    
    print_command_hint(f"manage.py products add {name}")
    confirm_continue()


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
    
    printed = set()
    
    def print_tree(name: str, prefix: str = "", is_last: bool = True):
        if name in printed:
            connector = "└── " if is_last else "├── "
            print(f"{prefix}{connector}{name} ↩️ (circular)")
            return
        
        printed.add(name)
        connector = "└── " if is_last else "├── "
        
        cfg = products.get(name, {})
        outputs = len(cfg.get("outputs", []))
        portfolio = cfg.get("portfolio", "")
        
        icon = "📦"
        print(f"{prefix}{connector}{icon} {name} [{portfolio}] ({outputs} outputs)")
        
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
        print("⚠️  No root products found (possible circular dependencies)")
        for name in products:
            print(f"  • {name}")
    
    print("\nLegend: 📦 product [portfolio] (output count)")


def show_status():
    """Show overall status."""
    clear_screen()
    print_header("Status Overview")
    
    # Profiles
    print("👤 PROFILES")
    configured = get_configured_profiles()
    if configured:
        for name, cfg in configured.items():
            print(f"   • {name}: {cfg.get('aws_profile')} ({cfg.get('aws_region')})")
    else:
        print("   (none configured)")
    
    # Portfolios
    print("\n📁 PORTFOLIOS")
    config = load_bootstrap_config()
    portfolios = config.get("portfolios", {})
    if portfolios:
        for name in portfolios:
            print(f"   • {name}")
    else:
        print("   (none configured)")
    
    # Products
    print("\n📦 PRODUCTS")
    catalog = load_catalog_config()
    products = catalog.get("products", {})
    if products:
        for name, cfg in products.items():
            deps = cfg.get("dependencies", [])
            dep_str = f" → {', '.join(deps)}" if deps else ""
            print(f"   • {name}{dep_str}")
    else:
        print("   (none configured)")
    
    # Bootstrap state
    print("\n🏗️  BOOTSTRAP")
    bootstrap_state_path = get_project_root() / ".bootstrap-state.json"
    if bootstrap_state_path.exists():
        with open(bootstrap_state_path) as f:
            state = json.load(f)
        envs = state.get("environments", {})
        if envs:
            for env, data in envs.items():
                ts = data.get("bootstrapped_at", "unknown")[:19]
                print(f"   • {env}: ✅ {ts}")
        else:
            print("   (not bootstrapped)")
    else:
        print("   (not bootstrapped)")
    
    # Deploy state
    print("\n🚀 DEPLOYMENTS")
    deploy_state_path = get_project_root() / ".deploy-state.json"
    if deploy_state_path.exists():
        with open(deploy_state_path) as f:
            state = json.load(f)
        envs = state.get("environments", {})
        if envs:
            for env, env_state in envs.items():
                print(f"   {env}:")
                for product, pstate in env_state.items():
                    version = pstate.get("version", "-")
                    if pstate.get("deployed_commit"):
                        status = "✅"
                    elif pstate.get("published_commit"):
                        status = "📤 (published)"
                    else:
                        status = "⏳"
                    print(f"      • {product}: {version} {status}")
        else:
            print("   (no deployments)")
    else:
        print("   (no deployments)")
    
    print_command_hint("manage.py status")
    confirm_continue()


# ============== NON-INTERACTIVE COMMANDS ==============


def cmd_profiles_list():
    """List profiles configured in profiles.yaml."""
    print_header("Configured Profiles")
    
    configured = get_configured_profiles()
    
    if not configured:
        print("No profiles configured in profiles.yaml")
        print("\nTo add: python scripts/manage.py profiles add <name>")
        return
    
    print(f"{'Name':<15} {'AWS Profile':<25} {'Region':<15} {'Account ID'}")
    print("-" * 70)
    
    for name, cfg in configured.items():
        print(
            f"{name:<15} "
            f"{cfg.get('aws_profile', '-'):<25} "
            f"{cfg.get('aws_region', '-'):<15} "
            f"{cfg.get('account_id', '-')}"
        )


def cmd_profiles_scan():
    """Scan available AWS profiles from ~/.aws/config."""
    print_header("Available AWS Profiles")
    
    profiles = scan_aws_profiles()
    
    if not profiles:
        print("No AWS profiles found in ~/.aws/config or ~/.aws/credentials")
        print("\nConfigure AWS CLI: aws configure sso")
        return
    
    print(f"{'Name':<25} {'Region':<15} {'SSO':<5} {'Account':<15} {'Source'}")
    print("-" * 75)
    
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


def cmd_profiles_add(env_name: str, aws_profile: str = None):
    """Add or update a profile in profiles.yaml."""
    # This is handled by interactive_add_profile() in interactive mode
    # For CLI mode, use simpler prompts
    
    profiles_path = get_project_root() / "profiles.yaml"
    
    if profiles_path.exists():
        with open(profiles_path) as f:
            data = yaml.safe_load(f) or {}
    else:
        data = {}
    
    if "profiles" not in data:
        data["profiles"] = {}
    
    aws_profiles = scan_aws_profiles()
    aws_profile_names = [p["name"] for p in aws_profiles]
    
    if not aws_profile:
        print("\nAvailable AWS profiles:")
        for i, p in enumerate(aws_profiles, 1):
            sso = " (SSO)" if p["is_sso"] else ""
            print(f"  {i}. {p['name']}{sso}")
        
        choice = input("\nSelect number or enter name: ").strip()
        
        if choice.isdigit():
            idx = int(choice) - 1
            if 0 <= idx < len(aws_profiles):
                aws_profile = aws_profiles[idx]["name"]
            else:
                print("Invalid selection")
                return
        else:
            aws_profile = choice
    
    aws_profile_data = next((p for p in aws_profiles if p["name"] == aws_profile), {})
    
    region = aws_profile_data.get("region") or input("AWS region [eu-west-1]: ").strip() or "eu-west-1"
    account_id = aws_profile_data.get("sso_account_id", "")
    
    if not account_id:
        identity = get_caller_identity(aws_profile)
        if identity:
            account_id = identity["account_id"]
        else:
            account_id = input("AWS account ID: ").strip()
    
    data["profiles"][env_name] = {
        "aws_profile": aws_profile,
        "aws_region": region,
        "account_id": account_id,
    }
    
    with open(profiles_path, "w") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False)
    
    print(f"\n✅ Profile '{env_name}' saved")


def cmd_profiles_login(env_name: str):
    """Login to AWS SSO for a profile."""
    configured = get_configured_profiles()
    
    if env_name in configured:
        aws_profile = configured[env_name].get("aws_profile")
    else:
        aws_profile = env_name
    
    aws_profiles = scan_aws_profiles()
    profile_data = next((p for p in aws_profiles if p["name"] == aws_profile), None)
    
    if profile_data and profile_data.get("is_sso"):
        sso_login(aws_profile)
    else:
        print(f"Profile '{aws_profile}' is not an SSO profile")
        identity = get_caller_identity(aws_profile)
        if identity:
            print(f"✅ Credentials valid: {identity['account_id']}")
        else:
            print("❌ Credentials invalid or expired")


def cmd_profiles_whoami(env_name: str = None):
    """Show current AWS identity for a profile."""
    if env_name:
        configured = get_configured_profiles()
        if env_name in configured:
            aws_profile = configured[env_name].get("aws_profile")
        else:
            aws_profile = env_name
    else:
        aws_profile = None
    
    print(f"\nAWS Identity: {aws_profile or 'default'}")
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
        print("❌ Could not retrieve identity")


def cmd_portfolios_list():
    """List portfolios in bootstrap.yaml."""
    print_header("Portfolios")
    
    config = load_bootstrap_config()
    portfolios = config.get("portfolios", {})
    
    if not portfolios:
        print("No portfolios configured")
        return
    
    for name, cfg in portfolios.items():
        print(f"📁 {name}")
        print(f"   Display:     {cfg.get('display_name', '-')}")
        print(f"   Description: {cfg.get('description', '-')}")
        print(f"   Provider:    {cfg.get('provider_name', '-')}")
        principals = cfg.get("principals", [])
        print(f"   Principals:  {len(principals)}")
        print()


def cmd_portfolios_add(name: str, env_name: str = None):
    """Add a new portfolio - CLI version."""
    # Simplified for CLI
    bootstrap_path = get_project_root() / "bootstrap.yaml"
    
    with open(bootstrap_path) as f:
        config = yaml.safe_load(f)
    
    if name in config.get("portfolios", {}):
        print(f"Portfolio '{name}' already exists")
        return
    
    display_name = input(f"Display name [{name.title()}]: ").strip() or name.title()
    description = input("Description: ").strip()
    
    principals = []
    if env_name:
        configured = get_configured_profiles()
        if env_name in configured:
            aws_profile = configured[env_name].get("aws_profile")
            principal = get_profile_principal(aws_profile)
            if principal:
                principals.append(principal)
                print(f"Added principal: {principal}")
    
    config.setdefault("portfolios", {})[name] = {
        "display_name": display_name,
        "description": description,
        "provider_name": "Platform Team",
        "principals": principals,
        "tags": {},
    }
    
    with open(bootstrap_path, "w") as f:
        yaml.dump(config, f, default_flow_style=False, sort_keys=False)
    
    print(f"\n✅ Portfolio '{name}' added")


def cmd_products_list():
    """List products in catalog.yaml."""
    print_header("Products")
    
    catalog = load_catalog_config()
    products = catalog.get("products", {})
    
    if not products:
        print("No products configured")
        return
    
    print(f"{'Name':<20} {'Portfolio':<20} {'Dependencies':<25} {'Outputs'}")
    print("-" * 80)
    
    for name, cfg in products.items():
        deps = ", ".join(cfg.get("dependencies", [])) or "-"
        outputs = len(cfg.get("outputs", []))
        portfolio = cfg.get("portfolio", "-")
        print(f"{name:<20} {portfolio:<20} {deps:<25} {outputs}")


def cmd_products_add(name: str):
    """Add a new product - CLI version."""
    # For CLI, delegate to interactive version
    interactive_add_product()


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

Commands:
    python manage.py profiles list|scan|add|login|whoami
    python manage.py portfolios list|add
    python manage.py products list|add
    python manage.py status
    python manage.py graph
        """,
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    
    # profiles
    profiles_parser = subparsers.add_parser("profiles", help="Manage AWS profiles")
    profiles_sub = profiles_parser.add_subparsers(dest="subcommand", required=True)
    
    profiles_sub.add_parser("list", help="List configured profiles")
    profiles_sub.add_parser("scan", help="Scan available AWS profiles")
    
    profiles_add = profiles_sub.add_parser("add", help="Add a profile")
    profiles_add.add_argument("name", help="Environment name")
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
    portfolios_add.add_argument("-e", "--environment", help="Environment for principal")
    
    # products
    products_parser = subparsers.add_parser("products", help="Manage products")
    products_sub = products_parser.add_subparsers(dest="subcommand", required=True)
    
    products_sub.add_parser("list", help="List products")
    products_add = products_sub.add_parser("add", help="Add a product")
    products_add.add_argument("name", help="Product name")
    
    # status & graph
    subparsers.add_parser("status", help="Show overall status")
    subparsers.add_parser("graph", help="Show dependency graph")
    
    args = parser.parse_args()
    
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
        show_status()
    
    elif args.command == "graph":
        show_dependency_graph()


if __name__ == "__main__":
    main()
