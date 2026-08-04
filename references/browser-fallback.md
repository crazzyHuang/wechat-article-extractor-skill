# Browser fallback

Use this path only after a direct request is blocked or when the user already has the article open.

1. Prefer an existing user-visible article tab over opening duplicates.
2. Ask the browser for a small, targeted payload instead of a whole-page snapshot:
   - title from `#activity-name`, `.rich_media_title`, or `meta[property="og:title"]`
   - author from `#js_author_name` or `meta[name="author"]`
   - account from `#js_name`, `.profile_nickname`, or `.wx_follow_nickname`
   - publish time from `#publish_time` or `#post-date`
   - rendered HTML from `#js_content`
   - image URLs from `#js_content img[data-src], #js_content img[src]`
3. If a CAPTCHA or environment verification appears, ask the user to complete it manually. Never solve, evade, or automate it without the user.
4. If browser DOM access remains unavailable, ask the user to save the successfully rendered page as HTML and pass it to `--html-file`.
5. If only screenshots are available, use OCR as a clearly labeled partial fallback. Do not claim that image-only OCR is a complete archive.

Never inspect cookies, local storage, browser profiles, passwords, authentication tokens, or unrelated tabs.
