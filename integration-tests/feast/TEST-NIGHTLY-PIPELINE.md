# Testing Feast Nightly Pipeline Before Merge

This guide provides multiple approaches to test the nightly pipeline before merging to main.

## Prerequisites

1. Access to an OpenShift/Kubernetes cluster with Tekton installed
2. `kubectl` or `oc` CLI configured
3. `tkn` CLI installed (Tekton CLI)
4. Appropriate permissions in the target namespace

## Testing Approaches

### Option 1: Syntax Validation (Quick Check)

Validate YAML syntax and Tekton resources without running the pipeline:

```bash
# Navigate to the feast directory
cd /Users/hari/Development/odh-konflux-central/integration-tests/feast

# Validate pipeline YAML syntax
kubectl apply --dry-run=client -f feast-nightly-pipeline.yaml

# Validate trigger YAML syntax
kubectl apply --dry-run=client -f .tekton/feast-nightly-trigger.yaml

# Validate with Tekton-specific checks (if tkn CLI is available)
tkn pipeline describe odh-nightly-test-feast --filename feast-nightly-pipeline.yaml
```

**Expected output**: No errors, resource definitions are valid

---

### Option 2: Local Pipeline Validation with tkn

Use the Tekton CLI to validate pipeline structure:

```bash
# List tasks in the pipeline
tkn pipeline describe odh-nightly-test-feast --filename feast-nightly-pipeline.yaml

# Check pipeline graph
tkn pipeline describe odh-nightly-test-feast --filename feast-nightly-pipeline.yaml --output graph
```

---

### Option 3: Manual Test Run (Recommended)

Create and run a test PipelineRun manually without waiting for the cron schedule.

#### Step 1: Apply the Pipeline to Your Cluster

```bash
# Make sure you're in the right namespace
export TEST_NAMESPACE="odh-integration-tests"  # or your test namespace
kubectl config set-context --current --namespace=${TEST_NAMESPACE}

# Apply the pipeline definition
kubectl apply -f feast-nightly-pipeline.yaml -n ${TEST_NAMESPACE}

# Verify the pipeline was created
tkn pipeline list -n ${TEST_NAMESPACE}
```

#### Step 2: Create a Test PipelineRun

Use the helper script or create manually:

```bash
# Using tkn CLI (interactive)
tkn pipeline start odh-nightly-test-feast \
  -n ${TEST_NAMESPACE} \
  --param git-url=https://github.com/feast-dev/feast.git \
  --param git-revision=master \
  --param oci-artifacts-repo=quay.io/opendatahub/odh-ci-artifacts \
  --workspace name=git-auth,emptyDir="" \
  --showlog

# Or using the test PipelineRun YAML (see Option 4)
```

#### Step 3: Monitor the Run

```bash
# Watch the pipeline run in real-time
tkn pipelinerun logs -f -n ${TEST_NAMESPACE}

# Or watch a specific run
PIPELINE_RUN_NAME=$(tkn pipelinerun list -n ${TEST_NAMESPACE} --limit 1 -o jsonpath='{.items[0].metadata.name}')
tkn pipelinerun logs ${PIPELINE_RUN_NAME} -f -n ${TEST_NAMESPACE}

# Check status
tkn pipelinerun describe ${PIPELINE_RUN_NAME} -n ${TEST_NAMESPACE}
```

---

### Option 4: Test with PipelineRun YAML

Create a test PipelineRun manifest for reproducible testing:

```bash
# Use the provided test-pipelinerun.yaml
kubectl apply -f test-pipelinerun.yaml -n ${TEST_NAMESPACE}

# Watch the execution
tkn pipelinerun logs test-feast-nightly-run -f -n ${TEST_NAMESPACE}
```

See `test-pipelinerun.yaml` for the complete manifest.

---

### Option 5: Test Trigger Configuration (Cron Schedule)

Test the trigger configuration without waiting for the actual schedule:

```bash
# Apply the Repository resource
kubectl apply -f .tekton/feast-nightly-trigger.yaml -n ${TEST_NAMESPACE}

# Check if the trigger is configured correctly
kubectl get repository feast-nightly-tests -n ${TEST_NAMESPACE} -o yaml

# Verify the cron expression
kubectl get repository feast-nightly-tests -n ${TEST_NAMESPACE} -o jsonpath='{.spec.settings.cron}'
```

**Validate cron schedule**:
```bash
# Install cronie or use online cron validator
# Cron: "30 2 * * *" means 2:30 AM UTC daily

# Convert to your timezone (IST = UTC+5:30)
# 2:30 AM UTC = 8:00 AM IST ✓
```

---

### Option 6: Test Individual Tasks

Test problematic tasks in isolation:

```bash
# Test the fetch-snapshot task
kubectl create -f - <<EOF
apiVersion: tekton.dev/v1
kind: TaskRun
metadata:
  name: test-fetch-snapshot
  namespace: ${TEST_NAMESPACE}
spec:
  taskSpec:
    params:
      - name: GIT_URL
      - name: GIT_REVISION
    results:
      - name: SNAPSHOT
      - name: git-commit
      - name: git-url
    steps:
      - name: generate-snapshot
        image: quay.io/konflux-ci/konflux-test:stable
        script: |
          #!/bin/bash
          set -e
          echo "Generating snapshot for nightly test"
          GIT_URL="\$(params.GIT_URL)"
          GIT_REVISION="\$(params.GIT_REVISION)"
          GIT_COMMIT=\$(git ls-remote "\${GIT_URL}" "\${GIT_REVISION}" | awk '{print \$1}')
          echo "Latest commit: \${GIT_COMMIT}"
          echo -n "\${GIT_COMMIT}" > "\$(results.git-commit.path)"
  params:
    - name: GIT_URL
      value: https://github.com/feast-dev/feast.git
    - name: GIT_REVISION
      value: master
EOF

# Watch the task run
tkn taskrun logs test-fetch-snapshot -f -n ${TEST_NAMESPACE}
```

---

## Common Issues and Solutions

### Issue 1: Pipeline Not Found

**Error**: `Error from server (NotFound): pipelines.tekton.dev "odh-nightly-test-feast" not found`

**Solution**:
```bash
# Apply the pipeline first
kubectl apply -f feast-nightly-pipeline.yaml -n ${TEST_NAMESPACE}
```

### Issue 2: Workspace Not Provided

**Error**: `error: required workspace "git-auth" not provided`

**Solution**:
```bash
# Provide workspace in the start command
tkn pipeline start odh-nightly-test-feast \
  --workspace name=git-auth,emptyDir="" \
  -n ${TEST_NAMESPACE}
```

### Issue 3: Image Pull Errors

**Error**: `Failed to pull image "quay.io/rhoai/rhoai-task-toolset:go-its"`

**Solution**:
```bash
# Check if you have access to the image registry
oc whoami -t | docker login -u $(oc whoami) --password-stdin quay.io

# Or create an imagePullSecret
kubectl create secret docker-registry quay-pull-secret \
  --docker-server=quay.io \
  --docker-username=<username> \
  --docker-password=<password> \
  -n ${TEST_NAMESPACE}
```

### Issue 4: EaaS Permission Issues

**Error**: `Failed to provision EaaS space`

**Solution**: This is expected in local testing. The EaaS tasks require specific Konflux infrastructure. You can:

1. **Mock the task** for local testing
2. **Skip cluster provisioning** and test against an existing cluster
3. **Test in the actual Konflux environment** where EaaS is available

---

## Testing Checklist

Before merging, ensure:

- [ ] YAML syntax is valid (`kubectl apply --dry-run=client`)
- [ ] Pipeline structure is correct (`tkn pipeline describe`)
- [ ] Cron schedule is accurate (2:30 AM UTC = 8:00 AM IST)
- [ ] All task references are resolvable
- [ ] Parameter defaults are correct
- [ ] Workspace configurations are valid
- [ ] Manual test run completes successfully (or fails at expected infrastructure-specific steps)
- [ ] Artifacts are properly collected and stored
- [ ] Finally block executes even on failure
- [ ] Documentation is accurate and complete

---

## Cleanup After Testing

```bash
# Delete test pipeline runs
kubectl delete pipelinerun -l tekton.dev/pipeline=odh-nightly-test-feast -n ${TEST_NAMESPACE}

# Delete test pipeline (optional - only if you want to clean up)
kubectl delete pipeline odh-nightly-test-feast -n ${TEST_NAMESPACE}

# Delete test repository trigger (optional)
kubectl delete repository feast-nightly-tests -n ${TEST_NAMESPACE}
```

---

## Next Steps

1. **Run syntax validation** (Option 1) - Takes 30 seconds
2. **Create manual test run** (Option 3) - Takes 30-60 minutes
3. **Review logs and results**
4. **Fix any issues found**
5. **Commit fixes to the branch**
6. **Create Pull Request** when tests pass

---

## Quick Test Script

For convenience, use the provided test script:

```bash
./test-nightly-pipeline.sh
```

This script will:
1. Validate YAML syntax
2. Apply the pipeline
3. Create a test PipelineRun
4. Monitor the execution
5. Show results

---

## Production Environment Testing

If you have access to a staging/dev Konflux environment:

```bash
# Apply to staging namespace
kubectl apply -f feast-nightly-pipeline.yaml -n konflux-staging
kubectl apply -f .tekton/feast-nightly-trigger.yaml -n konflux-staging

# Manually trigger to test
tkn pipeline start odh-nightly-test-feast -n konflux-staging --showlog
```

---

## Questions?

- **Can I test without a cluster?**: Yes, use Option 1 (syntax validation only)
- **Can I test without EaaS?**: Partially - you'll need to mock cluster provisioning tasks
- **How long does a full test take?**: 30-60 minutes depending on cluster provisioning
- **Can I test the cron trigger?**: Yes, but you'll need to wait for the scheduled time or manually trigger

---

## References

- [Tekton Pipeline Testing](https://tekton.dev/docs/pipelines/pipelines/#testing-a-pipeline)
- [tkn CLI Documentation](https://tekton.dev/docs/cli/)
- [PipelinesAsCode Testing](https://pipelinesascode.com/docs/guide/testing/)
