"""Counter-based int8 matrix generator. Must match the CUDA kernel in gpu_check.py bit for bit.

entry(tag, step, r, c) = low byte of mix64(key ^ ((base*n + r)*n + c)) as int8,
where key = mix64(seed) and base = (tag << 20) + step.
"""
import numpy as np

U = np.uint64


def mix64(x):
    """splitmix64 finalizer, vectorized, wrapping mod 2^64."""
    x = np.asarray(x, dtype=U)
    with np.errstate(over="ignore"):
        x = x + U(0x9E3779B97F4A7C15)
        x = (x ^ (x >> U(30))) * U(0xBF58476D1CE4E5B9)
        x = (x ^ (x >> U(27))) * U(0x94D049BB133111EB)
        return x ^ (x >> U(31))


def _gen(seed, n, tag, step, idx):
    key = mix64(U(seed))
    base = U((tag << 20) + step)
    with np.errstate(over="ignore"):
        ctr = (base * U(n)) * U(n) + idx
    return (mix64(key ^ ctr) & U(0xFF)).astype(np.uint8).view(np.int8)


def matrix(seed, n, tag, step):
    return _gen(seed, n, tag, step, np.arange(n * n, dtype=U)).reshape(n, n)


def row(seed, n, tag, step, r):
    return _gen(seed, n, tag, step, U(r) * U(n) + np.arange(n, dtype=U))


def col(seed, n, tag, step, c):
    return _gen(seed, n, tag, step, np.arange(n, dtype=U) * U(n) + U(c))
