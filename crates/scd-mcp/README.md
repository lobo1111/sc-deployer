# `scd-mcp`

`scd-mcp` is a small MCP (Model Context Protocol) server that exposes tools which wrap the `scd` CLI.
It lets Cursor (and other MCP clients) run `scd` safely via structured tool calls.

## Install

If you have Rust installed:

```bash
cargo install --path crates/scd-mcp --root ~/.local --force
```

## Use with Cursor

1. Ensure `scd` and `scd-mcp` are available in your PATH (commonly `~/.local/bin`).
2. Ensure your project has `.cursor/mcp.json` pointing at `scd-mcp` (created by `scd init`).
3. Restart Cursor.

## Configuration quick notes

`scd` treats local YAML as desired state:

- `.deployer/catalog.yaml`
  - set a per-product Service Catalog launch role via `launch_role_arn` (optional)
- `.deployer/profiles.yaml`
  - set per-environment per-product provisioning parameters via `product_parameters` (optional)

### Per-product launch role details

Service Catalog uses a **LAUNCH constraint** to decide which IAM role CloudFormation assumes for a product.

- By default, `scd sync` creates one environment-wide launch role named `scd-launch-role-<environment>` and configures LAUNCH constraints to use it.
- If you set `launch_role` on a product in `.deployer/catalog.yaml`, `scd sync` will create/ensure that IAM role exists and use it for that product's LAUNCH constraint.
- If you set `launch_role_arn` on a product in `.deployer/catalog.yaml`, `scd sync` will use that pre-existing role ARN for that product's LAUNCH constraint.

Notes:
- `launch_role_arn` must point at an IAM role that already exists in the target account.
- If you want scd to create the role, use `launch_role` instead.
- You can use `${account_id}` in `launch_role_arn` and scd will expand it during `scd sync`.

Inline policies:
- `launch_role.inline_policies` is supported (policy name -> policy document). scd applies these with IAM `PutRolePolicy` during `scd sync`.

