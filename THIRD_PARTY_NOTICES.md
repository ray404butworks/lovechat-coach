# Sources and attribution

## Primary coaching method: goutoujunshi

The v0.3 coaching kernel, scene routing, evidence separation, one-action reply composition,
feedback branches and bounded memory design are adapted from **goutoujunshi** by powerycy:
https://github.com/shengjidaguai-china/goutoujunshi, pinned revision
`6db7354a4002dc7c448a9c87ffdad8132570c9d3` (MIT).
Its 43 Markdown references are reproduced in the Agent skill and packaged runtime.
The full upstream copyright and license are preserved in both copies as
`UPSTREAM_LICENSE.txt`; `upstream.json` records the revision and file hashes.
`scripts/sync_coach_references.py --check` verifies both copies.
The Python coaching engine and desktop memory API are new local implementations
of the reviewed method, not the upstream CLI or a claim of upstream endorsement.
Upstream references describe their original tools and workflows; their commands
are not automatically executed. Runtime knowledge is historical material and is
not a live legal, medical or crisis directory.

## Desktop foundation and legacy CLI

LoveChat Coach（恋聊教练）is developed from the **OpenDate Skill and Python project**, https://github.com/sumitaich1998/OpenDate, by Sumit Aich. The original MIT license is preserved in `LICENSE`. The original `opendate` Python namespace remains for compatibility. Dating skills under `src/opendate/skills/registry/` retain their original content and attribution.

The native visual theme references Hallmark and open-claude-ui interaction patterns. Their upstream sources, licenses and the distinction between native adaptation and copied web assets are documented in `docs/theme-third-party-notices.md`.

The optional history integration references chatlog-keeper and chatlog-keeper-skill. Source/release URLs and verification metadata are in `docs/wechat-history-sources.json` and `src/opendate/wechat_reader.py`. Reader binaries, user authorization caches, account keys and private chat databases are not distributed with this source release. pyweixin-rpa is an optional external advanced adapter, not bundled as part of the basic TXT and OCR workflow.

Windows DPAPI is used through the OS API; the project does not collect or redistribute model account login tokens. Python packages are installed separately with their respective licenses.
