"""
conftest.py — pytest configuration for Indian Stock AI V6.5.

Adds the project root to sys.path so all `src.*` imports resolve
cleanly. This is the ONLY place sys.path manipulation should exist.
"""
import sys
import os

# Add project root to path for src.* imports
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
