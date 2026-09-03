"""Lightweight CLI health check for local development."""

import asyncio

from smms.api.app import health

if __name__ == "__main__":
    print(asyncio.run(health()))

