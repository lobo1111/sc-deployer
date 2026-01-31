use assert_cmd::prelude::*;
use predicates::prelude::*;
use std::fs;
use std::process::Command;
use tempfile::TempDir;

fn scd_cmd() -> Command {
    Command::new(assert_cmd::cargo::cargo_bin!("scd"))
}

#[test]
fn init_creates_expected_layout_and_git_repo() {
    let tmp = TempDir::new().unwrap();
    let project_dir = tmp.path().join("proj");

    scd_cmd()
        .arg("init")
        .arg("--name")
        .arg("proj")
        .current_dir(tmp.path())
        .assert()
        .success()
        .stdout(predicate::str::contains("Initialized project"));

    // Ensure we didn't create project files in the current dir
    assert!(!tmp.path().join(".deployer").exists());
    assert!(!tmp.path().join("products").exists());

    assert!(project_dir.join(".deployer").is_dir());
    assert!(project_dir.join(".deployer").join("profiles.yaml").is_file());
    assert!(project_dir.join(".deployer").join("bootstrap.yaml").is_file());
    assert!(project_dir.join(".deployer").join("catalog.yaml").is_file());
    assert!(project_dir.join("products").is_dir());
    assert!(project_dir.join(".gitignore").is_file());
    assert!(project_dir.join(".git").is_dir());
    assert!(project_dir.join(".cursor").is_dir());
    assert!(project_dir.join(".cursor").join("mcp.json").is_file());
    assert!(project_dir.join(".cursor").join("rules").join("scd.mdc").is_file());
    assert!(project_dir
        .join(".cursor")
        .join("skills")
        .join("scd-mcp")
        .join("SKILL.md")
        .is_file());
    assert!(project_dir.join("AGENTS.md").is_file());

    let gitignore = fs::read_to_string(project_dir.join(".gitignore")).unwrap();
    assert!(gitignore.contains(".deployer/.bootstrap-state.json"));
    assert!(gitignore.contains(".deployer/.deploy-state.json"));
}

#[test]
fn init_sample_creates_sample_product_files() {
    let tmp = TempDir::new().unwrap();
    let project_dir = tmp.path().join("proj");

    scd_cmd()
        .arg("init")
        .arg("--name")
        .arg("proj")
        .current_dir(tmp.path())
        .arg("--sample")
        .assert()
        .success();

    assert!(project_dir
        .join("products")
        .join("sample")
        .join("product.yaml")
        .is_file());
    assert!(project_dir
        .join("products")
        .join("sample")
        .join("template.yaml")
        .is_file());
}

#[test]
fn project_status_uses_project_override() {
    let tmp = TempDir::new().unwrap();
    let project_dir = tmp.path().join("proj");

    scd_cmd()
        .arg("init")
        .arg("--name")
        .arg("proj")
        .current_dir(tmp.path())
        .assert()
        .success();

    scd_cmd()
        .arg("--project")
        .arg(&project_dir)
        .arg("project-status")
        .assert()
        .success()
        .stdout(predicate::str::contains("Project root"));
}

#[test]
fn project_discovery_walks_upwards() {
    let tmp = TempDir::new().unwrap();
    let project_dir = tmp.path().join("proj");
    scd_cmd()
        .arg("init")
        .arg("--name")
        .arg("proj")
        .current_dir(tmp.path())
        .assert()
        .success();

    let nested = project_dir.join("a").join("b").join("c");
    fs::create_dir_all(&nested).unwrap();

    scd_cmd()
        .current_dir(&nested)
        .arg("project-status")
        .assert()
        .success()
        .stdout(predicate::str::contains(project_dir.to_string_lossy().to_string()));
}

