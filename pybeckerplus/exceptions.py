class BeckerError(Exception):
    """Base exception for pybeckerplus."""

class BeckerTimeoutError(BeckerError):
    """Timed out waiting for serial response or stick acknowledgement."""

class BeckerParseError(BeckerError):
    """Failed to parse a packet from the stick."""