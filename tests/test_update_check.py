import unittest
from unittest.mock import MagicMock, patch

import requests

from control.app import update_check


class _Response:
    def __init__(self, payload, status=200):
        self.payload, self.status_code = payload, status

    def json(self):
        return self.payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(response=self)


class UpdateCheckTests(unittest.TestCase):
    def setUp(self):
        update_check._cache = None
        direct = patch.object(update_check, "_network_selection", return_value={
            "proxy_mode": "direct", "proxy_url": "", "proxy_country": ""})
        direct.start()
        self.addCleanup(direct.stop)
        public = patch.object(update_check, "edition", return_value="public")
        public.start()
        self.addCleanup(public.stop)

    def test_newer_release_is_reported_without_applying_it(self):
        newer = list(update_check._version_tuple(update_check.VERSION))
        newer[-1] += 1
        payload = {"tag_name": "v" + ".".join(map(str, newer)),
                   "html_url": "https://example.invalid/release",
                   "published_at": "2026-08-01T00:00:00Z", "body": "notes"}
        with patch("control.app.update_check.requests.Session.get",
                   return_value=_Response(payload)):
            result = update_check.check(True)
        self.assertTrue(result["update_available"])
        self.assertEqual(result["current"], update_check.VERSION)
        self.assertNotIn("apply", result)

    def test_semantic_comparison(self):
        self.assertGreater(update_check._version_tuple("v1.10.0"), update_check._version_tuple("1.9.9"))

    def test_update_network_defaults_to_direct_and_rejects_bad_manual_url(self):
        self.assertEqual(update_check.validate_network_settings(None)["proxy_mode"], "direct")
        with self.assertRaises(update_check.UpdateNetworkError):
            update_check.validate_network_settings({"proxy_mode": "manual",
                                                     "proxy_url": "ftp://example.test/file"})

    def test_repository_can_be_overridden_without_changing_the_ui(self):
        self.assertEqual(update_check.repository(), "MddIdd/mdd-sim-gateway")
        with patch.dict("os.environ", {"MDD_UPDATE_REPOSITORY": "example/private"}):
            self.assertEqual(update_check.repository(), "example/private")

    def test_public_release_request_never_sends_a_github_token(self):
        payload = {"tag_name": "v1.0.0"}
        captured = {}

        def get(url, headers, timeout):
            captured["authorization"] = headers.get("Authorization")
            return _Response(payload)

        with patch.dict("os.environ", {"MDD_GITHUB_TOKEN": "must-not-be-used"}), patch(
                "control.app.update_check.requests.Session.get", side_effect=get):
            update_check.check(True)
        self.assertIsNone(captured["authorization"])

    def test_private_repository_does_not_prompt_for_authentication(self):
        with patch("control.app.update_check.requests.Session.get",
                   return_value=_Response({}, 401)):
            result = update_check.check(True)
        self.assertEqual(result["error_code"], "update.error.no_public_release")
        self.assertNotIn("auth", result["error"].lower())

    def test_country_exit_is_used_as_socks_proxy(self):
        session = MagicMock()
        session.proxies = {}
        session.get.return_value = _Response({"tag_name": "v1.0.0"})
        with patch.object(update_check, "_network_selection", return_value={
                "proxy_mode": "country", "proxy_url": "", "proxy_country": "us"}), \
                patch.object(update_check, "_proxy_url",
                             return_value="socks5h://172.17.0.1:22538"), \
                patch("control.app.update_check.requests.Session", return_value=session):
            update_check.check(True)
        self.assertFalse(session.trust_env)
        self.assertEqual(session.proxies["https"], "socks5h://172.17.0.1:22538")

    def test_full_edition_refuses_the_public_release_channel(self):
        with patch.object(update_check, "edition", return_value="full"), \
                patch("control.app.update_check.requests.Session.get") as get:
            result = update_check.check(True)
        self.assertEqual(result["error_code"], "update.error.private_channel")
        get.assert_not_called()


if __name__ == "__main__":
    unittest.main()
