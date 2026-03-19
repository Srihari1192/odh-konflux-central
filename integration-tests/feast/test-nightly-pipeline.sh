#!/bin/bash

# Test script for Feast Nightly Pipeline
# This script validates and tests the pipeline before merging

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Configuration
NAMESPACE="${TEST_NAMESPACE:-odh-integration-tests}"
PIPELINE_FILE="feast-nightly-pipeline.yaml"
TRIGGER_FILE=".tekton/feast-nightly-trigger.yaml"
TEST_RUN_FILE="test-pipelinerun.yaml"

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}Feast Nightly Pipeline Test Suite${NC}"
echo -e "${BLUE}========================================${NC}"
echo ""

# Function to print section headers
print_header() {
    echo ""
    echo -e "${BLUE}>>> $1${NC}"
    echo ""
}

# Function to print success
print_success() {
    echo -e "${GREEN}✓ $1${NC}"
}

# Function to print error
print_error() {
    echo -e "${RED}✗ $1${NC}"
}

# Function to print warning
print_warning() {
    echo -e "${YELLOW}⚠ $1${NC}"
}

# Check prerequisites
print_header "Checking Prerequisites"

if ! command -v kubectl &> /dev/null; then
    print_error "kubectl is not installed"
    exit 1
fi
print_success "kubectl found"

if ! command -v tkn &> /dev/null; then
    print_warning "tkn CLI not found (optional but recommended)"
    HAS_TKN=false
else
    print_success "tkn CLI found"
    HAS_TKN=true
fi

# Check if files exist
if [ ! -f "$PIPELINE_FILE" ]; then
    print_error "Pipeline file not found: $PIPELINE_FILE"
    exit 1
fi
print_success "Pipeline file found: $PIPELINE_FILE"

if [ ! -f "$TRIGGER_FILE" ]; then
    print_error "Trigger file not found: $TRIGGER_FILE"
    exit 1
fi
print_success "Trigger file found: $TRIGGER_FILE"

# Step 1: YAML Syntax Validation
print_header "Step 1: Validating YAML Syntax"

echo "Validating pipeline YAML..."
if kubectl apply --dry-run=client -f "$PIPELINE_FILE" &> /dev/null; then
    print_success "Pipeline YAML syntax is valid"
else
    print_error "Pipeline YAML syntax validation failed"
    kubectl apply --dry-run=client -f "$PIPELINE_FILE"
    exit 1
fi

echo "Validating trigger YAML..."
if kubectl apply --dry-run=client -f "$TRIGGER_FILE" &> /dev/null; then
    print_success "Trigger YAML syntax is valid"
else
    print_error "Trigger YAML syntax validation failed"
    kubectl apply --dry-run=client -f "$TRIGGER_FILE"
    exit 1
fi

# Step 2: Check namespace
print_header "Step 2: Checking Namespace"

if kubectl get namespace "$NAMESPACE" &> /dev/null; then
    print_success "Namespace exists: $NAMESPACE"
else
    print_warning "Namespace does not exist: $NAMESPACE"
    echo -n "Do you want to create it? (y/n): "
    read -r response
    if [ "$response" = "y" ]; then
        kubectl create namespace "$NAMESPACE"
        print_success "Created namespace: $NAMESPACE"
    else
        print_error "Cannot proceed without namespace"
        exit 1
    fi
fi

# Step 3: Tekton Pipeline Structure Validation
if [ "$HAS_TKN" = true ]; then
    print_header "Step 3: Validating Pipeline Structure"

    if tkn pipeline describe odh-nightly-test-feast --filename "$PIPELINE_FILE" &> /dev/null; then
        print_success "Pipeline structure is valid"
        echo ""
        echo "Pipeline details:"
        tkn pipeline describe odh-nightly-test-feast --filename "$PIPELINE_FILE"
    else
        print_error "Pipeline structure validation failed"
        exit 1
    fi
fi

# Step 4: Cron Schedule Validation
print_header "Step 4: Validating Cron Schedule"

CRON_SCHEDULE=$(grep -A 1 "cron:" "$TRIGGER_FILE" | tail -1 | sed 's/.*"\(.*\)".*/\1/')
echo "Cron schedule: $CRON_SCHEDULE"

# Validate cron expression (basic check)
if [[ "$CRON_SCHEDULE" =~ ^[0-9*,-/]+[[:space:]][0-9*,-/]+[[:space:]][0-9*,-/]+[[:space:]][0-9*,-/]+[[:space:]][0-9*,-/]+$ ]]; then
    print_success "Cron expression format is valid: $CRON_SCHEDULE"
    echo "  Expected: 30 2 * * * (2:30 AM UTC = 8:00 AM IST)"
else
    print_error "Cron expression format is invalid"
    exit 1
fi

# Step 5: Ask user if they want to deploy and test
print_header "Step 5: Deploy and Test (Optional)"

echo "The following steps will:"
echo "  1. Apply the pipeline to namespace: $NAMESPACE"
echo "  2. Create a test PipelineRun"
echo "  3. Monitor the execution"
echo ""
echo -n "Do you want to proceed with deployment and testing? (y/n): "
read -r response

if [ "$response" != "y" ]; then
    print_warning "Skipping deployment and testing"
    echo ""
    echo -e "${GREEN}========================================${NC}"
    echo -e "${GREEN}Validation Complete!${NC}"
    echo -e "${GREEN}========================================${NC}"
    echo ""
    echo "Next steps:"
    echo "  1. Review the validation results above"
    echo "  2. To test manually, run:"
    echo "     kubectl apply -f $PIPELINE_FILE -n $NAMESPACE"
    echo "     kubectl apply -f $TEST_RUN_FILE -n $NAMESPACE"
    echo "  3. To monitor: tkn pipelinerun logs -f -n $NAMESPACE"
    exit 0
fi

# Step 6: Apply Pipeline
print_header "Step 6: Applying Pipeline to Cluster"

echo "Applying pipeline to namespace: $NAMESPACE"
if kubectl apply -f "$PIPELINE_FILE" -n "$NAMESPACE"; then
    print_success "Pipeline applied successfully"
else
    print_error "Failed to apply pipeline"
    exit 1
fi

# Verify pipeline was created
if [ "$HAS_TKN" = true ]; then
    sleep 2
    if tkn pipeline list -n "$NAMESPACE" | grep -q "odh-nightly-test-feast"; then
        print_success "Pipeline verified in cluster"
    else
        print_error "Pipeline not found in cluster"
        exit 1
    fi
fi

# Step 7: Create Test PipelineRun
print_header "Step 7: Creating Test PipelineRun"

echo "Note: This will provision an ephemeral cluster and run tests."
echo "This may take 30-60 minutes and will consume cloud resources."
echo ""
echo -n "Continue? (y/n): "
read -r response

if [ "$response" != "y" ]; then
    print_warning "Skipping test run creation"
    echo ""
    echo "Pipeline has been applied. To create a test run manually:"
    echo "  kubectl apply -f $TEST_RUN_FILE -n $NAMESPACE"
    exit 0
fi

if [ -f "$TEST_RUN_FILE" ]; then
    echo "Using test PipelineRun from: $TEST_RUN_FILE"
    kubectl apply -f "$TEST_RUN_FILE" -n "$NAMESPACE"
    PIPELINE_RUN_NAME="test-feast-nightly-run"
else
    echo "Creating PipelineRun using tkn CLI..."
    if [ "$HAS_TKN" = true ]; then
        PIPELINE_RUN_NAME=$(tkn pipeline start odh-nightly-test-feast \
            -n "$NAMESPACE" \
            --param git-url=https://github.com/feast-dev/feast.git \
            --param git-revision=master \
            --workspace name=git-auth,emptyDir="" \
            --output name | tail -1)
    else
        print_error "Cannot create PipelineRun without tkn CLI or test file"
        exit 1
    fi
fi

print_success "PipelineRun created: $PIPELINE_RUN_NAME"

# Step 8: Monitor Execution
print_header "Step 8: Monitoring Execution"

if [ "$HAS_TKN" = true ]; then
    echo "Following logs... (Press Ctrl+C to stop watching, pipeline will continue)"
    echo ""
    tkn pipelinerun logs "$PIPELINE_RUN_NAME" -f -n "$NAMESPACE" || true

    # Show final status
    echo ""
    print_header "Final Status"
    tkn pipelinerun describe "$PIPELINE_RUN_NAME" -n "$NAMESPACE"
else
    echo "Monitor the run with:"
    echo "  kubectl get pipelinerun $PIPELINE_RUN_NAME -n $NAMESPACE -w"
fi

# Summary
print_header "Test Complete"

echo "Pipeline Run: $PIPELINE_RUN_NAME"
echo "Namespace: $NAMESPACE"
echo ""
echo "To check status:"
echo "  kubectl get pipelinerun $PIPELINE_RUN_NAME -n $NAMESPACE"
echo ""
if [ "$HAS_TKN" = true ]; then
    echo "To view logs:"
    echo "  tkn pipelinerun logs $PIPELINE_RUN_NAME -n $NAMESPACE"
    echo ""
fi
echo "To cleanup:"
echo "  kubectl delete pipelinerun $PIPELINE_RUN_NAME -n $NAMESPACE"
echo ""

print_success "Testing complete!"
