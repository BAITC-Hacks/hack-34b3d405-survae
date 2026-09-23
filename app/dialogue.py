"""Conservative tool routing when AI is unavailable, not a scripted conversation.

An active calculator is not permission to consume every future message. Only a
recognizable parameter answer may continue it; everything else goes to chat.
"""
import re


def local_route(message, session):
    text = message.lower().replace("ё", "е").strip()
    if re.search(r"достав|оплат|сертифик|артикул|аналог|demo-", text):
        return "general"
    if "подсвет" in text and re.search(r"кух|столеш|рабоч.*поверх", text):
        return "kitchen_lighting"
    if re.search(r"ламп(?:а|у|ы|очка|очку|очки|очек)\b", text):
        return "lamp"
    # Whole replies only: a camera "for a dry room" must not resume a kit.
    if session.get("kit") and re.fullmatch(
        r"(?:да|нет|есть|не знаю|\d+(?:[.,]\d+)?\s*(?:м|метр\w*)?|"
        r"(?:есть |нет |без )?(?:готовая |готовой )?розетк\w*(?: рядом)?|"
        r"сухое место(?:,? вдали от воды)?|сухо|есть брызги|влажно|"
        r"(?:теплый|нейтральный)(?: свет)?|(?:3000|4000)\s*[кk]?)", text
    ):
        return "kitchen_lighting"
    # Remove only the vocabulary of parameter corrections, not arbitrary words.
    remainder = re.sub(r"(?<!\w)(?:[eе]\s*\d{2}|gu\s*\d{1,2}|gx?\s*\d{1,2})(?!\w)", " ", text)
    has_socket = remainder != text
    remainder = re.sub(r"\d+(?:[.,]\d+)?", " ", remainder)
    remainder = re.sub(r"\b(?:не|до|нет|а|или|и|нужен|нужна|нужно|более|больше|максимум|макс|"
                       r"перепутал|теперь|ровно|цоколь|мощность|свет|теплый|теплая|нейтральный|"
                       r"холодный|вт|w|к|k|ватт|ватта|ваттов|не знаю|знаю|покажи|еще|варианты)\b", " ", remainder)
    if (has_socket or session.get("lamp")) and not re.sub(r"[\s,.;:!?+\-/–—]+", "", remainder):
        return "lamp"
    return "general"
