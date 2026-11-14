"""Self-signed TLS certificate for the local network.

Phone browsers only give camera access to HTTPS pages (or localhost), so the server
needs a certificate that lists the laptop's current LAN addresses. The certificate is
regenerated automatically when those addresses change (new Wi-Fi at the venue).
"""
import ipaddress
import socket
from datetime import datetime, timedelta, timezone

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from .config import CERT_DIR

CERT_FILE = CERT_DIR / "cert.pem"
KEY_FILE = CERT_DIR / "key.pem"
SANS_FILE = CERT_DIR / "sans.txt"


def local_ips() -> list[str]:
    ips = {"127.0.0.1"}
    try:
        ips.update(socket.gethostbyname_ex(socket.gethostname())[2])
    except OSError:
        pass
    try:  # the address of the interface that has the default route; sends nothing
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            ips.add(s.getsockname()[0])
    except OSError:
        pass
    return sorted(ips)


def ensure_cert() -> tuple[str, str, list[str]]:
    ips = local_ips()
    wanted = "\n".join(ips)
    if CERT_FILE.exists() and KEY_FILE.exists() and SANS_FILE.exists() and SANS_FILE.read_text() == wanted:
        return str(CERT_FILE), str(KEY_FILE), ips

    CERT_DIR.mkdir(parents=True, exist_ok=True)
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "EquipVision local server")])
    now = datetime.now(timezone.utc)
    sans = [x509.DNSName("localhost")] + [x509.IPAddress(ipaddress.ip_address(ip)) for ip in ips]
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=30))
        .add_extension(x509.SubjectAlternativeName(sans), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    KEY_FILE.write_bytes(key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    CERT_FILE.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    SANS_FILE.write_text(wanted)
    return str(CERT_FILE), str(KEY_FILE), ips
