"""Unit tests for k8s.smoke_ci_s3_test_dir."""

from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest import mock

from k8s import smoke_ci_s3_test_dir as s3_seed


class TestEnsureModelServerCiS3TestDir(unittest.TestCase):
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

    def test_uploads_missing_ir_objects_under_version_dir(self) -> None:
        client = mock.Mock()
        client.head_object.side_effect = _client_error("404")
        with mock.patch.object(s3_seed, "_s3_client", return_value=client):
            self.assertTrue(s3_seed.ensure_model_server_ci_s3_test_dir())
        uploaded_keys = {call.args[2] for call in client.upload_file.call_args_list}
        self.assertIn("test-dir/1/mnist.xml", uploaded_keys)
        self.assertIn("test-dir/1/mnist.bin", uploaded_keys)
        self.assertIn("openvino/model_repository/onnx/1/mnist.xml", uploaded_keys)
        self.assertIn("openvino/model_repository/onnx/1/mnist.bin", uploaded_keys)

    def test_skips_when_objects_exist(self) -> None:
        client = mock.Mock()
        with mock.patch.object(s3_seed, "_s3_client", return_value=client):
            self.assertTrue(s3_seed.ensure_model_server_ci_s3_test_dir())
        client.upload_file.assert_not_called()

    def test_raises_when_bucket_unset(self) -> None:
        os.environ.pop("CI_S3_BUCKET_NAME", None)
        os.environ.pop("MODELS_S3_BUCKET_NAME", None)
        with self.assertRaisesRegex(Exception, "CI_S3_BUCKET_NAME unset"):
            s3_seed.ensure_model_server_ci_s3_test_dir()


class TestModelRuntimePytestExtraArgs(unittest.TestCase):
    def test_skips_vllm_when_opt125m_missing(self) -> None:
        client = mock.Mock()
        client.head_object.side_effect = _client_error("404")
        with (
            mock.patch.object(s3_seed, "_resolve_ci_bucket", return_value=("b", "us-east-1", None, "k", "s")),
            mock.patch.object(s3_seed, "_s3_client", return_value=client),
        ):
            extra = s3_seed.model_runtime_pytest_extra_args()
        self.assertIn("TestVllmCpuX86S3Inference", extra)
        self.assertIn("TestTritonGRPC", extra)

    def test_skip_vllm_probe_defers_vllm_skips(self) -> None:
        extra = s3_seed.model_runtime_pytest_extra_args(skip_vllm_probe=True)
        self.assertIn("TestTritonGRPC", extra)
        self.assertNotIn("TestVllmCpuX86S3Inference", extra)


def _client_error(code: str):
    from botocore.exceptions import ClientError

    return ClientError({"Error": {"Code": code, "Message": "missing"}}, "HeadObject")


if __name__ == "__main__":
    unittest.main()
