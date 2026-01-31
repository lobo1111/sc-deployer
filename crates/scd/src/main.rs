use anyhow::Result;
use clap::Parser;

mod cli;
mod project;
mod config;
mod state;
mod aws;
mod deploy;
mod manage;

#[tokio::main]
async fn main() -> Result<()> {
    let cmd = cli::RootCmd::parse();
    cli::run(cmd).await
}

