"""Unit tests for k8s.smoke_ci_s3_test_dir."""

from __future__ import annotations

import os
import unittest
from unittest import mock

from k8s import smoke_ci_s3_test_dir as s3_probe


class TestResolveCiBucket(unittest.TestCase):
    def setUp(self) -> None:
        self.env = mock.patch.dict(
            os.environ,
            {
                "CI_S3_BUCKET_NAME": "mlflow-e2e",
                "CI_S3_BUCKET_REGION": "us-east-1",
                "AWS_ACCESS_KEY_ID": "test-key",
                "AWS_SECRET_ACCESS_KEY": "test-secret",
            },
            clear=False,
        )
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_raises_when_bucket_unset(self) -> None:
        os.environ.pop("CI_S3_BUCKET_NAME", None)
        os.environ.pop("MODELS_S3_BUCKET_NAME", None)
        with self.assertRaisesRegex(Exception, "CI_S3_BUCKET_NAME unset"):
            s3_probe._resolve_ci_bucket()


class TestLogCiS3Layout(unittest.TestCase):
    def test_log_model_server_warns_on_missing_markers(self) -> None:
        client = mock.Mock()
        client.head_object.side_effect = _client_error("404")
        with (
            mock.patch.object(
                s3_probe,
                "_resolve_ci_bucket",
                return_value=("b", "us-east-1", None, "k", "s"),
            ),
            mock.patch.object(s3_probe, "_s3_client", return_value=client),
            mock.patch("builtins.print") as print_mock,
        ):
            s3_probe.log_model_server_ci_s3_layout()
        printed = " ".join(str(c.args[0]) for c in print_mock.call_args_list)
        self.assertIn("WARN: CI S3 layout missing", printed)
        self.assertIn("test-dir/1/mnist.xml", printed)


class TestModelRuntimePytestExtraArgs(unittest.TestCase):
    def test_skips_vllm_when_opt125m_missing(self) -> None:
        client = mock.Mock()
        client.head_object.side_effect = _client_error("404")
        with (
            mock.patch.object(
                s3_probe,
                "_resolve_ci_bucket",
                return_value=("b", "us-east-1", None, "k", "s"),
            ),
            mock.patch.object(s3_probe, "_s3_client", return_value=client),
        ):
            extra = s3_probe.model_runtime_pytest_extra_args()
        self.assertIn("TestVllmCpuX86S3Inference", extra)
        self.assertIn("TestTritonGRPC", extra)

    def test_skip_s3_probe_defers_all_s3_skips(self) -> None:
        extra = s3_probe.model_runtime_pytest_extra_args(skip_s3_probe=True)
        self.assertEqual(extra, "")

    def test_allows_triton_grpc_when_inception_model_present(self) -> None:
        client = mock.Mock()
        client.head_object.return_value = {}
        with (
            mock.patch.object(
                s3_probe,
                "_resolve_ci_bucket",
                return_value=("b", "us-east-1", None, "k", "s"),
            ),
            mock.patch.object(s3_probe, "_s3_client", return_value=client),
            mock.patch.object(s3_probe, "ci_s3_object_ready", side_effect=[True, True]),
        ):
            extra = s3_probe.model_runtime_pytest_extra_args()
        self.assertNotIn("TestTritonGRPC", extra)


def _client_error(code: str):
    from botocore.exceptions import ClientError

    return ClientError({"Error": {"Code": code, "Message": "missing"}}, "HeadObject")


if __name__ == "__main__":
    unittest.main()
