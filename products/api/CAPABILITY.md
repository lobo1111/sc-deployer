# API Service

## Business Context

Provides the backend API for customer-facing applications. Enables mobile and web clients to access business data and perform transactions securely through a RESTful interface.

Primary consumers: Mobile applications, Web dashboard, Partner integrations

## Functional Capabilities

| Capability | Description |
|------------|-------------|
| RESTful Endpoints | Standard HTTP methods for CRUD operations |
| Database Access | Secure connection to relational database |
| Request Validation | Input validation and error handling |
| Rate Limiting | API Gateway throttling to prevent abuse |
| Observability | Logging, metrics, and distributed tracing |

## Technical Implementation

- **API Gateway**: Regional REST API with usage plans and throttling
- **Lambda Function**: Python 3.11 runtime with VPC connectivity
- **VPC Integration**: Lambda in private subnets for database access
- **Secrets Manager**: Database credentials retrieval at runtime
- **CloudWatch Logs**: Request/response logging
- **X-Ray**: Distributed tracing for performance analysis
- **IAM Role**: Least-privilege execution role

## Interfaces

### Inputs (Parameters)

| Parameter | Type | Description | Default |
|-----------|------|-------------|---------|
| Environment | String | Environment name | dev |
| VpcId | String | VPC for Lambda placement | (from networking) |
| SubnetIds | CommaDelimitedList | Private subnets for Lambda | (from networking) |
| DbEndpoint | String | Database endpoint URL | (from database) |
| DbPort | String | Database port | (from database) |
| DbSecretArn | String | Database credentials secret | (from database) |

### Outputs

| Output | Description | Consumers |
|--------|-------------|-----------|
| ApiEndpoint | Base URL for API calls | Mobile app, Web app |
| ApiKey | API key for authentication | Partner systems |

### Dependencies

- **networking**: Provides VPC and private subnets for Lambda
- **database**: Provides database endpoint and credentials

## Constraints & Limitations

- Single region deployment (no multi-region failover)
- No WebSocket support (REST only)
- No custom domain (uses API Gateway default domain)
- Cold start latency for Lambda (~500ms first request)
- Maximum 10,000 requests/second (API Gateway limit)
- No built-in authentication (API key only)
