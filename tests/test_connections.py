from __future__ import annotations

import unittest
from unittest import mock
from pathlib import Path

import yaml

from kvlib import connections


class Connections(unittest.TestCase):
    def test_organization_tokens_are_rendered_from_local_config(self):
        connections.clear_cache()
        rendered = connections.render("카카오벤처스 / {{company_name}} / {{shared_inbox}}")
        name = connections.get("organization.name")
        inbox = connections.get("organization.shared_inbox")
        self.assertEqual(rendered, f"{name} / {name} / {inbox}")

    def test_webflow_fallback_excludes_configured_domain_and_aliases(self):
        from skills.mail import clean

        config = {"organization.own_addresses": ["inbox@alias.example.com"],
                  "organization.internal_domain": "@newteam.example.com"}
        body = "staff@newteam.example.com\ninbox@alias.example.com\nfounder@example.org"
        with mock.patch.object(clean.connections, "get", side_effect=config.__getitem__):
            self.assertEqual(clean.webflow(body)["reply_to"], "founder@example.org")

    def test_example_deployment_closes_every_external_gate(self):
        path = Path(__file__).resolve().parents[1] / "deployment.example.yaml"
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        values = [value for section in raw.values() for value in section.values()]
        self.assertNotIn("on", values)
        self.assertNotIn("slack", values)
        self.assertNotIn(True, values)


if __name__ == "__main__":
    unittest.main()
