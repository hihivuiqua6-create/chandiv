"""Phân tích cầu Tài/Xỉu từ lịch sử quan sát được.

Đây là bộ phân tích heuristic: không thể bảo đảm kết quả và không cố gắng
suy đoán khi dữ liệu không đủ hoặc tín hiệu xung đột.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, asdict
from typing import Any, Iterable

VALID = {"TAI", "XIU"}

@dataclass
class Signal:
    name: str
    label: str
    direction: str | None
    strength: int
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _clean(history: Iterable[str]) -> list[str]:
    return [x for x in history if x in VALID]


def _bits(history: list[str]) -> str:
    return "".join("T" if x == "TAI" else "X" for x in history)


def _runs(history: list[str]) -> list[tuple[str, int]]:
    out: list[tuple[str, int]] = []
    for value in history:
        if out and out[-1][0] == value:
            out[-1] = (value, out[-1][1] + 1)
        else:
            out.append((value, 1))
    return out


def _direction(value: str) -> str:
    return "TAI" if value == "T" else "XIU"


def _alternating(bits: str, min_len: int = 4) -> bool:
    return len(bits) >= min_len and all(bits[i] != bits[i - 1] for i in range(1, len(bits)))


def analyze_bridges(history: Iterable[str], points: Iterable[int] | None = None, limit: int = 40) -> dict[str, Any]:
    h = _clean(history)[-limit:]
    p = list(points or [])[-len(h):] if h else []
    if not h:
        return {"signals": [], "dominant": None, "confidence": 0, "summary": "Chưa có dữ liệu."}

    bits = _bits(h)
    runs = _runs(h)
    signals: list[Signal] = []
    last = bits[-1]

    # Cầu bệt: cùng cửa liên tiếp; độ dài được báo rõ, không khẳng định thắng.
    tail_len = runs[-1][1]
    if tail_len >= 2:
        strength = min(96, 58 + tail_len * 6)
        signals.append(Signal("bet", "Cầu bệt", _direction(last), strength,
                              f"{_direction(last)} đang lặp {tail_len} phiên liên tiếp"))
        if tail_len >= 4:
            signals.append(Signal("bet_break", "Cảnh báo bệt dài / khả năng bẻ", "XIU" if last == "T" else "TAI",
                                  min(88, 55 + tail_len * 4),
                                  f"Chuỗi bệt đã đạt {tail_len}; theo dõi tín hiệu bẻ, không tự động đảo mù quáng"))

    # Cầu 1-1, 2-2, 3-3, 4-4, 5-5.
    if len(runs) >= 3:
        lengths = [n for _, n in runs]
        for size in range(1, 8):
            if len(lengths) >= 4 and all(n == size for n in lengths[-4:]):
                next_dir = "XIU" if runs[-1][0] == "TAI" else "TAI"
                signals.append(Signal(f"block_{size}_{size}", f"Cầu {size}-{size}", next_dir,
                                      min(94, 66 + size * 4),
                                      f"Bốn nhịp gần nhất có độ dài {size}-{size}-{size}-{size}"))
                break

    # Chuỗi 2 trở lên và mẫu lặp run-length như 321/123, 212, 2112, 1212.
    lengths = [n for _, n in runs]
    for size in range(2, 8):
        if len(lengths) >= 4 and all(n == size for n in lengths[-4:]):
            next_dir = "XIU" if runs[-1][0] == "TAI" else "TAI"
            signals.append(Signal("long_repeat", f"Cầu chuỗi {size} trở lên", next_dir, 72,
                                  f"Độ dài nhịp {size} lặp tối thiểu 4 lần"))
            break
    for pattern in ([3, 2, 1], [1, 2, 3], [2, 1, 2], [2, 1, 1, 2], [1, 2, 1, 2], [2, 1, 1, 2, 1, 2], [3, 1, 3, 1]):
        if len(lengths) >= len(pattern) and lengths[-len(pattern):] == pattern:
            next_dir = "XIU" if runs[-1][0] == "TAI" else "TAI"
            label = "Cầu " + "".join(map(str, pattern))
            signals.append(Signal("run_" + "".join(map(str, pattern)), label, next_dir, 78,
                                  f"Nhịp cuối có độ dài {'-'.join(map(str, pattern))}"))

    # Cầu đảo / xen kẽ 1-1 và các biến thể đảo chiều.
    if _alternating(bits[-12:], 4):
        signals.append(Signal("alternating", "Cầu đảo 1-1", "XIU" if last == "T" else "TAI", 84,
                              "Các phiên gần nhất đang luân phiên Tài/Xỉu"))
    if len(bits) >= 8 and _alternating(bits[-8:], 4) and bits[-8:] == bits[-8:][::-1]:
        signals.append(Signal("mirror", "Cầu nghịch đảo / đối xứng", "XIU" if last == "T" else "TAI", 76,
                              "Đoạn cuối có cấu trúc đối xứng"))

    # Mẫu chu kỳ nhị phân phổ biến và chu kỳ lặp tổng quát 2..6.
    for width in range(2, 7):
        if len(bits) >= width * 3:
            chunk = bits[-width:]
            if bits[-width * 3:] == chunk * 3:
                next_bit = chunk[(len(bits) % width)] if width else last
                signals.append(Signal(f"cycle_{width}", f"Cầu chu kỳ {width}", _direction(next_bit), 74,
                                      f"Mẫu {chunk} lặp 3 lần"))
                break

    # Cầu bẻ: hai nhịp cuối khác quy luật đang thống trị gần đây.
    if len(runs) >= 5:
        prior = [n for _, n in runs[-5:-1]]
        if len(set(prior)) == 1 and runs[-1][1] != prior[-1]:
            signals.append(Signal("break", "Cầu bẻ", None, 64,
                                  f"Nhịp mới ({runs[-1][1]}) phá quy luật độ dài {prior[-1]} trước đó"))

    # Xu hướng điểm xúc xắc: chỉ là tín hiệu phụ, không thay thế kết quả server.
    if len(p) >= 5:
        recent = p[-5:]
        avg = sum(recent) / len(recent)
        if avg >= 11.0:
            signals.append(Signal("dice_high", "Điểm xúc xắc cao", "TAI", 60, f"Trung bình 5 phiên: {avg:.2f}"))
        elif avg <= 10.0:
            signals.append(Signal("dice_low", "Điểm xúc xắc thấp", "XIU", 60, f"Trung bình 5 phiên: {avg:.2f}"))

    votes = Counter(s.direction for s in signals if s.direction in VALID)
    dominant = votes.most_common(1)[0][0] if votes else None
    total = sum(votes.values())
    confidence = int(min(95, 50 + (votes[dominant] / max(total, 1)) * 40)) if dominant else 0
    if len(h) < 5:
        confidence = min(confidence, 55)
    summary = "Chưa đủ tín hiệu rõ ràng"
    if dominant:
        summary = f"Nghiêng {dominant} với {votes[dominant]}/{total} tín hiệu; cần xem cả tên cầu và lịch sử gốc"
    return {
        "signals": [s.to_dict() for s in signals],
        "dominant": dominant,
        "confidence": confidence,
        "summary": summary,
        "run_lengths": lengths[-12:],
        "history": h,
        "points": p,
    }


def format_analysis(result: dict[str, Any], max_signals: int = 12) -> str:
    if not result.get("signals"):
        return "🧠 Chưa nhận diện được cầu đủ rõ."
    lines = [f"🧠 <b>NHẬN DIỆN CẦU</b>: {result.get('summary', '')}"]
    for item in result["signals"][:max_signals]:
        direction = item.get("direction") or "THEO DÕI"
        lines.append(f"• {item['label']}: <b>{direction}</b> ({item['strength']}%) — {item['detail']}")
    return "\n".join(lines)
