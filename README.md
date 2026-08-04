# WeChat Article Extractor Skill

一个面向 Codex/Agent 的微信公众号文章读取 skill。它从公开微信文章链接、浏览器已渲染页面或本地保存的 HTML 中提取结构化内容，并在结果不完整、需要验证或访问受限时明确停止。

## 主要变化

- 只允许 `https://mp.weixin.qq.com` 和 `https://weixin.sogou.com` 精确主机名，并逐次校验重定向。
- 不执行页面 JavaScript，不读取 Cookie、浏览器存储或账号凭据。
- 识别验证码、频率限制、登录要求、文章删除/违规等状态，不尝试绕过。
- 输出统一 JSON，包含正文、元数据、图片、完整度分数和警告。
- 可将正文转换为经过清理的 Markdown；已有文件默认不覆盖。
- 仅依赖 Python 3.10+ 标准库，附带单元测试。

## 安装

将整个仓库目录放入 Codex 的 skills 目录，确保目录名和 `SKILL.md` 中的 `name` 都是 `wechat-article-extractor-skill`。

## 使用

在 Codex 中可以直接说：

```text
请使用 $wechat-article-extractor-skill 总结这篇文章：
https://mp.weixin.qq.com/s/...
```

也可以直接运行解析器：

```powershell
python scripts/wechat_article.py "https://mp.weixin.qq.com/s/..." --pretty
```

解析已保存的 HTML：

```powershell
python scripts/wechat_article.py "https://mp.weixin.qq.com/s/..." --html-file "page.html" --pretty
```

保存 Markdown：

```powershell
python scripts/wechat_article.py "https://mp.weixin.qq.com/s/..." --format markdown --output "article.md"
```

若目标文件已经存在，需显式添加 `--force` 才会覆盖。

## 测试

```powershell
python -m unittest discover -s tests -v
```

## 安全边界

该 skill 只读取公开内容。它不会绕过验证码或登录，不会访问浏览器凭据，也不会执行文章中的脚本。遇到需要人工验证的页面时，应由用户在正常浏览器会话中完成验证，再读取已渲染的文章区域。详细约束见 `references/security-policy.md`。

## License

MIT

