"""Socket/TLS integration against synthetic peers, never real mail accounts."""

import socketserver
import ssl
import threading
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from nwpu_forwarder.errors import error_code
from nwpu_forwarder.mail import MailForwarder, MailParser
from nwpu_forwarder.receiver import MailReceiver

NL = bytes.fromhex("0d0a")


class TLSMockServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def get_request(self):
        conn, addr = super().get_request()
        conn.settimeout(10)
        try:
            return self.context.wrap_socket(conn, server_side=True), addr
        except Exception:
            conn.close()
            raise


class IMAPHandler(socketserver.StreamRequestHandler):
    def out(self, data):
        self.wfile.write(data + NL)
        self.wfile.flush()

    def handle(self):
        self.out(b"* OK synthetic IMAP ready")
        while line := self.rfile.readline():
            command = line.decode("ascii", "replace").strip()
            tag, _, rest = command.partition(" ")
            op = rest.split(" ")[0].upper()
            self.server.ops.append(rest if op != "LOGIN" else "LOGIN [redacted]")
            if op == "CAPABILITY":
                self.out(b"* CAPABILITY IMAP4rev1 IDLE")
            elif op == "LOGIN" and self.server.reject_login:
                self.out((tag + " NO authentication failed").encode())
                continue
            elif op in ("EXAMINE", "SELECT"):
                self.out(("* FLAGS (" + chr(92) + "Seen)").encode())
                self.out(f"* {len(self.server.mails)} EXISTS".encode())
                self.out(b"* OK [UIDVALIDITY 42] stable")
                self.out(b"* OK [UIDNEXT 999] next")
            elif rest.upper().startswith("UID SEARCH"):
                lower = int(rest.split()[-1].split(":")[0]) if ":" in rest else 0
                uids = [uid for uid in self.server.mails if uid >= lower]
                self.out(("* SEARCH " + " ".join(map(str, uids))).encode())
            elif rest.upper().startswith("UID FETCH"):
                uid = int(rest.split()[2])
                raw, received = self.server.mails[uid]
                if "RFC822.SIZE" in rest:
                    stamp = received.strftime("%d-%b-%Y %H:%M:%S +0000")
                    self.out(
                        f'* 1 FETCH (UID {uid} RFC822.SIZE {len(raw)} INTERNALDATE "{stamp}")'.encode()
                    )
                else:
                    partial = "<0.65536>" in rest
                    body = raw[:65536] if partial else raw
                    key = "BODY[]<0>" if partial else "BODY[]"
                    self.out(
                        f"* 1 FETCH (UID {uid} {key} {{{len(body)}}}".encode()
                        + NL
                        + body
                        + b")"
                    )
            elif op == "IDLE":
                self.out(b"+ idling")
                self.out(b"* 2 EXISTS")
                if self.rfile.readline().strip() != b"DONE":
                    return
            elif op == "LOGOUT":
                self.out(b"* BYE goodbye")
                self.out((tag + " OK logout").encode())
                return
            self.out((tag + " OK completed").encode())


class SMTPHandler(socketserver.StreamRequestHandler):
    def out(self, data):
        self.wfile.write(data + NL)
        self.wfile.flush()

    def handle(self):
        self.out(b"220 synthetic SMTP")
        while line := self.rfile.readline():
            op = line.split(b" ")[0].strip().upper()
            if op in (b"EHLO", b"HELO"):
                self.out(
                    NL.join(
                        [
                            b"250-localhost",
                            b"250-AUTH PLAIN LOGIN",
                            b"250 SIZE 20971520",
                        ]
                    )
                )
            elif op == b"AUTH":
                self.out(
                    b"535 authentication failed"
                    if self.server.reject_login
                    else b"235 authenticated"
                )
            elif op in (b"MAIL", b"RCPT", b"RSET"):
                self.out(b"250 OK")
            elif op == b"DATA":
                self.out(b"354 send data")
                chunks = []
                while chunk := self.rfile.readline():
                    if chunk == b"." + NL:
                        break
                    chunks.append(chunk[1:] if chunk.startswith(b"..") else chunk)
                self.server.messages.append(b"".join(chunks))
                self.out(b"250 accepted")
            elif op == b"QUIT":
                self.out(b"221 goodbye")
                return
            else:
                self.out(b"250 OK")


@pytest.fixture
def tls_certificate(tmp_path, monkeypatch):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName("localhost")]), critical=False
        )
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    cert_path = tmp_path / "cert.pem"
    key_path = tmp_path / "key.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    monkeypatch.setenv("SSL_CERT_FILE", str(cert_path))
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert_path, key_path)
    return context


@pytest.fixture
def peers(tls_certificate):
    imap = TLSMockServer(("127.0.0.1", 0), IMAPHandler)
    smtp = TLSMockServer(("127.0.0.1", 0), SMTPHandler)
    for server in (imap, smtp):
        server.context = tls_certificate
        server.reject_login = False
        server.mails = {}
        server.messages = []
        server.ops = []
        threading.Thread(target=server.serve_forever, daemon=True).start()
    yield imap, smtp
    for server in (imap, smtp):
        server.shutdown()
        server.server_close()


def test_real_tls_imap_to_smtp_roundtrip(peers, config, state, raw_mail):
    imap, smtp = peers
    cfg = replace(
        config,
        imap_host="localhost",
        imap_port=imap.server_address[1],
        smtp_host="localhost",
        smtp_port=smtp.server_address[1],
    )
    receiver = MailReceiver(cfg, "synthetic-password", state)
    receiver.connect()
    receiver.scan()
    imap.mails[7] = (raw_mail, datetime.now(timezone.utc))
    receiver.scan()
    task = state.claim(cfg.account_key)
    assert task and task["uid"] == 7 and receiver.idle_supported
    receiver.wait(threading.Event(), 2)
    receiver.close()
    MailForwarder(cfg, "synthetic-password").send(task)
    state.sent(task["id"])
    message = MailParser.parse(smtp.messages[0])
    assert message["From"] == config.qq_address and message["Subject"].startswith(
        "[西工大] 中文"
    )
    assert task["id"] in message.get_content() and state.claim(cfg.account_key) is None
    assert any(op.startswith("EXAMINE") for op in imap.ops) and any(
        "BODY.PEEK[]" in op for op in imap.ops
    )
    assert not any("STORE" in op for op in imap.ops)


@pytest.mark.parametrize("protocol", ["imap", "smtp"])
def test_real_authentication_failure(peers, config, state, protocol):
    imap, smtp = peers
    server = imap if protocol == "imap" else smtp
    server.reject_login = True
    cfg = replace(
        config,
        imap_host="localhost",
        imap_port=imap.server_address[1],
        smtp_host="localhost",
        smtp_port=smtp.server_address[1],
    )
    with pytest.raises(Exception) as exc:
        if protocol == "imap":
            MailReceiver(cfg, "synthetic", state).connect()
        else:
            MailForwarder(cfg, "synthetic").test()
    assert error_code(exc.value) == "authentication_failed"


def test_tls_hostname_validation(peers, config, state):
    imap, _ = peers
    cfg = replace(config, imap_host="127.0.0.1", imap_port=imap.server_address[1])
    with pytest.raises(ssl.SSLCertVerificationError):
        MailReceiver(cfg, "synthetic", state).connect()
