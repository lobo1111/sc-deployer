use anyhow::Result;
use clap::Parser;

mod cli;
mod code_upload;
mod config;
mod deploy;
mod manage;
mod project;
mod state;
mod aws;

#[tokio::main]
async fn main() -> Result<()> {
    let cmd = cli::RootCmd::parse();
    cli::run(cmd).await
}

