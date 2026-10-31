# Before any request model is defined: they all refuse NaN / Infinity (core/finite_numbers.py).
from backend.core.finite_numbers import refuse_non_finite_in_models

refuse_non_finite_in_models()
