"""Repair upstream v0.3.5 PowerShell named argument binding, preserving guards.

Run only against the SHA-256-verified official v0.3.5 source bundle. PowerShell
array splatting treats '-DbPath' as a positional value; named splatting needs
a dictionary. No changes to DLL validation, key verification or client cleanup.
"""

from pathlib import Path
import hashlib
import sys


def patch(root: Path) -> None:
    path = root / "chatlog_keeper/active_key.py"
    source = path.read_text(encoding="utf-8")
    old = '            "$scriptArgs = @((ConvertFrom-Json -InputObject $argsJson))",'
    new = '''            "$rawArgs = @((ConvertFrom-Json -InputObject $argsJson))",
            "$scriptArgs = @{}",
            "for ($i = 0; $i -lt $rawArgs.Count; $i++) {",
            "  $argName = [string]$rawArgs[$i]",
            "  switch -Exact ($argName) {",
            "    '-RequireClosedClient' { $scriptArgs['RequireClosedClient'] = $true }",
            "    '-NoDebugForKey' { $scriptArgs['NoDebugForKey'] = $true }",
            "    { $_ -in @('-DbPath','-WeixinDllPath','-QQExePath','-WrapperNode','-TimeoutSeconds') } {",
            "      $i++; if ($i -ge $rawArgs.Count) { throw 'missing helper argument' }",
            "      $scriptArgs[$argName.Substring(1)] = $rawArgs[$i]",
            "    }",
            "    default { throw 'unsupported helper argument' }",
            "  }",
            "}",'''
    if new in source:
        return
    expected = "ecc632189bb70965a07af7f5a2076ac6e162dcdaf634fe05cbb545eecfa89ff3"
    if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
        raise RuntimeError("Source digest mismatch: only the verified v0.3.5 file can be patched")
    if source.count(old) != 1:
        raise RuntimeError("Unexpected source: refusing to patch this reader version")
    path.write_text(source.replace(old, new), encoding="utf-8")


if __name__ == "__main__":
    patch(Path(sys.argv[1]))
