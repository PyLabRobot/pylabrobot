"""Mutual TLS with a private demo CA and distinct server/client identities."""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import grpc
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID


@dataclass(frozen=True)
class Certificates:
  directory: Path

  def read(self, name: str) -> bytes:
    return (self.directory / name).read_bytes()

  def client_credentials(self) -> grpc.ChannelCredentials:
    return grpc.ssl_channel_credentials(
      root_certificates=self.read("ca.pem"),
      private_key=self.read("client-key.pem"),
      certificate_chain=self.read("client.pem"),
    )

  def server_credentials(self) -> grpc.ServerCredentials:
    return grpc.ssl_server_credentials(
      [(self.read("server-key.pem"), self.read("server.pem"))],
      root_certificates=self.read("ca.pem"),
      require_client_auth=True,
    )


def create_certificates(directory: Path) -> Certificates:
  """Create seven-day loopback-only test identities in a new private directory."""
  directory.mkdir(mode=0o700, parents=True, exist_ok=False)
  now = datetime.now(timezone.utc)
  ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
  ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Celigo simulator demo CA")])

  def builder(subject, public_key):
    return (
      x509.CertificateBuilder()
      .subject_name(subject)
      .issuer_name(ca_name)
      .public_key(public_key)
      .serial_number(x509.random_serial_number())
      .not_valid_before(now - timedelta(minutes=1))
      .not_valid_after(now + timedelta(days=7))
    )

  ca = (
    builder(ca_name, ca_key.public_key())
    .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
    .add_extension(x509.KeyUsage(False, False, False, False, False, True, True, False, False), True)
    .sign(ca_key, hashes.SHA256())
  )
  (directory / "ca.pem").write_bytes(ca.public_bytes(serialization.Encoding.PEM))
  for name, purpose in (
    ("server", ExtendedKeyUsageOID.SERVER_AUTH),
    ("client", ExtendedKeyUsageOID.CLIENT_AUTH),
  ):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, f"celigo-sim-{name}")])
    cert = (
      builder(subject, key.public_key())
      .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
      .add_extension(x509.ExtendedKeyUsage([purpose]), critical=False)
      .add_extension(
        x509.KeyUsage(True, False, True, False, False, False, False, False, False), True
      )
    )
    if name == "server":
      cert = cert.add_extension(
        x509.SubjectAlternativeName(
          [x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]
        ),
        critical=False,
      )
    signed = cert.sign(ca_key, hashes.SHA256())
    (directory / f"{name}.pem").write_bytes(signed.public_bytes(serialization.Encoding.PEM))
    key_path = directory / f"{name}-key.pem"
    key_path.write_bytes(
      key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
      )
    )
    key_path.chmod(0o600)
  # The CA private key is never retained; these are disposable demo credentials.
  return Certificates(directory)
