"""
Generate self-signed SSL certificate for HTTPS
Run this once: python generate_cert.py
"""
from cryptography import x509
from cryptography.x509.oid import NameOID
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization
import datetime
import ipaddress
import os

def generate_ssl_cert(cert_file='cert.pem', key_file='key.pem', ip_address='156.106.168.213'):
    """Generate a self-signed SSL certificate."""
    
    # Generate private key
    key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
        backend=default_backend()
    )
    
    # Create certificate
    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COUNTRY_NAME, "CH"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "ITU"),
        x509.NameAttribute(NameOID.COMMON_NAME, ip_address),
    ])
    
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.datetime.utcnow())
        .not_valid_after(datetime.datetime.utcnow() + datetime.timedelta(days=365))
        .add_extension(
            x509.SubjectAlternativeName([
                x509.IPAddress(ipaddress.IPv4Address(ip_address)),
                x509.DNSName("localhost"),
            ]),
            critical=False,
        )
        .sign(key, hashes.SHA256(), default_backend())
    )
    
    # Write certificate
    with open(cert_file, "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))
    
    # Write private key
    with open(key_file, "wb") as f:
        f.write(key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption()
        ))
    
    print(f"SSL certificate generated successfully!")
    print(f"  Certificate: {os.path.abspath(cert_file)}")
    print(f"  Private key: {os.path.abspath(key_file)}")
    print(f"\nRestart api.py to enable HTTPS.")

if __name__ == '__main__':
    generate_ssl_cert()
