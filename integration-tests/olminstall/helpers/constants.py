"""Shared defaults for Konflux olminstall tooling."""

DEFAULT_NAMESPACE = "rhoai-tenant"
DEFAULT_APP = "testops-playpen"
DEFAULT_PRODUCT = "rhoai"
PRODUCT_CHOICES = ("rhoai", "odh")
DEFAULT_LIST_COUNT = 10
# How many recent PipelineRuns to scan for --list-supported-ocp (newest first).
LIST_SUPPORTED_OCP_MAX_PRS = 40
DEFAULT_KONFLUX_UI = ""
DEFAULT_KA_HOST = ""
DEFAULT_KONFLUX_SERVER = ""
PENDING_REASONS = {"", "PipelineRunPending", "ResolvingPipelineRef"}

# Non-secret CLI context stored on Snapshot / PipelineRun for watch and archive UX.
OLMINSTALL_WRITE_ANNOTATION_KEYS = (
    "olminstall.product",
    "olminstall.update-channel",
    "olminstall.rhoai-version",
    "olminstall.ocp-version",
    "olminstall.scripts-repo-url",
    "olminstall.scripts-repo-revision",
)
# Order when printing from existing PipelineRuns (includes run-owner from annotate).
OLMINSTALL_CTX_PRINT_KEYS = OLMINSTALL_WRITE_ANNOTATION_KEYS + ("olminstall.run-owner",)
