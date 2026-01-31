use assert_cmd::prelude::*;
use predicates::prelude::*;
use std::fs;
use std::process::Command;
use tempfile::TempDir;

fn scd_cmd() -> Command {
    Command::new(assert_cmd::cargo::cargo_bin!("scd"))
}

#[test]
fn profiles_list_and_set_work() {
    let tmp = TempDir::new().unwrap();

    scd_cmd()
        .arg("init")
        .arg("--name")
        .arg("proj")
        .current_dir(tmp.path())
        .assert()
        .success();

    let project_dir = tmp.path().join("proj");

    scd_cmd()
        .arg("--project")
        .arg(&project_dir)
        .args([
            "profiles",
            "set",
            "-e",
            "dev",
            "--aws-profile",
            "dummy",
            "--region",
            "us-east-1",
            "--account-id",
            "111111111111",
        ])
        .assert()
        .success()
        .stdout(predicate::str::contains("Profile saved"));

    scd_cmd()
        .arg("--project")
        .arg(&project_dir)
        .args(["profiles", "list"])
        .assert()
        .success()
        .stdout(predicate::str::contains("dev"))
        .stdout(predicate::str::contains("dummy"));
}

#[test]
fn products_add_creates_files_and_updates_catalog() {
    let tmp = TempDir::new().unwrap();

    scd_cmd()
        .arg("init")
        .arg("--name")
        .arg("proj")
        .current_dir(tmp.path())
        .assert()
        .success();

    let project_dir = tmp.path().join("proj");

    scd_cmd()
        .arg("--project")
        .arg(&project_dir)
        .args([
            "products",
            "add",
            "--name",
            "networking",
            "--output",
            "VpcId",
        ])
        .assert()
        .success()
        .stdout(predicate::str::contains("Product added"));

    assert!(project_dir
        .join("products")
        .join("networking")
        .join("product.yaml")
        .is_file());
    assert!(project_dir
        .join("products")
        .join("networking")
        .join("template.yaml")
        .is_file());

    let catalog = fs::read_to_string(project_dir.join(".deployer").join("catalog.yaml")).unwrap();
    assert!(catalog.contains("networking"));

    scd_cmd()
        .arg("--project")
        .arg(&project_dir)
        .args(["products", "list"])
        .assert()
        .success()
        .stdout(predicate::str::contains("networking"));
}

#[test]
fn completion_outputs_script() {
    scd_cmd()
        .args(["completion", "bash"])
        .assert()
        .success()
        .stdout(predicate::str::contains("scd"));
}

