"""
Pytest configuration and shared fixtures for product tests.
"""

import os
from pathlib import Path

import pytest
import yaml


def get_project_root() -> Path:
    """Get the project root directory."""
    return Path(__file__).parent.parent


def get_products_root() -> Path:
    """Get the products directory."""
    return get_project_root() / "products"


@pytest.fixture
def project_root() -> Path:
    """Fixture providing project root path."""
    return get_project_root()


@pytest.fixture
def products_root() -> Path:
    """Fixture providing products directory path."""
    return get_products_root()


@pytest.fixture
def load_template():
    """Factory fixture to load a product's CloudFormation template."""
    def _load(product_name: str) -> dict:
        template_path = get_products_root() / product_name / "template.yaml"
        with open(template_path) as f:
            return yaml.safe_load(f)
    return _load


@pytest.fixture
def load_product_config():
    """Factory fixture to load a product's configuration."""
    def _load(product_name: str) -> dict:
        config_path = get_products_root() / product_name / "product.yaml"
        with open(config_path) as f:
            return yaml.safe_load(f)
    return _load


@pytest.fixture
def load_capability():
    """Factory fixture to load a product's capability document."""
    def _load(product_name: str) -> str:
        capability_path = get_products_root() / product_name / "CAPABILITY.md"
        with open(capability_path) as f:
            return f.read()
    return _load


# Integration test fixtures

@pytest.fixture
def aws_session():
    """
    Fixture providing AWS session for integration tests.
    Requires AWS_PROFILE and AWS_REGION environment variables.
    """
    import boto3
    
    profile = os.environ.get("AWS_PROFILE", "default")
    region = os.environ.get("AWS_REGION", "eu-west-1")
    
    return boto3.Session(profile_name=profile, region_name=region)


@pytest.fixture
def environment():
    """Get target environment from environment variable."""
    return os.environ.get("TEST_ENVIRONMENT", "dev")


@pytest.fixture
def deploy_state(project_root, environment):
    """Load deployment state for the target environment."""
    import json
    
    state_file = project_root / ".deploy-state.json"
    if not state_file.exists():
        pytest.skip("No deploy state found - run deployment first")
    
    with open(state_file) as f:
        state = json.load(f)
    
    env_state = state.get("environments", {}).get(environment, {})
    if not env_state:
        pytest.skip(f"No deployment state for environment: {environment}")
    
    return env_state
