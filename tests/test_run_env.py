from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _print_env(env_file: Path, *keys: str) -> list[str]:
    result = subprocess.run(
        ["bash", str(ROOT / "run.sh"), "--print-env-file", str(env_file), *keys],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.splitlines()


def test_load_env_keeps_dollars_strips_quotes_and_accepts_export(tmp_path: Path):
    env_file = tmp_path / "accounts.env"
    env_file.write_text(
        "\n".join(
            [
                "# comment",
                "SECRET_KEY=abc$123$not_a_var",
                'USER1_PASSWORD_HASH="scrypt$16384$8$1$salt$hash"',
                "export USER1_PASSWORD='correct horse'",
                "USER2_PASSWORD=plain",
                "not a key",
                "",
            ]
        )
        + "\n",
        newline="\n",
    )
    assert _print_env(
        env_file,
        "SECRET_KEY",
        "USER1_PASSWORD_HASH",
        "USER1_PASSWORD",
        "USER2_PASSWORD",
    ) == [
        "SECRET_KEY=abc$123$not_a_var",
        "USER1_PASSWORD_HASH=scrypt$16384$8$1$salt$hash",
        "USER1_PASSWORD=correct horse",
        "USER2_PASSWORD=plain",
    ]


def test_load_env_strips_crlf_and_does_not_run_values(tmp_path: Path):
    marker = tmp_path / "pwned"
    env_file = tmp_path / "crlf.env"
    env_file.write_bytes(f"USER1_PASSWORD=\"horse\"\r\n$(touch {marker})=1\nSAFE=ok\r\n".encode())
    assert _print_env(env_file, "USER1_PASSWORD", "SAFE") == ["USER1_PASSWORD=horse", "SAFE=ok"]
    assert not marker.exists()
