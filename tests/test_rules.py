import unittest

from netsplice.config import parse_config
from netsplice.rules import BLOCK, DIRECT, Destination, parse_rule


def route(config: dict, host: str, port: int) -> str:
    return parse_config(config).router.decide(Destination(host, port))


class RuleTests(unittest.TestCase):
    def test_default_applies_when_no_rule_matches(self):
        cfg = {"default": "block", "rules": [{"action": "direct", "match": {"port": [443]}}]}
        self.assertEqual(route(cfg, "example.com", 80), BLOCK)

    def test_default_is_direct_when_unset(self):
        self.assertEqual(route({}, "example.com", 80), DIRECT)

    def test_first_matching_rule_wins(self):
        cfg = {
            "upstreams": {"corp": {"type": "http", "host": "p", "port": 3128}},
            "rules": [
                {"action": "direct", "match": {"domain": ["example.com"]}},
                {"action": "corp", "match": {"domain": ["example.com"]}},
            ],
        }
        self.assertEqual(route(cfg, "example.com", 443), DIRECT)

    def test_domain_matches_exact_and_subdomains_only(self):
        cfg = {"rules": [{"action": "block", "match": {"domain": ["example.com"]}}]}
        self.assertEqual(route(cfg, "example.com", 1), BLOCK)
        self.assertEqual(route(cfg, "a.b.example.com", 1), BLOCK)
        self.assertEqual(route(cfg, "notexample.com", 1), DIRECT)
        self.assertEqual(route(cfg, "example.com.evil.net", 1), DIRECT)

    def test_domain_matching_ignores_case_trailing_dot_and_wildcard_prefix(self):
        cfg = {"rules": [{"action": "block", "match": {"domain": ["*.Example.COM."]}}]}
        self.assertEqual(route(cfg, "WWW.example.com.", 1), BLOCK)

    def test_cidr_matches_ip_destinations_only(self):
        cfg = {"rules": [{"action": "block", "match": {"cidr": ["10.0.0.0/8", "fd00::/8"]}}]}
        self.assertEqual(route(cfg, "10.1.2.3", 1), BLOCK)
        self.assertEqual(route(cfg, "fd12::1", 1), BLOCK)
        self.assertEqual(route(cfg, "11.0.0.1", 1), DIRECT)
        self.assertEqual(route(cfg, "intranet.example", 1), DIRECT)

    def test_port_list(self):
        cfg = {"rules": [{"action": "block", "match": {"port": [25, 465]}}]}
        self.assertEqual(route(cfg, "x.com", 465), BLOCK)
        self.assertEqual(route(cfg, "x.com", 443), DIRECT)

    def test_conditions_inside_one_rule_are_anded(self):
        cfg = {"rules": [{"action": "block", "match": {"domain": ["example.com"], "port": [22]}}]}
        self.assertEqual(route(cfg, "example.com", 22), BLOCK)
        self.assertEqual(route(cfg, "example.com", 443), DIRECT)

    def test_rule_without_match_is_catch_all(self):
        cfg = {"default": "block", "rules": [{"action": "direct"}]}
        self.assertEqual(route(cfg, "anything", 1), DIRECT)

    def test_upstream_action_is_accepted(self):
        cfg = {
            "upstreams": {"tor": {"type": "socks5", "host": "127.0.0.1", "port": 9050}},
            "rules": [{"action": "tor", "match": {"domain": ["onion.example"]}}],
        }
        self.assertEqual(route(cfg, "onion.example", 80), "tor")


class ValidationTests(unittest.TestCase):
    def assertRejects(self, cfg):
        with self.assertRaises(ValueError):
            parse_config(cfg)

    def test_unknown_action_is_rejected(self):
        self.assertRejects({"rules": [{"action": "nowhere", "match": {"port": [1]}}]})

    def test_unknown_default_is_rejected(self):
        self.assertRejects({"default": "nowhere"})

    def test_unknown_match_key_is_rejected(self):
        self.assertRejects({"rules": [{"action": "direct", "match": {"ip": ["1.2.3.4"]}}]})

    def test_bad_cidr_is_rejected(self):
        self.assertRejects({"rules": [{"action": "direct", "match": {"cidr": ["not-a-net"]}}]})

    def test_bad_port_is_rejected(self):
        self.assertRejects({"rules": [{"action": "direct", "match": {"port": [0]}}]})
        self.assertRejects({"rules": [{"action": "direct", "match": {"port": [70000]}}]})
        self.assertRejects({"rules": [{"action": "direct", "match": {"port": [True]}}]})

    def test_reserved_upstream_name_is_rejected(self):
        self.assertRejects({"upstreams": {"direct": {"type": "http", "host": "p", "port": 1}}})

    def test_upstream_needs_known_type_and_fields(self):
        self.assertRejects({"upstreams": {"x": {"type": "ftp", "host": "p", "port": 1}}})
        self.assertRejects({"upstreams": {"x": {"type": "http", "port": 1}}})
        self.assertRejects({"upstreams": {"x": {"type": "http", "host": "p"}}})

    def test_listen_must_be_host_port(self):
        self.assertRejects({"listen": "1080"})
        self.assertRejects({"listen": "127.0.0.1:notaport"})

    def test_parse_rule_accepts_single_string_values(self):
        rule = parse_rule({"action": "block", "match": {"domain": "example.com", "port": 25}})
        self.assertEqual(rule.domains, ("example.com",))
        self.assertEqual(rule.ports, frozenset({25}))


if __name__ == "__main__":
    unittest.main()
