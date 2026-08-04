# Result schema

The CLI prints UTF-8 JSON unless `--format markdown` is selected.

- `done`: true only when the extraction passes minimum completeness checks.
- `status`: `ok`, `partial`, `invalid_url`, `fetch_error`, `captcha_required`, `rate_limited`, `login_required`, `deleted`, `blocked`, or `empty`.
- `code`: stable numeric compatibility code. `0` means complete.
- `source_url`: user-provided article URL.
- `final_url`: final URL after validated redirects.
- `acquisition`: `direct_http` or `saved_html`.
- `data`: normalized article and account fields.
- `body_text_length`: visible body text length.
- `paragraph_count`: extracted paragraph and heading count.
- `image_count`: unique body image count.
- `completeness.score`: number from 0 to 1.
- `completeness.warnings`: reasons the result may be incomplete.

Do not summarize when `done` is false unless the user explicitly accepts a partial summary. Always repeat the relevant warnings.
