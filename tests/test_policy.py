import unittest
from app.policy import allowed_url, public_addresses, blocked, in_window

class PolicyTests(unittest.TestCase):
    def test_only_exact_demo_origin(self):
        self.assertTrue(allowed_url("http://mock_oem:8003/parts"))
        for url in ["http://169.254.169.254/latest", "http://mock_oem.evil:8003/",
                    "http://user:pass@mock_oem:8003/", "file:///etc/passwd",
                    "http://mock_oem:9000/", "http://localhost:8003/"]:
            self.assertFalse(allowed_url(url), url)
    def test_reject_private_dns_and_mixed_answers(self):
        self.assertFalse(public_addresses(["127.0.0.1"]))
        self.assertFalse(public_addresses(["8.8.8.8", "10.0.0.1"]))
        self.assertFalse(public_addresses(["::1"]))
        self.assertTrue(public_addresses(["8.8.8.8"]))
    def test_stop_on_block(self):
        self.assertTrue(blocked(429, ""))
        self.assertTrue(blocked(200, "Verify you are human"))
        self.assertFalse(blocked(200, "Parts catalogue"))
    def test_window(self):
        import app.policy as policy
        old = policy.START, policy.END
        try:
            policy.START, policy.END = 22, 6
            self.assertTrue(in_window(23))
            self.assertTrue(in_window(5))
            self.assertFalse(in_window(12))
        finally:
            policy.START, policy.END = old

if __name__ == "__main__":
    unittest.main()
