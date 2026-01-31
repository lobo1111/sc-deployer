use assert_cmd::prelude::*;
use predicates::prelude::*;
use std::fs;
use std::process::Command;
use tempfile::TempDir;

fn scd_cmd() -> Command {
    Command::new(assert_cmd::cargo::cargo_bin!("scd"))
}

#[test]
fn deploy_validate_fails_on_cycle() {
    let tmp = TempDir::new().unwrap();
    let project_dir = tmp.path().join("proj");
    scd_cmd()
        .arg("init")
        .arg("--name")
        .arg("proj")
        .current_dir(tmp.path())
        .assert()
        .success();

    // Configure environment
    fs::write(
        project_dir.join(".deployer").join("profiles.yaml"),
        r#"profiles:
  dev:
    aws_profile: dummy
    aws_region: us-east-1
    account_id: "111111111111"
"#,
    )
    .unwrap();

    // Create cyclic catalog
    fs::write(
        project_dir.join(".deployer").join("catalog.yaml"),
        r#"settings:
  state_file: .deploy-state.json
  version_format: "%Y.%m.%d.%H%M%S"

products:
  a:
    path: a
    portfolio: ""
    dependencies: [b]
    parameter_mapping: {}
    outputs: [OutA]
  b:
    path: b
    portfolio: ""
    dependencies: [a]
    parameter_mapping: {}
    outputs: [OutB]
"#,
    )
    .unwrap();

    // Minimal bootstrap state presence for validate() to reach cycle detection first.
    fs::write(
        project_dir.join(".deployer").join(".bootstrap-state.json"),
        r#"{"schema_version":"1.0","environments":{"dev":{"account_id":"111111111111","region":"us-east-1","template_bucket":{"name":"x"},"ecr_repositories":{},"portfolios":{},"products":{},"launch_role":null,"bootstrapped_at":null}}}"#,
    )
    .unwrap();

    scd_cmd()
        .arg("--project")
        .arg(&project_dir)
        .args(["deploy", "validate", "-e", "dev"])
        .assert()
        .failure()
        .stderr(predicate::str::contains("cycle"));
}

#[test]
fn deploy_plan_prints_topological_order() {
    let tmp = TempDir::new().unwrap();
    let project_dir = tmp.path().join("proj");
    scd_cmd()
        .arg("init")
        .arg("--name")
        .arg("proj")
        .current_dir(tmp.path())
        .assert()
        .success();

    fs::write(
        project_dir.join(".deployer").join("catalog.yaml"),
        r#"settings:
  state_file: .deploy-state.json
  version_format: "%Y.%m.%d.%H%M%S"

products:
  networking:
    path: networking
    portfolio: ""
    dependencies: []
    parameter_mapping: {}
    outputs: [VpcId]
  database:
    path: database
    portfolio: ""
    dependencies: [networking]
    parameter_mapping:
      VpcId: networking.VpcId
    outputs: [DbEndpoint]
"#,
    )
    .unwrap();

    scd_cmd()
        .arg("--project")
        .arg(&project_dir)
        .args(["deploy", "plan", "-e", "dev"])
        .assert()
        .success()
        .stdout(predicate::str::contains("Deployment order:"))
        .stdout(predicate::str::contains("networking"))
        .stdout(predicate::str::contains("database"));
}

