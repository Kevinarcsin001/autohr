"""时间线核验单测（评估报告 P1-6）。"""
from app.schemas.candidate_structure import WorkHistoryEntry
from app.services.timeline_check import detect_timeline_issues


def _entry(
    company: str | None,
    start: str | None,
    end: str | None,
) -> WorkHistoryEntry:
    return WorkHistoryEntry(company=company, start_date=start, end_date=end)


class TestDetectTimelineIssues:
    def test_no_overlap_passes(self) -> None:
        wh = [
            _entry("A公司", "2020-01", "2022-06"),
            _entry("B公司", "2022-07", "2024-01"),
        ]
        assert detect_timeline_issues(wh) == []

    def test_overlap_detected(self) -> None:
        wh = [
            _entry("A公司", "2020-01", "2022-06"),
            _entry("B公司", "2021-03", "2024-01"),  # 与 A 重叠 15 个月
        ]
        warnings = detect_timeline_issues(wh)
        assert len(warnings) == 1
        assert "A公司" in warnings[0] and "B公司" in warnings[0]
        assert "重叠" in warnings[0]

    def test_input_order_irrelevant(self) -> None:
        """乱序输入（后段在前）同样能检出重叠。"""
        wh = [
            _entry("B公司", "2021-03", "2024-01"),
            _entry("A公司", "2020-01", "2022-06"),
        ]
        assert len(detect_timeline_issues(wh)) == 1

    def test_present_end_overlaps_later_job(self) -> None:
        """end=至今 但又有新经历 → 必然重叠。"""
        wh = [
            _entry("A公司", "2020-01", "至今"),
            _entry("B公司", "2023-01", None),
        ]
        assert len(detect_timeline_issues(wh)) == 1

    def test_unparseable_dates_skipped(self) -> None:
        """解析不出的条目跳过不报（零误报优先）。"""
        wh = [
            _entry("A公司", "很久以前", "前年"),
            _entry("B公司", "2020-01", "2021-01"),
        ]
        assert detect_timeline_issues(wh) == []

    def test_end_before_start_skipped(self) -> None:
        """end < start（填反）→ 跳过不报。"""
        wh = [
            _entry("A公司", "2022-01", "2020-01"),
            _entry("B公司", "2020-05", "2021-01"),
        ]
        assert detect_timeline_issues(wh) == []

    def test_chinese_date_format(self) -> None:
        wh = [
            _entry("A公司", "2020年3月", "2021年5月"),
            _entry("B公司", "2021年1月", "2023年2月"),
        ]
        assert len(detect_timeline_issues(wh)) == 1

    def test_year_only_format(self) -> None:
        wh = [
            _entry("A公司", "2020", "2022"),
            _entry("B公司", "2021", "2023"),
        ]
        assert len(detect_timeline_issues(wh)) == 1

    def test_empty_or_none(self) -> None:
        assert detect_timeline_issues([]) == []
        assert detect_timeline_issues(None) == []

    def test_multiple_overlaps(self) -> None:
        wh = [
            _entry("A公司", "2020-01", "2022-01"),
            _entry("B公司", "2021-01", "2023-01"),
            _entry("C公司", "2022-06", "2024-01"),
        ]
        # A-B 重叠 + B-C 重叠 = 2 条
        assert len(detect_timeline_issues(wh)) == 2
