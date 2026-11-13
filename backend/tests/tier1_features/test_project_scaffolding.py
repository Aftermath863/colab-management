"""Tier 1 Feature Tests: Project Scaffolding, Architecture Spec, and Layout Compliance (F0)."""

import json
from pathlib import Path
import tomllib
import unittest


class TestProjectScaffolding(unittest.TestCase):
    """Verify architecture spec consistency, manifests, and .agents layout compliance."""

    @classmethod
    def setUpClass(cls):
        cls.root_dir = Path(__file__).resolve().parents[3]

    def test_architecture_spec_and_layout_compliance(self):
        """Verify docs/ARCHITECTURE.md port 1113 specification and clean .agents layout."""
        # 1. Architecture doc existence and port compliance
        arch_path = self.root_dir / "docs" / "ARCHITECTURE.md"
        self.assertTrue(arch_path.is_file(), "docs/ARCHITECTURE.md must exist")
        arch_content = arch_path.read_text(encoding="utf-8")
        self.assertGreater(len(arch_content), 100, "docs/ARCHITECTURE.md must be populated")

        # Port 1113 must be specified for Web and API
        self.assertIn("1113", arch_content, "ARCHITECTURE.md must explicitly document port 1113")
        legacy_port = "80" + "80"
        self.assertNotIn(f"{legacy_port}:{legacy_port}", arch_content, f"Old port {legacy_port} must be eradicated")

        # 2. Layout compliance: no suite test files (test_*.py) or runtime DBs inside .agents/
        agents_dir = self.root_dir / ".agents"
        if agents_dir.is_dir():
            for p in agents_dir.rglob("*"):
                if p.is_file():
                    # No test suite files in .agents
                    self.assertFalse(
                        p.name.startswith("test_") and p.suffix == ".py",
                        f"Layout violation: Test suite file found in .agents: {p}",
                    )
                    # No database files in .agents
                    self.assertNotIn(
                        p.suffix.lower(),
                        {".db", ".sqlite", ".sqlite3"},
                        f"Layout violation: Database file found in .agents: {p}",
                    )
                    # AGENTS.md or GEMINI.md must never exist
                    self.assertNotIn(
                        p.name.upper(),
                        {"AGENTS.MD", "GEMINI.MD"},
                        f"Layout violation: Forbidden system prompt file in .agents: {p}",
                    )

    def test_manifests_parse_and_metadata(self):
        """Verify backend/pyproject.toml and frontend/package.json syntax and metadata."""
        # Backend pyproject.toml
        pyproject_path = self.root_dir / "backend" / "pyproject.toml"
        self.assertTrue(pyproject_path.is_file(), "backend/pyproject.toml must exist")
        with open(pyproject_path, "rb") as f:
            pyproject_data = tomllib.load(f)

        project_info = pyproject_data.get("project", {})
        self.assertEqual(project_info.get("name"), "colab-management-backend")
        self.assertEqual(project_info.get("requires-python"), ">=3.12")

        deps = project_info.get("dependencies", [])
        dep_str = " ".join(deps).lower()
        for required_lib in ["fastapi", "uvicorn", "pydantic", "cryptography", "asyncssh", "aiosqlite"]:
            self.assertIn(required_lib, dep_str, f"Missing required dependency: {required_lib}")

        # Frontend package.json
        pkg_json_path = self.root_dir / "frontend" / "package.json"
        self.assertTrue(pkg_json_path.is_file(), "frontend/package.json must exist")
        with open(pkg_json_path, "r", encoding="utf-8") as f:
            pkg_data = json.load(f)

        self.assertEqual(pkg_data.get("name"), "colab-management-frontend")
        fe_deps = pkg_data.get("dependencies", {})
        self.assertIn("react", fe_deps)
        self.assertIn("19", fe_deps["react"])
        self.assertTrue(
            "xterm" in fe_deps or "@xterm/xterm" in fe_deps,
            "xterm dependency missing from package.json",
        )
        self.assertIn("@monaco-editor/react", fe_deps)

    def test_readme_and_repo_structure(self):
        """Verify README.md documents port 1113 and core project layout directories exist."""
        readme_path = self.root_dir / "README.md"
        self.assertTrue(readme_path.is_file(), "README.md must exist at repository root")
        content = readme_path.read_text(encoding="utf-8")
        self.assertIn("1113", content, "README.md must document port 1113")
        legacy_port = "80" + "80"
        self.assertNotIn(f"{legacy_port}:{legacy_port}", content, f"Deprecated port {legacy_port}:{legacy_port} must not appear in README.md")

        for required_dir in ["backend/app", "backend/tests", "frontend/src", "docs"]:
            dir_path = self.root_dir / required_dir
            self.assertTrue(dir_path.is_dir(), f"Required directory missing: {required_dir}")

        gitignore_path = self.root_dir / ".gitignore"
        self.assertTrue(gitignore_path.is_file(), ".gitignore must exist")
        gi_content = gitignore_path.read_text(encoding="utf-8")
        for excluded in ["__pycache__", ".venv", "*.db", "*.key", "data/"]:
            self.assertIn(excluded, gi_content, f"Critical exclusion missing from .gitignore: {excluded}")



if __name__ == "__main__":
    unittest.main()
