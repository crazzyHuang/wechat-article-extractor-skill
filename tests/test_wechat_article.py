from __future__ import annotations

import importlib.util
import io
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "wechat_article.py"
SPEC = importlib.util.spec_from_file_location("wechat_article", SCRIPT)
assert SPEC and SPEC.loader
wechat_article = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = wechat_article
SPEC.loader.exec_module(wechat_article)


ARTICLE_HTML = """
<!doctype html>
<html><head>
<meta property="og:title" content="安全提取测试">
<meta property="og:description" content="这是一篇用于测试的公众号文章">
<meta property="og:image" content="https://mmbiz.qpic.cn/cover.jpg">
<meta name="author" content="测试作者">
<script>
var msg_title = "安全提取测试";
var nickname = "测试公众号";
var user_name = "gh_test";
var biz = "TXpBek1UQXdNQT09";
var ct = "1720000000";
var msg_link = "https://mp.weixin.qq.com/s/test";
</script></head>
<body><h1 id="activity-name">安全提取测试</h1>
<div id="js_content">
<p>第一段正文包含足够的信息，用来证明解析器确实提取到了文章内容，而不是只读取标题。</p>
<p>第二段正文继续提供测试文字，并包含 <strong>重点内容</strong>。</p>
<h2>一个小标题</h2>
<p>第三段正文用于满足完整性检查，同时验证段落数量和 Markdown 转换。</p>
<img data-src="https://mmbiz.qpic.cn/article-image.jpg" alt="示例图">
<script>globalThis.PWNED = true;</script>
</div></body></html>
"""


class WeChatArticleTests(unittest.TestCase):
    def test_stdout_is_reconfigured_from_gbk_to_utf8(self) -> None:
        raw = io.BytesIO()
        stream = io.TextIOWrapper(raw, encoding="gbk", newline="")
        original_stdout = sys.stdout
        try:
            sys.stdout = stream
            wechat_article.write_stdout_utf8("正文包含不换行空格：\u00a0\n")
            stream.flush()
        finally:
            sys.stdout = original_stdout
        self.assertEqual(raw.getvalue().decode("utf-8"), "正文包含不换行空格：\u00a0\n")

    def test_exact_host_validation(self) -> None:
        valid = wechat_article.validate_url("https://mp.weixin.qq.com/s/abc?x=1#fragment")
        self.assertEqual(valid, "https://mp.weixin.qq.com/s/abc?x=1")
        for malicious in (
            "http://mp.weixin.qq.com/s/abc",
            "https://mp.weixin.qq.com.evil.example/s/abc",
            "https://evil.example/?next=https://mp.weixin.qq.com/s/abc",
            "https://user@mp.weixin.qq.com/s/abc",
        ):
            with self.assertRaises(wechat_article.InputError):
                wechat_article.validate_url(malicious)

    def test_extract_complete_article(self) -> None:
        result = wechat_article.extract_article(
            ARTICLE_HTML,
            "https://mp.weixin.qq.com/s/test",
            "https://mp.weixin.qq.com/s/test",
            "saved_html",
        )
        self.assertTrue(result["done"])
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["data"]["msg_title"], "安全提取测试")
        self.assertEqual(result["data"]["account_name"], "测试公众号")
        self.assertGreaterEqual(result["paragraph_count"], 4)
        self.assertEqual(result["image_count"], 1)
        self.assertNotIn("<script", result["data"]["msg_content_html"])
        self.assertIn("## 一个小标题", result["data"]["msg_content_markdown"])

    def test_page_script_is_never_executed(self) -> None:
        hostile = ARTICLE_HTML.replace(
            "var msg_title = \"安全提取测试\";",
            "var msg_title = (function(){ throw new Error('must not run'); })();",
        )
        result = wechat_article.extract_article(hostile)
        self.assertEqual(result["data"]["msg_title"], "安全提取测试")
        self.assertTrue(result["done"])

    def test_captcha_detection(self) -> None:
        result = wechat_article.extract_article(
            "<html><body>环境异常，请完成验证</body></html>",
            "https://mp.weixin.qq.com/s/test",
            "https://mp.weixin.qq.com/mp/wappoc_appmsgcaptcha?poc_token=x",
            "direct_http",
        )
        self.assertFalse(result["done"])
        self.assertEqual(result["status"], "captcha_required")

    def test_image_note_json_like_data(self) -> None:
        image_html = """
        <html><head><meta property="og:title" content="图片笔记"></head><body>
        <script>
        var nickname = '图片号';
        var ct = '1720000000';
        var picture_page_info_list = [{cdn_url:'https://mmbiz.qpic.cn/one.jpg'}, {cdn_url:'https://mmbiz.qpic.cn/two.jpg'}];
        </script><div id="js_content"></div></body></html>
        """
        result = wechat_article.extract_article(image_html)
        self.assertTrue(result["done"])
        self.assertEqual(result["data"]["msg_type"], "image")
        self.assertEqual(result["image_count"], 2)

    def test_saved_html_cli_and_no_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            html_file = root / "page.html"
            output_file = root / "article.md"
            html_file.write_text(ARTICLE_HTML, encoding="utf-8")
            first = wechat_article.main([
                "https://mp.weixin.qq.com/s/test",
                "--html-file", str(html_file),
                "--format", "markdown",
                "--output", str(output_file),
            ])
            with redirect_stderr(io.StringIO()):
                second = wechat_article.main([
                    "https://mp.weixin.qq.com/s/test",
                    "--html-file", str(html_file),
                    "--format", "markdown",
                    "--output", str(output_file),
                ])
            self.assertEqual(first, 0)
            self.assertEqual(second, 3)
            self.assertIn("# 安全提取测试", output_file.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
