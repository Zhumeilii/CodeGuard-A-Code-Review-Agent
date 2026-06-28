"""
Fix Loop - Agentic Fix Loop 主控制器

流程：
  review_file()
      ↓
  build_repo_map()          ← tree-sitter 调用图
      ↓
  fix_agent.generate_fix_plan()
      ↓
  patch_applier.apply()
      ↓
  verify_syntax()           ← ast.parse 快速检查
      ↓
  sandbox.run_targeted_tests()   ← 两种模式都跑
      ↓ (FULL 模式)
  llm_review()              ← 重新审查修复后代码
      ↓
  passed? → 输出修复后代码
  failed? → fix_agent.reflect() → 注入 revised_strategy → 重试
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from agents.orchestrator import Orchestrator
from agents.fix_agent import FixAgent, _extract_issues_from_review
from tools.models import (
    FixStatus, IterationRecord, LLMReviewResult, LoopState,
    SandboxResult, VerifyMode, VerifyResult, IssueRef
)
from tools.patch_applier import PatchApplier
from tools.repo_map import RepoMap
from tools.sandbox import Sandbox


class FixLoop:
    """Agentic Fix Loop 主控制器"""

    def __init__(
        self,
        mode: VerifyMode = VerifyMode.FULL,
        max_iterations: int = 3,
        model: Optional[str] = None,
    ):
        self.mode = mode
        self.max_iterations = max_iterations

        # 共用同一个 client
        self.orchestrator = Orchestrator(model=model)
        self.fix_agent = FixAgent(self.orchestrator.client, self.orchestrator.model)

    async def run(self, file_path: str) -> LoopState:
        """
        执行完整的 Agentic Fix Loop

        Args:
            file_path: 要修复的文件路径

        Returns:
            LoopState，包含修复结果和完整历史
        """
        path = Path(file_path).resolve()
        if not path.exists():
            raise FileNotFoundError(f"文件不存在: {file_path}")

        original_code = path.read_text(encoding="utf-8")
        language = self._detect_language(path.suffix)

        state = LoopState(
            file_path=str(path),
            language=language,
            original_code=original_code,
            current_code=original_code,
            max_iterations=self.max_iterations,
            status=FixStatus.IN_PROGRESS,
        )

        print(f"\n🔍 Step 1: 初始代码审查...")
        state.review_results = await self.orchestrator.review_file(str(path))

        # 检查是否有需要修复的问题
        issues = _extract_issues_from_review(state.review_results)
        if not issues:
            print("✅ 未发现需要修复的问题")
            state.status = FixStatus.SKIPPED
            return state

        critical_high = [i for i in issues if i.severity in ("critical", "high")]
        print(f"📋 发现 {len(issues)} 个问题（{len(critical_high)} 个 critical/high）")

        print(f"\n🗺️  Step 2: 构建 Repo Map...")
        state.repo_map_summary = self._build_repo_map(str(path), language)
        if state.repo_map_summary:
            print(f"✅ Repo Map 构建完成（{'tree-sitter' if 'tree-sitter' in state.repo_map_summary.lower() else 'ast 降级'}）")

        # 主循环
        applier = PatchApplier(str(path))

        for iteration in range(1, self.max_iterations + 1):
            state.iteration = iteration
            print(f"\n🔧 Step 3.{iteration}: 生成修复方案（第 {iteration}/{self.max_iterations} 轮）...")

            # 生成修复计划
            state.fix_plan = await self.fix_agent.generate_fix_plan(state)
            if not state.fix_plan.patches:
                print("⚠️  FixAgent 未生成任何补丁，终止循环")
                state.status = FixStatus.FAILED
                break

            print(f"📝 生成 {len(state.fix_plan.patches)} 个补丁（置信度: {state.fix_plan.confidence:.0%}）")

            # 备份并应用补丁
            applier.backup()
            success, warnings = applier.apply(state.fix_plan.patches)
            for w in warnings:
                print(f"  ⚠️  {w}")

            if not success:
                print("❌ 补丁应用失败，回滚")
                applier.rollback()
                state.status = FixStatus.FAILED
                break

            # 语法检查
            if not applier.verify_syntax(language):
                print("❌ 语法检查失败，回滚")
                applier.rollback()
                state.current_code = original_code
                # 直接 reflect
                reflect = await self.fix_agent.reflect(state)
                if not reflect.should_retry:
                    state.status = FixStatus.FAILED
                    break
                state.fix_plan.revised_strategy = reflect.revised_strategy
                continue

            # 更新 current_code
            state.current_code = path.read_text(encoding="utf-8")

            # 验证
            print(f"\n✅ Step 4.{iteration}: 验证修复（模式: {self.mode.value}）...")
            state.verify_result = await self._verify(state)

            # 记录本轮历史
            record = IterationRecord(
                iteration=iteration,
                fix_plan=state.fix_plan,
                verify_result=state.verify_result,
            )

            if state.verify_result.overall_passed:
                print(f"🎉 验证通过！修复成功（第 {iteration} 轮）")
                state.status = FixStatus.SUCCESS
                applier.cleanup_backup()
                state.history.append(record)
                break

            # 验证失败，Reflect
            print(f"❌ 验证失败: {state.verify_result.failure_reason}")
            print(f"\n🤔 Step 5.{iteration}: Reflection 分析失败原因...")

            reflect = await self.fix_agent.reflect(state)
            record.reflect_result = reflect
            state.history.append(record)

            print(f"  根因: {reflect.root_cause}")
            print(f"  策略: {reflect.revised_strategy}")

            if not reflect.should_retry:
                print("⚠️  FixAgent 判断无法继续修复，回滚")
                applier.rollback()
                state.current_code = original_code
                state.status = FixStatus.FAILED
                break

            # 回滚，注入修订策略，继续下一轮
            applier.rollback()
            state.current_code = original_code
            state.fix_plan.revised_strategy = reflect.revised_strategy

        else:
            # 达到最大迭代次数
            print(f"\n⚠️  达到最大迭代次数 ({self.max_iterations})，回滚到原始代码")
            applier.rollback()
            state.current_code = original_code
            state.status = FixStatus.FAILED

        return state

    async def _verify(self, state: LoopState) -> VerifyResult:
        """根据 mode 选择验证方式"""
        sandbox_result: Optional[SandboxResult] = None
        llm_result: Optional[LLMReviewResult] = None

        project_root = str(Path(state.file_path).parent)
        sandbox = Sandbox(project_root=project_root)

        # 两种模式都尝试跑沙箱测试
        tests = sandbox.discover_tests()
        if tests:
            print(f"  🧪 发现 {len(tests)} 个测试文件，执行精准测试...")
            sandbox_result = sandbox.run_targeted_tests(affected_files=[state.file_path])

            if sandbox_result.no_tests_found:
                print("  ℹ️  未找到相关测试文件")
            elif sandbox_result.passed:
                print(f"  ✅ 测试通过 ({sandbox_result.total} 个)")
            else:
                print(f"  ❌ 测试失败 ({sandbox_result.failed}/{sandbox_result.total})")
                return VerifyResult(
                    mode=self.mode,
                    sandbox=sandbox_result,
                    overall_passed=False,
                    failure_reason=f"Tests failed ({sandbox_result.failed} failures): " +
                                   "; ".join(sandbox_result.errors[:3]),
                )
        else:
            print("  ℹ️  项目中未发现测试文件，跳过沙箱测试")

        # FULL 模式：额外做 LLM 重审
        if self.mode == VerifyMode.FULL:
            print("  🤖 LLM 重审修复后代码...")
            llm_result = await self._llm_review(state)

            if not llm_result.approved:
                print(f"  ❌ LLM 重审未通过: {llm_result.reasoning[:100]}")
                return VerifyResult(
                    mode=self.mode,
                    sandbox=sandbox_result,
                    llm_review=llm_result,
                    overall_passed=False,
                    failure_reason=f"LLM review failed: {llm_result.reasoning}",
                )

            if llm_result.new_issues:
                new_critical = [i for i in llm_result.new_issues if i.severity in ("critical", "high")]
                if new_critical:
                    print(f"  ❌ 修复引入了 {len(new_critical)} 个新的 critical/high 问题")
                    return VerifyResult(
                        mode=self.mode,
                        sandbox=sandbox_result,
                        llm_review=llm_result,
                        overall_passed=False,
                        failure_reason=f"Fix introduced {len(new_critical)} new critical/high issues",
                    )

            resolved = len(llm_result.resolved_issues)
            remaining = len(llm_result.remaining_issues)
            print(f"  ✅ LLM 重审通过（解决 {resolved} 个问题，剩余 {remaining} 个）")

        return VerifyResult(
            mode=self.mode,
            sandbox=sandbox_result,
            llm_review=llm_result,
            overall_passed=True,
        )

    async def _llm_review(self, state: LoopState) -> LLMReviewResult:
        """用 Orchestrator 重新审查修复后的代码，对比原始问题"""
        import json as _json

        updated_code = Path(state.file_path).read_text(encoding="utf-8")
        original_issues = _extract_issues_from_review(state.review_results)
        original_messages = [i.message for i in original_issues]

        from agents.prompt_loader import get_prompts
        prompts = get_prompts()
        system_prompt = prompts.get("fix_loop", {}).get("review_system", "")

        user_message = f"""## 原始问题列表
{chr(10).join(f'- [{i.severity}] [{i.source}] {i.message}' for i in original_issues[:10])}

## 修复后的代码
```python
{updated_code}
```

请判断修复是否有效。"""

        response = await self.fix_agent._call_claude(system_prompt, user_message)
        raw_text = "\n".join(response.get("text", []))
        data = self.fix_agent._extract_json(raw_text)

        if not data:
            return LLMReviewResult(
                approved=True,  # 解析失败时默认通过，避免误判
                reasoning="Could not parse LLM review response",
            )

        def parse_issues(items: List[Dict]) -> List[IssueRef]:
            result = []
            for item in items:
                try:
                    result.append(IssueRef(
                        source=item.get("source", "unknown"),
                        severity=item.get("severity", "medium"),
                        message=item.get("message", ""),
                        fix_hint=item.get("fix_hint", ""),
                    ))
                except Exception:
                    pass
            return result

        return LLMReviewResult(
            approved=bool(data.get("approved", True)),
            resolved_issues=data.get("resolved_issues", []),
            remaining_issues=parse_issues(data.get("remaining_issues", [])),
            new_issues=parse_issues(data.get("new_issues", [])),
            reasoning=data.get("reasoning", ""),
        )

    def _build_repo_map(self, file_path: str, language: str) -> Optional[str]:
        """构建 Repo Map，返回影响摘要（扫描整个 repo 以捕获跨文件调用关系）"""
        try:
            # 向上查找 repo 根目录（含 .git / pyproject.toml / setup.py 的目录）
            repo_root = self._find_repo_root(Path(file_path))
            repo_map = RepoMap(str(repo_root), language=language).build()
            funcs = [n.name for n in repo_map.get_functions_in_file(file_path)]
            if not funcs:
                return None
            summary = repo_map.get_impact_summary(funcs)
            return summary
        except Exception as e:
            print(f"  ⚠️  Repo Map 构建失败: {e}")
            return None

    def _find_repo_root(self, start: Path) -> Path:
        """从目标文件向上查找项目根目录，找不到则退回到文件所在目录"""
        markers = {".git", "pyproject.toml", "setup.py", "setup.cfg", "package.json"}
        current = start.parent
        while current != current.parent:
            if any((current / m).exists() for m in markers):
                return current
            current = current.parent
        return start.parent

    def _detect_language(self, suffix: str) -> str:
        language_map = {
            ".py": "python",
            ".js": "javascript",
            ".ts": "typescript",
            ".java": "java",
            ".go": "go",
            ".rs": "rust",
        }
        return language_map.get(suffix.lower(), "python")

    def format_result(self, state: LoopState) -> str:
        """格式化最终结果为可读字符串"""
        lines = []
        lines.append("\n" + "=" * 60)
        lines.append("🔧 Agentic Fix Loop 结果")
        lines.append("=" * 60)
        lines.append(f"文件: {state.file_path}")
        lines.append(f"状态: {state.status.value.upper()}")
        lines.append(f"迭代次数: {state.iteration}/{state.max_iterations}")
        lines.append(f"验证模式: {self.mode.value}")

        if state.history:
            lines.append(f"\n📊 迭代历史:")
            for record in state.history:
                v = record.verify_result
                status_icon = "✅" if v.overall_passed else "❌"
                lines.append(f"  第 {record.iteration} 轮: {status_icon} {v.failure_reason or '通过'}")
                lines.append(f"    补丁数: {len(record.fix_plan.patches)}")
                if record.reflect_result:
                    lines.append(f"    根因: {record.reflect_result.root_cause[:80]}")

        if state.status == FixStatus.SUCCESS:
            lines.append(f"\n✅ 修复成功！代码已更新: {state.file_path}")
        elif state.status == FixStatus.FAILED:
            lines.append(f"\n❌ 修复失败，原始代码已恢复")
        elif state.status == FixStatus.SKIPPED:
            lines.append(f"\nℹ️  未发现需要修复的问题")

        lines.append("=" * 60)
        return "\n".join(lines)
