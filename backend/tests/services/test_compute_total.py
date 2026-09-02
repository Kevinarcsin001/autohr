"""compute_total（固定权重综合分）与三段取样单测（评估报告 P0-2 / P2-7）。"""
from app.services.scorer import build_scoring_snippet, compute_total


class TestComputeTotal:
    def test_default_weights(self) -> None:
        # 0.35*90 + 0.25*80 + 0.15*75 + 0.15*80 + 0.10*85 = 83.25 → 83
        assert (
            compute_total(
                skill=90, experience=80, education=75, stability=80, potential=85
            )
            == 83
        )

    def test_explicit_weights(self) -> None:
        assert (
            compute_total(
                skill=100,
                experience=0,
                education=0,
                stability=0,
                potential=0,
                weights={"skill": 1.0},
            )
            == 100
        )

    def test_weights_normalized_when_sum_ne_one(self) -> None:
        # 权重和 = 2 → 归一化后各 0.5 → (100+0)/2 = 50
        assert (
            compute_total(
                skill=100,
                experience=0,
                education=0,
                stability=0,
                potential=0,
                weights={"skill": 1.0, "experience": 1.0},
            )
            == 50
        )

    def test_all_zero_weights_falls_back_to_equal(self) -> None:
        # 全零权重 = 配置错误 → 等权兜底 (80*5)/5 = 80
        assert (
            compute_total(
                skill=80,
                experience=80,
                education=80,
                stability=80,
                potential=80,
                weights={k: 0.0 for k in (
                    "skill", "experience", "education", "stability", "potential"
                )},
            )
            == 80
        )

    def test_clamped_to_range(self) -> None:
        assert (
            compute_total(
                skill=100,
                experience=100,
                education=100,
                stability=100,
                potential=100,
                weights={"skill": 1.0},
            )
            == 100
        )

    def test_same_dims_same_total_deterministic(self) -> None:
        """横向可比性的核心：相同维度分必得相同 total（与 LLM 加权脱钩）。"""
        args = dict(
            skill=70, experience=60, education=90, stability=50, potential=75
        )
        assert compute_total(**args) == compute_total(**args)


class TestThreeSegmentSnippet:
    def test_short_text_returned_as_is(self) -> None:
        assert build_scoring_snippet("短文本", max_chars=1000) == "短文本"

    def test_middle_segment_preserved(self) -> None:
        """P2-7 核心断言：中段内容不再被丢弃。"""
        head = "H" * 500
        middle = "M" * 500
        tail = "T" * 500
        long_text = head + middle + tail
        snippet = build_scoring_snippet(long_text, max_chars=900)
        assert "truncated" in snippet
        # 三段都应出现在结果里
        assert "H" in snippet
        assert "M" in snippet
        assert "T" in snippet

    def test_length_bounded(self) -> None:
        long_text = "x" * 9000
        snippet = build_scoring_snippet(long_text, max_chars=3000)
        # 3 段 + 2 个分隔符标记
        assert len(snippet) <= 3000 + 60
