"""
Generate a self-signed SSL certificate for the ITU Tracking API server.
Run once: python generate_cert.py
Produces cert.pem and key.pem in the same directory.

WARNING: The private key (key.pem) is stored WITHOUT encryption.
Ensure file permissions are restricted (e.g., chmod 600 on Linux,
or restrict NTFS permissions to Administrators only on Windows).
"""
import datetime
import ipaddress
import os
from cryptography import x509
from cryptography.x509.oid import NameOID
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa

BASE_DIR = os.path.dirname(__file__)
CERT_FILE = os.path.join(BASE_DIR, 'cert.pem')
KEY_FILE  = os.path.join(BASE_DIR, 'key.pem')

# Generate private key
key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

# Build certificate
subject = issuer = x509.Name([
    x509.NameAttribute(NameOID.COUNTRY_NAME, "CH"),
    x509.NameAttribute(NameOID.ORGANIZATION_NAME, "ITU"),
    x509.NameAttribute(NameOID.COMMON_NAME, "156.106.168.185"),
])

cert = (
    x509.CertificateBuilder()
    .subject_name(subject)
    .issuer_name(issuer)
    .public_key(key.public_key())
    .serial_number(x509.random_serial_number())
    .not_valid_before(datetime.datetime.now(datetime.timezone.utc))
    .not_valid_after(datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=3650))
    .add_extension(
        x509.SubjectAlternativeName([
            x509.IPAddress(ipaddress.IPv4Address("156.106.168.185")),
            x509.DNSName("localhost"),
            x509.IPAddress(ipaddress.IPv4Address("127.0.0.1")),
        ]),
        critical=False,
    )
    .sign(key, hashes.SHA256())
)

# Write key
with open(KEY_FILE, "wb") as f:
    f.write(key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    ))

# Write cert
with open(CERT_FILE, "wb") as f:
    f.write(cert.public_bytes(serialization.Encoding.PEM))

print(f"Generated: {CERT_FILE}")
print(f"Generated: {KEY_FILE}")
