"""
Unit tests for api product CloudFormation template.

These tests validate template structure, required resources, security
configuration, and outputs without deploying to AWS.
"""

import pytest


class TestApiTemplate:
    """Tests for api template structure and resources."""

    def test_template_has_required_resources(self, load_template):
        """Template should contain all required API resources."""
        template = load_template("api")
        resources = template.get("Resources", {})
        
        required_types = {
            "AWS::ApiGateway::RestApi",
            "AWS::Lambda::Function",
            "AWS::IAM::Role",  # Lambda execution role
        }
        
        actual_types = {r.get("Type") for r in resources.values()}
        missing = required_types - actual_types
        
        assert not missing, f"Missing required resource types: {missing}"

    def test_template_has_required_outputs(self, load_template):
        """Template should export required outputs."""
        template = load_template("api")
        outputs = template.get("Outputs", {})
        
        required_outputs = {"ApiEndpoint", "ApiKey"}
        actual_outputs = set(outputs.keys())
        missing = required_outputs - actual_outputs
        
        assert not missing, f"Missing required outputs: {missing}"

    def test_lambda_in_vpc(self, load_template):
        """Lambda function should be configured for VPC access."""
        template = load_template("api")
        resources = template.get("Resources", {})
        
        for name, resource in resources.items():
            if resource.get("Type") == "AWS::Lambda::Function":
                props = resource.get("Properties", {})
                vpc_config = props.get("VpcConfig", {})
                
                assert vpc_config, f"Lambda {name} must have VpcConfig for database access"
                assert "SubnetIds" in vpc_config or "Fn::Ref" in str(vpc_config), \
                    f"Lambda {name} must specify SubnetIds"

    def test_lambda_has_execution_role(self, load_template):
        """Lambda function should have an execution role."""
        template = load_template("api")
        resources = template.get("Resources", {})
        
        # Find Lambda functions
        for name, resource in resources.items():
            if resource.get("Type") == "AWS::Lambda::Function":
                props = resource.get("Properties", {})
                assert "Role" in props, f"Lambda {name} must have an execution Role"

    def test_api_gateway_has_deployment(self, load_template):
        """API Gateway should have a deployment and stage."""
        template = load_template("api")
        resources = template.get("Resources", {})
        
        resource_types = {r.get("Type") for r in resources.values()}
        
        assert "AWS::ApiGateway::Deployment" in resource_types, \
            "API Gateway deployment is required"
        assert "AWS::ApiGateway::Stage" in resource_types, \
            "API Gateway stage is required"

    def test_lambda_has_logging(self, load_template):
        """Lambda should have CloudWatch logging configured."""
        template = load_template("api")
        resources = template.get("Resources", {})
        
        # Check for log group or logging in Lambda config
        has_log_group = any(
            r.get("Type") == "AWS::Logs::LogGroup"
            for r in resources.values()
        )
        
        # Logging is automatically created, but explicit is better
        # This is a soft check - warn if no explicit log group
        if not has_log_group:
            pytest.skip("Consider adding explicit CloudWatch Log Group for Lambda")


class TestApiProductConfig:
    """Tests for api product.yaml configuration."""

    def test_depends_on_networking_and_database(self, load_product_config):
        """API should depend on both networking and database."""
        config = load_product_config("api")
        deps = set(config.get("dependencies", []))
        
        assert "networking" in deps, "API must depend on networking"
        assert "database" in deps, "API must depend on database"

    def test_parameter_mappings_complete(self, load_product_config):
        """Parameter mappings should include all dependency outputs needed."""
        config = load_product_config("api")
        mappings = config.get("parameter_mapping", {})
        
        # Required mappings
        required_params = {"VpcId", "SubnetIds", "DbEndpoint", "DbPort", "DbSecretArn"}
        actual_params = set(mappings.keys())
        missing = required_params - actual_params
        
        assert not missing, f"Missing parameter mappings: {missing}"

    def test_outputs_declared(self, load_product_config):
        """API should declare its outputs."""
        config = load_product_config("api")
        outputs = config.get("outputs", [])
        
        output_names = {o["name"] if isinstance(o, dict) else o for o in outputs}
        
        assert "ApiEndpoint" in output_names, "ApiEndpoint output required"
        assert "ApiKey" in output_names, "ApiKey output required"


class TestApiSecurity:
    """Security-focused tests for api product."""

    def test_lambda_not_using_admin_policy(self, load_template):
        """Lambda execution role should not use AdministratorAccess."""
        template = load_template("api")
        resources = template.get("Resources", {})
        
        for name, resource in resources.items():
            if resource.get("Type") == "AWS::IAM::Role":
                props = resource.get("Properties", {})
                managed_policies = props.get("ManagedPolicyArns", [])
                
                for policy in managed_policies:
                    policy_str = str(policy)
                    assert "AdministratorAccess" not in policy_str, \
                        f"Role {name} should not use AdministratorAccess"

    def test_api_gateway_has_throttling(self, load_template):
        """API Gateway should have throttling configured."""
        template = load_template("api")
        resources = template.get("Resources", {})
        
        # Check for usage plan or method settings with throttling
        has_usage_plan = any(
            r.get("Type") == "AWS::ApiGateway::UsagePlan"
            for r in resources.values()
        )
        
        has_method_settings = False
        for name, resource in resources.items():
            if resource.get("Type") == "AWS::ApiGateway::Stage":
                props = resource.get("Properties", {})
                if "MethodSettings" in props:
                    has_method_settings = True
        
        assert has_usage_plan or has_method_settings, \
            "API Gateway should have throttling via UsagePlan or MethodSettings"


class TestApiCapability:
    """Tests for api CAPABILITY.md documentation."""

    def test_capability_document_exists(self, load_capability):
        """CAPABILITY.md should exist."""
        capability = load_capability("api")
        assert capability, "CAPABILITY.md should not be empty"

    def test_capability_documents_endpoints(self, load_capability):
        """CAPABILITY.md should document API capabilities."""
        capability = load_capability("api")
        
        assert "endpoint" in capability.lower() or "api" in capability.lower(), \
            "CAPABILITY.md should document API endpoints"

    def test_capability_documents_all_dependencies(self, load_capability, load_product_config):
        """CAPABILITY.md should document all dependencies."""
        capability = load_capability("api")
        config = load_product_config("api")
        
        for dep in config.get("dependencies", []):
            assert dep in capability.lower(), \
                f"CAPABILITY.md should document dependency on {dep}"
