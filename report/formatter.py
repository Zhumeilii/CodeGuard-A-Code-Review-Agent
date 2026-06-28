"""
Report Formatter - 格式化审查报告

将多个 Agent 的结果整合成易读的报告
"""
from typing import Dict, Any
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.markdown import Markdown
import json


class ReportFormatter:
    """报告格式化器"""

    def __init__(self):
        self.console = Console()

    def format_terminal(self, results: Dict[str, Any]) -> None:
        """在终端输出格式化的报告"""
        self.console.print("\n")
        self.console.print(Panel.fit(
            "[bold cyan]代码审查报告[/bold cyan]",
            border_style="cyan"
        ))

        # 1. 正确性审查
        if "correctness" in results and "error" not in results["correctness"]:
            self._print_correctness_section(results["correctness"])
        elif "bug" in results and "error" not in results["bug"]:
            self._print_bug_section(results["bug"])

        # 2. 安全扫描
        if "security" in results and "error" not in results["security"]:
            self._print_security_section(results["security"])

        # 3. 可维护性审查
        if "maintainability" in results and "error" not in results["maintainability"]:
            self._print_maintainability_section(results["maintainability"])
        elif "quality" in results and "error" not in results["quality"]:
            self._print_quality_section(results["quality"])

        # 4. 企业规范
        if "policy" in results and "error" not in results["policy"]:
            self._print_policy_section(results["policy"])

        self.console.print("\n")

    def _print_quality_section(self, quality: Dict[str, Any]) -> None:
        """打印代码质量部分"""
        self.console.print("\n[bold yellow]📊 代码质量分析[/bold yellow]")

        score = quality.get("score", 0)
        score_color = "green" if score >= 80 else "yellow" if score >= 60 else "red"
        self.console.print(f"评分: [{score_color}]{score}/100[/{score_color}]")

        if quality.get("summary"):
            self.console.print(f"总结: {quality['summary']}")

        issues = quality.get("issues", [])
        if issues:
            table = Table(show_header=True, header_style="bold magenta")
            table.add_column("类型", style="cyan")
            table.add_column("严重性", style="yellow")
            table.add_column("行号", style="green")
            table.add_column("问题", style="white")

            for issue in issues[:10]:  # 最多显示 10 个
                table.add_row(
                    issue.get("type", "unknown"),
                    issue.get("severity", "unknown"),
                    str(issue.get("line", "-")),
                    issue.get("message", "")[:60]
                )

            self.console.print(table)

    def _print_bug_section(self, bug: Dict[str, Any]) -> None:
        """打印 Bug 检测部分"""
        self.console.print("\n[bold red]🐛 潜在 Bug 检测[/bold red]")

        risk_level = bug.get("risk_level", "unknown")
        risk_color = "red" if risk_level == "critical" else "orange" if risk_level == "high" else "yellow"
        self.console.print(f"风险等级: [{risk_color}]{risk_level}[/{risk_color}]")

        if bug.get("summary"):
            self.console.print(f"总结: {bug['summary']}")

        bugs = bug.get("bugs", [])
        if bugs:
            for b in bugs[:5]:  # 最多显示 5 个
                severity_color = "red" if b.get("severity") == "critical" else "yellow"
                self.console.print(f"\n  [{severity_color}]●[/{severity_color}] {b.get('message', '')}")
                if b.get("fix"):
                    self.console.print(f"    修复: {b['fix']}")

    def _print_correctness_section(self, correctness: Dict[str, Any]) -> None:
        """打印正确性审查部分"""
        self.console.print("\n[bold red]🧭 正确性审查[/bold red]")

        risk_level = correctness.get("risk_level", "unknown")
        risk_color = "red" if risk_level == "critical" else "orange" if risk_level == "high" else "yellow"
        self.console.print(f"风险等级: [{risk_color}]{risk_level}[/{risk_color}]")

        if correctness.get("summary"):
            self.console.print(f"总结: {correctness['summary']}")

        issues = correctness.get("findings", correctness.get("issues", []))
        if issues:
            for issue in issues[:5]:
                severity_color = "red" if issue.get("severity") == "critical" else "yellow"
                self.console.print(f"\n  [{severity_color}]●[/{severity_color}] {issue.get('message', '')}")
                recommendation = issue.get("recommendation") or issue.get("fix")
                if recommendation:
                    self.console.print(f"    修复: {recommendation}")

    def _print_perf_section(self, perf: Dict[str, Any]) -> None:
        """打印性能分析部分"""
        self.console.print("\n[bold blue]⚡ 性能分析[/bold blue]")

        score = perf.get("performance_score", 0)
        score_color = "green" if score >= 80 else "yellow" if score >= 60 else "red"
        self.console.print(f"性能评分: [{score_color}]{score}/100[/{score_color}]")

        if perf.get("summary"):
            self.console.print(f"总结: {perf['summary']}")

        issues = perf.get("issues", [])
        if issues:
            for issue in issues[:5]:
                self.console.print(f"\n  ⚠️  {issue.get('message', '')}")
                if issue.get("optimization"):
                    self.console.print(f"    优化: {issue['optimization']}")

    def _print_security_section(self, security: Dict[str, Any]) -> None:
        """打印安全扫描部分"""
        self.console.print("\n[bold magenta]🔒 安全漏洞扫描[/bold magenta]")

        score = security.get("security_score", 0)
        risk = security.get("risk_level", "unknown")

        score_color = "green" if score >= 80 else "yellow" if score >= 60 else "red"
        self.console.print(f"安全评分: [{score_color}]{score}/100[/{score_color}]")
        self.console.print(f"风险等级: {risk}")

        if security.get("summary"):
            self.console.print(f"总结: {security['summary']}")

        vulns = security.get("findings", security.get("vulnerabilities", []))
        if vulns:
            for vuln in vulns[:5]:
                severity = vuln.get("severity", "unknown")
                severity_color = "red" if severity == "critical" else "orange" if severity == "high" else "yellow"
                self.console.print(f"\n  [{severity_color}]🚨[/{severity_color}] {vuln.get('message', '')}")
                recommendation = vuln.get("recommendation") or vuln.get("remediation")
                if recommendation:
                    self.console.print(f"    修复: {recommendation}")

    def _print_practice_section(self, practice: Dict[str, Any]) -> None:
        """打印最佳实践部分"""
        self.console.print("\n[bold green]✨ 最佳实践推荐[/bold green]")

        score = practice.get("practice_score", 0)
        score_color = "green" if score >= 80 else "yellow" if score >= 60 else "red"
        self.console.print(f"实践评分: [{score_color}]{score}/100[/{score_color}]")

        if practice.get("summary"):
            self.console.print(f"总结: {practice['summary']}")

        recommendations = practice.get("recommendations", [])
        if recommendations:
            for rec in recommendations[:5]:
                priority = rec.get("priority", "low")
                priority_icon = "🔴" if priority == "high" else "🟡" if priority == "medium" else "🟢"
                self.console.print(f"\n  {priority_icon} {rec.get('message', '')}")
                if rec.get("best_practice"):
                    self.console.print(f"    建议: {rec['best_practice']}")

    def _print_maintainability_section(self, maintainability: Dict[str, Any]) -> None:
        """打印可维护性审查部分"""
        self.console.print("\n[bold green]🛠 可维护性审查[/bold green]")

        score = maintainability.get("score", 0)
        score_color = "green" if score >= 80 else "yellow" if score >= 60 else "red"
        self.console.print(f"维护性评分: [{score_color}]{score}/100[/{score_color}]")

        if maintainability.get("summary"):
            self.console.print(f"总结: {maintainability['summary']}")

        issues = maintainability.get("findings", maintainability.get("issues", []))
        if issues:
            for issue in issues[:5]:
                severity = issue.get("severity", "low")
                icon = "🔴" if severity == "high" else "🟡" if severity == "medium" else "🟢"
                self.console.print(f"\n  {icon} {issue.get('message', '')}")
                recommendation = issue.get("recommendation") or issue.get("suggestion")
                if recommendation:
                    self.console.print(f"    建议: {recommendation}")

    def _print_policy_section(self, policy: Dict[str, Any]) -> None:
        """打印企业规范审查部分"""
        self.console.print("\n[bold cyan]📚 企业规范审查[/bold cyan]")

        findings = policy.get("findings", [])
        self.console.print(
            f"扫描规范: {policy.get('scanned_clauses', 0)} | "
            f"违规: {policy.get('violated_count', len(findings))}"
        )

        for finding in findings[:5]:
            rule_id = finding.get("rule_id") or finding.get("clause_id", "-")
            message = finding.get("message") or finding.get("violated_rule", "")
            self.console.print(f"\n  ● [{rule_id}] {message}")
            line = finding.get("line") or finding.get("code_location")
            if line:
                self.console.print(f"    行号: {line}")
            evidence = finding.get("evidence") or finding.get("explanation")
            if evidence:
                self.console.print(f"    说明: {evidence}")
            recommendation = finding.get("recommendation") or finding.get("suggestion")
            if recommendation:
                self.console.print(f"    建议: {recommendation}")

    def format_json(self, results: Dict[str, Any]) -> str:
        """输出 JSON 格式的报告"""
        return json.dumps(results, ensure_ascii=False, indent=2)

    def format_markdown(self, results: Dict[str, Any]) -> str:
        """输出 Markdown 格式的报告"""
        md = "# 代码审查报告\n\n"

        # 正确性审查
        if "correctness" in results and "error" not in results["correctness"]:
            md += "## 🧭 正确性审查\n\n"
            correctness = results["correctness"]
            md += f"**风险等级**: {correctness.get('risk_level', 'unknown')}\n\n"
            md += f"{correctness.get('summary', '')}\n\n"

            issues = correctness.get("findings", correctness.get("issues", []))
            if issues:
                md += "### 问题列表\n\n"
                for issue in issues:
                    md += f"- **{issue.get('type')}** ({issue.get('severity')}): {issue.get('message', '')}\n"
                    recommendation = issue.get('recommendation') or issue.get('fix')
                    if recommendation:
                        md += f"  - 修复: {recommendation}\n"
                md += "\n"
        elif "quality" in results and "error" not in results["quality"]:
            md += "## 📊 代码质量分析\n\n"
            quality = results["quality"]
            md += f"**评分**: {quality.get('score', 0)}/100\n\n"
            md += f"{quality.get('summary', '')}\n\n"

            issues = quality.get("issues", [])
            if issues:
                md += "### 问题列表\n\n"
                for issue in issues:
                    md += f"- **{issue.get('type')}** ({issue.get('severity')})"
                    if issue.get('line'):
                        md += f" - 行 {issue['line']}"
                    md += f": {issue.get('message', '')}\n"
                md += "\n"

        # Bug 检测（旧结果兼容）
        if "bug" in results and "error" not in results["bug"]:
            md += "## 🐛 潜在 Bug 检测\n\n"
            bug = results["bug"]
            md += f"**风险等级**: {bug.get('risk_level', 'unknown')}\n\n"
            md += f"{bug.get('summary', '')}\n\n"

            bugs = bug.get("bugs", [])
            if bugs:
                md += "### Bug 列表\n\n"
                for b in bugs:
                    md += f"- **{b.get('type')}** ({b.get('severity')}): {b.get('message', '')}\n"
                    if b.get('fix'):
                        md += f"  - 修复: {b['fix']}\n"
                md += "\n"

        # 安全扫描
        if "security" in results and "error" not in results["security"]:
            md += "## 🔒 安全漏洞扫描\n\n"
            security = results["security"]
            md += f"**安全评分**: {security.get('security_score', 0)}/100\n\n"
            md += f"**风险等级**: {security.get('risk_level', 'unknown')}\n\n"

        # 可维护性审查
        if "maintainability" in results and "error" not in results["maintainability"]:
            md += "## 🛠 可维护性审查\n\n"
            maintainability = results["maintainability"]
            md += f"**维护性评分**: {maintainability.get('score', 0)}/100\n\n"
            md += f"{maintainability.get('summary', '')}\n\n"

            issues = maintainability.get("findings", maintainability.get("issues", []))
            if issues:
                md += "### 问题列表\n\n"
                for issue in issues:
                    md += f"- **{issue.get('type')}** ({issue.get('severity')}): {issue.get('message', '')}\n"
                    recommendation = issue.get('recommendation') or issue.get('suggestion')
                    if recommendation:
                        md += f"  - 建议: {recommendation}\n"
                md += "\n"
        elif "practice" in results and "error" not in results["practice"]:
            md += "## ✨ 最佳实践推荐\n\n"
            practice = results["practice"]
            md += f"**实践评分**: {practice.get('practice_score', 0)}/100\n\n"

        # 企业规范
        if "policy" in results and "error" not in results["policy"]:
            md += "## 📚 企业规范审查\n\n"
            policy = results["policy"]
            md += f"**扫描规范**: {policy.get('scanned_clauses', 0)}\n\n"
            md += f"**违规数量**: {policy.get('violated_count', 0)}\n\n"
            for finding in policy.get("findings", []):
                rule_id = finding.get("rule_id") or finding.get("clause_id", "-")
                message = finding.get("message") or finding.get("violated_rule", "")
                md += f"- **{rule_id}**: {message}\n"
                line = finding.get("line") or finding.get("code_location")
                if line:
                    md += f"  - 行号: {line}\n"
                evidence = finding.get("evidence") or finding.get("explanation")
                if evidence:
                    md += f"  - 说明: {evidence}\n"
                recommendation = finding.get("recommendation") or finding.get("suggestion")
                if recommendation:
                    md += f"  - 建议: {recommendation}\n"
            md += "\n"

        return md
