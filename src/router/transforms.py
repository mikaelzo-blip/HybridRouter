import copy
from typing import Any
from src.schemas import ContextTransform


def strip_terminal_noise(text: str, keep_last_lines: int = 40) -> str:
    lines = text.splitlines()
    if len(lines) <= keep_last_lines:
        return text
    return "\n".join(lines[-keep_last_lines:])


def inject_system_prompt(messages: list[dict[str, Any]], content: str) -> list[dict[str, Any]]:
    new_messages = copy.deepcopy(messages)
    for msg in new_messages:
        if msg.get("role") == "system":
            existing = msg.get("content", "")
            msg["content"] = f"{existing}\n\n{content}".strip()
            return new_messages

    new_messages.insert(0, {"role": "system", "content": content})
    return new_messages


def apply_transforms(
    transforms: list[ContextTransform],
    messages: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[str]]:
    applied: list[str] = []
    current_messages = copy.deepcopy(messages)

    for tf in transforms:
        if tf.action == "strip_terminal_noise":
            keep = tf.keep_last_lines or 40
            for msg in current_messages:
                if msg.get("role") in ("user", "tool"):
                    content = msg.get("content")
                    if isinstance(content, str):
                        msg["content"] = strip_terminal_noise(content, keep)
            applied.append("strip_terminal_noise")
        elif tf.action == "inject_system_prompt":
            if tf.content:
                current_messages = inject_system_prompt(current_messages, tf.content)
            applied.append("inject_system_prompt")

    return current_messages, applied
