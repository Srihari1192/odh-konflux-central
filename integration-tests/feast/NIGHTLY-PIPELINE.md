# Feast Nightly Integration Test Pipeline

## Overview

The Feast nightly integration test pipeline runs comprehensive E2E tests against the latest master branch of the Feast repository. This ensures continuous validation of the Feast operator and feature server components in an OpenShift environment.

## Schedule

**Runs daily at 8:00 AM IST (2:30 AM UTC)**

The pipeline is triggered automatically via cron schedule defined in `.tekton/feast-nightly-trigger.yaml`.

## Pipeline Architecture

### Pipeline File
- **Location**: `integration-tests/feast/feast-nightly-pipeline.yaml`
- **Name**: `odh-nightly-test-feast`

### Trigger Configuration
- **Location**: `integration-tests/feast/.tekton/feast-nightly-trigger.yaml`
- **Cron Schedule**: `30 2 * * *` (2:30 AM UTC = 8:00 AM IST)

## Workflow

### 1. Fetch Snapshot
- Fetches the latest commit from the Feast master branch
- Creates a snapshot JSON with component images and git metadata
- Components:
  - `odh-feast-operator-ci`: Feast operator controller
  - `odh-feature-server-ci`: Feast feature server

### 2. Provision EaaS Space
- Creates an isolated namespace for ephemeral cluster provisioning
- Uses Konflux's EaaS (Ephemeral-as-a-Service) system

### 3. Provision Cluster
- Creates an ephemeral Hypershift cluster on AWS
- Automatically selects the latest supported OpenShift version
- Instance type: `m5.2xlarge`

### 4. Deploy and Test
Main testing phase with the following steps:

#### a. Get Kubeconfig
- Retrieves credentials for the ephemeral cluster

#### b. Deploy and E2E Tests
- **Feast Operator E2E Tests**:
  - Installs and deploys the Feast operator
  - Waits for deployment to be ready
  - Runs operator E2E test suite

- **Registry REST API Tests**:
  - Tests the Feast Registry REST API functionality
  - Validates integration with OpenShift/Kubernetes

- **Version Compatibility Tests**:
  - Tests previous version compatibility
  - Validates upgrade path from previous version to current

#### c. Must-Gather
- Collects diagnostic information:
  - Feast-specific diagnostics using `must-gather`
  - OpenShift cluster diagnostics
- Runs even if tests fail to ensure debugging information is available

#### d. Push Artifacts
- Stores test artifacts in git repository temporarily
- Artifacts path: `test-artifacts/nightly-<pipelinerun-name>`

#### e. Status Validation
- Verifies all tests passed before marking pipeline as successful

### 5. Finally Block (Always Runs)
Executes regardless of test success or failure:

#### a. Pull CI Artifacts
- Retrieves artifacts from git storage

#### b. Push to OCI Registry
- Publishes artifacts to: `quay.io/opendatahub/odh-ci-artifacts:nightly-<pipelinerun-name>`
- Permanent storage for test results and diagnostics

#### c. Notify Results
- Logs pipeline results (status, artifacts URL, git revision)
- Can be extended to send Slack/email notifications

#### d. Cleanup
- Removes temporary artifacts from git repository

## Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `SNAPSHOT` | "" | Snapshot spec (auto-generated in nightly runs) |
| `oci-artifacts-repo` | `quay.io/opendatahub/odh-ci-artifacts` | OCI registry for test artifacts |
| `artifact-browser-url` | (URL) | Artifact browser for viewing results |
| `git-url` | `https://github.com/feast-dev/feast.git` | Feast repository URL |
| `git-revision` | `master` | Git branch/tag to test |

## Test Coverage

1. **Operator Installation & Deployment**
   - Validates operator deployment on OpenShift
   - Checks controller manager availability

2. **End-to-End Functionality**
   - Complete operator E2E test suite
   - Feature store creation and management

3. **Registry REST API**
   - Integration tests for Registry REST API
   - Kubernetes/OpenShift-specific validations

4. **Version Compatibility**
   - Previous version compatibility testing
   - Upgrade path validation

## Artifacts

### Storage Locations

1. **Temporary**: Git repository (`odh-build-metadata`, branch `ci-artifacts`)
   - Path: `test-artifacts/nightly-<pipelinerun-name>`
   - Cleaned up after OCI push

2. **Permanent**: OCI Registry
   - URL: `quay.io/opendatahub/odh-ci-artifacts:nightly-<pipelinerun-name>`
   - Contains:
     - Must-gather diagnostics
     - Test logs
     - Cluster information

### Accessing Artifacts

```bash
# Pull artifacts from OCI registry
oras pull quay.io/opendatahub/odh-ci-artifacts:nightly-<pipelinerun-name>

# Or use the artifact browser (if configured)
# Visit: https://app-artifact-browser.apps.rosa.konflux-qe.zmr9.p3.openshiftapps.com
```

## Monitoring

### View Pipeline Runs

```bash
# List recent nightly runs
tkn pipelinerun list -n odh-integration-tests | grep odh-nightly-test-feast

# View specific run
tkn pipelinerun describe <pipelinerun-name> -n odh-integration-tests

# View logs
tkn pipelinerun logs <pipelinerun-name> -n odh-integration-tests -f
```

### Check Schedule Status

```bash
# Verify the cron trigger is configured
kubectl get repository feast-nightly-tests -n odh-integration-tests -o yaml
```

## Differences from PR Pipeline

| Aspect | PR Pipeline | Nightly Pipeline |
|--------|-------------|------------------|
| **Trigger** | Pull request events | Cron schedule (daily 8AM IST) |
| **Branch** | PR branch | master/main |
| **Snapshot** | From PR group components | Generated from latest master |
| **PR Updates** | Comments on PR with results | Logs results (can add notifications) |
| **Artifacts Prefix** | `test-artifacts/<pr-name>` | `test-artifacts/nightly-<name>` |
| **Purpose** | Validate changes before merge | Continuous validation of stable branch |

## Troubleshooting

### Pipeline Fails to Start

1. Check the Repository resource is created:
   ```bash
   kubectl get repository feast-nightly-tests -n odh-integration-tests
   ```

2. Verify cron schedule syntax:
   ```bash
   kubectl describe repository feast-nightly-tests -n odh-integration-tests
   ```

### Test Failures

1. Check the pipeline run logs:
   ```bash
   tkn pipelinerun logs <pipelinerun-name> -n odh-integration-tests
   ```

2. Download must-gather artifacts from OCI registry

3. Review specific task logs:
   ```bash
   tkn pipelinerun logs <pipelinerun-name> -n odh-integration-tests -t deploy-and-test
   ```

### Cluster Provisioning Issues

- Check EaaS space provisioning:
  ```bash
  tkn pipelinerun logs <pipelinerun-name> -n odh-integration-tests -t provision-eaas-space
  ```

- Verify cluster creation:
  ```bash
  tkn pipelinerun logs <pipelinerun-name> -n odh-integration-tests -t provision-cluster
  ```

## Maintenance

### Updating the Schedule

Edit `.tekton/feast-nightly-trigger.yaml` and modify the cron expression:

```yaml
settings:
  cron: "30 2 * * *"  # 2:30 AM UTC = 8:00 AM IST
  timezone: "UTC"
```

### Updating Test Images

Update the component images in the `fetch-snapshot` task in `feast-nightly-pipeline.yaml`:

```yaml
"odh-feast-operator-ci": {
  "image": "quay.io/opendatahub/odh-feast-operator:latest",
  ...
}
```

### Adding Notifications

Extend the `notify-results` step in the finally block to send notifications:

```bash
# Example: Send Slack notification
curl -X POST -H 'Content-type: application/json' \
  --data '{"text":"Nightly test status: ${PIPELINE_STATUS}"}' \
  ${SLACK_WEBHOOK_URL}
```

## Related Files

- `pr-group-testing-pipeline.yaml` - PR-triggered integration tests
- `.tekton/feast-nightly-trigger.yaml` - Cron trigger configuration
- `feast-nightly-pipeline.yaml` - Main nightly pipeline definition

## References

- [Konflux Documentation](https://konflux-ci.dev/)
- [Tekton Pipelines](https://tekton.dev/docs/pipelines/)
- [PipelinesAsCode](https://pipelinesascode.com/)
- [Feast Documentation](https://docs.feast.dev/)
