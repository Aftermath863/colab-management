"""Integration tests for ColabClient against Mock Colab Suite."""

import base64
import unittest
import uuid

from backend.app.services.colab_client import (
    ColabClient,
    QuotaExceededError,
    TooManyAssignmentsError,
    generate_colab_notebook_url,
    strip_xssi_prefix,
    uuid_to_web_safe_base64,
)
from backend.tests.mock_colab.mock_hub import MockControlHub


class TestColabClient(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.hub = MockControlHub()
        cls.hub.start()
        cls.control_url = cls.hub.urls["control_plane"]
        cls.proxy_url = cls.hub.urls["runtime_proxy"]

    @classmethod
    def tearDownClass(cls):
        cls.hub.stop()

    def setUp(self):
        self.hub.reset()
        self.client = ColabClient(base_url=self.control_url)
        self.test_token = "mock_valid_access_token_123"

    def test_strip_xssi_prefix(self):
        prefixed = ")]}'\n{\"key\": \"val\"}"
        self.assertEqual(strip_xssi_prefix(prefixed), "{\"key\": \"val\"}")
        plain = "{\"key\": \"val\"}"
        self.assertEqual(strip_xssi_prefix(plain), plain)

    def test_uuid_to_web_safe_base64(self):
        test_uuid = uuid.UUID("12345678-1234-5678-1234-567812345678")
        transformed = uuid_to_web_safe_base64(test_uuid)
        self.assertEqual(len(transformed), 44)
        self.assertNotIn("-", transformed)
        self.assertTrue(transformed.endswith("........"))
        self.assertEqual(transformed, "12345678_1234_5678_1234_567812345678........")

    def test_generate_colab_notebook_url(self):
        email = "developer@gmail.com"
        endpoint = "t-12345678-abcd"
        url = generate_colab_notebook_url(email, endpoint)

        self.assertIn("authuser=developer%40gmail.com", url)
        self.assertIn("dbu=%2Ftun%2Fm%2Ft-12345678-abcd", url)
        self.assertTrue(url.endswith("#datalabBackendUrl=https://colab.research.google.com/tun/m/t-12345678-abcd"))

    def test_get_ccu_info(self):
        ccu = self.client.get_ccu_info(self.test_token)
        self.assertIn("current_balance", ccu)
        self.assertIn("consumption_rate_hourly", ccu)
        self.assertIn("assignments_count", ccu)
        self.assertGreater(ccu["current_balance"], 0.0)

    def test_two_step_assign_and_unassign(self):
        nbh = uuid_to_web_safe_base64(uuid.uuid4())

        # 1. Assign T4 instance
        res = self.client.assign(
            access_token=self.test_token,
            notebook_hash=nbh,
            accelerator="T4",
            shape="standard",
        )
        self.assertIn("endpoint", res)
        self.assertIn("runtimeProxyInfo", res)
        endpoint = res["endpoint"]
        proxy_info = res["runtimeProxyInfo"]
        self.assertTrue(proxy_info["url"].startswith("http"))
        self.assertTrue(len(proxy_info["token"]) > 0)

        # 2. Verify assignments list includes endpoint
        assignments = self.client.list_assignments(self.test_token)
        endpoints = [a.get("endpoint") for a in assignments]
        self.assertIn(endpoint, endpoints)

        # 3. Idempotent assign probe returns existing assignment
        res_idempotent = self.client.assign(
            access_token=self.test_token,
            notebook_hash=nbh,
            accelerator="T4",
            shape="standard",
        )
        self.assertEqual(res_idempotent["endpoint"], endpoint)

        # 4. Unassign instance
        unassigned = self.client.unassign(self.test_token, endpoint)
        self.assertTrue(unassigned)

        # 5. Verify assignment removed
        remaining = self.client.list_assignments(self.test_token)
        remaining_endpoints = [a.get("endpoint") for a in remaining]
        self.assertNotIn(endpoint, remaining_endpoints)

    def test_high_ram_accelerator_shape_rule(self):
        nbh = uuid_to_web_safe_base64(uuid.uuid4())
        # Requesting shape="hm" for L4 must not send shape=hm (omitted) and succeed
        res = self.client.assign(
            access_token=self.test_token,
            notebook_hash=nbh,
            accelerator="L4",
            shape="hm",
        )
        self.assertIn("endpoint", res)
        self.client.unassign(self.test_token, res["endpoint"])

    def test_quota_exceeded_error_mapping(self):
        # Configure mock control plane to reject accelerator with HTTP 400
        self.hub.control_plane.state.quota_unavailable_accelerators.add("A100")
        nbh = uuid_to_web_safe_base64(uuid.uuid4())

        with self.assertRaises(QuotaExceededError):
            self.client.assign(
                access_token=self.test_token,
                notebook_hash=nbh,
                accelerator="A100",
            )

    def test_too_many_assignments_error_mapping(self):
        # Configure mock control plane concurrency limit to 0 to trigger HTTP 412
        self.hub.control_plane.state.concurrency_limit = 0
        nbh = uuid_to_web_safe_base64(uuid.uuid4())

        with self.assertRaises(TooManyAssignmentsError):
            self.client.assign(
                access_token=self.test_token,
                notebook_hash=nbh,
                accelerator="T4",
            )

    def test_runtime_data_plane_vfs_operations(self):
        # Assign an instance to get runtime proxy credentials
        nbh = uuid_to_web_safe_base64(uuid.uuid4())
        assign_res = self.client.assign(self.test_token, nbh)
        proxy_info = assign_res["runtimeProxyInfo"]
        p_url = proxy_info["url"]
        p_tok = proxy_info["token"]

        # 1. List contents of root directory
        root_dir = self.client.list_contents(p_url, p_tok, "")
        self.assertEqual(root_dir.get("type"), "directory")
        self.assertIn("content", root_dir)

        # 2. Upload file
        filename = "train.py"
        code_text = "print('Hello Colab Cloud')\n"
        code_b64 = base64.b64encode(code_text.encode("utf-8")).decode("ascii")
        upload_res = self.client.upload_file(
            p_url, p_tok, filename, code_b64, file_name=filename
        )
        self.assertEqual(upload_res.get("name"), filename)

        # 3. Download file
        download_res = self.client.download_file(p_url, p_tok, filename)
        self.assertEqual(download_res.get("format"), "base64")
        decoded_text = base64.b64decode(download_res["content"]).decode("utf-8")
        self.assertEqual(decoded_text, code_text)

        # 4. Delete file
        deleted = self.client.delete_file(p_url, p_tok, filename)
        self.assertTrue(deleted)

        # Clean up
        self.client.unassign(self.test_token, assign_res["endpoint"])


if __name__ == "__main__":
    unittest.main()
