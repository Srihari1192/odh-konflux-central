#!/bin/bash

# Quick validation script for Feast Nightly Pipeline
# This script only validates syntax and structure without deploying

set -e

# Colors
GREEN='\033[0;32m'
RED='\033[0;31m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}Feast Nightly Pipeline - Quick Validation${NC}"
echo -e "${BLUE}========================================${NC}"
echo ""

PIPELINE_FILE="feast-nightly-pipeline.yaml"
TRIGGER_FILE=".tekton/feast-nightly-trigger.yaml"
ERRORS=0

# Function to check file
check_file() {
    local file=$1
    local description=$2

    echo -n "Checking $description... "

    if [ ! -f "$file" ]; then
        echo -e "${RED}✗ File not found${NC}"
        ERRORS=$((ERRORS + 1))
        return 1
    fi

    if kubectl apply --dry-run=client -f "$file" &> /dev/null; then
        echo -e "${GREEN}✓ Valid${NC}"
        return 0
    else
        echo -e "${RED}✗ Invalid${NC}"
        echo "Error details:"
        kubectl apply --dry-run=client -f "$file"
        ERRORS=$((ERRORS + 1))
        return 1
    fi
}

# Check kubectl is available
if ! command -v kubectl &> /dev/null; then
    echo -e "${RED}✗ kubectl is not installed${NC}"
    echo "Please install kubectl to run validation"
    exit 1
fi

echo -e "${BLUE}1. Validating YAML Files${NC}"
echo ""

check_file "$PIPELINE_FILE" "Pipeline YAML"
check_file "$TRIGGER_FILE" "Trigger YAML"

echo ""
echo -e "${BLUE}2. Validating Cron Schedule${NC}"
echo ""

CRON_SCHEDULE=$(grep -A 1 "cron:" "$TRIGGER_FILE" | tail -1 | sed 's/.*"\(.*\)".*/\1/' || echo "")

if [ -z "$CRON_SCHEDULE" ]; then
    echo -e "${RED}✗ Cron schedule not found${NC}"
    ERRORS=$((ERRORS + 1))
else
    echo "Cron schedule: $CRON_SCHEDULE"
    if [[ "$CRON_SCHEDULE" =~ ^[0-9*,-/]+[[:space:]][0-9*,-/]+[[:space:]][0-9*,-/]+[[:space:]][0-9*,-/]+[[:space:]][0-9*,-/]+$ ]]; then
        echo -e "${GREEN}✓ Valid cron expression${NC}"

        # Check if it's the expected schedule
        if [ "$CRON_SCHEDULE" = "30 2 * * *" ]; then
            echo -e "${GREEN}✓ Matches expected schedule (2:30 AM UTC = 8:00 AM IST)${NC}"
        else
            echo -e "${YELLOW}⚠ Schedule differs from expected: 30 2 * * *${NC}"
        fi
    else
        echo -e "${RED}✗ Invalid cron expression format${NC}"
        ERRORS=$((ERRORS + 1))
    fi
fi

echo ""
echo -e "${BLUE}3. Checking Pipeline Structure${NC}"
echo ""

# Check for required tasks
if grep -q "name: fetch-snapshot" "$PIPELINE_FILE"; then
    echo -e "${GREEN}✓ fetch-snapshot task found${NC}"
else
    echo -e "${RED}✗ fetch-snapshot task not found${NC}"
    ERRORS=$((ERRORS + 1))
fi

if grep -q "name: provision-eaas-space" "$PIPELINE_FILE"; then
    echo -e "${GREEN}✓ provision-eaas-space task found${NC}"
else
    echo -e "${RED}✗ provision-eaas-space task not found${NC}"
    ERRORS=$((ERRORS + 1))
fi

if grep -q "name: provision-cluster" "$PIPELINE_FILE"; then
    echo -e "${GREEN}✓ provision-cluster task found${NC}"
else
    echo -e "${RED}✗ provision-cluster task not found${NC}"
    ERRORS=$((ERRORS + 1))
fi

if grep -q "name: deploy-and-test" "$PIPELINE_FILE"; then
    echo -e "${GREEN}✓ deploy-and-test task found${NC}"
else
    echo -e "${RED}✗ deploy-and-test task not found${NC}"
    ERRORS=$((ERRORS + 1))
fi

# Check for finally block
if grep -q "finally:" "$PIPELINE_FILE"; then
    echo -e "${GREEN}✓ finally block found${NC}"
else
    echo -e "${YELLOW}⚠ finally block not found${NC}"
fi

echo ""
echo -e "${BLUE}4. Checking Parameters${NC}"
echo ""

# Check required parameters
declare -a params=("git-url" "git-revision" "oci-artifacts-repo" "artifact-browser-url")

for param in "${params[@]}"; do
    if grep -q "name: $param" "$PIPELINE_FILE"; then
        echo -e "${GREEN}✓ Parameter '$param' defined${NC}"
    else
        echo -e "${YELLOW}⚠ Parameter '$param' not found${NC}"
    fi
done

echo ""
echo -e "${BLUE}5. Checking Workspaces${NC}"
echo ""

if grep -q "name: git-auth" "$PIPELINE_FILE"; then
    echo -e "${GREEN}✓ git-auth workspace defined${NC}"
else
    echo -e "${YELLOW}⚠ git-auth workspace not found${NC}"
fi

# Summary
echo ""
echo -e "${BLUE}========================================${NC}"
if [ $ERRORS -eq 0 ]; then
    echo -e "${GREEN}✓ All validations passed!${NC}"
    echo -e "${BLUE}========================================${NC}"
    echo ""
    echo "The pipeline is ready for testing."
    echo ""
    echo "Next steps:"
    echo "  1. Run full test: ./test-nightly-pipeline.sh"
    echo "  2. Or apply manually:"
    echo "     kubectl apply -f $PIPELINE_FILE -n odh-integration-tests"
    echo "     kubectl apply -f test-pipelinerun.yaml -n odh-integration-tests"
    exit 0
else
    echo -e "${RED}✗ Validation failed with $ERRORS error(s)${NC}"
    echo -e "${BLUE}========================================${NC}"
    echo ""
    echo "Please fix the errors above before proceeding."
    exit 1
fi
