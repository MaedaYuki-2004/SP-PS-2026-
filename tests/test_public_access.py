import unittest
from core.public_access import client_key, public_url


class PublicAccessTests(unittest.TestCase):
    def test_direct_and_disabled_proxy_ignore_spoofed_headers(self):
        headers = {"CF-Connecting-IP": "203.0.113.5", "X-Forwarded-For": "203.0.113.6"}
        self.assertEqual(client_key("192.168.1.4", headers, "cloudflare"), "192.168.1.4")
        self.assertEqual(client_key("127.0.0.1", headers, "none"), "127.0.0.1")

    def test_cloudflare_uses_only_valid_cf_ip(self):
        self.assertEqual(client_key("::1", {"CF-Connecting-IP": "2001:db8::1"}, "cloudflare"), "2001:db8::1")
        for value in ["", "invalid", "203.0.113.5, 203.0.113.6"]:
            self.assertEqual(client_key("127.0.0.1", {"CF-Connecting-IP": value,
                "X-Forwarded-For": "203.0.113.9"}, "cloudflare"), "127.0.0.1")

    def test_ngrok_uses_last_ip(self):
        self.assertEqual(client_key("127.0.0.1", {"X-Forwarded-For": "bad, 203.0.113.6"}, "ngrok"), "203.0.113.6")

    def test_public_url_validation(self):
        self.assertEqual(public_url("https://example.com"), "https://example.com/")
        for value in ["", "http://example.com", "https://user:pw@example.com",
                      "https://example.com/?secret=x", "https://example.com/#x",
                      "https://localhost", "https://example.com:bad", "https://exa mple.com"]:
            with self.assertRaises(ValueError):
                public_url(value)


if __name__ == "__main__":
    unittest.main()
