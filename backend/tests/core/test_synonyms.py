"""共享同义词表单测：严格等价归一（评估报告 P0-3）。"""
from app.core.synonyms import expand_skills, expand_term, skills_satisfied


class TestExpandTerm:
    def test_known_alias(self) -> None:
        assert "javascript" in expand_term("js")
        assert "js" in expand_term("JavaScript")
        assert "es6" in expand_term("js")

    def test_chinese_alias(self) -> None:
        assert "llm" in expand_term("大模型")
        assert "大模型" in expand_term("LLM")

    def test_unknown_term_returns_self(self) -> None:
        assert expand_term("fastapi") == frozenset({"fastapi"})

    def test_normalizes_case_and_strip(self) -> None:
        assert expand_term("  JS  ") == expand_term("js")

    def test_empty(self) -> None:
        assert expand_term("") == frozenset()
        assert expand_term("   ") == frozenset()


class TestSkillsSatisfied:
    def test_exact_match(self) -> None:
        assert skills_satisfied("python", {"python", "django"})

    def test_alias_match(self) -> None:
        assert skills_satisfied("js", {"vue", "javascript"})

    def test_not_satisfied_by_related_but_different_tech(self) -> None:
        """语义边界：相关但不同技术不算达标（与 reasoning 相关性表的区别）。"""
        assert not skills_satisfied("kubernetes", {"docker"})
        assert not skills_satisfied("typescript", {"javascript"})

    def test_missing_skill(self) -> None:
        assert not skills_satisfied("rust", {"python", "go"})

    def test_empty_candidates(self) -> None:
        assert not skills_satisfied("python", set())


class TestExpandSkills:
    def test_union_expansion(self) -> None:
        out = expand_skills({"js", "python"})
        assert "javascript" in out
        assert "py" in out
        assert "js" in out and "python" in out
