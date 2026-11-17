import random
import smtplib
import socket
import ssl

from imapclient.exceptions import LoginError


def error_code(error: Exception) -> str:
    # Never expose server replies: they can echo addresses or authentication data.
    if isinstance(error, (LoginError, smtplib.SMTPAuthenticationError)):
        return "authentication_failed"
    if isinstance(error, ssl.SSLError):
        return "tls_failed"
    if isinstance(error, (TimeoutError, socket.timeout)):
        return "network_timeout"
    if isinstance(error, smtplib.SMTPResponseException):
        return "smtp_permanent" if error.smtp_code >= 500 else "smtp_temporary"
    if isinstance(error, smtplib.SMTPRecipientsRefused):
        return "recipient_rejected"
    if isinstance(error, smtplib.SMTPException):
        return "delivery_uncertain"
    if isinstance(error, (ConnectionError, OSError, EOFError)):
        return "network_or_storage_failed"
    if isinstance(error, (ValueError, TypeError, RuntimeError, FileNotFoundError)):
        return "configuration_required"
    return "operation_failed"


def backoff(attempt: int) -> float:
    return min(
        1800.0, 30.0 * 2 ** min(max(attempt - 1, 0), 6) * random.uniform(0.8, 1.2)
    )
