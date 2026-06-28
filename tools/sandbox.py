"""
Sandbox - subprocess pytest 执行器

功能：
- 发现项目中的测试文件
- 执行 pytest 并解析结果
- 支持精准测试（只跑与修改文件相关的测试）
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import List, Optional

from .models import SandboxResult


class Sandbox:
    """pytest 沙箱执行器"""

    def __init__(self, project_root: str, timeout: int = 60):
        self.project_root = Path(project_root)
        self.timeout = timeout

    def discover_tests(self) -> List[str]:
        """
        用 pytest --collect-only 发现测试文件
        返回测试文件路径列表，空列表表示没有测试
        """
        try:
            result = subprocess.run(
                [sys.executable, "-m", "pytest", "--collect-only", "-q", "--no-header"],
                cwd=str(self.project_root),
                capture_output=True,
                text=True,
                timeout=30,
            )
            # returncode 5 = no tests collected
            if result.returncode == 5:
                return []

            # 从输出中提取测试文件路径
            test_files = set()
            for line in result.stdout.splitlines():
                # 格式: <Module test_xxx.py> 或 test_xxx.py::test_func
                if "::" in line:
                    file_part = line.split("::")[0].strip()
                    if file_part.endswith(".py"):
                        test_files.add(file_part)
                elif line.strip().endswith(".py") and ("test" in line.lower()):
                    test_files.add(line.strip())

            return list(test_files)
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return []

    def run_tests(self, test_paths: Optional[List[str]] = None) -> SandboxResult:
        """
        执行 pytest，解析结果
        test_paths 为 None 时跑全部测试
        """
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            report_file = f.name

        cmd = [
            sys.executable, "-m", "pytest",
            "--tb=short",
            "--no-header",
            "-q",
        ]

        # 尝试使用 json-report 插件
        has_json_report = self._check_json_report()
        if has_json_report:
            cmd += [f"--json-report", f"--json-report-file={report_file}"]

        if test_paths:
            cmd += test_paths

        start = time.time()
        try:
            proc = subprocess.run(
                cmd,
                cwd=str(self.project_root),
                capture_output=True,
                text=True,
                timeout=self.timeout,
            )
        except subprocess.TimeoutExpired:
            return SandboxResult(
                passed=False,
                errors=[f"Tests timed out after {self.timeout}s"],
                stdout="",
                returncode=-1,
                duration_seconds=self.timeout,
            )
        except FileNotFoundError:
            return SandboxResult(
                passed=False,
                errors=["pytest not found. Install with: pip install pytest"],
                stdout="",
                returncode=-1,
            )

        duration = time.time() - start

        # returncode 5 = no tests collected
        if proc.returncode == 5:
            return SandboxResult(
                passed=True,
                no_tests_found=True,
                stdout=proc.stdout,
                returncode=proc.returncode,
                duration_seconds=duration,
            )

        passed = proc.returncode == 0

        # 解析 JSON 报告（如果有）
        if has_json_report:
            return self._parse_json_report(report_file, proc, passed, duration)

        # 降级：解析文本输出
        return self._parse_text_output(proc, passed, duration)

    def run_targeted_tests(self, affected_files: List[str]) -> SandboxResult:
        """
        只跑与修改文件相关的测试
        策略：查找与 affected_files 同名的 test_*.py 文件
        """
        all_tests = self.discover_tests()
        if not all_tests:
            return SandboxResult(passed=True, no_tests_found=True)

        # 找出相关测试文件
        targeted = []
        for affected in affected_files:
            stem = Path(affected).stem  # e.g. "example_code"
            for test_file in all_tests:
                test_stem = Path(test_file).stem  # e.g. "test_example_code"
                if stem in test_stem or test_stem in stem:
                    targeted.append(test_file)

        # 如果没找到相关测试，跑全部
        test_paths = targeted if targeted else None
        return self.run_tests(test_paths)

    def _check_json_report(self) -> bool:
        """检查 pytest-json-report 是否已安装"""
        try:
            result = subprocess.run(
                [sys.executable, "-m", "pytest", "--json-report", "--help"],
                capture_output=True,
                timeout=5,
            )
            return result.returncode == 0
        except Exception:
            return False

    def _parse_json_report(
        self, report_file: str, proc: subprocess.CompletedProcess,
        passed: bool, duration: float
    ) -> SandboxResult:
        """解析 pytest-json-report 输出"""
        try:
            with open(report_file) as f:
                report = json.load(f)

            summary = report.get("summary", {})
            errors = []
            for test in report.get("tests", []):
                if test.get("outcome") in ("failed", "error"):
                    call = test.get("call", {})
                    longrepr = call.get("longrepr", "")
                    if longrepr:
                        errors.append(f"{test['nodeid']}: {longrepr[:300]}")

            return SandboxResult(
                passed=passed,
                total=summary.get("total", 0),
                failed=summary.get("failed", 0) + summary.get("error", 0),
                errors=errors,
                stdout=proc.stdout,
                returncode=proc.returncode,
                duration_seconds=duration,
            )
        except Exception:
            return self._parse_text_output(proc, passed, duration)
        finally:
            Path(report_file).unlink(missing_ok=True)

    def _parse_text_output(
        self, proc: subprocess.CompletedProcess, passed: bool, duration: float
    ) -> SandboxResult:
        """解析 pytest 文本输出（降级）"""
        stdout = proc.stdout + proc.stderr
        total = 0
        failed = 0
        errors = []

        for line in stdout.splitlines():
            # 解析 "X passed, Y failed in Zs"
            if " passed" in line or " failed" in line or " error" in line:
                import re
                m = re.search(r"(\d+) passed", line)
                if m:
                    total += int(m.group(1))
                m = re.search(r"(\d+) failed", line)
                if m:
                    failed += int(m.group(1))
                m = re.search(r"(\d+) error", line)
                if m:
                    failed += int(m.group(1))
            # 收集 FAILED 行
            if line.startswith("FAILED "):
                errors.append(line[7:].strip()[:200])

        return SandboxResult(
            passed=passed,
            total=total,
            failed=failed,
            errors=errors[:10],  # 最多保留 10 条错误
            stdout=stdout[:2000],
            returncode=proc.returncode,
            duration_seconds=duration,
        )
