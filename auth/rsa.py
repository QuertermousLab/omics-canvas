"""RSA for the sign-in form (as RStudio Server does): the page encrypts the password with the server's public key
before sending it, so it never crosses the network in clear text while the site is served over plain HTTP.

PKCS#1 v1.5 encryption padding, a 2048-bit key made when the auth server starts (kept in memory only). The browser
cannot use Web Crypto on a plain-HTTP page, so web/login.mjs pads and encrypts with BigInt. Standard library only.
"""

import secrets

SMALL_PRIMES = [p for p in range(3, 2000, 2) if all(p % d for d in range(3, int(p ** 0.5) + 1, 2))]


def _probably_prime(n, rounds=40):
    if n < 2:
        return False
    for p in SMALL_PRIMES:
        if n % p == 0:
            return n == p
    d, s = n - 1, 0
    while d % 2 == 0:
        d //= 2
        s += 1
    for _ in range(rounds):
        a = secrets.randbelow(n - 3) + 2
        x = pow(a, d, n)
        if x in (1, n - 1):
            continue
        for _ in range(s - 1):
            x = pow(x, 2, n)
            if x == n - 1:
                break
        else:
            return False
    return True


def _prime(bits):
    while True:
        candidate = secrets.randbits(bits) | (1 << (bits - 1)) | (1 << (bits - 2)) | 1
        if _probably_prime(candidate):
            return candidate


class KeyPair:
    def __init__(self, bits=2048, e=65537):
        while True:
            p, q = _prime(bits // 2), _prime(bits // 2)
            phi = (p - 1) * (q - 1)
            if p != q and phi % e and (p * q).bit_length() == bits:
                break
        self.n, self.e = p * q, e
        self.d = pow(e, -1, phi)  # Python 3.8+
        self.size = (self.n.bit_length() + 7) // 8

    def public(self):
        return {"n": format(self.n, "x"), "e": format(self.e, "x"), "bits": self.n.bit_length()}

    def decrypt(self, cipher):
        """bytes -> plaintext bytes (PKCS#1 v1.5 type 2 padding), ValueError if it is not one of ours."""
        if len(cipher) != self.size:
            raise ValueError("bad length")
        message = pow(int.from_bytes(cipher, "big"), self.d, self.n).to_bytes(self.size, "big")
        if message[0:2] != b"\x00\x02":
            raise ValueError("bad padding")
        end = message.find(b"\x00", 2)
        if end < 10:
            raise ValueError("bad padding")
        return message[end + 1:]

    def encrypt(self, plain):
        """The page's side, for tests."""
        padding_length = self.size - len(plain) - 3
        if padding_length < 8:
            raise ValueError("message too long")
        padding = bytes(secrets.randbelow(255) + 1 for _ in range(padding_length))
        message = b"\x00\x02" + padding + b"\x00" + plain
        return pow(int.from_bytes(message, "big"), self.e, self.n).to_bytes(self.size, "big")
