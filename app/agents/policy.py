from app.agents.base import AgentResult, EmailContext
from app.db.models import Email, Topic
from app.inference.router import InferenceRouter
from app.privacy.redaction import RedactedText
from app.rag.retrieval import Retriever


class PolicyAgent:
    """Finds the passages of the knowledge base that bear on this email (FR-7).

    Calls no language model; the only model involved is the local embedding model, and
    the query is the redacted email.
    """

    name = "policy"

    def __init__(self, *, router: InferenceRouter, retriever: Retriever) -> None:
        self._router = router
        self._retriever = retriever

    async def run(self, ctx: EmailContext) -> AgentResult:
        email = await ctx.session.get_one(Email, ctx.email_id)
        query = RedactedText(f"{email.subject_redacted or ''}\n{email.body_redacted or ''}")
        embedding = await self._router.embed_query(ctx.session, email_id=email.id, text=query)
        # "other" has no documents of its own: search everything.
        topic = email.topic.value if email.topic and email.topic is not Topic.OTHER else None
        hits = await self._retriever.search(query, embedding, topic=topic)

        # Numbered here; the drafting agent cites these numbers and nothing else.
        email.policy_context = [
            {
                "id": number,
                "title": hit.chunk.title,
                "heading": hit.chunk.heading,
                "url": hit.chunk.url,
                "fetched_at": hit.chunk.fetched_at,
                "text": hit.chunk.text,
                "semantic_rank": hit.semantic_rank,
                "keyword_rank": hit.keyword_rank,
            }
            for number, hit in enumerate(hits, start=1)
        ]
        await ctx.session.flush()
        return AgentResult()
