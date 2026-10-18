import json
import unittest

from majlis.tunnel import accepted_url


class RegistrationTests(unittest.TestCase):
    def event(self, data):
        return json.dumps({"type": "v1.tcpip_forward.register.accepted", "data": data})

    def test_realistic_qr_payload_keeps_https_address(self):
        data = (
            "dbe23b54b4dc45.lhr.life tunneled with tls termination, https://dbe23b54b4dc45.lhr.life\r\nOpen your tunnel with QR:\r\n"
            + "\x1b[7m  \x1b[0m" * 1200
        )
        self.assertGreater(len(data), 4096)
        self.assertEqual(accepted_url(self.event(data)), "https://dbe23b54b4dc45.lhr.life")

    def test_large_payload_is_rejected(self):
        self.assertIsNone(accepted_url(self.event("https://example.lhr.life\n" + "x" * 70000)))

    def test_untrusted_domain_is_rejected(self):
        self.assertIsNone(accepted_url(self.event("https://example.lhr.life.attacker.example")))

    def test_url_outside_prefix_is_rejected(self):
        self.assertIsNone(accepted_url(self.event("x" * 5000 + " https://example.lhr.life")))


if __name__ == "__main__":
    unittest.main()
