import io
from unittest.mock import MagicMock, patch

from django.core.exceptions import ImproperlyConfigured
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, override_settings

from tower.asset_delivery import asset_key, asset_url
from tower.catalog import presentation_catalog
from tower.management.commands.upload_tower_assets import upload_inventory, verify_public
from tower.set_preview_views import preview_context


@override_settings(TOWER_SET_ASSET_BASE_URL="https://assets.example.com", TOWER_ASSET_BASE_URL="https://assets.example.com")
class AssetDeliveryTests(SimpleTestCase):
    def test_all_published_assets_and_frames_get_content_addressed_https_urls(self):
        rows = upload_inventory()
        self.assertEqual(len(rows), 88)
        for row in rows:
            url = asset_url(row["path"])
            self.assertEqual(url, f'https://assets.example.com/tower/{row["sha256"]}/{row["path"]}')
            self.assertIn(row["content_type"], ("image/png", "image/webp", "image/svg+xml"))
        self.assertTrue(presentation_catalog()["cards"][0]["frame_url"].startswith("https://assets.example.com/tower/"))
        preview = preview_context({})
        self.assertTrue(preview["background_url"].startswith("https://assets.example.com/tower/"))
        self.assertTrue(all(item["url"].startswith("https://assets.example.com/tower/") for item in preview["bundles"][0]["components"]))

    def test_invalid_remote_base_and_paths_are_rejected(self):
        for base in ("http://example.com", "https://user:secret@example.com", "https://example.com/?token=1", "//example.com", "https://example.com/#hash"):
            with self.subTest(base=base), override_settings(TOWER_SET_ASSET_BASE_URL=base):
                with self.assertRaises(ImproperlyConfigured):
                    asset_url("sets/nis-coder/v1/companion-placeholder.svg")
        for path in ("../settings.py", "sets/../../settings.py", "/sets/test.png", "sets\\test.png", "https://example.com/test.png"):
            with self.subTest(path=path), self.assertRaises(ImproperlyConfigured):
                asset_key(path)

    @override_settings(TOWER_SET_ASSET_BASE_URL="", STORAGES={"staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}})
    def test_unconfigured_cdn_keeps_local_files_working(self):
        self.assertEqual(asset_url("frames/common.webp"), "/static/tower/frames/common.webp")

    @patch("boto3.client")
    @patch("requests.Session")
    def test_dry_run_never_uploads_or_makes_network_requests(self, session, boto):
        output = io.StringIO()
        call_command("upload_tower_assets", stdout=output)
        self.assertIn("88 files", output.getvalue())
        boto.assert_not_called()
        session.assert_not_called()

    @patch.dict("os.environ", {}, clear=True)
    def test_missing_credentials_stop_before_upload(self):
        with self.assertRaisesMessage(CommandError, "R2_ACCOUNT_ID"):
            call_command("upload_tower_assets", upload=True, stdout=io.StringIO())

    def test_public_verification_checks_complete_bytes_and_rejects_corruption(self):
        row = upload_inventory()[0]
        session = MagicMock()
        response = session.get.return_value.__enter__.return_value
        response.iter_content.return_value = [row["local"].read_bytes()]
        verify_public(row, "https://assets.example.com", session)
        response.iter_content.return_value = [b"corrupted"]
        with self.assertRaises(ValueError):
            verify_public(row, "https://assets.example.com", session)
