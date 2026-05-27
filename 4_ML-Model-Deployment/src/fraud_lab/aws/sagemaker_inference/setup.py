from __future__ import annotations

from setuptools import setup


setup(
    name="fraud-entry-serving",
    version="1.0.0",
    py_modules=[
        "fraud_entry",
        "inference",
        "input_fn",
        "model_fn",
        "output_fn",
        "predict_fn",
    ],
)
