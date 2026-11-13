"""Tier 1 Feature Tests: Docker Containerization Specifications (F6)."""

from pathlib import Path
import re
import unittest
import yaml


class TestDockerContainerization(unittest.TestCase):
    """Verify Dockerfile multi-stage specification and docker-compose.yml configuration."""

    @classmethod
    def setUpClass(cls):
        cls.root_dir = Path(__file__).resolve().parents[3]
        cls.dockerfile_path = cls.root_dir / "Dockerfile"
        cls.compose_path = cls.root_dir / "docker-compose.yml"

    def test_dockerfile_multi_stage_spec(self):
        """Verify Dockerfile multi-stage directives, exposed ports, healthcheck, and uvicorn CMD."""
        self.assertTrue(self.dockerfile_path.is_file(), "Dockerfile must exist at repository root")
        content = self.dockerfile_path.read_text(encoding="utf-8")

        # Stage 1: Node 22 build stage
        self.assertRegex(
            content,
            r"FROM\s+node:22[^\s]*\s+AS\s+frontend-builder",
            "Dockerfile must include Stage 1 Node 22 frontend builder",
        )
        self.assertIn("npm run build", content)

        # Stage 2: Python 3.12 runtime daemon stage
        self.assertRegex(
            content,
            r"FROM\s+python:3\.12[^\s]*\s+AS\s+runtime",
            "Dockerfile must include Stage 2 Python 3.12 runtime daemon",
        )

        # Exposed ports
        self.assertRegex(content, r"EXPOSE\s+1113", "Dockerfile must expose port 1113")
        self.assertRegex(content, r"EXPOSE\s+2220-2229", "Dockerfile must expose port range 2220-2229")

        # Volume mounts
        self.assertIn('VOLUME ["/data", "/workspace"]', content)

        # Healthcheck
        self.assertIn("HEALTHCHECK", content)
        self.assertIn("http://127.0.0.1:1113/healthz", content)

        # CMD launching uvicorn on port 1113
        self.assertIn('"uvicorn"', content)
        self.assertIn('"1113"', content)
        legacy_port = "80" + "80"
        self.assertNotIn(legacy_port, content, f"Dockerfile must not contain references to deprecated port {legacy_port}")

    def test_docker_compose_config_spec(self):
        """Verify docker-compose.yml services, port mappings, volumes, and healthcheck."""
        self.assertTrue(self.compose_path.is_file(), "docker-compose.yml must exist at repository root")
        with open(self.compose_path, "r", encoding="utf-8") as f:
            compose_data = yaml.safe_load(f)

        services = compose_data.get("services", {})
        self.assertIn("colab-manager", services, "Service 'colab-manager' must be defined")

        service = services["colab-manager"]

        # Ports mapping
        ports = service.get("ports", [])
        port_strings = [str(p) for p in ports]
        self.assertTrue(
            any("1113:1113" in p for p in port_strings),
            f"Port 1113:1113 mapping missing from {port_strings}",
        )
        self.assertTrue(
            any("2220-2229:2220-2229" in p for p in port_strings),
            f"SSH port pool 2220-2229:2220-2229 mapping missing from {port_strings}",
        )

        # Volumes mapping
        volumes = service.get("volumes", [])
        vol_strings = [str(v) for v in volumes]
        self.assertTrue(any("./data:/data" in v for v in vol_strings))
        self.assertTrue(any("./workspace:/workspace" in v for v in vol_strings))

        # Environment
        env = service.get("environment", {})
        if isinstance(env, list):
            env_map = {}
            for item in env:
                if "=" in item:
                    k, v = item.split("=", 1)
                    env_map[k] = v
            env = env_map
        self.assertEqual(str(env.get("PORT")), "1113")

        # Healthcheck
        hc = service.get("healthcheck", {})
        hc_test = " ".join(hc.get("test", []))
        self.assertIn("1113/healthz", hc_test)

    def test_docker_compose_service_environment_and_restart(self):
        """Verify docker-compose.yml restart policy, container name, and environment variables."""
        with open(self.compose_path, "r", encoding="utf-8") as f:
            compose_data = yaml.safe_load(f)

        service = compose_data.get("services", {}).get("colab-manager", {})
        self.assertEqual(service.get("restart"), "unless-stopped")
        self.assertEqual(service.get("container_name"), "colab-manager")

        env = service.get("environment", {})
        if isinstance(env, list):
            env_map = {}
            for item in env:
                if "=" in item:
                    k, v = item.split("=", 1)
                    env_map[k] = v
            env = env_map
        self.assertIn("PORT", env)
        self.assertIn("ADMIN_PASSWORD", env)
        self.assertIn("MASTER_KEY", env)
        self.assertEqual(str(env.get("TZ")), "Asia/Shanghai")



if __name__ == "__main__":
    unittest.main()
