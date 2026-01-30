#!/usr/bin/env python3
"""
Shared configuration loader for SC Deployer.
"""

from pathlib import Path

import yaml


def get_deployer_root() -> Path:
    """Get deployer directory (configs, scripts)."""
    return Path(__file__).parent.parent


def get_repo_root() -> Path:
    """Get repository root directory."""
    return Path(__file__).parent.parent.parent


def get_products_root() -> Path:
    """Get products directory (in repo root)."""
    return get_repo_root() / "products"


# Alias for backward compatibility
def get_project_root() -> Path:
    """Alias for get_deployer_root."""
    return get_deployer_root()


def load_profiles(profiles_file: str = "profiles.yaml") -> dict:
    """Load profiles from profiles.yaml."""
    path = get_project_root() / profiles_file
    if not path.exists():
        return {"profiles": {}}
    with open(path) as f:
        return yaml.safe_load(f)


def load_config_with_profiles(config_path: Path) -> dict:
    """Load a config file and merge in profiles from profiles.yaml."""
    with open(config_path) as f:
        config = yaml.safe_load(f)

    # Load profiles from separate file
    profiles_file = config.get("settings", {}).get("profiles_file", "profiles.yaml")
    profiles_data = load_profiles(profiles_file)

    # Merge profiles into config
    config["profiles"] = profiles_data.get("profiles", {})

    return config


def load_bootstrap_config(path: Path = None) -> dict:
    """Load bootstrap configuration with profiles."""
    if path is None:
        path = get_project_root() / "bootstrap.yaml"
    return load_config_with_profiles(path)


def load_catalog_config(path: Path = None) -> dict:
    """Load catalog configuration with profiles."""
    if path is None:
        path = get_project_root() / "catalog.yaml"
    if not path.exists():
        return {"profiles": {}, "products": {}}
    return load_config_with_profiles(path)


def get_environment_config(config: dict, environment: str) -> dict:
    """Get configuration for a specific environment."""
    env_config = config.get("profiles", {}).get(environment, {})
    if not env_config:
        raise ValueError(
            f"Environment '{environment}' not found in profiles.yaml"
        )
    return env_config
