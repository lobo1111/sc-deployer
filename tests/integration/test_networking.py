"""
Integration tests for deployed networking product.

These tests verify that the networking infrastructure is correctly
deployed and functional in AWS.

Requirements:
- AWS credentials configured
- networking product deployed to target environment
- TEST_ENVIRONMENT environment variable set (default: dev)
"""

import pytest


class TestNetworkingDeployment:
    """Tests for deployed networking resources."""

    def test_vpc_exists(self, aws_session, deploy_state):
        """VPC should exist and be available."""
        vpc_id = deploy_state.get("networking", {}).get("outputs", {}).get("VpcId")
        if not vpc_id:
            pytest.skip("VpcId not found in deploy state")
        
        ec2 = aws_session.client("ec2")
        response = ec2.describe_vpcs(VpcIds=[vpc_id])
        
        assert len(response["Vpcs"]) == 1, f"VPC {vpc_id} not found"
        assert response["Vpcs"][0]["State"] == "available", "VPC should be available"

    def test_public_subnets_exist(self, aws_session, deploy_state):
        """Public subnets should exist and be in different AZs."""
        subnet_ids = deploy_state.get("networking", {}).get("outputs", {}).get("PublicSubnetIds")
        if not subnet_ids:
            pytest.skip("PublicSubnetIds not found in deploy state")
        
        subnet_list = subnet_ids.split(",")
        
        ec2 = aws_session.client("ec2")
        response = ec2.describe_subnets(SubnetIds=subnet_list)
        
        assert len(response["Subnets"]) == len(subnet_list), "All public subnets should exist"
        
        # Check different AZs
        azs = {s["AvailabilityZone"] for s in response["Subnets"]}
        assert len(azs) >= 2, "Public subnets should span at least 2 AZs"

    def test_private_subnets_exist(self, aws_session, deploy_state):
        """Private subnets should exist and be in different AZs."""
        subnet_ids = deploy_state.get("networking", {}).get("outputs", {}).get("PrivateSubnetIds")
        if not subnet_ids:
            pytest.skip("PrivateSubnetIds not found in deploy state")
        
        subnet_list = subnet_ids.split(",")
        
        ec2 = aws_session.client("ec2")
        response = ec2.describe_subnets(SubnetIds=subnet_list)
        
        assert len(response["Subnets"]) == len(subnet_list), "All private subnets should exist"
        
        # Check different AZs
        azs = {s["AvailabilityZone"] for s in response["Subnets"]}
        assert len(azs) >= 2, "Private subnets should span at least 2 AZs"

    def test_internet_gateway_attached(self, aws_session, deploy_state):
        """Internet gateway should be attached to VPC."""
        vpc_id = deploy_state.get("networking", {}).get("outputs", {}).get("VpcId")
        if not vpc_id:
            pytest.skip("VpcId not found in deploy state")
        
        ec2 = aws_session.client("ec2")
        response = ec2.describe_internet_gateways(
            Filters=[{"Name": "attachment.vpc-id", "Values": [vpc_id]}]
        )
        
        assert len(response["InternetGateways"]) >= 1, \
            "Internet gateway should be attached to VPC"

    def test_nat_gateway_exists(self, aws_session, deploy_state):
        """NAT gateway should exist for private subnet internet access."""
        vpc_id = deploy_state.get("networking", {}).get("outputs", {}).get("VpcId")
        if not vpc_id:
            pytest.skip("VpcId not found in deploy state")
        
        ec2 = aws_session.client("ec2")
        response = ec2.describe_nat_gateways(
            Filters=[
                {"Name": "vpc-id", "Values": [vpc_id]},
                {"Name": "state", "Values": ["available"]}
            ]
        )
        
        assert len(response["NatGateways"]) >= 1, \
            "NAT gateway should exist and be available"

    def test_security_group_exists(self, aws_session, deploy_state):
        """Security group should exist."""
        sg_id = deploy_state.get("networking", {}).get("outputs", {}).get("SecurityGroupId")
        if not sg_id:
            pytest.skip("SecurityGroupId not found in deploy state")
        
        ec2 = aws_session.client("ec2")
        response = ec2.describe_security_groups(GroupIds=[sg_id])
        
        assert len(response["SecurityGroups"]) == 1, f"Security group {sg_id} not found"
