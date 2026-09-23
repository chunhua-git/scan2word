# -*- coding: utf-8 -*-
"""验证打包产物：用真实样本跑一次无界面转换，并测量启动开销。

单独做成脚本（而不是写在 .ps1 里）的原因：
  1. 中文路径交给 Python，避开 PowerShell 5.1 的 ANSI 编码坑；
  2. 顺便量出"单文件版每次启动要多花几秒解压"，给选型一个真实数字。
"""
from __future__ import annotations

import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding="utf-8")


def run_selftest(exe: str, sample: str, outdir: str, timeout: int = 900):
    """跑一次 --selftest，返回 (耗时秒, 结果文本)。结果文件由 exe 写在自己旁边。"""
    result_file = os.path.join(os.path.dirname(os.path.abspath(exe)), "selftest_result.txt")
    try:
        os.remove(result_file)
    except OSError:
        pass
    if os.path.isdir(outdir):
        import shutil
        shutil.rmtree(outdir, ignore_errors=True)

    t0 = time.time()
    proc = subprocess.run([exe, "--selftest", sample, outdir],
                          capture_output=True, timeout=timeout)
    dt = time.time() - t0

    text = ""
    for _ in range(50):                      # 结果文件可能比进程退出稍晚落盘
        if os.path.exists(result_file):
            with open(result_file, encoding="utf-8") as fh:
                text = fh.read()
            if "SELFTEST" in text:
                break
        time.sleep(0.1)
    return dt, text, proc.returncode


def main() -> int:
    if len(sys.argv) < 2:
        print("用法：verify_exe.py <exe路径>")
        return 2
    exe = sys.argv[1]
    if not os.path.exists(exe):
        print(f"❌ 找不到 {exe}")
        return 1

    # 矢量样本转换只要零点几秒，所以总耗时≈启动开销，正好用来横向对比两种形态
    sample = os.path.join(ROOT, "probe_vector.pdf")
    if not os.path.exists(sample):
        sample = os.path.join(ROOT, "samples", "d7271293e64e9585b5c065a2e1164066.pdf")
    outdir = os.path.join(ROOT, "selftest_out")

    size_mb = os.path.getsize(exe) / 1024 / 1024
    print(f"  产物：{os.path.basename(exe)}（{size_mb:.0f} MB）")
    print(f"  样本：{os.path.basename(sample)}")

    dt, text, code = run_selftest(exe, sample, outdir)
    for line in text.strip().splitlines():
        print("    " + line)
    if "SELFTEST PASS" not in text:
        print(f"  ❌ 自检失败（exit={code}，耗时 {dt:.1f}s）")
        return 1

    kind = "单文件版（每次启动要解压）" if "单文件" in os.path.basename(exe) else "目录版"
    print(f"  ✅ 自检通过 | {kind} | 本次总耗时 {dt:.1f}s（含启动与一次真实转换）")

    # 冷/热各测一次，给出更接近实际的启动开销
    times = []
    for _ in range(2):
        d, _t, _c = run_selftest(exe, sample, outdir)
        times.append(d)
    print(f"  启动开销参考：首次 {times[0]:.1f}s，再次 {times[1]:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
