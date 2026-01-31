use anyhow::{Context, Result};
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;
use std::fs;
use std::path::Path;

#[derive(Debug, Clone, Serialize, Deserialize, Default, PartialEq, Eq)]
pub struct ProfilesFile {
    #[serde(default)]
    pub profiles: BTreeMap<String, Profile>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct Profile {
    pub aws_profile: String,
    pub aws_region: String,
    pub account_id: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, Default, PartialEq, Eq)]
pub struct BootstrapFile {
    #[serde(default)]
    pub settings: BootstrapSettings,

    #[serde(default)]
    pub template_bucket: TemplateBucket,

    #[serde(default)]
    pub ecr_repositories: Vec<EcrRepository>,

    #[serde(default)]
    pub portfolios: BTreeMap<String, PortfolioSpec>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct BootstrapSettings {
    #[serde(default = "default_bootstrap_state_file")]
    pub state_file: String,
}

fn default_bootstrap_state_file() -> String {
    ".bootstrap-state.json".to_string()
}

impl Default for BootstrapSettings {
    fn default() -> Self {
        Self {
            state_file: default_bootstrap_state_file(),
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, Default, PartialEq, Eq)]
pub struct TemplateBucket {
    #[serde(default = "default_template_bucket_prefix")]
    pub name_prefix: String,
    #[serde(default = "default_true")]
    pub versioning: bool,
    #[serde(default = "default_sse")]
    pub encryption: String,
}

fn default_template_bucket_prefix() -> String {
    "sc-templates".to_string()
}
fn default_true() -> bool {
    true
}
fn default_sse() -> String {
    "AES256".to_string()
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct EcrRepository {
    pub name: String,
    #[serde(default = "default_true")]
    pub scan_on_push: bool,
    #[serde(default = "default_ecr_tag_mutability")]
    pub image_tag_mutability: String,
}

fn default_ecr_tag_mutability() -> String {
    "IMMUTABLE".to_string()
}

#[derive(Debug, Clone, Serialize, Deserialize, Default, PartialEq, Eq)]
pub struct PortfolioSpec {
    #[serde(default)]
    pub display_name: String,
    #[serde(default)]
    pub description: String,
    #[serde(default = "default_provider_name")]
    pub provider_name: String,
    #[serde(default)]
    pub principals: Vec<String>,
    #[serde(default)]
    pub tags: BTreeMap<String, String>,
}

fn default_provider_name() -> String {
    "Platform Team".to_string()
}

#[derive(Debug, Clone, Serialize, Deserialize, Default, PartialEq, Eq)]
pub struct CatalogFile {
    #[serde(default)]
    pub settings: CatalogSettings,

    #[serde(default)]
    pub products: BTreeMap<String, ProductSpec>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct CatalogSettings {
    #[serde(default = "default_deploy_state_file")]
    pub state_file: String,

    #[serde(default = "default_version_format")]
    pub version_format: String,
}

fn default_deploy_state_file() -> String {
    ".deploy-state.json".to_string()
}
fn default_version_format() -> String {
    "%Y.%m.%d.%H%M%S".to_string()
}

impl Default for CatalogSettings {
    fn default() -> Self {
        Self {
            state_file: default_deploy_state_file(),
            version_format: default_version_format(),
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, Default, PartialEq, Eq)]
pub struct ProductSpec {
    pub path: String,

    #[serde(default)]
    pub portfolio: String,

    #[serde(default)]
    pub ecr_repository: Option<String>,

    #[serde(default)]
    pub dependencies: Vec<String>,

    #[serde(default)]
    pub parameter_mapping: BTreeMap<String, String>,

    #[serde(default)]
    pub outputs: Vec<String>,
}

pub fn load_yaml<T: for<'de> Deserialize<'de>>(path: &Path) -> Result<T> {
    let data = fs::read_to_string(path).with_context(|| format!("read {}", path.display()))?;
    serde_yaml::from_str(&data).with_context(|| format!("parse yaml {}", path.display()))
}

pub fn save_yaml<T: Serialize>(path: &Path, value: &T) -> Result<()> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)
            .with_context(|| format!("create dir {}", parent.display()))?;
    }
    let s = serde_yaml::to_string(value).context("serialize yaml")?;
    fs::write(path, s).with_context(|| format!("write {}", path.display()))?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn yaml_roundtrip_profiles() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("profiles.yaml");

        let mut pf = ProfilesFile::default();
        pf.profiles.insert(
            "dev".to_string(),
            Profile {
                aws_profile: "sandbox".to_string(),
                aws_region: "us-east-1".to_string(),
                account_id: "111111111111".to_string(),
            },
        );

        save_yaml(&path, &pf).unwrap();
        let loaded: ProfilesFile = load_yaml(&path).unwrap();
        assert_eq!(loaded, pf);
    }

    #[test]
    fn yaml_roundtrip_catalog() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("catalog.yaml");

        let mut cf = CatalogFile::default();
        cf.products.insert(
            "networking".to_string(),
            ProductSpec {
                path: "networking".to_string(),
                portfolio: "infra".to_string(),
                ecr_repository: None,
                dependencies: vec![],
                parameter_mapping: BTreeMap::new(),
                outputs: vec!["VpcId".to_string()],
            },
        );

        save_yaml(&path, &cf).unwrap();
        let loaded: CatalogFile = load_yaml(&path).unwrap();
        assert_eq!(loaded, cf);
    }
}

