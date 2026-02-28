//! Upload Lambda and AppSync code artifacts to S3.
//!
//! Follows ironhide patterns:
//! - Lambda: code/<function-name>/ per function, zipped
//! - AppSync: resolvers/ directory with .js files
//!
//! S3 keys include a content-hash suffix to force deployment when code changes.

use anyhow::{Context, Result};
use aws_sdk_s3::Client as S3Client;
use sha2::{Digest, Sha256};
use std::collections::BTreeMap;
use std::fs;
use std::io::{Seek, Write};
use std::path::Path;

/// Collect and upload all code artifacts for a product.
/// Returns artifact_id -> S3 key for injection into deploy params.
pub async fn upload_product_code(
    s3: &S3Client,
    bucket_name: &str,
    product: &str,
    version: &str,
    product_path: &Path,
    dry_run: bool,
) -> Result<BTreeMap<String, String>> {
    let mut artifacts = BTreeMap::new();

    // 1. Lambda: code/<function-name>/
    let code_dir = product_path.join("code");
    if code_dir.is_dir() {
        for entry in fs::read_dir(&code_dir).context("read code dir")? {
            let entry = entry?;
            let path = entry.path();
            if path.is_dir() {
                let fn_name = path
                    .file_name()
                    .and_then(|n| n.to_str())
                    .unwrap_or("unknown");
                let zip_bytes = zip_directory(&path)?;
                let hash = sha256_hex(&zip_bytes);
                let suffix = &hash[..hash.len().min(12)];
                let s3_key = format!("{}/{}/code/{}-{}.zip", product, version, fn_name, suffix);
                if dry_run {
                    println!("  [DRY RUN] upload s3://{bucket_name}/{s3_key} (Lambda {fn_name})");
                } else {
                    s3.put_object()
                        .bucket(bucket_name)
                        .key(&s3_key)
                        .body(aws_sdk_s3::primitives::ByteStream::from(zip_bytes))
                        .content_type("application/zip")
                        .send()
                        .await
                        .with_context(|| format!("upload Lambda {fn_name}"))?;
                }
                artifacts.insert(fn_name.to_string(), s3_key);
            }
        }
    }

    // 2. AppSync: resolvers/**/*.js
    let resolvers_dir = product_path.join("resolvers");
    if resolvers_dir.is_dir() {
        for entry in walk_resolvers(&resolvers_dir)? {
            let (rel_path, content) = entry?;
            let hash = sha256_hex(&content);
            let suffix = &hash[..hash.len().min(12)];
            // Use path with / replaced by - for S3 key (no nested dirs in key)
            let flat_key = rel_path.replace('/', "-").replace('\\', "-");
            let s3_key = format!(
                "{}/{}/resolvers/{}-{}.js",
                product, version, flat_key, suffix
            );
            let rel_no_ext = rel_path.strip_suffix(".js").unwrap_or(&rel_path);
            let artifact_id = format!("resolvers/{}", rel_no_ext);
            if dry_run {
                println!(
                    "  [DRY RUN] upload s3://{bucket_name}/{s3_key} (resolver {artifact_id})"
                );
            } else {
                s3.put_object()
                    .bucket(bucket_name)
                    .key(&s3_key)
                    .body(aws_sdk_s3::primitives::ByteStream::from(content))
                    .content_type("application/javascript")
                    .send()
                    .await
                    .with_context(|| format!("upload resolver {artifact_id}"))?;
            }
            artifacts.insert(artifact_id, s3_key);
        }
    }

    Ok(artifacts)
}

fn sha256_hex(data: &[u8]) -> String {
    let mut h = Sha256::new();
    h.update(data);
    let out = h.finalize();
    out.iter().map(|b| format!("{:02x}", b)).collect()
}

fn zip_directory(dir: &Path) -> Result<Vec<u8>> {
    let mut buf = Vec::new();
    {
        let mut zip = zip::ZipWriter::new(std::io::Cursor::new(&mut buf));
        let options = zip::write::SimpleFileOptions::default()
            .compression_method(zip::CompressionMethod::Deflated)
            .unix_permissions(0o755);
        add_dir_to_zip(&mut zip, dir, dir, options)?;
        zip.finish()?;
    }
    Ok(buf)
}

fn add_dir_to_zip<W: Write + Seek>(
    zip: &mut zip::ZipWriter<W>,
    dir: &Path,
    base: &Path,
    options: zip::write::SimpleFileOptions,
) -> Result<()> {
    for entry in fs::read_dir(dir).context("read dir for zip")? {
        let entry = entry?;
        let path = entry.path();
        let rel = path.strip_prefix(base).unwrap_or(&path);
        let name = rel.to_string_lossy().replace('\\', "/");

        if path.is_dir() {
            if !name.ends_with('/') {
                zip.add_directory(format!("{}/", name), options)?;
            }
            add_dir_to_zip(zip, &path, base, options)?;
        } else {
            zip.start_file(&name, options)?;
            let mut f = fs::File::open(&path).with_context(|| format!("open {}", path.display()))?;
            std::io::copy(&mut f, zip)?;
        }
    }
    Ok(())
}

fn walk_resolvers(dir: &Path) -> Result<Vec<Result<(String, Vec<u8>)>>> {
    let mut out = Vec::new();
    for entry in fs::read_dir(dir).context("read resolvers dir")? {
        let entry = entry?;
        let path = entry.path();
        if path.is_dir() {
            for sub in walk_resolvers(&path)? {
                out.push(sub);
            }
        } else if path.extension().map_or(false, |e| e == "js") {
            let rel = path
                .strip_prefix(dir)
                .unwrap_or(&path)
                .to_string_lossy()
                .replace('\\', "/");
            let content = fs::read(&path)
                .with_context(|| format!("read resolver {}", path.display()))?;
            out.push(Ok((rel, content)));
        }
    }
    Ok(out)
}
