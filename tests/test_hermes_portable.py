"""Integration check using Hermes's real portable plugin loader (CI runtime)."""
import tempfile
import unittest
from pathlib import Path

from hermes_cli.agent_plugins import load_agent_plugin

ROOT = Path(__file__).resolve().parents[1] / "hermes"


class HermesPortableIntegration(unittest.TestCase):
    def test_installable_package_loads_all_skills_and_gated_remote_transport(self):
        with tempfile.TemporaryDirectory() as data:
            package = load_agent_plugin(ROOT, Path(data))
        self.assertEqual(package.name, "kumiho-memory")
        self.assertEqual(package.diagnostics, ())
        self.assertEqual(
            {skill.name for skill in package.skills},
            {"kumiho-memory", "memory-capture", "kumiho-onboard",
             "kumiho-personalize", "dream-state", "kumiho-backfill"},
        )
        self.assertEqual(
            dict(package.mcp_servers),
            {"kumiho-memory": {
                "url": "https://mcp.kumiho.cloud/mcp",
                "strict_redirect_headers": True,
                "trust": "untrusted",
            }},
        )
        for skill in package.skills:
            self.assertTrue(skill.skill_md.is_file())
            self.assertTrue(skill.skill_md.resolve().is_relative_to(ROOT.resolve()))
        self.assertFalse(package.server_declarations)


if __name__ == "__main__":
    unittest.main()
