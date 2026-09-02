"""mask_pii_text 单测：PII 出境脱敏（评估报告 P0-1）。"""
from app.core.pii_mask import mask_pii_text


class TestMaskPiiText:
    def test_masks_phone(self) -> None:
        out = mask_pii_text("联系电话 13812345678，欢迎联系。")
        assert "13812345678" not in out
        assert "[手机号]" in out
        assert "联系电话" in out  # 非敏感内容保留

    def test_phone_inside_longer_digits_not_masked(self) -> None:
        """12 位以上数字串不是手机号，不掩码（避免破坏订单号等）。"""
        text = "单号 1138123456780 保留"
        assert mask_pii_text(text) == text

    def test_masks_email(self) -> None:
        out = mask_pii_text("邮箱 zhang.san@example.com 请投递")
        assert "zhang.san@example.com" not in out
        assert "[邮箱]" in out

    def test_masks_id_card(self) -> None:
        out = mask_pii_text("身份证 110101199003078515X")
        assert "110101199003078515X" not in out
        assert "[证件号]" in out

    def test_name_replaced_when_known(self) -> None:
        out = mask_pii_text("张三在字节跳动工作，张三负责推荐系统。", name="张三")
        assert "张三" not in out
        assert "该候选人" in out
        assert "字节跳动" in out  # 公司非 PII，保留

    def test_short_name_not_replaced(self) -> None:
        """单字名不做替换（误伤率不可接受）。"""
        text = "李 明 的简历"
        assert mask_pii_text(text, name="李") == text

    def test_empty_name_no_replace(self) -> None:
        out = mask_pii_text("内容A", name="")
        assert out == "内容A"

    def test_combined(self) -> None:
        text = "张三 13812345678 zhangsan@example.com 110101199003078515X"
        out = mask_pii_text(text, name="张三")
        assert "张三" not in out
        assert "13812345678" not in out
        assert "zhangsan@example.com" not in out
        assert "110101199003078515X" not in out

    def test_no_pii_returns_unchanged(self) -> None:
        text = "精通 Python 与 FastAPI，五年后端经验。"
        assert mask_pii_text(text) == text

    def test_empty_text(self) -> None:
        assert mask_pii_text("") == ""
