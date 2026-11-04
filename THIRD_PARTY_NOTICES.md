# Sources and attribution

LoveChat Coach（恋聊教练）is developed from the **OpenDate Skill and Python project**, https://github.com/sumitaich1998/OpenDate, by Sumit Aich. The original MIT license is preserved in `LICENSE`. The original `opendate` Python namespace remains for compatibility. Dating skills under `src/opendate/skills/registry/` retain their original content and attribution.

The native visual theme references Hallmark and open-claude-ui interaction patterns. Their upstream sources, licenses and the distinction between native adaptation and copied web assets are documented in `docs/theme-third-party-notices.md`.

The optional history integration references chatlog-keeper and chatlog-keeper-skill. Source/release URLs and verification metadata are in `docs/wechat-history-sources.json` and `src/opendate/wechat_reader.py`. Reader binaries, user authorization caches, account keys and private chat databases are not distributed with this source release. pyweixin-rpa is an optional external advanced adapter, not bundled as part of the basic TXT and OCR workflow.

Windows DPAPI is used through the OS API; the project does not collect or redistribute model account login tokens. Python packages are installed separately with their respective licenses.
