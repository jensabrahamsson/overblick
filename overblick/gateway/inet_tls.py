"""
TLS certificate handling for the Internet Gateway.

Three modes:
1. Provided cert: tls_cert_path + tls_key_path from config
2. Auto self-signed: Generated via cryptography.x509, stored in data dir
3. No TLS: Only allowed with host=127.0.0.1 (dev mode)
"""

import datetime
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def _generate_self_signed(
    cert_path: Path,
    key_path: Path,
    hostname: str = "localhost",
    valid_days: int = 365,
) -> None:
    """Generate a self-signed TLS certificate and private key.

    Uses the cryptography library (already a core dependency).
    """
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, hostname),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Överblick Internet Gateway"),
    ])

    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=valid_days))
        .add_extension(
            x509.SubjectAlternativeName([
                x509.DNSName("localhost"),
                x509.DNSName(hostname),
                x509.IPAddress(
                    __import__("ipaddress").ip_address("127.0.0.1")
                ),
                x509.IPAddress(
                    __import__("ipaddress").ip_address("::1")
                ),
            ]),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )

    cert_path.parent.mkdir(parents=True, exist_ok=True)

    with open(key_path, "wb") as f:
        f.write(key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        ))

    with open(cert_path, "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))

    logger.info(
        "Generated self-signed TLS certificate: %s (valid %d days)",
        cert_path, valid_days,
    )


def resolve_tls(
    tls_cert_path: str,
    tls_key_path: str,
    tls_auto_selfsigned: bool,
    data_dir: Path,
    host: str = "0.0.0.0",
) -> Optional[tuple[str, str]]:
    """Resolve TLS certificate and key paths.

    Returns:
        Tuple of (cert_path, key_path) if TLS enabled, None if disabled.

    Raises:
        FileNotFoundError: If provided cert/key paths don't exist.
        RuntimeError: If TLS disabled on non-localhost host.
    """
    # Mode 1: Provided certificates
    if tls_cert_path and tls_key_path:
        cert = Path(tls_cert_path)
        key = Path(tls_key_path)

        if not cert.exists():
            raise FileNotFoundError(f"TLS certificate not found: {cert}")
        if not key.exists():
            raise FileNotFoundError(f"TLS key not found: {key}")

        logger.info("Using provided TLS certificate: %s", cert)
        return str(cert), str(key)

    # Mode 2: Auto self-signed
    if tls_auto_selfsigned:
        tls_dir = data_dir / "tls"
        cert = tls_dir / "server.crt"
        key = tls_dir / "server.key"

        if not cert.exists() or not key.exists():
            logger.info("Generating self-signed TLS certificate...")
            _generate_self_signed(cert, key)
        else:
            # Check if cert is still valid (regenerate if expired)
            try:
                from cryptography import x509 as x509_mod

                with open(cert, "rb") as f:
                    loaded_cert = x509_mod.load_pem_x509_certificate(f.read())

                now = datetime.datetime.now(datetime.timezone.utc)
                if loaded_cert.not_valid_after_utc < now:
                    logger.warning("Self-signed certificate expired, regenerating...")
                    _generate_self_signed(cert, key)
                else:
                    days_left = (loaded_cert.not_valid_after_utc - now).days
                    logger.info(
                        "Using existing self-signed certificate (%d days remaining)",
                        days_left,
                    )
            except Exception as e:
                logger.warning("Failed to check certificate validity: %s", e)
                _generate_self_signed(cert, key)

        return str(cert), str(key)

    # Mode 3: No TLS — only allowed for localhost
    if host != "127.0.0.1":
        raise RuntimeError(
            f"TLS is disabled but host is {host}. "
            "Plaintext on public interfaces is not allowed. "
            "Set tls_auto_selfsigned=true or provide TLS certificates."
        )

    logger.warning("TLS disabled — running in dev mode (localhost only)")
    return None
