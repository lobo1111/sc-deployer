# SC-Deployer Skills

Skills define the capabilities and behaviors of AI agents that assist with the development workflow.

## Workflow Overview

```
HUMAN                              AGENTS
  │                                  │
  │  Business Requirement            │
  ├─────────────────────────────────►│ requirement-shaper
  │◄─────────────────────────────────┤ (conversation)
  │  Clarify & Confirm               │
  ├─────────────────────────────────►│
  │                                  │
  │  Execution Plan (draft)          │
  │◄─────────────────────────────────┤ work-order-planner
  │  (work orders + parallelization) │   (analyzes dependencies)
  │  Approve / Modify                │
  ├─────────────────────────────────►│
  │                                  │
  │                                  │ orchestrator
  │                                  │   ├── Lane A: sub-agent-1
  │                                  │   │     ├── implementer
  │                                  │   │     ├── capability-writer
  │                                  │   │     └── test-writer
  │                                  │   │
  │                                  │   ├── Lane B: sub-agent-2 (parallel)
  │                                  │   │     ├── implementer
  │                                  │   │     ├── capability-writer
  │                                  │   │     └── test-writer
  │                                  │   │
  │                                  │   └── [sync point: merge lanes]
  │                                  │
  │  PR Ready for Review             │
  │◄─────────────────────────────────┤ pr-reviewer
  │  Approve / Request Changes       │
  ├─────────────────────────────────►│
  │                                  │
  │                                  │ deployer (dev, stage)
  │                                  │
  │  Prod Approval Request           │
  │◄─────────────────────────────────┤ release-manager
  │  Approve Production              │
  ├─────────────────────────────────►│
  │                                  │ deployer (prod)
  │                                  │
```

## Parallelization Strategy

When requirements affect multiple products, work is parallelized:

```
                    develop
                       │
                       ▼
            feature/123-integration
                 /     │     \
                /      │      \
               ▼       ▼       ▼
         Lane A    Lane B    Lane C
       (agent-1)  (agent-2)  (agent-3)
       networking  database   [waits]
           │          │          │
           ▼          ▼          │
       [complete] [complete]     │
                \    /           │
                 ▼  ▼            │
              [merge]            │
                 │               │
                 ▼               ▼
              Lane C (api - depends on A & B)
                 │
                 ▼
            [complete]
                 │
                 ▼
         PR to develop
```

### Dependency Rules

1. **Product Dependencies**: If product A depends on B (in catalog.yaml), A's work orders wait for B
2. **Output Dependencies**: If work order needs output from another, it waits
3. **Same Product**: Work orders on same product are sequential (same lane)
4. **Independent Products**: Can run in parallel (separate lanes)

### Branching Strategy

| Scenario | Strategy | Branches |
|----------|----------|----------|
| Single product | Single branch | `feature/123-api-auth` |
| Multiple independent products | Parallel branches | `feature/123-networking-xxx`, `feature/123-database-xxx` |
| Multiple dependent products | Phased parallel | Phase 1 parallel, Phase 2 after merge |

## Skills

| Skill | Purpose |
|-------|---------|
| [guide](guide.yaml) | Routes human interactions to correct workflow point |
| [requirement-shaper](requirement-shaper.yaml) | Conversational refinement of business requirements |
| [work-order-planner](work-order-planner.yaml) | Creates work orders with parallelization plan |
| [orchestrator](orchestrator.yaml) | Manages parallel sub-agents and synchronization |
| [implementer](implementer.yaml) | Implements CloudFormation changes (can be sub-agent) |
| [capability-writer](capability-writer.yaml) | Updates CAPABILITY.md documentation |
| [test-writer](test-writer.yaml) | Creates unit and integration tests |
| [pr-reviewer](pr-reviewer.yaml) | Reviews PRs before human review |
| [deployer](deployer.yaml) | Executes deployments |
| [release-manager](release-manager.yaml) | Manages production releases |

## Human Interaction Points

1. **Requirement Shaping** - Define and clarify business needs
2. **Execution Plan Approval** - Review work orders, parallelization, and sub-agent assignment
3. **PR Review** - Final review after agent review
4. **Production Approval** - Approve release to production

## Execution Plan Example

When human approves work orders, they see:

```
## Work Orders

| ID     | Title              | Product    | Depends On | Lane |
|--------|-------------------|------------|------------|------|
| WO-001 | Add VPC endpoints | networking | -          | A    |
| WO-002 | Add RDS proxy     | database   | -          | B    |
| WO-003 | Add Lambda auth   | api        | WO-001,002 | C    |

## Execution Plan

**Phase 1: Foundation** (parallel)
- Lane A (agent-1): WO-001 on feature/123-networking
- Lane B (agent-2): WO-002 on feature/123-database
- Sync: Merge to feature/123-integration

**Phase 2: Dependent** (after Phase 1)
- Lane C (agent-1): WO-003 on feature/123-api
- Sync: Final PR to develop

## Sub-Agent Assignment

| Agent   | Lane | Products   | Work Orders |
|---------|------|------------|-------------|
| agent-1 | A→C  | networking, api | WO-001, WO-003 |
| agent-2 | B    | database   | WO-002 |

**Speedup:** 2 parallel lanes reduce time by ~40%
```

## Usage

Skills are consumed by AI orchestration systems. Each skill definition includes:
- `trigger`: What activates the skill
- `context`: What information the skill needs
- `actions`: What the skill can do
- `outputs`: What the skill produces
- `transitions`: Where the workflow goes next

## Consistency Guarantees

The orchestrator ensures:
- **No conflicting edits**: Parallel lanes work on different products/files
- **Dependency ordering**: Work orders wait for dependencies to complete
- **Merge safety**: Validation runs before each merge
- **Progress visibility**: Human can see status at any time
