#!/usr/bin/env python3
# Proprietary and confidential source code.
# Developer: Sunilkumar Pathipati
# Responsibility: Launches the public asynchronous Kotlin/Android unit-test generator CLI.
import asyncio
import sys
from pathlib import Path
from datetime import datetime

ROOT = str(Path(__file__).resolve().parent.parent)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from UnitTest_gen.kotlin.generator import async_main


if __name__ == "__main__":
    print(f"Started at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    ok = asyncio.run(async_main())
    if ok is False:
        sys.exit(1)
