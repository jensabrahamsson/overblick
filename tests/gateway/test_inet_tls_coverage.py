"""Additional tests for inet_tls — cover lines 138-139."""

import datetime
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from overblick.gateway.inet_tls import _generate_self_signed, resolve_tls


class TestInetTlsCoverage:
    def test_auto_selfsigned_regenerates_truly_expired_cert(self):
        """Cover lines 138-139: cert is expired, regenerate."""
        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = Path(tmpdir)
            tls_dir = data_dir / "tls"
            tls_dir.mkdir()

            cert_path = tls_dir / "server.crt"
            key_path = tls_dir / "server.key"

            # Generate a valid cert first
            _generate_self_signed(cert_path, key_path)

            # Mock the certificate to appear expired
            mock_cert = MagicMock()
            mock_cert.not_valid_after_utc = datetime.datetime(
                2020, 1, 1, tzinfo=datetime.UTC
            )

            with patch("overblick.gateway.inet_tls._generate_self_signed") as mock_gen:
                with patch("cryptography.x509.load_pem_x509_certificate", return_value=mock_cert):
                    result = resolve_tls(
                        tls_cert_path="",
                        tls_key_path="",
                        tls_auto_selfsigned=True,
                        data_dir=data_dir,
                        host="0.0.0.0",
                    )

                    assert result is not None
                    mock_gen.assert_called_once()
