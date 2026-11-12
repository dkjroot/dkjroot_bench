import re
from collections import deque
from threading import Lock
from time import monotonic


def prompt_weight(value):
    if isinstance(value, dict):
        return 4 + sum(prompt_weight(key) + (768 if key == "inlineData" else prompt_weight(item))
                       for key, item in value.items() if key != "thoughtSignature")
    if isinstance(value, list):
        return 2 + sum(prompt_weight(item) for item in value)
    return 1 + len(str(value).encode("utf-8", errors="replace")) / 3


class GeminiPacer:
    def __init__(self, settings, stop):
        self.stop = stop
        self.tpm = settings.gemini_input_tpm or (2_000_000 if settings.model == "gemini-3.8-flash" else None)
        self.rpm = settings.gemini_rpm
        self.lock = Lock()
        self.sent = deque()
        self.cooldown = 0
        self.minimum_gap = 0
        self.scale = 1

    def reserve(self, weight):
        while not self.stop.is_set():
            with self.lock:
                now = monotonic()
                while self.sent and self.sent[0][0] <= now - 60:
                    self.sent.popleft()
                tokens = weight * self.scale
                due = self.cooldown
                if self.sent:
                    last_time, last_tokens = self.sent[-1]
                    due = max(due, last_time + self.minimum_gap,
                              last_time + (60 * last_tokens / (self.tpm * .9) if self.tpm else 0),
                              last_time + (60 / self.rpm if self.rpm else 0))
                    if ((self.tpm and sum(entry[1] for entry in self.sent) + tokens > self.tpm)
                            or (self.rpm and len(self.sent) >= self.rpm)):
                        due = max(due, self.sent[0][0] + 60)
                if due <= now:
                    entry = [now, tokens]
                    self.sent.append(entry)
                    return entry
            self.stop.wait(due - now)
        return None

    def measured(self, entry, weight, tokens):
        if type(tokens) is int and tokens > 0:
            with self.lock:
                entry[1] = max(entry[1], tokens)
                self.scale = max(self.scale, tokens / weight)

    def limited(self, entry, failure, retry_after):
        message = str(failure.get("message", ""))
        details = failure.get("details")
        details = [item for item in details if isinstance(item, dict)] if isinstance(details, list) else []
        violations = [item for detail in details if isinstance(detail.get("violations"), list)
                      for item in detail["violations"] if isinstance(item, dict)]
        quota = message + " " + " ".join(str(item.get("quotaId", "")) for item in violations)
        if re.search(r"per[ _-]?day|daily|billing tier cap", quota, re.I):
            return False
        delay = 0
        hints = [str(retry_after)] + [str(item.get("retryDelay", "")) for item in details]
        hint = re.search(r"retry in (\d+(?:\.\d+)?)s", message, re.I)
        if hint:
            hints.append(hint[1])
        for value in hints:
            if re.fullmatch(r"\d{1,9}(?:\.\d+)?s?", value):
                delay = max(delay, float(value.removesuffix("s")))
        # A day-scale or permanent limit needs intervention in the viewer.
        if delay > 120:
            return False
        with self.lock:
            entry[1] = 0
            self.cooldown = max(self.cooldown, monotonic() + (delay or 15))
            self.minimum_gap = 1
            for item in violations:
                metric = str(item.get("quotaMetric", ""))
                value = str(item.get("quotaValue", ""))
                if re.search(r"per[ _-]?minute", str(item.get("quotaId", "")), re.I) and value.isascii() and value.isdecimal():
                    self.lower_limit(metric, int(value))
            match = re.search(r"metric: ([\w./-]+), limit: (\d+)[^\n]*per minute", message, re.I)
            if match:
                self.lower_limit(match[1], int(match[2]))
        return True

    def lower_limit(self, metric, value):
        if value > 0 and "input_token" in metric:
            self.tpm = min(self.tpm or value, value)
        elif value > 0 and "request" in metric:
            self.rpm = min(self.rpm or value, value)
