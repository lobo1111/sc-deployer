use anyhow::{Context, Result};
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;
use std::fs;
use std::path::Path;

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct BootstrapState {
    #[serde(default = "bootstrap_schema_v1")]
    pub schema_version: String,

    #[serde(default)]
    pub environments: BTreeMap<String, BootstrapEnvState>,
}

fn bootstrap_schema_v1() -> String {
    "1.0".to_string()
}

impl Default for BootstrapState {
    fn default() -> Self {
        Self {
            schema_version: bootstrap_schema_v1(),
            environments: BTreeMap::new(),
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, Default, PartialEq, Eq)]
pub struct BootstrapEnvState {
    #[serde(default)]
    pub account_id: String,
    #[serde(default)]
    pub region: String,

    // Minimal cache fields; AWS ids/arns will be filled during `sync`.
    #[serde(default)]
    pub template_bucket: Option<ResourceRef>,

    #[serde(default)]
    pub ecr_repositories: BTreeMap<String, ResourceRef>,

    #[serde(default)]
    pub portfolios: BTreeMap<String, ResourceRef>,

    #[serde(default)]
    pub products: BTreeMap<String, ResourceRef>,

    #[serde(default)]
    pub launch_role: Option<ResourceRef>,

    #[serde(default)]
    pub bootstrapped_at: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct DeployState {
    #[serde(default = "deploy_schema_v2")]
    pub schema_version: String,

    #[serde(default)]
    pub environments: BTreeMap<String, DeployEnvState>,
}

fn deploy_schema_v2() -> String {
    "2.0".to_string()
}

impl Default for DeployState {
    fn default() -> Self {
        Self {
            schema_version: deploy_schema_v2(),
            environments: BTreeMap::new(),
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, Default, PartialEq, Eq)]
pub struct DeployEnvState {
    /// Product name -> per-product state
    #[serde(default)]
    pub products: BTreeMap<String, DeployProductState>,
}

#[derive(Debug, Clone, Serialize, Deserialize, Default, PartialEq, Eq)]
pub struct DeployProductState {
    #[serde(default)]
    pub version: Option<String>,

    #[serde(default)]
    pub published_at: Option<String>,

    #[serde(default)]
    pub published_commit: Option<String>,

    #[serde(default)]
    pub published_hash: Option<String>,

    #[serde(default)]
    pub deployed_at: Option<String>,

    #[serde(default)]
    pub deployed_commit: Option<String>,

    #[serde(default)]
    pub provisioned_product_id: Option<String>,

    #[serde(default)]
    pub provisioned_product_name: Option<String>,

    #[serde(default)]
    pub outputs: BTreeMap<String, String>,
}

#[derive(Debug, Clone, Serialize, Deserialize, Default, PartialEq, Eq)]
pub struct ResourceRef {
    #[serde(default)]
    pub id: Option<String>,
    #[serde(default)]
    pub arn: Option<String>,
    #[serde(default)]
    pub name: Option<String>,
    #[serde(default)]
    pub uri: Option<String>,
}

pub fn load_json<T: for<'de> Deserialize<'de> + Default>(path: &Path) -> Result<T> {
    if !path.exists() {
        return Ok(T::default());
    }
    let data = fs::read_to_string(path).with_context(|| format!("read {}", path.display()))?;
    serde_json::from_str(&data).with_context(|| format!("parse json {}", path.display()))
}

pub fn save_json<T: Serialize>(path: &Path, value: &T) -> Result<()> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)
            .with_context(|| format!("create dir {}", parent.display()))?;
    }
    let s = serde_json::to_string_pretty(value).context("serialize json")?;
    fs::write(path, s).with_context(|| format!("write {}", path.display()))?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn json_roundtrip_bootstrap_state() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join(".bootstrap-state.json");

        let mut st = BootstrapState::default();
        st.environments.insert(
            "dev".to_string(),
            BootstrapEnvState {
                account_id: "111111111111".to_string(),
                region: "us-east-1".to_string(),
                template_bucket: Some(ResourceRef {
                    name: Some("bucket".to_string()),
                    ..Default::default()
                }),
                ..Default::default()
            },
        );

        save_json(&path, &st).unwrap();
        let loaded: BootstrapState = load_json(&path).unwrap();
        assert_eq!(loaded, st);
    }
}

