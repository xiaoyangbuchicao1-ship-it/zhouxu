"""Check internal source links against immutable retrieved text, never legal truth."""

import re
from urllib.parse import parse_qs, unquote, urlsplit

from backend.contracts.modules.loop import ReflectionResult
from backend.contracts.schemas.core import Error, ModuleResponse
from backend.modules.reflection.service import RuleReflection

SOURCE_LINK = re.compile(r'(?:https?://|/)[^\s<>\)\]\x60"，。；]+')
MARKER = re.compile(r"\[\[(ZX:[0-9a-f]{16})\]\]")


def expand_citations(text, corpus):
    def resolve(match):
        try:
            return f"[核对原文]({corpus.citation(match[1])})"
        except LookupError:
            return match[0]

    return MARKER.sub(resolve, text or "")


def invalid_citations(text, corpus):
    invalid = []
    for link in SOURCE_LINK.findall(text or ""):
        url = urlsplit(link)
        path = unquote(url.path)
        if path.startswith("/maritime/sources/"):
            path = "/api" + path
        if not path.startswith("/api/maritime/sources/"):
            if url.hostname and (
                url.hostname.endswith(".invalid") or url.hostname == "api.maritime"
            ):
                invalid.append(link)
            continue
        parts = path.split("/", 5)
        snapshot = parse_qs(url.query).get("snapshot", [""])[0]
        try:
            if (
                len(parts) != 6
                or parts[4] not in {"laws", "cases"}
                or not re.fullmatch("[0-9a-f]{64}", snapshot)
            ):
                raise LookupError("invalid_source")
            corpus.source(parts[4], parts[5], snapshot)
        except (LookupError, ValueError):
            invalid.append(link)
    invalid.extend(match[0] for match in MARKER.finditer(text or ""))
    return invalid


class MaritimeReflection(RuleReflection):
    def __init__(self, settings, corpus):
        super().__init__(settings)
        self.corpus = corpus

    def validate_final(self, text):
        if invalid_citations(text, self.corpus):
            return Error(
                code="citation_unresolved",
                message="正文包含无法核验的来源链接，本次未通过引用校验。已保留草稿，请重做并核对真实原文。",
                retryable=True,
            )
        return None

    def transform(self, text):
        return expand_citations(text, self.corpus)

    async def handle(self, request):
        proposed = request.input.proposed
        if proposed.kind == "answer":
            proposed.answer = self.transform(proposed.answer)
            invalid = invalid_citations(proposed.answer, self.corpus)
            if invalid:
                return ModuleResponse(
                    status="ok",
                    output=ReflectionResult(
                        decision="retry",
                        issues=["internal_citation_unresolved"],
                        feedback="引用未通过真实快照校验。请逐字复制 search_maritime_corpus 回执中的 citation 标记，系统自动转换为原文链接。不要编写网址；无法核验则撤回该引用及依赖它的确定性结论，并明确资料缺口。无效引用："
                        + ", ".join(invalid[:5]),
                    ),
                )
        return await super().handle(request)
