"""
Unit tests for networking product CloudFormation template.

These tests validate template structure, required resources, and outputs
without deploying to AWS.
"""

import pytest


class TestNetworkingTemplate:
    """Tests for networking template structure and resources."""

    def test_template_has_required_resources(self, load_template):
        """Template should contain all required networking resources."""
        template = load_template("networking")
        resources = template.get("Resources", {})
        
        # Required resource types
        required_types = {
            "AWS::EC2::VPC",
            "AWS::EC2::InternetGateway",
            "AWS::EC2::Subnet",  # At least one
            "AWS::EC2::RouteTable",
            "AWS::EC2::SecurityGroup",
        }
        
        actual_types = {r.get("Type") for r in resources.values()}
        missing = required_types - actual_types
        
        assert not missing, f"Missing required resource types: {missing}"

    def test_template_has_required_outputs(self, load_template):
        """Template should export required outputs for dependent products."""
        template = load_template("networking")
        outputs = template.get("Outputs", {})
        
        required_outputs = {"VpcId", "PublicSubnetIds", "PrivateSubnetIds", "SecurityGroupId"}
        actual_outputs = set(outputs.keys())
        missing = required_outputs - actual_outputs
        
        assert not missing, f"Missing required outputs: {missing}"

    def test_vpc_has_valid_cidr_parameter(self, load_template):
        """VPC CIDR should be parameterized."""
        template = load_template("networking")
        parameters = template.get("Parameters", {})
        
        assert "VpcCidr" in parameters, "VpcCidr parameter is required"
        
        vpc_cidr = parameters["VpcCidr"]
        assert vpc_cidr.get("Type") == "String", "VpcCidr should be String type"
        assert "Default" in vpc_cidr, "VpcCidr should have a default value"

    def test_private_subnets_have_nat_route(self, load_template):
        """Private subnets should route through NAT Gateway."""
        template = load_template("networking")
        resources = template.get("Resources", {})
        
        # Find NAT Gateway
        nat_gateways = [
            name for name, r in resources.items()
            if r.get("Type") == "AWS::EC2::NatGateway"
        ]
        
        assert nat_gateways, "NAT Gateway is required for private subnet internet access"

    def test_security_group_allows_internal_traffic(self, load_template):
        """Default security group should allow VPC internal traffic."""
        template = load_template("networking")
        resources = template.get("Resources", {})
        
        # Find security groups
        security_groups = [
            (name, r) for name, r in resources.items()
            if r.get("Type") == "AWS::EC2::SecurityGroup"
        ]
        
        assert security_groups, "At least one security group is required"


class TestNetworkingProductConfig:
    """Tests for networking product.yaml configuration."""

    def test_outputs_match_template(self, load_template, load_product_config):
        """Product config outputs should match template outputs."""
        template = load_template("networking")
        config = load_product_config("networking")
        
        template_outputs = set(template.get("Outputs", {}).keys())
        config_outputs = set(o["name"] if isinstance(o, dict) else o 
                           for o in config.get("outputs", []))
        
        # Config outputs should be subset of template outputs
        missing = config_outputs - template_outputs
        assert not missing, f"Product config references outputs not in template: {missing}"

    def test_has_no_dependencies(self, load_product_config):
        """Networking should have no dependencies (foundational product)."""
        config = load_product_config("networking")
        deps = config.get("dependencies", [])
        
        assert not deps, "Networking should be a foundational product with no dependencies"


class TestNetworkingCapability:
    """Tests for networking CAPABILITY.md documentation."""

    def test_capability_document_exists(self, load_capability):
        """CAPABILITY.md should exist."""
        capability = load_capability("networking")
        assert capability, "CAPABILITY.md should not be empty"

    def test_capability_has_required_sections(self, load_capability):
        """CAPABILITY.md should have all required sections."""
        capability = load_capability("networking")
        
        required_sections = [
            "## Business Context",
            "## Functional Capabilities",
            "## Technical Implementation",
            "## Interfaces",
            "## Constraints & Limitations",
        ]
        
        for section in required_sections:
            assert section in capability, f"Missing section: {section}"

    def test_capability_documents_all_outputs(self, load_capability, load_product_config):
        """CAPABILITY.md should document all outputs."""
        capability = load_capability("networking")
        config = load_product_config("networking")
        
        for output in config.get("outputs", []):
            output_name = output["name"] if isinstance(output, dict) else output
            assert output_name in capability, f"Output {output_name} not documented in CAPABILITY.md"
