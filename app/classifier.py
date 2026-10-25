from __future__ import annotations

from dataclasses import dataclass
from statistics import median


AUTOMATION_MARKERS = (
    "curl ",
    "wget ",
    "busybox",
    "base64 -d",
    "chmod +x",
    "/tmp/",
    "nohup ",
    "crontab",
    "python -c",
    "python3 -c",
    "perl -e",
    "sh -c",
    "bash -c",
)


@dataclass(frozen=True)
class Classification:
    score: int
    label: str
    reasons: tuple[str, ...]


def classify(commands: list[dict]) -> Classification:
    """Estimate automation likelihood from timing and command-shape signals.

    This intentionally says nothing about whether an AI model was involved. Fast
    humans, pasted commands, orchestration tools, and bots can look alike.
    """
    if not commands:
        return Classification(0, "unknown", ("No command timing evidence yet",))

    score = 0
    reasons: list[str] = []
    first_delay = float(commands[0].get("since_start_ms") or 0)
    if first_delay <= 1_000:
        score += 25
        reasons.append("first command arrived within 1 second")
    elif first_delay <= 3_000:
        score += 12
        reasons.append("first command arrived within 3 seconds")
    elif first_delay >= 8_000:
        score -= 5
        reasons.append("deliberate pause before first command")

    gaps = [float(c["gap_ms"]) for c in commands[1:] if c.get("gap_ms") is not None]
    if gaps:
        med_gap = median(gaps)
        if med_gap <= 350:
            score += 25
            reasons.append("sub-350 ms median command gap")
        elif med_gap <= 1_000:
            score += 15
            reasons.append("sub-second median command gap")
        elif med_gap <= 2_500:
            score += 8
            reasons.append("rapid command cadence")
        elif med_gap >= 8_000:
            score -= 8
            reasons.append("human-paced command gaps")

        burst_ratio = sum(g <= 500 for g in gaps) / len(gaps)
        if burst_ratio >= 0.6:
            score += 20
            reasons.append("most commands arrived in a burst")

    cps_values = []
    for command in commands:
        typed_ms = float(command.get("typed_ms") or 0)
        text = str(command.get("command") or "")
        if typed_ms >= 40 and text:
            cps_values.append(len(text) / (typed_ms / 1_000))
    if cps_values:
        med_cps = median(cps_values)
        if med_cps >= 35:
            score += 25
            reasons.append("paste-like input speed")
        elif med_cps >= 15:
            score += 15
            reasons.append("very fast typing speed")
        elif med_cps <= 8:
            score -= 5
            reasons.append("human-range typing speed")

    shaped = 0
    for entry in commands:
        command = str(entry.get("command") or "").lower()
        if any(marker in command for marker in AUTOMATION_MARKERS):
            shaped += 1
        if "&&" in command or ";" in command or "|" in command:
            shaped += 1
    if shaped:
        points = min(20, shaped * 5)
        score += points
        reasons.append("automation-oriented command structure")

    if len(commands) >= 8:
        elapsed = float(commands[-1].get("since_start_ms") or 0)
        if elapsed <= 10_000:
            score += 15
            reasons.append("eight commands issued within 10 seconds")

    score = max(0, min(100, round(score)))
    if score >= 70:
        label = "likely_automated"
    elif score <= 30 and len(commands) >= 2:
        label = "likely_human"
    else:
        label = "uncertain"

    if not reasons:
        reasons.append("insufficient distinguishing timing signals")
    return Classification(score, label, tuple(reasons))
