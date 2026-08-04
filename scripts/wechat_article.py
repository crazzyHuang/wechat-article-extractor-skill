#!/usr/bin/env python3
"""Safely extract public WeChat article content without executing page scripts."""

from __future__ import annotations

import argparse
import ast
import html as html_lib
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable, Iterator
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


ALLOWED_HOSTS = frozenset({"mp.weixin.qq.com", "weixin.sogou.com"})
MAX_HTML_BYTES = 8 * 1024 * 1024
MAX_REDIRECTS = 5

ERRORS = {
    0: "成功",
    1000: "文章获取失败",
    1001: "文章内容不完整",
    1002: "请求失败",
    1003: "响应为空",
    1004: "访问过于频繁",
    1006: "公众号已迁移",
    1010: "需要人工验证",
    1011: "需要登录",
    2001: "请提供文章内容或链接",
    2002: "链接已过期",
    2003: "内容涉嫌侵权，无法查看",
    2005: "内容已被发布者删除",
    2006: "内容因违规无法查看",
    2009: "不支持的链接",
}

BLOCK_MARKERS = (
    ("访问过于频繁", "rate_limited", 1004),
    ("环境异常", "captcha_required", 1010),
    ("完成验证", "captcha_required", 1010),
    ("链接已过期", "blocked", 2002),
    ("该内容已被发布者删除", "deleted", 2005),
    ("此内容因违规无法查看", "blocked", 2006),
    ("涉嫌侵权，无法查看", "blocked", 2003),
    ("请在微信客户端打开链接", "login_required", 1011),
)

VOID_TAGS = frozenset({"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"})
DROP_TAGS = frozenset({"script", "style", "iframe", "object", "embed", "form", "input", "button", "textarea", "select"})
SAFE_HTML_TAGS = frozenset({"p", "br", "section", "div", "span", "strong", "b", "em", "i", "u", "s", "blockquote", "pre", "code", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "li", "table", "thead", "tbody", "tr", "th", "td", "img", "a", "hr"})


class InputError(ValueError):
    pass


class FetchError(RuntimeError):
    pass


@dataclass
class Node:
    tag: str
    attrs: dict[str, str] = field(default_factory=dict)
    children: list[Node | str] = field(default_factory=list)


class TreeParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Node("document")
        self.stack = [self.root]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        clean_attrs = {str(k).lower(): "" if v is None else str(v) for k, v in attrs}
        node = Node(tag.lower(), clean_attrs)
        self.stack[-1].children.append(node)
        if tag.lower() not in VOID_TAGS:
            self.stack.append(node)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag.lower() not in VOID_TAGS:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        wanted = tag.lower()
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == wanted:
                del self.stack[index:]
                return

    def handle_data(self, data: str) -> None:
        if data:
            self.stack[-1].children.append(data)


class SafeRedirectHandler(HTTPRedirectHandler):
    def __init__(self) -> None:
        super().__init__()
        self.redirect_count = 0

    def redirect_request(self, req: Request, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> Request | None:
        self.redirect_count += 1
        if self.redirect_count > MAX_REDIRECTS:
            raise FetchError("too many redirects")
        safe_url = validate_url(urljoin(req.full_url, newurl))
        return super().redirect_request(req, fp, code, msg, headers, safe_url)


def validate_url(value: str) -> str:
    try:
        parsed = urlsplit(value.strip())
    except ValueError as exc:
        raise InputError("invalid URL") from exc
    hostname = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme.lower() != "https" or hostname not in ALLOWED_HOSTS:
        raise InputError("URL must use HTTPS and an exact allowed WeChat hostname")
    try:
        port = parsed.port
    except ValueError as exc:
        raise InputError("invalid URL port") from exc
    if parsed.username or parsed.password or port not in (None, 443):
        raise InputError("credentials and custom ports are not allowed")
    path = parsed.path or "/"
    return urlunsplit(("https", hostname, path, parsed.query, ""))


def fetch_html(url: str, timeout: float) -> tuple[str, str]:
    safe_url = validate_url(url)
    redirect_handler = SafeRedirectHandler()
    opener = build_opener(redirect_handler)
    request = Request(
        safe_url,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/127 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.5",
        },
        method="GET",
    )
    try:
        with opener.open(request, timeout=timeout) as response:
            final_url = validate_url(response.geturl())
            content_type = response.headers.get_content_type()
            if content_type not in {"text/html", "application/xhtml+xml", "text/plain"}:
                raise FetchError(f"unexpected content type: {content_type}")
            payload = response.read(MAX_HTML_BYTES + 1)
            if len(payload) > MAX_HTML_BYTES:
                raise FetchError("response exceeds size limit")
            charset = response.headers.get_content_charset() or "utf-8"
            try:
                return payload.decode(charset), final_url
            except (LookupError, UnicodeDecodeError):
                return payload.decode("utf-8", errors="replace"), final_url
    except (HTTPError, URLError, TimeoutError, FetchError) as exc:
        raise FetchError(str(exc)) from exc


def iter_nodes(node: Node) -> Iterator[Node]:
    for child in node.children:
        if isinstance(child, Node):
            yield child
            yield from iter_nodes(child)


def node_classes(node: Node) -> set[str]:
    return {part for part in node.attrs.get("class", "").split() if part}


def find_first(root: Node, *, tag: str | None = None, node_id: str | None = None, class_name: str | None = None) -> Node | None:
    for node in iter_nodes(root):
        if tag is not None and node.tag != tag:
            continue
        if node_id is not None and node.attrs.get("id") != node_id:
            continue
        if class_name is not None and class_name not in node_classes(node):
            continue
        return node
    return None


def find_all(root: Node, *, tag: str | None = None, class_name: str | None = None) -> list[Node]:
    result = []
    for node in iter_nodes(root):
        if tag is not None and node.tag != tag:
            continue
        if class_name is not None and class_name not in node_classes(node):
            continue
        result.append(node)
    return result


def text_content(node: Node, preserve_breaks: bool = False) -> str:
    parts: list[str] = []

    def visit(current: Node | str) -> None:
        if isinstance(current, str):
            parts.append(current)
            return
        if current.tag in DROP_TAGS:
            return
        for child in current.children:
            visit(child)
        if preserve_breaks and current.tag in {"p", "div", "section", "br", "li", "h1", "h2", "h3", "h4", "blockquote", "pre"}:
            parts.append("\n")

    visit(node)
    value = "".join(parts).replace("\u00a0", " ")
    if preserve_breaks:
        value = re.sub(r"[ \t]+", " ", value)
        value = re.sub(r"\n[ \t]+", "\n", value)
        return re.sub(r"\n{3,}", "\n\n", value).strip()
    return re.sub(r"\s+", " ", value).strip()


def raw_text_content(node: Node) -> str:
    parts: list[str] = []

    def visit(current: Node | str) -> None:
        if isinstance(current, str):
            parts.append(current)
            return
        for child in current.children:
            visit(child)

    visit(node)
    return "".join(parts)


def get_meta(root: Node, *, name: str | None = None, prop: str | None = None) -> str | None:
    for node in iter_nodes(root):
        if node.tag != "meta":
            continue
        if name is not None and node.attrs.get("name", "").lower() == name.lower():
            return node.attrs.get("content") or None
        if prop is not None and node.attrs.get("property", "").lower() == prop.lower():
            return node.attrs.get("content") or None
    return None


def safe_web_url(value: str | None) -> str | None:
    if not value:
        return None
    candidate = html_lib.unescape(value.strip())
    parsed = urlsplit(candidate)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        return None
    if parsed.username or parsed.password:
        return None
    return candidate


def _read_quoted(script: str, start: int) -> tuple[str, int] | None:
    quote = script[start]
    chars: list[str] = []
    index = start + 1
    while index < len(script):
        char = script[index]
        if char == quote:
            return "".join(chars), index + 1
        if char == "\\" and index + 1 < len(script):
            index += 1
            escaped = script[index]
            mapping = {"n": "\n", "r": "\r", "t": "\t", "b": "\b", "f": "\f", "v": "\v", "0": "\0"}
            if escaped == "x" and index + 2 < len(script):
                token = script[index + 1:index + 3]
                if re.fullmatch(r"[0-9a-fA-F]{2}", token):
                    chars.append(chr(int(token, 16)))
                    index += 2
                else:
                    chars.append(escaped)
            elif escaped == "u" and index + 4 < len(script):
                token = script[index + 1:index + 5]
                if re.fullmatch(r"[0-9a-fA-F]{4}", token):
                    chars.append(chr(int(token, 16)))
                    index += 4
                else:
                    chars.append(escaped)
            else:
                chars.append(mapping.get(escaped, escaped))
        else:
            chars.append(char)
        index += 1
    return None


def _read_balanced(script: str, start: int) -> tuple[str, int] | None:
    pairs = {"[": "]", "{": "}"}
    stack = [pairs[script[start]]]
    index = start + 1
    while index < len(script):
        char = script[index]
        if char in {'"', "'", "`"}:
            quoted = _read_quoted(script, index)
            if quoted is None:
                return None
            _, index = quoted
            continue
        if char in pairs:
            stack.append(pairs[char])
        elif char == stack[-1]:
            stack.pop()
            if not stack:
                return script[start:index + 1], index + 1
        index += 1
    return None


def _parse_literal(raw: str) -> Any:
    raw = raw.strip()
    if not raw:
        return None
    if raw[0] in {'"', "'", "`"}:
        parsed = _read_quoted(raw, 0)
        return parsed[0] if parsed else None
    number = re.match(r"^-?\d+(?:\.\d+)?", raw)
    if number:
        token = number.group(0)
        return float(token) if "." in token else int(token)
    for token, value in (("true", True), ("false", False), ("null", None), ("undefined", None)):
        if raw.startswith(token):
            return value
    if raw[0] in "[{":
        balanced = _read_balanced(raw, 0)
        if not balanced:
          …1207 tokens truncated….strip() else ""
        if tag == "code":
            return f"`{inner.strip()}`" if inner.strip() else ""
        if tag == "pre":
            return f"\n```\n{text_content(current, preserve_breaks=True)}\n```\n\n"
        if tag == "blockquote":
            quoted = text_content(current, preserve_breaks=True)
            return "\n" + "\n".join(f"> {line}" for line in quoted.splitlines()) + "\n\n"
        if tag == "img":
            src = safe_web_url(current.attrs.get("data-src") or current.attrs.get("src"))
            alt = clean_inline_text(current.attrs.get("alt")) or "图片"
            return f"\n![{alt}]({src})\n\n" if src else ""
        if tag == "a":
            href = safe_web_url(current.attrs.get("href"))
            label = inner.strip() or href or ""
            return f"[{label}]({href})" if href else label
        if tag in {"ul", "ol"}:
            lines: list[str] = []
            index = 1
            for child in current.children:
                if isinstance(child, Node) and child.tag == "li":
                    body = render(child, list_depth + 1, tag == "ol", index).strip()
                    prefix = f"{index}. " if tag == "ol" else "- "
                    lines.append("  " * list_depth + prefix + body)
                    index += 1
            return "\n" + "\n".join(lines) + "\n\n"
        if tag == "li":
            return inner
        if tag == "hr":
            return "\n---\n\n"
        if tag in {"table", "thead", "tbody", "tr", "th", "td"}:
            return inner + ("\n" if tag == "tr" else " ")
        return inner

    value = "".join(render(child) for child in node.children)
    value = re.sub(r"[ \t]+\n", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def unique_image_urls(node: Node | None) -> list[str]:
    if node is None:
        return []
    result: list[str] = []
    seen: set[str] = set()
    for image in find_all(node, tag="img"):
        src = safe_web_url(image.attrs.get("data-src") or image.attrs.get("src"))
        if src and src not in seen:
            seen.add(src)
            result.append(src)
    return result


def block_result(html_text: str, source_url: str | None, final_url: str | None, acquisition: str) -> dict[str, Any] | None:
    if final_url and "/mp/wappoc_appmsgcaptcha" in final_url:
        return failure("captcha_required", 1010, source_url, final_url, acquisition, ["WeChat returned a verification page"])
    for marker, status, code in BLOCK_MARKERS:
        if marker in html_text and 'id="js_content"' not in html_text and "id='js_content'" not in html_text:
            return failure(status, code, source_url, final_url, acquisition, [marker])
    return None


def failure(status: str, code: int, source_url: str | None, final_url: str | None, acquisition: str, warnings: list[str]) -> dict[str, Any]:
    return {
        "done": False,
        "status": status,
        "code": code,
        "message": ERRORS.get(code, "提取失败"),
        "source_url": source_url,
        "final_url": final_url,
        "acquisition": acquisition,
        "data": None,
        "body_text_length": 0,
        "paragraph_count": 0,
        "image_count": 0,
        "completeness": {"score": 0.0, "warnings": warnings},
    }


def extract_article(html_text: str, source_url: str | None = None, final_url: str | None = None, acquisition: str = "saved_html") -> dict[str, Any]:
    blocked = block_result(html_text, source_url, final_url, acquisition)
    if blocked:
        return blocked
    if not html_text.strip():
        return failure("empty", 1003, source_url, final_url, acquisition, ["Empty HTML input"])

    parser = TreeParser()
    parser.feed(html_text)
    root = parser.root
    scripts = [raw_text_content(node) for node in find_all(root, tag="script")]

    body_node = find_first(root, node_id="js_content")
    title = clean_inline_text(
        extract_script_assignment(scripts, "msg_title")
        or get_meta(root, prop="og:title")
        or first_text(root, ids=("activity-name",), classes=("rich_media_title",))
    )
    author = clean_inline_text(get_meta(root, name="author") or first_text(root, ids=("js_author_name",)))
    account_name = clean_inline_text(
        first_text(root, ids=("js_name",), classes=("profile_nickname", "wx_follow_nickname"))
        or extract_script_assignment(scripts, "nickname")
        or extract_script_assignment(scripts, "nick_name")
    )
    description = clean_inline_text(get_meta(root, prop="og:description") or get_meta(root, name="description") or extract_script_assignment(scripts, "msg_desc"))
    cover = safe_web_url(get_meta(root, prop="og:image") or extract_script_assignment(scripts, "msg_cdn_url"))
    account_avatar = safe_web_url(extract_script_assignment(scripts, "ori_head_img_url") or extract_script_assignment(scripts, "hd_head_img"))
    account_id = clean_inline_text(extract_script_assignment(scripts, "user_name"))
    biz = clean_inline_text(extract_script_assignment(scripts, "biz"))
    msg_link = safe_web_url(extract_script_assignment(scripts, "msg_link") or get_meta(root, prop="og:url") or source_url)
    source_link = safe_web_url(extract_script_assignment(scripts, "msg_source_url"))

    publish_value = None
    for field_name in ("ct", "create_time", "publish_time"):
        publish_value = extract_script_assignment(scripts, field_name)
        if publish_value is not None:
            break
    publish_display, publish_timestamp = format_publish_time(publish_value)
    if not publish_display:
        publish_display, publish_timestamp = format_publish_time(first_text(root, ids=("publish_time", "post-date")))

    images = unique_image_urls(body_node)
    picture_info = extract_script_assignment(scripts, "picture_page_info_list")
    if isinstance(picture_info, str):
        picture_info = _parse_literal(picture_info)
    if isinstance(picture_info, list):
        for item in picture_info:
            if isinstance(item, dict):
                src = safe_web_url(item.get("cdn_url") or item.get("url"))
                if src and src not in images:
                    images.append(src)

    body_text = text_content(body_node, preserve_breaks=True) if body_node else ""
    content_html = sanitize_html(body_node) if body_node else ""
    markdown = markdown_from_node(body_node) if body_node else ""
    if not body_text and description:
        body_text = description
        markdown = description
    if images and not re.search(r"!\[[^]]*\]\(https?://", markdown):
        markdown = (markdown + "\n\n" if markdown else "") + "\n\n".join(f"![图片]({src})" for src in images)

    body_type = "post"
    if picture_info or (images and len(body_text) < 80):
        body_type = "image"
    elif find_first(root, class_name="page_share_audio") or find_first(root, node_id="voice_parent"):
        body_type = "voice"
    elif find_first(root, node_id="js_share_content"):
        body_type = "repost"
    elif "video" in node_classes(find_first(root, tag="body") or Node("body")):
        body_type = "video"

    paragraph_count = 0
    if body_node:
        paragraph_count = sum(1 for node in iter_nodes(body_node) if node.tag in {"p", "li", "blockquote", "h1", "h2", "h3", "h4"} and text_content(node))

    warnings: list[str] = []
    score = 0.0
    if title:
        score += 0.2
    else:
        warnings.append("Missing article title")
    if account_name or author:
        score += 0.1
    else:
        warnings.append("Missing author and account name")
    if publish_display:
        score += 0.1
    else:
        warnings.append("Missing publish time")
    if len(body_text) >= 500:
        score += 0.45
    elif len(body_text) >= 80:
        score += 0.35
    elif images:
        score += 0.3
    else:
        warnings.append("Article body is empty or too short")
    if paragraph_count >= 3 or images:
        score += 0.1
    else:
        warnings.append("Too few body structure signals")
    if msg_link or source_url:
        score += 0.05
    score = round(min(score, 1.0), 2)
    done = bool(title and (len(body_text) >= 80 or images) and score >= 0.6)
    status = "ok" if done else "partial"
    code = 0 if done else 1001

    data = {
        "account_name": account_name,
        "account_avatar": account_avatar,
        "account_id": account_id,
        "account_biz": biz,
        "msg_title": title,
        "msg_desc": description,
        "msg_content_html": content_html or None,
        "msg_content_text": body_text or None,
        "msg_content_markdown": markdown or None,
        "msg_cover": cover,
        "msg_author": author,
        "msg_type": body_type,
        "msg_publish_time": publish_display,
        "msg_publish_timestamp": publish_timestamp,
        "msg_link": msg_link,
        "msg_source_url": source_link,
        "image_urls": images,
    }
    return {
        "done": done,
        "status": status,
        "code": code,
        "message": ERRORS[code],
        "source_url": source_url,
        "final_url": final_url,
        "acquisition": acquisition,
        "data": data,
        "body_text_length": len(body_text),
        "paragraph_count": paragraph_count,
        "image_count": len(images),
        "completeness": {"score": score, "warnings": warnings},
    }


def result_markdown(result: dict[str, Any]) -> str:
    data = result.get("data") or {}
    title = data.get("msg_title") or "未命名微信文章"
    lines = [f"# {title}", ""]
    if data.get("msg_author"):
        lines.append(f"> 作者：{data['msg_author']}")
    if data.get("account_name"):
        lines.append(f"> 公众号：{data['account_name']}")
    if data.get("msg_publish_time"):
        lines.append(f"> 发布时间：{data['msg_publish_time']}")
    if result.get("source_url"):
        lines.append(f"> 原文：{result['source_url']}")
    lines.extend(["", "---", "", data.get("msg_content_markdown") or data.get("msg_content_text") or ""])
    warnings = result.get("completeness", {}).get("warnings") or []
    if warnings:
        lines.extend(["", "---", "", "提取警告："])
        lines.extend(f"- {warning}" for warning in warnings)
    return "\n".join(lines).rstrip() + "\n"


def read_html_file(path: Path) -> str:
    if not path.is_file():
        raise InputError(f"HTML file does not exist: {path}")
    if path.stat().st_size > MAX_HTML_BYTES:
        raise InputError("HTML file exceeds size limit")
    payload = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return payload.decode(encoding)
        except UnicodeDecodeError:
            continue
    return payload.decode("utf-8", errors="replace")


def write_output(path: Path, content: str, force: bool) -> None:
    if path.exists() and not force:
        raise InputError(f"output already exists: {path}; pass --force to replace it")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")


def configure_utf8_stream(stream: Any) -> None:
    reconfigure = getattr(stream, "reconfigure", None)
    if not callable(reconfigure):
        return
    try:
        reconfigure(encoding="utf-8")
    except (OSError, ValueError):
        # StringIO, redirected streams, and some embedding hosts cannot be
        # reconfigured. They commonly accept Unicode directly, so leave them.
        pass


def write_stdout_utf8(content: str) -> None:
    configure_utf8_stream(sys.stdout)
    try:
        sys.stdout.write(content)
    except UnicodeEncodeError:
        # Preserve JSON/Markdown exactly when a legacy Windows console remains
        # locked to a code page such as GBK.
        raw = getattr(sys.stdout, "buffer", None)
        if raw is None:
            raise
        raw.write(content.encode("utf-8"))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_url", nargs="?", help="Public WeChat article URL")
    parser.add_argument("--html-file", type=Path, help="Parse a browser-saved HTML file instead of fetching")
    parser.add_argument("--format", choices=("json", "markdown"), default="json")
    parser.add_argument("--output", type=Path, help="Write output to this path")
    parser.add_argument("--force", action="store_true", help="Allow replacing an existing output file")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON")
    parser.add_argument("--timeout", type=float, default=20.0, help="Direct request timeout in seconds")
    return parser


def main(argv: list[str] | None = None) -> int:
    configure_utf8_stream(sys.stderr)
    args = build_parser().parse_args(argv)
    try:
        source_url = validate_url(args.source_url) if args.source_url else None
        if args.html_file:
            html_text = read_html_file(args.html_file)
            final_url = source_url
            acquisition = "saved_html"
        else:
            if not source_url:
                raise InputError("provide a WeChat URL or --html-file")
            html_text, final_url = fetch_html(source_url, args.timeout)
            acquisition = "direct_http"
        result = extract_article(html_text, source_url, final_url, acquisition)
    except InputError as exc:
        result = failure("invalid_url", 2009, args.source_url, None, "none", [str(exc)])
    except FetchError as exc:
        result = failure("fetch_error", 1002, args.source_url, None, "direct_http", [str(exc)])

    if args.format == "markdown":
        output = result_markdown(result)
    else:
        output = json.dumps(result, ensure_ascii=False, indent=2 if args.pretty else None, default=str) + "\n"
    try:
        if args.output:
            write_output(args.output, output, args.force)
        else:
            write_stdout_utf8(output)
    except InputError as exc:
        sys.stderr.write(f"error: {exc}\n")
        return 3
    return 0 if result.get("done") else 2


if __name__ == "__main__":
    raise SystemExit(main())
