"""Tests for TLS certificate handling."""

import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from overblick.gateway.inet_tls import _generate_self_signed, resolve_tls


class TestGenerateSelfSigned:
    """Tests for _generate_self_signed function."""

    def test_generates_cert_and_key(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cert_path = Path(tmpdir) / "server.crt"
            key_path = Path(tmpdir) / "server.key"

            _generate_self_signed(cert_path, key_path, hostname="test.example.com")

            assert cert_path.exists()
            assert key_path.exists()

            # Check file permissions
            assert oct(key_path.stat().st_mode)[-3:] == "600"
            assert oct(cert_path.stat().st_mode)[-3:] == "644"

            # Check file contents
            cert_content = cert_path.read_text()
            key_content = key_path.read_text()

            assert "BEGIN CERTIFICATE" in cert_content
            assert "END CERTIFICATE" in cert_content
            assert "BEGIN PRIVATE KEY" in key_content or "BEGIN RSA PRIVATE KEY" in key_content
            assert "END PRIVATE KEY" in key_content or "END RSA PRIVATE KEY" in key_content

    def test_generates_with_default_hostname(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cert_path = Path(tmpdir) / "server.crt"
            key_path = Path(tmpdir) / "server.key"

            _generate_self_signed(cert_path, key_path)

            assert cert_path.exists()
            assert key_path.exists()

    def test_creates_parent_directories(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cert_path = Path(tmpdir) / "deep" / "dir" / "server.crt"
            key_path = Path(tmpdir) / "deep" / "dir" / "server.key"

            _generate_self_signed(cert_path, key_path)

            assert cert_path.exists()
            assert key_path.exists()


class TestResolveTLS:
    """Tests for resolve_tls function."""

    def test_provided_certificates(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cert_path = Path(tmpdir) / "cert.pem"
            key_path = Path(tmpdir) / "key.pem"

            # Create dummy certificate files
            cert_path.write_text("-----BEGIN CERTIFICATE-----\ndummy\n-----END CERTIFICATE-----")
            key_path.write_text("-----BEGIN PRIVATE KEY-----\ndummy\n-----END PRIVATE KEY-----")

            result = resolve_tls(
                tls_cert_path=str(cert_path),
                tls_key_path=str(key_path),
                tls_auto_selfsigned=False,
                data_dir=Path(tmpdir),
                host="0.0.0.0",
            )

            assert result is not None
            cert_result, key_result = result
            assert cert_result == str(cert_path)
            assert key_result == str(key_path)

    def test_provided_certificate_not_found(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cert_path = Path(tmpdir) / "cert.pem"
            key_path = Path(tmpdir) / "key.pem"

            # Only create key, not cert
            key_path.write_text("-----BEGIN PRIVATE KEY-----\ndummy\n-----END PRIVATE KEY-----")

            with pytest.raises(FileNotFoundError, match="TLS certificate not found"):
                resolve_tls(
                    tls_cert_path=str(cert_path),
                    tls_key_path=str(key_path),
                    tls_auto_selfsigned=False,
                    data_dir=Path(tmpdir),
                    host="0.0.0.0",
                )

    def test_provided_key_not_found(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cert_path = Path(tmpdir) / "cert.pem"
            key_path = Path(tmpdir) / "key.pem"

            # Only create cert, not key
            cert_path.write_text("-----BEGIN CERTIFICATE-----\ndummy\n-----END CERTIFICATE-----")

            with pytest.raises(FileNotFoundError, match="TLS key not found"):
                resolve_tls(
                    tls_cert_path=str(cert_path),
                    tls_key_path=str(key_path),
                    tls_auto_selfsigned=False,
                    data_dir=Path(tmpdir),
                    host="0.0.0.0",
                )

    def test_auto_selfsigned_generates_new(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = Path(tmpdir)

            result = resolve_tls(
                tls_cert_path="",
                tls_key_path="",
                tls_auto_selfsigned=True,
                data_dir=data_dir,
                host="0.0.0.0",
            )

            assert result is not None
            cert_path, key_path = result

            assert Path(cert_path).exists()
            assert Path(key_path).exists()
            assert Path(cert_path).parent == data_dir / "tls"
            assert Path(key_path).parent == data_dir / "tls"

    def test_auto_selfsigned_reuses_existing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = Path(tmpdir)
            tls_dir = data_dir / "tls"
            tls_dir.mkdir()

            cert_path = tls_dir / "server.crt"
            key_path = tls_dir / "server.key"

            # Create existing certificate
            _generate_self_signed(cert_path, key_path)

            # Mock the entire validation check to avoid datetime issues
            with patch("overblick.gateway.inet_tls._generate_self_signed") as mock_generate:
                result = resolve_tls(
                    tls_cert_path="",
                    tls_key_path="",
                    tls_auto_selfsigned=True,
                    data_dir=data_dir,
                    host="0.0.0.0",
                )

                assert result is not None
                assert result[0] == str(cert_path)
                assert result[1] == str(key_path)
                # Should not regenerate since cert exists
                mock_generate.assert_not_called()

    def test_no_tls_on_localhost_allowed(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            result = resolve_tls(
                tls_cert_path="",
                tls_key_path="",
                tls_auto_selfsigned=False,
                data_dir=Path(tmpdir),
                host="127.0.0.1",
            )

            assert result is None

    def test_no_tls_on_public_host_raises_error(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with pytest.raises(RuntimeError, match="TLS is disabled but host is 0.0.0.0"):
                resolve_tls(
                    tls_cert_path="",
                    tls_key_path="",
                    tls_auto_selfsigned=False,
                    data_dir=Path(tmpdir),
                    host="0.0.0.0",
                )

    def test_auto_selfsigned_regenerates_expired_cert(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = Path(tmpdir)
            tls_dir = data_dir / "tls"
            tls_dir.mkdir()

            cert_path = tls_dir / "server.crt"
            key_path = tls_dir / "server.key"

            # Create existing certificate
            _generate_self_signed(cert_path, key_path)

            # Mock the validation to raise an exception (simulating expired cert)
            with patch("overblick.gateway.inet_tls._generate_self_signed") as mock_generate:
                # Make the validation fail by patching the import inside the function
                with patch("cryptography.x509") as mock_x509:
                    mock_x509.load_pem_x509_certificate.side_effect = Exception(
                        "Certificate expired"
                    )

                    result = resolve_tls(
                        tls_cert_path="",
                        tls_key_path="",
                        tls_auto_selfsigned=True,
                        data_dir=data_dir,
                        host="0.0.0.0",
                    )

                    # Should regenerate on error
                    assert result is not None
                    assert result[0] == str(cert_path)
                    assert result[1] == str(key_path)
                    # Should have regenerated
                    mock_generate.assert_called_once()

    def test_auto_selfsigned_handles_cert_validation_error(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = Path(tmpdir)
            tls_dir = data_dir / "tls"
            tls_dir.mkdir()

            cert_path = tls_dir / "server.crt"
            key_path = tls_dir / "server.key"

            # Create existing certificate
            _generate_self_signed(cert_path, key_path)

            # Mock the validation to raise an exception
            with patch("overblick.gateway.inet_tls._generate_self_signed") as mock_generate:
                # Make the validation fail by patching the import inside the function
                with patch("cryptography.x509") as mock_x509:
                    mock_x509.load_pem_x509_certificate.side_effect = Exception("Validation failed")

                    result = resolve_tls(
                        tls_cert_path="",
                        tls_key_path="",
                        tls_auto_selfsigned=True,
                        data_dir=data_dir,
                        host="0.0.0.0",
                    )

                    # Should regenerate on error
                    assert result is not None
                    assert result[0] == str(cert_path)
                    assert result[1] == str(key_path)
                    # Should have regenerated
                    mock_generate.assert_called_once()
