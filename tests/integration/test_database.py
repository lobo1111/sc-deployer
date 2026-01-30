"""
Integration tests for deployed database product.

These tests verify that the database infrastructure is correctly
deployed, secure, and accessible from within the VPC.

Requirements:
- AWS credentials configured
- database product deployed to target environment
- TEST_ENVIRONMENT environment variable set (default: dev)
"""

import pytest


class TestDatabaseDeployment:
    """Tests for deployed database resources."""

    def test_rds_instance_exists(self, aws_session, deploy_state):
        """RDS instance should exist and be available."""
        endpoint = deploy_state.get("database", {}).get("outputs", {}).get("DatabaseEndpoint")
        if not endpoint:
            pytest.skip("DatabaseEndpoint not found in deploy state")
        
        # Extract instance identifier from endpoint
        # Endpoint format: instance-id.xxxxx.region.rds.amazonaws.com
        instance_id = endpoint.split(".")[0]
        
        rds = aws_session.client("rds")
        response = rds.describe_db_instances(DBInstanceIdentifier=instance_id)
        
        assert len(response["DBInstances"]) == 1, f"RDS instance {instance_id} not found"
        
        instance = response["DBInstances"][0]
        assert instance["DBInstanceStatus"] == "available", "RDS instance should be available"

    def test_rds_not_publicly_accessible(self, aws_session, deploy_state):
        """RDS instance should not be publicly accessible."""
        endpoint = deploy_state.get("database", {}).get("outputs", {}).get("DatabaseEndpoint")
        if not endpoint:
            pytest.skip("DatabaseEndpoint not found in deploy state")
        
        instance_id = endpoint.split(".")[0]
        
        rds = aws_session.client("rds")
        response = rds.describe_db_instances(DBInstanceIdentifier=instance_id)
        instance = response["DBInstances"][0]
        
        assert instance["PubliclyAccessible"] is False, \
            "RDS instance must not be publicly accessible"

    def test_rds_encrypted(self, aws_session, deploy_state):
        """RDS instance should have encryption at rest enabled."""
        endpoint = deploy_state.get("database", {}).get("outputs", {}).get("DatabaseEndpoint")
        if not endpoint:
            pytest.skip("DatabaseEndpoint not found in deploy state")
        
        instance_id = endpoint.split(".")[0]
        
        rds = aws_session.client("rds")
        response = rds.describe_db_instances(DBInstanceIdentifier=instance_id)
        instance = response["DBInstances"][0]
        
        assert instance["StorageEncrypted"] is True, \
            "RDS instance must have storage encryption enabled"

    def test_rds_multi_az(self, aws_session, deploy_state, environment):
        """RDS instance should have Multi-AZ enabled (prod only)."""
        if environment == "dev":
            pytest.skip("Multi-AZ not required for dev environment")
        
        endpoint = deploy_state.get("database", {}).get("outputs", {}).get("DatabaseEndpoint")
        if not endpoint:
            pytest.skip("DatabaseEndpoint not found in deploy state")
        
        instance_id = endpoint.split(".")[0]
        
        rds = aws_session.client("rds")
        response = rds.describe_db_instances(DBInstanceIdentifier=instance_id)
        instance = response["DBInstances"][0]
        
        assert instance["MultiAZ"] is True, \
            f"RDS instance should have Multi-AZ enabled in {environment}"

    def test_secrets_manager_secret_exists(self, aws_session, deploy_state):
        """Database credentials secret should exist in Secrets Manager."""
        secret_arn = deploy_state.get("database", {}).get("outputs", {}).get("DatabaseSecretArn")
        if not secret_arn:
            pytest.skip("DatabaseSecretArn not found in deploy state")
        
        secrets = aws_session.client("secretsmanager")
        response = secrets.describe_secret(SecretId=secret_arn)
        
        assert response["ARN"] == secret_arn, "Secret should exist"
        assert "DeletedDate" not in response, "Secret should not be deleted"

    def test_secret_contains_credentials(self, aws_session, deploy_state):
        """Secret should contain database credentials."""
        import json
        
        secret_arn = deploy_state.get("database", {}).get("outputs", {}).get("DatabaseSecretArn")
        if not secret_arn:
            pytest.skip("DatabaseSecretArn not found in deploy state")
        
        secrets = aws_session.client("secretsmanager")
        response = secrets.get_secret_value(SecretId=secret_arn)
        
        secret_value = json.loads(response["SecretString"])
        
        assert "username" in secret_value, "Secret should contain username"
        assert "password" in secret_value, "Secret should contain password"
        assert "host" in secret_value or "endpoint" in secret_value, \
            "Secret should contain host/endpoint"

    def test_database_port_correct(self, aws_session, deploy_state):
        """Database port should match expected value."""
        port = deploy_state.get("database", {}).get("outputs", {}).get("DatabasePort")
        if not port:
            pytest.skip("DatabasePort not found in deploy state")
        
        assert port == "5432", "PostgreSQL default port should be 5432"
