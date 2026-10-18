"""Stub command harness for the autoimprove journeys. It refuses every call, so a journey makes no nested model call."""
import sys

_ = sys.stderr.write("fixture harness: this journey makes no model calls\n")
sys.exit(3)
