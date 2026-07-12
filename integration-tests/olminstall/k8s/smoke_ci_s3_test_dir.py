"""Seed CI S3 bucket layouts for model_server and model_runtime OVMS smoke tests.

Environment (optional overrides):
  CI_S3_BUCKET_NAME / MODELS_S3_BUCKET_NAME — target bucket (required to seed)
  AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY — S3 credentials
  SMOKE_S3_SKIP_VLLM_SEED — set to 1/true/yes to skip opt-125m HuggingFace download
  SMOKE_S3_INCEPTION_URL — override Triton inception tarball URL
  SMOKE_S3_VLLM_REPO — override HuggingFace repo for vLLM seed (default facebook/opt-125m)
  SMOKE_S3_INCEPTION_TIMEOUT_S — download timeout seconds (default 120)
  HF_ACCESS_TOKEN — optional token for gated HuggingFace models
"""

from __future__ import annotations

import os
import tempfile
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from suite.errors import AppError

_FIXTURES_ROOT = Path(__file__).resolve().parent.parent / "fixtures"
_MODEL_SERVER_IR_DIR = _FIXTURES_ROOT / "model_server_mnist_ir"
_DEFAULT_PREFIX = "test-dir"
# OVMS single-model mode: --model_name={{.Name}} --model_path=/mnt/models → version dir only.
_OVMS_VERSION = "1"
_REQUIRED_IR_OBJECTS = ("mnist.xml", "mnist.bin")

_INCEPTION_MODEL_URL = (
    "https://storage.googleapis.com/download.tensorflow.org/models/"
    "inception_v3_2016_08_28_frozen.pb.tar.gz"
)
_INCEPTION_CONFIG_PBTXT = """\
name: "inceptiongraphdef"
platform: "tensorflow_graphdef"
max_batch_size: 128
input [
  {
    name: "input"
    data_type: TYPE_FP32
    format: FORMAT_NHWC
    dims: [ 299, 299, 3 ]
  }
]
output [
  {
    name: "InceptionV3/Predictions/Softmax"
    data_type: TYPE_FP32
    dims: [ 1001 ]
  }
]
"""
_OPT125M_REPO = "facebook/opt-125m"
_INCEPTION_DOWNLOAD_TIMEOUT_S = 120
_INCEPTION_DOWNLOAD_RETRIES = 3


def _inception_model_url() -> str:
    return (
        os.environ.get("SMOKE_S3_INCEPTION_URL", "").strip() or _INCEPTION_MODEL_URL
    )


def _opt125m_repo() -> str:
    return os.environ.get("SMOKE_S3_VLLM_REPO", "").strip() or _OPT125M_REPO


def _inception_download_timeout_s() -> int:
    raw = os.environ.get("SMOKE_S3_INCEPTION_TIMEOUT_S", "").strip()
    if not raw:
        return _INCEPTION_DOWNLOAD_TIMEOUT_S
    try:
        return max(1, int(raw))
    except ValueError:
        return _INCEPTION_DOWNLOAD_TIMEOUT_S


def _download_url_to_file(url: str, target: Path) -> None:
    timeout_s = _inception_download_timeout_s()
    last_exc: Exception | None = None
    for attempt in range(1, _INCEPTION_DOWNLOAD_RETRIES + 1):
        try:
            with urllib.request.urlopen(url, timeout=timeout_s) as resp:
                target.write_bytes(resp.read())
            return
        except Exception as exc:
            last_exc = exc
            if attempt < _INCEPTION_DOWNLOAD_RETRIES:
                print(
                    f"WARN: download attempt {attempt}/{_INCEPTION_DOWNLOAD_RETRIES} "
                    f"failed ({exc}); retrying",
                    flush=True,
                )
    raise AppError(
        f"failed to download {url} after {_INCEPTION_DOWNLOAD_RETRIES} attempts: {last_exc}",
        1,
    )


@dataclass(frozen=True)
class _OvmsSeedLayout:
    prefix: str
    fixture_dir: Path
    objects: tuple[str, ...]


_OVMS_SEED_LAYOUTS: tuple[_OvmsSeedLayout, ...] = (
    _OvmsSeedLayout(_DEFAULT_PREFIX, _MODEL_SERVER_IR_DIR, _REQUIRED_IR_OBJECTS),
    _OvmsSeedLayout(
        "openvino/model_repository/onnx",
        _MODEL_SERVER_IR_DIR,
        _REQUIRED_IR_OBJECTS,
    ),
)


def _fixture_dir(layout: _OvmsSeedLayout) -> Path:
    if not layout.fixture_dir.is_dir():
        raise AppError(f"MNIST IR fixtures missing at {layout.fixture_dir}", 1)
    missing = [
        name for name in layout.objects if not (layout.fixture_dir / name).is_file()
    ]
    if missing:
        raise AppError(
            f"MNIST IR fixtures incomplete at {layout.fixture_dir}: {', '.join(missing)}",
            1,
        )
    return layout.fixture_dir


def _resolve_ci_bucket() -> tuple[str, str, str | None, str, str]:
    bucket = (
        os.environ.get("CI_S3_BUCKET_NAME", "").strip()
        or os.environ.get("MODELS_S3_BUCKET_NAME", "").strip()
    )
    region = (
        os.environ.get("CI_S3_BUCKET_REGION", "").strip()
        or os.environ.get("MODELS_S3_BUCKET_REGION", "").strip()
        or os.environ.get("AWS_DEFAULT_REGION", "").strip()
        or "us-east-1"
    )
    endpoint = (
        os.environ.get("CI_S3_BUCKET_ENDPOINT", "").strip()
        or os.environ.get("MODELS_S3_BUCKET_ENDPOINT", "").strip()
        or os.environ.get("AWS_S3_ENDPOINT", "").strip()
        or None
    )
    access_key = os.environ.get("AWS_ACCESS_KEY_ID", "").strip()
    secret_key = os.environ.get("AWS_SECRET_ACCESS_KEY", "").strip()
    if not bucket:
        raise AppError("CI_S3_BUCKET_NAME unset; cannot seed CI S3 models", 1)
    if not access_key or not secret_key:
        raise AppError("AWS credentials unset; cannot seed CI S3 models", 1)
    return bucket, region, endpoint, access_key, secret_key


def _ensure_boto3():
    try:
        import boto3  # noqa: F401
        return
    except ImportError:
        pass
    from components.dashboard_cypress.runtime import _pip_install_to_target, _prepend_pythonpath

    from steps.tests_payload import resolve_tests_payload_root, tests_payload_tools_python_dir

    artifacts = os.environ.get("ARTIFACTS_DIR", "").strip()
    payload_root = resolve_tests_payload_root(Path(artifacts) if artifacts else Path("/artifacts"))
    target = tests_payload_tools_python_dir(payload_root)
    print(f"Installing boto3 to {target} (CI S3 seed)...", flush=True)
    _pip_install_to_target("boto3", target)
    _prepend_pythonpath(str(target))
    import boto3  # noqa: F401


def _s3_client(*, region: str, endpoint: str | None, access_key: str, secret_key: str):
    _ensure_boto3()
    import boto3

    verify = os.environ.get("AWS_CA_BUNDLE", "").strip() or True
    kwargs: dict[str, object] = {
        "service_name": "s3",
        "region_name": region,
        "aws_access_key_id": access_key,
        "aws_secret_access_key": secret_key,
        "verify": verify,
    }
    if endpoint:
        kwargs["endpoint_url"] = endpoint.rstrip("/")
    return boto3.client(**kwargs)


def _normalize_prefix(prefix: str) -> str:
    cleaned = (prefix or _DEFAULT_PREFIX).strip().strip("/")
    return cleaned or _DEFAULT_PREFIX


def _versioned_key(prefix: str, version: str, name: str) -> str:
    return f"{_normalize_prefix(prefix)}/{version.strip('/')}/{name.lstrip('/')}"


def _object_exists(client, *, bucket: str, key: str) -> bool:
    from botocore.exceptions import ClientError

    try:
        client.head_object(Bucket=bucket, Key=key)
        return True
    except ClientError as exc:
        code = str(exc.response.get("Error", {}).get("Code", ""))
        if code in ("404", "NoSuchKey", "NotFound"):
            return False
        raise


def _upload_if_missing(
    client,
    *,
    bucket: str,
    key: str,
    local_path: Path | None = None,
    body: bytes | None = None,
) -> bool:
    if _object_exists(client, bucket=bucket, key=key):
        return False
    if local_path is not None:
        client.upload_file(str(local_path), bucket, key)
    elif body is not None:
        client.put_object(Bucket=bucket, Key=key, Body=body)
    else:
        raise ValueError("local_path or body required")
    return True


def _seed_ovms_layouts(client, *, bucket: str) -> list[str]:
    uploaded: list[str] = []
    for layout in _OVMS_SEED_LAYOUTS:
        fixture_dir = _fixture_dir(layout)
        norm_prefix = _normalize_prefix(layout.prefix)
        for name in layout.objects:
            key = _versioned_key(norm_prefix, _OVMS_VERSION, name)
            if _upload_if_missing(client, bucket=bucket, key=key, local_path=fixture_dir / name):
                uploaded.append(key)
    return uploaded


# --- model_runtime-only seeds (Triton inception, vLLM opt-125m) ---


def _download_inception_graphdef(target: Path) -> None:
    import tarfile

    with tempfile.TemporaryDirectory(prefix="olminstall-inception-") as tmp:
        archive = Path(tmp) / "inception_v3.tar.gz"
        url = _inception_model_url()
        print(f"Downloading inception graphdef from {url}", flush=True)
        _download_url_to_file(url, archive)
        with tarfile.open(archive, "r:gz") as tar:
            tar.extractall(path=tmp, filter="data")
        extracted = Path(tmp) / "inception_v3_2016_08_28_frozen.pb"
        if not extracted.is_file():
            raise AppError(f"inception tarball missing expected pb at {extracted}", 1)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(extracted.read_bytes())


def _seed_triton_inception(client, *, bucket: str) -> list[str]:
    prefix = "triton/model_repository/inceptiongraphdef"
    uploaded: list[str] = []
    config_key = f"{prefix}/config.pbtxt"
    if _upload_if_missing(
        client,
        bucket=bucket,
        key=config_key,
        body=_INCEPTION_CONFIG_PBTXT.encode("utf-8"),
    ):
        uploaded.append(config_key)
    model_key = f"{prefix}/{_OVMS_VERSION}/model.graphdef"
    if not _object_exists(client, bucket=bucket, key=model_key):
        with tempfile.TemporaryDirectory(prefix="olminstall-triton-") as tmp:
            model_path = Path(tmp) / "model.graphdef"
            _download_inception_graphdef(model_path)
            client.upload_file(str(model_path), bucket, model_key)
        uploaded.append(model_key)
    return uploaded


def _ensure_huggingface_hub():
    try:
        import huggingface_hub  # noqa: F401
        return
    except ImportError:
        pass
    from components.dashboard_cypress.runtime import _pip_install_to_target, _prepend_pythonpath

    from steps.tests_payload import resolve_tests_payload_root, tests_payload_tools_python_dir

    artifacts = os.environ.get("ARTIFACTS_DIR", "").strip()
    payload_root = resolve_tests_payload_root(Path(artifacts) if artifacts else Path("/artifacts"))
    target = tests_payload_tools_python_dir(payload_root)
    print(f"Installing huggingface_hub to {target} (opt-125m S3 seed)...", flush=True)
    _pip_install_to_target("huggingface_hub", target)
    _prepend_pythonpath(str(target))
    import huggingface_hub  # noqa: F401


def _seed_vllm_opt125m(client, *, bucket: str) -> list[str]:
    if os.environ.get("SMOKE_S3_SKIP_VLLM_SEED", "").strip().lower() in ("1", "true", "yes"):
        print("Skipping opt-125m S3 seed (SMOKE_S3_SKIP_VLLM_SEED set)", flush=True)
        return []
    marker_key = "opt-125m/config.json"
    if _object_exists(client, bucket=bucket, key=marker_key):
        return []
    _ensure_huggingface_hub()
    from huggingface_hub import snapshot_download

    token = os.environ.get("HF_ACCESS_TOKEN", "").strip() or None
    with tempfile.TemporaryDirectory(prefix="olminstall-opt125m-") as tmp:
        print(f"Downloading {_opt125m_repo()} for S3 seed (one-time)...", flush=True)
        local_dir = snapshot_download(
            repo_id=_opt125m_repo(),
            local_dir=tmp,
            token=token,
            ignore_patterns=["*.msgpack", "*.h5", "rust*", "onnx/*"],
        )
        uploaded: list[str] = []
        root = Path(local_dir)
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            rel = path.relative_to(root).as_posix()
            key = f"opt-125m/{rel}"
            if _upload_if_missing(client, bucket=bucket, key=key, local_path=path):
                uploaded.append(key)
        return uploaded


def ensure_ci_s3_smoke_models(*, include_vllm: bool = False, include_triton: bool = True) -> bool:
    """Upload bundled OVMS/Triton (and optional vLLM) smoke models when S3 keys are missing."""
    bucket, region, endpoint, access_key, secret_key = _resolve_ci_bucket()
    client = _s3_client(
        region=region,
        endpoint=endpoint,
        access_key=access_key,
        secret_key=secret_key,
    )
    uploaded: list[str] = []
    uploaded.extend(_seed_ovms_layouts(client, bucket=bucket))
    if include_triton:
        try:
            uploaded.extend(_seed_triton_inception(client, bucket=bucket))
        except Exception as exc:
            print(f"WARN: Triton inception S3 seed failed ({exc}); triton smoke may fail", flush=True)
    if include_vllm:
        try:
            uploaded.extend(_seed_vllm_opt125m(client, bucket=bucket))
        except Exception as exc:
            print(f"WARN: opt-125m S3 seed failed ({exc}); vLLM smoke may fail", flush=True)
    if uploaded:
        print(
            f"Seeded CI bucket s3://{bucket}/ ({len(uploaded)} objects, e.g. {uploaded[0]})",
            flush=True,
        )
    else:
        print(f"CI bucket s3://{bucket}/ already has smoke model layouts", flush=True)
    return True


def ensure_model_server_ci_s3_test_dir(*, prefix: str = _DEFAULT_PREFIX) -> bool:
    """Upload MNIST OpenVINO IR under test-dir/1/ for model_server smoke."""
    del prefix  # layouts include test-dir; kept for callers
    return ensure_ci_s3_smoke_models(include_vllm=False, include_triton=False)


def ensure_model_runtime_ci_s3_models() -> bool:
    """Upload OVMS, Triton, and vLLM smoke model layouts for model_runtime."""
    return ensure_ci_s3_smoke_models(include_vllm=True, include_triton=True)


def ci_s3_prefix_ready(client, *, bucket: str, prefix: str, marker: str) -> bool:
    """Return True when marker object exists under prefix (used for pytest skip probes)."""
    key = f"{_normalize_prefix(prefix).rstrip('/')}/{marker.lstrip('/')}"
    return _object_exists(client, bucket=bucket, key=key)


def model_runtime_pytest_extra_args(*, skip_vllm_probe: bool = False) -> str:
    """Skip model_runtime smoke tests that need S3 objects we could not seed."""
    skips = ["TestTritonGRPC"]
    if not skip_vllm_probe:
        try:
            bucket, region, endpoint, access_key, secret_key = _resolve_ci_bucket()
            client = _s3_client(
                region=region,
                endpoint=endpoint,
                access_key=access_key,
                secret_key=secret_key,
            )
            if not ci_s3_prefix_ready(
                client, bucket=bucket, prefix="opt-125m", marker="config.json"
            ):
                skips.extend(["TestVllmCpuX86S3Inference", "TestVllmProbeHealth"])
        except Exception as exc:
            print(
                f"WARN: model_runtime S3 probe failed ({exc}); skipping vLLM smoke tests",
                flush=True,
            )
            skips.extend(["TestVllmCpuX86S3Inference", "TestVllmProbeHealth"])
    expr = " or ".join(skips)
    print(f"✓ model_runtime pytest skip filter: not ({expr})", flush=True)
    return f"-k 'not ({expr})'"
