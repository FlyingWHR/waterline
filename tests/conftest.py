"""Shared test setup: tests use tiny exams (n=64, a few steps), which only a dev API accepts."""
import os

os.environ.setdefault("ALLOW_CLIENT_SIZES", "1")
