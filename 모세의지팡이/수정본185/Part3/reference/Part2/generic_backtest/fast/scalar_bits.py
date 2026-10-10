"""Typed object-column evidence, retaining boolean tags and float payloads."""
import numpy as np


def bool_float_bytes(array):
    tags=np.fromiter((1 if isinstance(v,(bool,np.bool_)) else
                      2 if isinstance(v,(float,np.floating)) else 0 for v in array),
                     dtype=np.uint8,count=len(array))
    if not tags.all():return None
    # NumPy copies existing float64 bits (including NaN payloads and signed
    # zero); the separate tag distinguishes True/False from 1.0/0.0.
    return tags.tobytes()+array.astype('>f8').tobytes()
