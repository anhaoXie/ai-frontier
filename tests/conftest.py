# -*- coding: utf-8 -*-
"""让 tests/ 能直接 `import src.xxx`，无论从哪个目录跑 pytest。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
