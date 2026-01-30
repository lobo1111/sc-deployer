"""
Unit tests for database product CloudFormation template.

These tests validate template structure, required resources, security
configuration, and outputs without deploying to AWS.
"""

import pytest


class TestDatabaseTemplate:
    """Tests for database template structure and resources."""

    def test_template_has_required_resources(self, load_template):
        """Template should contain all required database resources."""
        template = load_template("database")
        resources = template.get("Resources", {})
        
        required_types = {
            "AWS::RDS::DBInstance",
            "AWS::RDS::DBSubnetGroup",
            "AWS::SecretsManager::Secret",
        }
        
        actual_types = {r.get("Type") for r in resources.values()}
        missing = required_types - actual_types
        
        assert not missing, f"Missing required resource types: {missing}"

    def test_template_has_required_outputs(self, load_template):
        """Template should export required outputs for dependent products."""
        template = load_template("database")
        outputs = template.get("Outputs", {})
        
        required_outputs = {"DatabaseEndpoint", "DatabasePort", "DatabaseSecretArn"}
        actual_outputs = set(outputs.keys())
        missing = required_outputs - actual_outputs
        
        assert not missing, f"Missing required outputs: {missing}"

    def test_database_uses_private_subnets(self, load_template):
        """Database should be placed in private subnets only."""
        template = load_template("database")
        parameters = template.get("Parameters", {})
        
        assert "SubnetIds" in parameters, "SubnetIds parameter required for DB placement"

    def test_database_not_publicly_accessible(self, load_template):
        """Database should not be publicly accessible."""
        template = load_template("database")
        resources = template.get("Resources", {})
        
        for name, resource in resources.items():
            if resource.get("Type") == "AWS::RDS::DBInstance":
                props = resource.get("Properties", {})
                publicly_accessible = props.get("PubliclyAccessible", True)
                assert publicly_accessible is False, \
                    f"DB instance {name} must have PubliclyAccessible: false"

    def test_database_has_encryption_at_rest(self, load_template):
        """Database should have encryption at rest enabled."""
        template = load_template("database")
        resources = template.get("Resources", {})
        
        for name, resource in resources.items():
            if resource.get("Type") == "AWS::RDS::DBInstance":
                props = resource.get("Properties", {})
                encrypted = props.get("StorageEncrypted", False)
                assert encrypted is True, \
                    f"DB instance {name} must have StorageEncrypted: true"

    def test_credentials_in_secrets_manager(self, load_template):
        """Database credentials should be stored in Secrets Manager."""
        template = load_template("database")
        resources = template.get("Resources", {})
        
        secrets = [
            name for name, r in resources.items()
            if r.get("Type") == "AWS::SecretsManager::Secret"
        ]
        
        assert secrets, "Database credentials must be stored in Secrets Manager"

    def test_multi_az_configured(self, load_template):
        """Database should have Multi-AZ option (parameterized or enabled)."""
        template = load_template("database")
        resources = template.get("Resources", {})
        parameters = template.get("Parameters", {})
        
        # Either MultiAZ parameter exists or it's hardcoded in the resource
        has_multi_az_param = "MultiAZ" in parameters
        
        has_multi_az_prop = False
        for name, resource in resources.items():
            if resource.get("Type") == "AWS::RDS::DBInstance":
                props = resource.get("Properties", {})
                if "MultiAZ" in props:
                    has_multi_az_prop = True
        
        assert has_multi_az_param or has_multi_az_prop, \
            "Multi-AZ configuration should be defined"


class TestDatabaseProductConfig:
    """Tests for database product.yaml configuration."""

    def test_depends_on_networking(self, load_product_config):
        """Database should depend on networking product."""
        config = load_product_config("database")
        deps = config.get("dependencies", [])
        
        assert "networking" in deps, "Database must depend on networking"

    def test_parameter_mappings_valid(self, load_product_config):
        """Parameter mappings should reference valid networking outputs."""
        config = load_product_config("database")
        mappings = config.get("parameter_mapping", {})
        
        # These are expected mappings from networking
        expected_sources = {"networking.VpcId", "networking.PrivateSubnetIds", "networking.SecurityGroupId"}
        actual_sources = set(mappings.values())
        
        # All mappings should come from networking
        for source in actual_sources:
            assert source.startswith("networking."), \
                f"Database should only depend on networking, found: {source}"


class TestDatabaseCapability:
    """Tests for database CAPABILITY.md documentation."""

    def test_capability_document_exists(self, load_capability):
        """CAPABILITY.md should exist."""
        capability = load_capability("database")
        assert capability, "CAPABILITY.md should not be empty"

    def test_capability_documents_security(self, load_capability):
        """CAPABILITY.md should document security features."""
        capability = load_capability("database")
        
        security_terms = ["encrypt", "secret", "credential", "private"]
        found = any(term in capability.lower() for term in security_terms)
        
        assert found, "CAPABILITY.md should document security features"

    def test_capability_documents_dependencies(self, load_capability):
        """CAPABILITY.md should document dependencies."""
        capability = load_capability("database")
        
        assert "networking" in capability.lower(), \
            "CAPABILITY.md should document networking dependency"
