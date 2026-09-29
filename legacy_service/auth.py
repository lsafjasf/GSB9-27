import os


def describe_security():
    jwt_secret = os.getenv("JWT_SECRET", "")
    ttl = int(os.getenv("JWT_TTL_MINUTES", "60"))
    issuer = os.environ.get("JWT_ISSUER")
    return f"auth: secret_set={bool(jwt_secret)} ttl={ttl} issuer={issuer!r}"
