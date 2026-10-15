# -*- coding: utf-8 -*-
"""技能分发探针：在工作区生成标记文件，证明技能已完整分发并被 agent 执行。

标准库实现，无外部依赖。
"""
import argparse
import sys
import time
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="skill sync probe")
    parser.add_argument("--out", default="skill_probe_result.txt")
    args = parser.parse_args()

    out = Path(args.out)
    out.write_text(
        "技能分发链路 OK\n"
        f"触发时间: {time.strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"探针版本: 1\n",
        encoding="utf-8",
    )
    print(f"OK -> {out.resolve()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
