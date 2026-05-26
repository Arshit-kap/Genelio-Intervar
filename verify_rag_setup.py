#!/usr/bin/env python
"""Verify RAG project dependencies are installed."""

import sys

packages_to_check = [
    'langchain',
    'langchain_community', 
    'openai',
    'chromadb',
    'faiss',
    'python_dotenv',
    'pydantic',
    'numpy',
    'pandas'
]

print("=" * 60)
print("RAG DEPENDENCIES VERIFICATION")
print("=" * 60)
print(f"\nPython Version: {sys.version}\n")

failed = []
success = []

for package in packages_to_check:
    try:
        __import__(package)
        success.append(package)
        print(f"✓ {package:25} - INSTALLED")
    except ImportError:
        failed.append(package)
        print(f"✗ {package:25} - NOT INSTALLED")

print("\n" + "=" * 60)
print(f"SUMMARY: {len(success)} installed, {len(failed)} missing")
print("=" * 60)

if failed:
    print(f"\nMissing packages: {', '.join(failed)}")
    print("\nTo install missing packages, run:")
    print(f"pip install {' '.join(failed)}")
else:
    print("\n✓ All RAG dependencies are installed!")
    print("You can now start developing your RAG application.")

sys.exit(0 if not failed else 1)
