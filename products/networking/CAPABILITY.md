# Networking

## Business Context

Provides the foundational network infrastructure for all workloads. Enables secure, isolated environments for applications with controlled internet access and internal communication.

Primary consumers: All other products (database, api, future services)

## Functional Capabilities

| Capability | Description |
|------------|-------------|
| Network Isolation | Dedicated VPC with private address space, isolated from other customers |
| Public Access | Public subnets with internet gateway for load balancers and bastion hosts |
| Private Compute | Private subnets with NAT gateway for secure outbound-only internet access |
| Multi-AZ | Resources spread across availability zones for high availability |
| Security Boundaries | Security groups for network-level access control |

## Technical Implementation

- **VPC**: Dedicated /16 CIDR block (configurable)
- **Public Subnets**: 2x /24 subnets across AZs with route to Internet Gateway
- **Private Subnets**: 2x /24 subnets across AZs with route to NAT Gateway
- **Internet Gateway**: Enables inbound/outbound internet for public subnets
- **NAT Gateway**: Enables outbound-only internet for private subnets
- **Security Group**: Default SG allowing internal VPC traffic

## Interfaces

### Inputs (Parameters)

| Parameter | Type | Description | Default |
|-----------|------|-------------|---------|
| Environment | String | Environment name (dev/stage/prod) | dev |
| VpcCidr | String | CIDR block for VPC | 10.0.0.0/16 |

### Outputs

| Output | Description | Consumers |
|--------|-------------|-----------|
| VpcId | VPC identifier | database, api |
| PublicSubnetIds | Comma-separated public subnet IDs | Load balancers, bastion |
| PrivateSubnetIds | Comma-separated private subnet IDs | database, api |
| SecurityGroupId | Default security group ID | database, api |

### Dependencies

None - this is a foundational product.

## Constraints & Limitations

- Single region deployment
- Fixed 2-AZ architecture (not configurable)
- Single NAT Gateway (not HA - for cost optimization in non-prod)
- No VPC peering or Transit Gateway support
- No VPN or Direct Connect integration
