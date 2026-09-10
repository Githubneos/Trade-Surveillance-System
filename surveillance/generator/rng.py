"""Named, order-independent random streams.

Naive ``SeedSequence.spawn()`` allocates child streams positionally, so inserting a new
scenario shifts every stream created after it and silently regenerates the whole dataset.
Deriving each stream from a hash of its *name* instead means adding ``wash_ring_07``
leaves the background activity and every other scenario byte-identical -- which is what
makes "the metrics moved" a trustworthy signal when tuning detectors.
"""

from __future__ import annotations

import hashlib

import numpy as np

_MASK = (1 << 64) - 1


def stream_seed(master_seed: int, name: str) -> int:
    digest = hashlib.blake2b(name.encode("utf-8"), digest_size=8).digest()
    return (master_seed * 0x9E3779B97F4A7C15 ^ int.from_bytes(digest, "big")) & _MASK


def rng_for(master_seed: int, name: str) -> np.random.Generator:
    """Return an independent Generator for the named component."""
    return np.random.default_rng(stream_seed(master_seed, name))
