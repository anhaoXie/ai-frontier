# -*- coding: utf-8 -*-
"""语法检查：编译 src/ 和 tools/ 下所有 py 文件。
放在临时文件里跑，因为 PowerShell 不支持 heredoc。"""
import ast
import io
import pathlib
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
root = pathlib.Path(__file__).resolve().parent.parent
bad = 0
files = sorted((root / "src").glob("*.py")) + sorted((root / "tools").glob("*.py"))
for p in files:
    try:
        ast.parse(p.read_text(encoding="utf-8"))
        print(f"  OK   {p.relative_to(root)}")
    except SyntaxError as e:
        bad += 1
        print(f"  FAIL {p.relative_to(root)}: line {e.lineno}: {e.msg}")
print(f"\n共 {len(files)} 个文件，{bad} 个有语法错误")
sys.exit(1 if bad else 0)
