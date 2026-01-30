# Database

## Business Context

Provides managed relational database services for applications requiring persistent, transactional data storage. Enables applications to store and retrieve structured data with high availability and automated backups.

Primary consumers: api, future backend services

## Functional Capabilities

| Capability | Description |
|------------|-------------|
| Relational Storage | PostgreSQL-compatible database for structured data |
| High Availability | Multi-AZ deployment for automatic failover |
| Automated Backups | Daily snapshots with point-in-time recovery |
| Secure Credentials | Database credentials stored in Secrets Manager |
| Encryption | Data encrypted at rest and in transit |

## Technical Implementation

- **RDS PostgreSQL**: Managed PostgreSQL 15 instance
- **Multi-AZ**: Standby replica in second AZ for failover
- **DB Subnet Group**: Private subnets only (no public access)
- **Security Group**: Ingress from VPC CIDR on port 5432 only
- **Secrets Manager**: Auto-generated credentials with rotation capability
- **Parameter Group**: Optimized PostgreSQL settings
- **Encryption**: AWS KMS encryption at rest, SSL in transit

## Interfaces

### Inputs (Parameters)

| Parameter | Type | Description | Default |
|-----------|------|-------------|---------|
| Environment | String | Environment name | dev |
| VpcId | String | VPC for database placement | (from networking) |
| SubnetIds | CommaDelimitedList | Private subnets for DB | (from networking) |
| SecurityGroupId | String | Security group for access control | (from networking) |
| InstanceClass | String | RDS instance type | db.t3.micro |
| AllocatedStorage | Number | Storage in GB | 20 |

### Outputs

| Output | Description | Consumers |
|--------|-------------|-----------|
| DatabaseEndpoint | RDS endpoint hostname | api |
| DatabasePort | Database port (5432) | api |
| DatabaseSecretArn | Secrets Manager ARN for credentials | api |

### Dependencies

- **networking**: Provides VPC, private subnets, and security group

## Constraints & Limitations

- PostgreSQL only (no MySQL/Aurora option)
- Single database instance (no read replicas)
- Fixed port 5432
- No public accessibility (by design)
- Storage auto-scaling not enabled
- No cross-region replication
