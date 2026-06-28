"""
Fix Agent - 修复生成 + Reflection

职责：
1. 分析审查结果，生成结构化修复计划（FixPlan）
2. 修复失败后进行 Reflection，分析根因并给出修订策略
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from .base_agent import BaseAgent
from tools.models import (
    FilePatch, FixPlan, IssueRef, LoopState, ReflectResult, VerifyResult
)

# severity 排序权重（越高越优先修复）
_SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}


def _extract_issues_from_review(review_results: Dict[str, Any]) -> List[IssueRef]:
    """从各 Agent 审查结果中提取 IssueRef 列表，按 severity 排序"""
    issues: List[IssueRef] = []

    field_map = {
        "correctness": ("findings", "recommendation"),
        "security": ("findings", "recommendation"),
        "maintainability": ("findings", "recommendation"),
        # Legacy result compatibility for older reports/tests.
        "security_legacy": ("vulnerabilities", "remediation"),
        "bug": ("bugs", "fix"),
        "quality": ("issues", "suggestion"),
        "perf": ("issues", "optimization"),
        "practice": ("recommendations", "best_practice"),
    }

    for source, (list_key, hint_key) in field_map.items():
        result_key = "security" if source == "security_legacy" else source
        agent_result = review_results.get(result_key, {})
        if "error" in agent_result:
            continue
        for item in agent_result.get(list_key, []):
            issues.append(IssueRef(
                source=result_key,
                severity=item.get("severity", "medium"),
                line=item.get("line"),
                message=item.get("message", ""),
                fix_hint=item.get(hint_key, item.get("fix", item.get("suggestion", item.get("remediation", "")))),
            ))

    return sorted(issues, key=lambda i: _SEVERITY_ORDER.get(i.severity, 99))


class FixAgent(BaseAgent):
    """修复生成 Agent"""

    def __init__(self, client, model: str):
        super().__init__(client, model, "fix")

    async def generate_fix_plan(self, state: LoopState) -> FixPlan:
        """
        根据审查结果和 Repo Map 生成修复计划

        Args:
            state: 当前 LoopState，包含 review_results、repo_map_summary、current_code

        Returns:
            FixPlan
        """
        issues = _extract_issues_from_review(state.review_results)

        # 只修复 critical 和 high，medium 可选，low 跳过（避免过度修改）
        priority_issues = [i for i in issues if i.severity in ("critical", "high", "medium")]
        if not priority_issues:
            priority_issues = issues[:5]  # 至少尝试修复前 5 个

        system_prompt = self._build_fix_system_prompt()
        user_message = self._build_fix_user_message(state, priority_issues)

        response = await self._call_claude(system_prompt, user_message)
        return self._parse_fix_plan(response, priority_issues, state.file_path)

    async def reflect(self, state: LoopState) -> ReflectResult:
        """
        修复失败后进行 Reflection

        Args:
            state: 包含上次 fix_plan 和 verify_result 的 LoopState

        Returns:
            ReflectResult，包含根因分析和修订策略
        """
        from agents.prompt_loader import get_agent_system_prompt
        system_prompt = get_agent_system_prompt("fix_agent", {}, )
        # reflect 使用专用 prompt
        from agents.prompt_loader import get_prompts
        prompts = get_prompts()
        reflect_prompt = prompts.get("fix_agent", {}).get("reflect_system", "")
        if reflect_prompt:
            system_prompt = reflect_prompt

        user_message = self._build_reflect_user_message(state)
        response = await self._call_claude(system_prompt, user_message)
        return self._parse_reflect_result(response)

    def _build_fix_system_prompt(self) -> str:
        from agents.prompt_loader import get_agent_system_prompt
        return get_agent_system_prompt("fix_agent", {})

    def _build_fix_user_message(self, state: LoopState, issues: List[IssueRef]) -> str:
        issues_text = "\n".join(
            f"[{i.severity.upper()}] [{i.source}] Line {i.line or '?'}: {i.message}\n  Fix hint: {i.fix_hint}"
            for i in issues
        )

        repo_map_section = ""
        if state.repo_map_summary:
            repo_map_section = f"\n## Repo Map（影响范围分析）\n{state.repo_map_summary}\n"

        revised_section = ""
        if state.fix_plan and state.fix_plan.revised_strategy:
            revised_section = f"\n## 上次修复失败的修订策略\n{state.fix_plan.revised_strategy}\n请严格按照此策略修复。\n"

        return f"""## 文件路径
{state.file_path}

## 当前代码
```python
{state.current_code}
```
{repo_map_section}
## 需要修复的问题（按优先级排序）
{issues_text}
{revised_section}
请生成修复补丁 JSON。"""

    def _build_reflect_user_message(self, state: LoopState) -> str:
        verify = state.verify_result
        failure_reason = verify.failure_reason if verify else "Unknown"

        sandbox_info = ""
        if verify and verify.sandbox and not verify.sandbox.passed:
            errors = "\n".join(verify.sandbox.errors[:5])
            sandbox_info = f"\n### 测试失败\n```\n{errors}\n```"

        llm_info = ""
        if verify and verify.llm_review and not verify.llm_review.approved:
            new_issues = "\n".join(
                f"- [{i.severity}] {i.message}" for i in verify.llm_review.new_issues[:5]
            )
            llm_info = f"\n### LLM 重审发现新问题\n{new_issues}"

        patches_text = ""
        if state.fix_plan:
            patches_text = "\n".join(
                f"- Line {p.start_line}: {p.description}"
                for p in state.fix_plan.patches
            )

        return f"""## 上次修复失败

### 失败原因
{failure_reason}
{sandbox_info}
{llm_info}

### 上次应用的补丁
{patches_text}

### 当前代码
```python
{state.current_code}
```

请分析失败原因并给出修订策略。"""

    def _parse_fix_plan(
        self, response: Dict[str, Any], issues: List[IssueRef], file_path: str
    ) -> FixPlan:
        """解析 LLM 返回的修复计划"""
        raw_text = "\n".join(response.get("text", []))
        data = self._extract_json(raw_text)

        if not data:
            return FixPlan(issues_addressed=issues)

        patches = []
        for p in data.get("patches", []):
            try:
                patches.append(FilePatch(
                    file_path=p.get("file_path", file_path),
                    original_snippet=p.get("original_snippet", ""),
                    patched_snippet=p.get("patched_snippet", ""),
                    start_line=int(p.get("start_line", 0)),
                    end_line=int(p.get("end_line", 0)),
                    description=p.get("description", ""),
                ))
            except (ValueError, TypeError):
                continue

        return FixPlan(
            issues_addressed=issues,
            patches=patches,
            affected_callers=data.get("affected_callers", []),
            confidence=float(data.get("confidence", 0.8)),
        )

    def _parse_reflect_result(self, response: Dict[str, Any]) -> ReflectResult:
        """解析 Reflection 结果"""
        raw_text = "\n".join(response.get("text", []))
        data = self._extract_json(raw_text)

        if not data:
            return ReflectResult(
                root_cause="Unable to parse reflection response",
                revised_strategy="Try a more conservative fix approach",
                should_retry=False,
            )

        return ReflectResult(
            root_cause=data.get("root_cause", "Unknown"),
            revised_strategy=data.get("revised_strategy", ""),
            should_retry=bool(data.get("should_retry", False)),
            skip_issue_messages=data.get("skip_issue_messages", []),
        )

    def _extract_json(self, text: str) -> Optional[Dict]:
        """从文本中提取 JSON"""
        # 尝试 ```json 代码块
        match = re.search(r"```json\s*([\s\S]*?)```", text)
        if match:
            try:
                return json.loads(match.group(1).strip())
            except json.JSONDecodeError:
                pass

        # 尝试直接解析整个文本
        try:
            return json.loads(text.strip())
        except json.JSONDecodeError:
            pass

        # 尝试找第一个 { ... } 块
        match = re.search(r"\{[\s\S]*\}", text)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass

        return None

    async def analyze(self, code: str, language: str, context=None):
        raise NotImplementedError("FixAgent does not implement analyze(). Use generate_fix_plan() instead.")
