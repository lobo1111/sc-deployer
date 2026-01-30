"""
Integration tests for deployed api product.

These tests verify that the API is correctly deployed, accessible,
and properly integrated with dependencies.

Requirements:
- AWS credentials configured
- api product deployed to target environment
- TEST_ENVIRONMENT environment variable set (default: dev)
"""

import pytest
import requests


class TestApiDeployment:
    """Tests for deployed API resources."""

    def test_api_endpoint_exists(self, deploy_state):
        """API endpoint should be defined in outputs."""
        endpoint = deploy_state.get("api", {}).get("outputs", {}).get("ApiEndpoint")
        
        assert endpoint, "ApiEndpoint should be in deploy state outputs"
        assert endpoint.startswith("https://"), "API endpoint should be HTTPS"

    def test_api_responds(self, deploy_state):
        """API endpoint should respond to requests."""
        endpoint = deploy_state.get("api", {}).get("outputs", {}).get("ApiEndpoint")
        api_key = deploy_state.get("api", {}).get("outputs", {}).get("ApiKey")
        
        if not endpoint:
            pytest.skip("ApiEndpoint not found in deploy state")
        
        headers = {}
        if api_key:
            headers["x-api-key"] = api_key
        
        # Try health check endpoint if it exists, otherwise root
        for path in ["/health", "/", "/api/health"]:
            try:
                response = requests.get(
                    f"{endpoint.rstrip('/')}{path}",
                    headers=headers,
                    timeout=30
                )
                # Any response (even 404) means API is working
                assert response.status_code < 500, \
                    f"API returned server error: {response.status_code}"
                return
            except requests.exceptions.RequestException:
                continue
        
        pytest.fail("API endpoint did not respond to any test paths")

    def test_api_requires_auth(self, deploy_state):
        """API should require authentication (return 403 without key)."""
        endpoint = deploy_state.get("api", {}).get("outputs", {}).get("ApiEndpoint")
        
        if not endpoint:
            pytest.skip("ApiEndpoint not found in deploy state")
        
        try:
            response = requests.get(
                f"{endpoint.rstrip('/')}/",
                timeout=30
                # No API key
            )
            # Should get 401 or 403 without auth
            assert response.status_code in [401, 403], \
                f"API should require auth, got {response.status_code}"
        except requests.exceptions.RequestException as e:
            pytest.skip(f"Could not connect to API: {e}")


class TestApiLambda:
    """Tests for API Lambda function."""

    def test_lambda_function_exists(self, aws_session, deploy_state, environment):
        """Lambda function should exist."""
        lambda_client = aws_session.client("lambda")
        
        # Lambda function name typically follows pattern: env-api-xxx
        # List functions and find the one for this API
        paginator = lambda_client.get_paginator("list_functions")
        
        found = False
        for page in paginator.paginate():
            for func in page["Functions"]:
                if environment in func["FunctionName"] and "api" in func["FunctionName"].lower():
                    found = True
                    break
            if found:
                break
        
        assert found, f"Lambda function for api in {environment} not found"

    def test_lambda_in_vpc(self, aws_session, deploy_state, environment):
        """Lambda function should be in VPC."""
        lambda_client = aws_session.client("lambda")
        
        # Find the API Lambda function
        paginator = lambda_client.get_paginator("list_functions")
        
        for page in paginator.paginate():
            for func in page["Functions"]:
                if environment in func["FunctionName"] and "api" in func["FunctionName"].lower():
                    response = lambda_client.get_function(FunctionName=func["FunctionName"])
                    config = response["Configuration"]
                    
                    vpc_config = config.get("VpcConfig", {})
                    assert vpc_config.get("SubnetIds"), \
                        f"Lambda {func['FunctionName']} should be in VPC"
                    return
        
        pytest.skip("API Lambda function not found")

    def test_lambda_has_db_access(self, aws_session, deploy_state, environment):
        """Lambda should have security group allowing DB access."""
        lambda_client = aws_session.client("lambda")
        ec2 = aws_session.client("ec2")
        
        # Find the API Lambda function
        paginator = lambda_client.get_paginator("list_functions")
        
        for page in paginator.paginate():
            for func in page["Functions"]:
                if environment in func["FunctionName"] and "api" in func["FunctionName"].lower():
                    response = lambda_client.get_function(FunctionName=func["FunctionName"])
                    config = response["Configuration"]
                    
                    vpc_config = config.get("VpcConfig", {})
                    sg_ids = vpc_config.get("SecurityGroupIds", [])
                    
                    if sg_ids:
                        # Check security group allows outbound to DB port
                        sg_response = ec2.describe_security_groups(GroupIds=sg_ids)
                        for sg in sg_response["SecurityGroups"]:
                            for rule in sg.get("IpPermissionsEgress", []):
                                # Check for port 5432 or all traffic
                                if rule.get("IpProtocol") == "-1":
                                    return  # All traffic allowed
                                if rule.get("FromPort", 0) <= 5432 <= rule.get("ToPort", 0):
                                    return  # DB port allowed
                        
                        pytest.fail("Lambda security group should allow outbound to DB port 5432")
                    return
        
        pytest.skip("API Lambda function not found")


class TestApiGateway:
    """Tests for API Gateway configuration."""

    def test_api_gateway_exists(self, aws_session, deploy_state, environment):
        """API Gateway should exist."""
        apigw = aws_session.client("apigateway")
        
        response = apigw.get_rest_apis()
        
        found = False
        for api in response["items"]:
            if environment in api["name"].lower() or "api" in api["name"].lower():
                found = True
                break
        
        assert found, f"API Gateway for {environment} not found"

    def test_api_has_stage(self, aws_session, deploy_state, environment):
        """API Gateway should have a deployed stage."""
        apigw = aws_session.client("apigateway")
        
        response = apigw.get_rest_apis()
        
        for api in response["items"]:
            if environment in api["name"].lower() or "api" in api["name"].lower():
                stages = apigw.get_stages(restApiId=api["id"])
                assert stages["item"], f"API {api['name']} should have at least one stage"
                return
        
        pytest.skip("API Gateway not found")
